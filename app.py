"""Servidor principal de DirectLine: Flask + Socket.IO + SQLite."""
from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from flask import Flask, current_app, jsonify, render_template, request, session
from flask_socketio import emit, join_room
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from config import Config
from extensions import db, socketio
from models import Conversation, DeviceKey, FriendRequest, Friendship, Message, User, utcnow
from security import admin_required, current_user_id, login_required, new_device_key, sha256

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,24}$")
DEVICE_ID_RE = re.compile(r"^[a-f0-9-]{20,80}$", re.I)
ONLINE_SIDS: dict[int, set[str]] = defaultdict(set)
MESSAGE_TIMES: dict[int, deque[float]] = defaultdict(deque)


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    socketio.init_app(app)

    with app.app_context():
        db.create_all()

    register_routes(app)
    register_socket_events()
    return app


def _json_error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def _normalize_username(value: str) -> str:
    return value.strip().lower()


def _friend_ids(user_id: int) -> list[int]:
    rows = Friendship.query.filter(
        or_(Friendship.user_low_id == user_id, Friendship.user_high_id == user_id)
    ).all()
    return [row.user_high_id if row.user_low_id == user_id else row.user_low_id for row in rows]


def _is_friend(first_id: int, second_id: int) -> bool:
    low, high = sorted((first_id, second_id))
    return Friendship.query.filter_by(user_low_id=low, user_high_id=high).first() is not None


def _get_or_create_conversation(first_id: int, second_id: int) -> Conversation:
    low, high = sorted((first_id, second_id))
    conversation = Conversation.query.filter_by(user_low_id=low, user_high_id=high).first()
    if conversation:
        return conversation
    conversation = Conversation(user_low_id=low, user_high_id=high)
    db.session.add(conversation)
    db.session.flush()
    return conversation


def register_routes(app: Flask) -> None:
    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/admin")
    def admin_page():
        return render_template("admin.html")

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "service": "DirectLine"})

    @app.post("/api/auth/login")
    def login():
        data = request.get_json(silent=True) or {}
        username = _normalize_username(str(data.get("username", "")))
        key = str(data.get("device_key", "")).strip().upper()
        device_id = str(data.get("device_id", "")).strip().lower()

        if not USERNAME_RE.fullmatch(username):
            return _json_error("El usuario debe tener entre 3 y 24 caracteres: letras, números o _.")
        if len(key) < 16 or len(key) > 24:
            return _json_error("La clave del dispositivo no tiene el formato esperado.")
        if not DEVICE_ID_RE.fullmatch(device_id):
            return _json_error("No se pudo identificar este navegador. Recarga la página e inténtalo otra vez.")

        device = DeviceKey.query.filter_by(key_hash=sha256(key)).first()
        if not device or device.revoked_at is not None or not device.user:
            return _json_error("Clave incorrecta o desactivada. Pide una clave al administrador.", 403)
        if device.user.is_disabled:
            return _json_error("Esta cuenta está desactivada. Contacta al administrador.", 403)
        if device.user.username != username:
            return _json_error("Esa clave pertenece a otro nombre de usuario.", 403)

        device_hash = sha256(device_id)
        if device.claimed_device_hash and device.claimed_device_hash != device_hash:
            return _json_error("Esta clave ya está vinculada a otro navegador o dispositivo.", 403)
        if not device.claimed_device_hash:
            device.claimed_device_hash = device_hash

        token = __import__("secrets").token_urlsafe(32)
        device.session_token_hash = sha256(token)
        device.last_used_at = utcnow()
        device.user.last_seen_at = utcnow()
        db.session.commit()

        session.clear()
        session.permanent = True
        session["user_id"] = device.user_id
        session["device_key_id"] = device.id
        session["session_token"] = token
        return jsonify({"ok": True, "user": device.user.public_dict(), "message": "¡Bienvenido!"})

    @app.post("/api/auth/logout")
    @login_required
    def logout():
        device = db.session.get(DeviceKey, session.get("device_key_id"))
        if device:
            device.session_token_hash = None
            db.session.commit()
        session.clear()
        return jsonify({"ok": True})

    @app.get("/api/me")
    @login_required
    def me():
        user = db.session.get(User, current_user_id())
        return jsonify({"user": user.public_dict(online=True)})

    @app.get("/api/friends")
    @login_required
    def friends():
        user_id = current_user_id()
        ids = _friend_ids(user_id)
        users = User.query.filter(User.id.in_(ids)).order_by(User.display_name.asc()).all() if ids else []
        return jsonify({"friends": [u.public_dict(online=bool(ONLINE_SIDS.get(u.id))) for u in users]})

    @app.get("/api/friend-requests")
    @login_required
    def friend_requests():
        user_id = current_user_id()
        incoming = FriendRequest.query.filter_by(receiver_id=user_id, status="pending").order_by(FriendRequest.created_at.desc()).all()
        outgoing = FriendRequest.query.filter_by(sender_id=user_id, status="pending").order_by(FriendRequest.created_at.desc()).all()
        return jsonify({
            "incoming": [{"id": r.id, "user": r.sender.public_dict()} for r in incoming],
            "outgoing": [{"id": r.id, "user": r.receiver.public_dict()} for r in outgoing],
        })

    @app.post("/api/friend-requests")
    @login_required
    def send_friend_request():
        data = request.get_json(silent=True) or {}
        username = _normalize_username(str(data.get("username", "")))
        if not USERNAME_RE.fullmatch(username):
            return _json_error("Escribe un nombre de usuario válido.")
        sender_id = current_user_id()
        target = User.query.filter_by(username=username).first()
        if not target or target.is_disabled:
            return _json_error("No encontramos a ese usuario.", 404)
        if target.id == sender_id:
            return _json_error("No puedes agregarte a ti mismo.")
        if _is_friend(sender_id, target.id):
            return _json_error("Ya son amigos.", 409)

        existing = FriendRequest.query.filter(
            or_(
                (FriendRequest.sender_id == sender_id) & (FriendRequest.receiver_id == target.id),
                (FriendRequest.sender_id == target.id) & (FriendRequest.receiver_id == sender_id),
            ), FriendRequest.status == "pending"
        ).first()
        if existing:
            if existing.sender_id == target.id:
                return _json_error("Esta persona ya te envió una solicitud. Acéptala en Solicitudes.", 409)
            return _json_error("Ya enviaste una solicitud a esa persona.", 409)

        item = FriendRequest(sender_id=sender_id, receiver_id=target.id)
        db.session.add(item)
        db.session.commit()
        socketio.emit("friend_request:new", {"from": db.session.get(User, sender_id).public_dict()}, to=f"user:{target.id}")
        return jsonify({"ok": True, "message": "Solicitud enviada."}), 201

    @app.post("/api/friend-requests/<int:request_id>/accept")
    @login_required
    def accept_friend_request(request_id: int):
        user_id = current_user_id()
        item = db.session.get(FriendRequest, request_id)
        if not item or item.receiver_id != user_id or item.status != "pending":
            return _json_error("La solicitud no existe o ya fue atendida.", 404)
        low, high = sorted((item.sender_id, item.receiver_id))
        db.session.add(Friendship(user_low_id=low, user_high_id=high))
        item.status = "accepted"
        conversation = _get_or_create_conversation(low, high)
        db.session.commit()
        socketio.emit("friend_request:accepted", {"user_id": user_id}, to=f"user:{item.sender_id}")
        return jsonify({"ok": True, "conversation_id": conversation.id})

    @app.post("/api/friend-requests/<int:request_id>/decline")
    @login_required
    def decline_friend_request(request_id: int):
        item = db.session.get(FriendRequest, request_id)
        if not item or item.receiver_id != current_user_id() or item.status != "pending":
            return _json_error("La solicitud no existe o ya fue atendida.", 404)
        item.status = "declined"
        db.session.commit()
        return jsonify({"ok": True})

    @app.get("/api/conversations")
    @login_required
    def conversations():
        user_id = current_user_id()
        rows = Conversation.query.filter(
            or_(Conversation.user_low_id == user_id, Conversation.user_high_id == user_id)
        ).all()
        result = []
        for row in rows:
            other = db.session.get(User, row.other_user_id(user_id))
            last = Message.query.filter_by(conversation_id=row.id).order_by(Message.id.desc()).first()
            result.append({
                "id": row.id,
                "friend": other.public_dict(online=bool(ONLINE_SIDS.get(other.id))),
                "last_message": last.body if last else "Todavía no hay mensajes",
                "last_message_at": last.created_at.isoformat() if last else row.created_at.isoformat(),
            })
        result.sort(key=lambda x: x["last_message_at"], reverse=True)
        return jsonify({"conversations": result})

    @app.get("/api/conversations/<int:conversation_id>/messages")
    @login_required
    def get_messages(conversation_id: int):
        user_id = current_user_id()
        conversation = db.session.get(Conversation, conversation_id)
        if not conversation or not conversation.includes(user_id):
            return _json_error("No tienes acceso a esta conversación.", 403)
        try:
            before_id = int(request.args.get("before_id", "0"))
            limit = min(max(int(request.args.get("limit", "40")), 1), 100)
        except ValueError:
            return _json_error("Parámetros de paginación inválidos.")
        query = Message.query.filter_by(conversation_id=conversation_id)
        if before_id > 0:
            query = query.filter(Message.id < before_id)
        rows = query.order_by(Message.id.desc()).limit(limit).all()
        rows.reverse()
        return jsonify({"messages": [m.public_dict() for m in rows], "has_more": len(rows) == limit})

    @app.get("/api/search-users")
    @login_required
    def search_users():
        query = _normalize_username(request.args.get("q", ""))[:24]
        if len(query) < 2:
            return jsonify({"users": []})
        user_id = current_user_id()
        ids = _friend_ids(user_id)
        users = User.query.filter(User.username.contains(query), User.id != user_id, User.is_disabled.is_(False)).limit(10).all()
        return jsonify({"users": [u.public_dict(online=bool(ONLINE_SIDS.get(u.id))) | {"already_friend": u.id in ids} for u in users]})

    @app.post("/api/admin/device-keys")
    @admin_required
    def create_device_key():
        data = request.get_json(silent=True) or {}
        username = _normalize_username(str(data.get("username", "")))
        display_name = str(data.get("display_name", "")).strip()[:40]
        label = str(data.get("label", "iPad")).strip()[:60] or "iPad"
        if not USERNAME_RE.fullmatch(username):
            return _json_error("El usuario debe tener entre 3 y 24 caracteres: letras, números o _.")
        if not display_name:
            display_name = username
        user = User.query.filter_by(username=username).first()
        if user and user.is_disabled:
            return _json_error("Esa cuenta está desactivada.", 409)
        if not user:
            user = User(username=username, display_name=display_name)
            db.session.add(user)
            db.session.flush()
        key = new_device_key()
        device = DeviceKey(label=label, key_hash=sha256(key), user_id=user.id)
        db.session.add(device)
        db.session.commit()
        # La clave solo se devuelve al crearla; después solo existe su hash.
        return jsonify({"ok": True, "username": username, "display_name": user.display_name, "label": label, "device_key": key}), 201

    @app.get("/api/admin/device-keys")
    @admin_required
    def list_device_keys():
        rows = DeviceKey.query.order_by(DeviceKey.created_at.desc()).limit(200).all()
        return jsonify({"devices": [{
            "id": d.id,
            "label": d.label,
            "username": d.user.username if d.user else "sin asignar",
            "revoked": d.revoked_at is not None,
            "claimed": d.claimed_device_hash is not None,
            "last_used_at": d.last_used_at.isoformat() if d.last_used_at else None,
        } for d in rows]})

    @app.post("/api/admin/device-keys/<int:device_id>/revoke")
    @admin_required
    def revoke_device_key(device_id: int):
        device = db.session.get(DeviceKey, device_id)
        if not device:
            return _json_error("No encontramos esa clave.", 404)
        device.revoked_at = utcnow()
        device.session_token_hash = None
        db.session.commit()
        return jsonify({"ok": True})

    @app.errorhandler(404)
    def not_found(_error):
        if request.path.startswith("/api/"):
            return jsonify({"error": "Ruta no encontrada."}), 404
        return "Página no encontrada", 404


def _socket_user_id() -> int | None:
    return current_user_id()


def register_socket_events() -> None:
    @socketio.on("connect")
    def on_connect(auth=None):
        user_id = _socket_user_id()
        if not user_id:
            return False
        ONLINE_SIDS[user_id].add(request.sid)
        join_room(f"user:{user_id}")
        User.query.filter_by(id=user_id).update({"last_seen_at": utcnow()})
        db.session.commit()
        for friend_id in _friend_ids(user_id):
            socketio.emit("presence:update", {"user_id": user_id, "online": True}, to=f"user:{friend_id}")

    @socketio.on("disconnect")
    def on_disconnect(reason=None):
        user_id = _socket_user_id()
        if not user_id:
            return
        ONLINE_SIDS[user_id].discard(request.sid)
        if not ONLINE_SIDS[user_id]:
            ONLINE_SIDS.pop(user_id, None)
            for friend_id in _friend_ids(user_id):
                socketio.emit("presence:update", {"user_id": user_id, "online": False}, to=f"user:{friend_id}")

    @socketio.on("conversation:join")
    def on_conversation_join(data):
        user_id = _socket_user_id()
        if not user_id:
            return {"ok": False, "error": "Sesión vencida."}
        try:
            conversation_id = int((data or {}).get("conversation_id"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "Conversación inválida."}
        conversation = db.session.get(Conversation, conversation_id)
        if not conversation or not conversation.includes(user_id):
            return {"ok": False, "error": "No tienes acceso a esa conversación."}
        join_room(f"conversation:{conversation_id}")
        return {"ok": True}

    @socketio.on("message:send")
    def on_message_send(data):
        user_id = _socket_user_id()
        if not user_id:
            return {"ok": False, "error": "Inicia sesión otra vez."}
        data = data or {}
        try:
            conversation_id = int(data.get("conversation_id"))
        except (TypeError, ValueError):
            return {"ok": False, "error": "Conversación inválida."}
        body = str(data.get("body", "")).strip()
        max_length = int(current_app.config["MAX_MESSAGE_LENGTH"])
        if not body:
            return {"ok": False, "error": "El mensaje está vacío."}
        if len(body) > max_length:
            return {"ok": False, "error": f"El máximo es de {max_length} caracteres."}
        conversation = db.session.get(Conversation, conversation_id)
        if not conversation or not conversation.includes(user_id):
            return {"ok": False, "error": "No tienes acceso a esa conversación."}

        now = time.monotonic()
        times = MESSAGE_TIMES[user_id]
        while times and now - times[0] > 10:
            times.popleft()
        if len(times) >= 20:
            return {"ok": False, "error": "Vas muy rápido. Espera unos segundos."}
        times.append(now)

        message = Message(conversation_id=conversation_id, sender_id=user_id, body=body)
        db.session.add(message)
        db.session.commit()
        payload = message.public_dict()
        join_room(f"conversation:{conversation_id}")
        emit("message:new", payload, to=f"conversation:{conversation_id}")
        return {"ok": True, "message": payload}

    @socketio.on("typing:start")
    def on_typing_start(data):
        user_id = _socket_user_id()
        if not user_id:
            return
        try:
            conversation_id = int((data or {}).get("conversation_id"))
        except (TypeError, ValueError):
            return
        conversation = db.session.get(Conversation, conversation_id)
        if conversation and conversation.includes(user_id):
            emit("typing:update", {"user_id": user_id, "typing": True}, to=f"conversation:{conversation_id}", include_self=False)

    @socketio.on("typing:stop")
    def on_typing_stop(data):
        user_id = _socket_user_id()
        if not user_id:
            return
        try:
            conversation_id = int((data or {}).get("conversation_id"))
        except (TypeError, ValueError):
            return
        conversation = db.session.get(Conversation, conversation_id)
        if conversation and conversation.includes(user_id):
            emit("typing:update", {"user_id": user_id, "typing": False}, to=f"conversation:{conversation_id}", include_self=False)


app = create_app()


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)
