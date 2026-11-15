# DirectLine

Chat web para amigos, construido con **Python + Flask + Flask-SocketIO**, HTML, CSS y JavaScript. Usa SQLite para guardar cuentas, amistades, conversaciones y mensajes.

## Lo que ya incluye

- Acceso con una clave distinta por dispositivo.
- Una clave se asigna a un usuario y se vincula al primer navegador que la utiliza.
- Solicitudes de amistad: enviar, aceptar y rechazar.
- Conversaciones privadas entre amigos.
- Mensajes guardados en SQLite y enviados en tiempo real con Socket.IO.
- Indicador de conexión y aviso de que alguien está escribiendo.
- Panel de administración para crear y revocar claves.
- Diseño adaptable a móvil, tablet y computadora.
- Validación de entradas, sesión HttpOnly, hash SHA-256 de las claves, límite básico de mensajes y salida de texto segura contra HTML inyectado.

## Requisitos

- Windows 10/11, macOS o Linux.
- Python 3.10 o superior.
- Conexión a internet la primera vez para instalar dependencias y cargar Socket.IO desde su CDN.

## Inicio en Windows

1. Descomprime la carpeta `DirectLine`.
2. Instala Python desde https://www.python.org/downloads/ y activa la opción **Add Python to PATH** durante la instalación.
3. Abre la carpeta y haz doble clic en `run.bat`.
4. La primera ejecución crea un entorno virtual, instala dependencias y copia `.env.example` a `.env`. Se abrirá el Bloc de notas para que cambies `SECRET_KEY` y `ADMIN_PASSWORD`; guarda el archivo y ciérralo.
5. Abre `http://127.0.0.1:5000`.
6. Entra a `http://127.0.0.1:5000/admin`, escribe la contraseña que guardaste en `.env` y crea una clave para cada amigo/dispositivo.
7. Comparte cada clave por privado con su dueño. La clave solo se muestra al crearla: guárdala y entrégala en ese momento.

## Probar desde otros dispositivos de la misma casa

1. Conecta la computadora y los iPad a la misma red Wi-Fi.
2. Averigua la IP local de la computadora con `ipconfig` en CMD; busca la dirección IPv4 del adaptador Wi-Fi.
3. En el iPad abre `http://IP-DE-TU-PC:5000` (sustituye el texto por la IPv4 real).
4. Si Windows Firewall pregunta, permite Python en redes privadas.
5. Cada persona entra con su usuario y su propia clave.

La dirección local solo funciona dentro de esa red. No publiques tu IP local ni abras puertos del router para intentar hacerlo accesible desde internet.

## Publicarlo en internet para amigos que están lejos

El archivo `render.yaml` prepara el servidor para Render. La aplicación usa Socket.IO y el servidor de producción está configurado con Gunicorn en modo threaded y `simple-websocket`, que es una combinación compatible con Flask-SocketIO.

### 1. Crear una base de datos PostgreSQL

Para no perder los mensajes cuando el servicio web se reinicie, usa una base PostgreSQL externa en lugar de SQLite. Una opción para pruebas es Neon: crea un proyecto PostgreSQL, copia la cadena de conexión (`postgresql://...`) y guárdala en un lugar privado. El plan gratuito actual incluye límites de cómputo y almacenamiento y puede suspender el cómputo al agotar la cuota mensual; no lo consideres almacenamiento de producción ni una garantía de servicio continuo.

### 2. Subir el proyecto a GitHub

1. Crea un repositorio privado en GitHub.
2. Descomprime esta carpeta y sube su contenido al repositorio.
3. No subas `.env`, bases de datos locales, contraseñas ni la carpeta `.venv`. El archivo `.gitignore` ya excluye los archivos locales importantes.

### 3. Crear el servidor en Render

1. Abre Render y conecta tu cuenta de GitHub.
2. Selecciona **New > Blueprint** y elige el repositorio de DirectLine.
3. Render detectará `render.yaml`. Cuando lo solicite, escribe `ADMIN_PASSWORD`: será la contraseña para abrir `/admin`. No la compartas.
4. En la configuración del servicio, confirma que `DATABASE_URL` tenga la URL PostgreSQL que copiaste de Neon.
5. Confirma el despliegue. Render instalará `requirements.txt` y arrancará Gunicorn.
6. Cuando termine, abre la dirección `https://...onrender.com/health`. Debe responder `{"status":"ok","service":"DirectLine"}`.
7. Entra a `https://TU-DOMINIO.onrender.com/admin` y genera una clave distinta para cada amigo y dispositivo.

### 4. Probar el chat en vivo

Abre la dirección pública en dos dispositivos distintos. Crea claves diferentes desde el panel de administrador, inicia sesión con cada usuario, envía una solicitud de amistad, acéptala y prueba mandar mensajes. La dirección usa HTTPS y Socket.IO negocia la conexión segura WebSocket (`wss`) cuando el navegador lo permite.

### Límites del plan gratis

- Render ofrece instancias web gratuitas para pruebas, pero se duermen tras 15 minutos sin tráfico entrante y pueden tardar alrededor de un minuto en arrancar otra vez.
- El PostgreSQL gratis de Render caduca a los 30 días, por eso este proyecto no lo usa como base principal.
- Neon ofrece un nivel gratuito con cuotas de cómputo y almacenamiento; al llegar a sus límites, la base puede suspenderse hasta el siguiente periodo. Consulta sus límites actuales antes de depender de él.
- Por estas limitaciones, el modo gratuito es para pruebas con amigos, no para prometer disponibilidad 24/7. Para un chat siempre disponible se necesita un plan de hosting y base de datos que no se duerman ni caduquen.

### Seguridad importante

- Mantén privado el repositorio mientras sea un proyecto de prueba.
- Nunca compartas `ADMIN_PASSWORD`, `SECRET_KEY` ni `DATABASE_URL` en un chat, captura o repositorio.
- Genera una clave individual por persona/dispositivo y revócala si se pierde el equipo.
- Esta versión vincula la clave a un identificador guardado en el navegador; no puede certificar de forma infalible que el hardware sea físicamente un iPad.
- Antes de invitar a más personas, conviene añadir protección CSRF completa, recuperación de cuentas, bloqueo y reporte, moderación y copias de seguridad automatizadas.


## Ejecutar pruebas

```bash
python -m pip install pytest
python -m pytest -q
```

## Cómo funciona la clave por dispositivo

El administrador crea una clave asignada a un nombre de usuario. La base de datos guarda un hash de la clave, no la clave original. La primera vez que se usa, queda vinculada a un identificador aleatorio guardado en el navegador. Si se intenta utilizar la misma clave desde otro navegador o dispositivo, se rechaza. Si el usuario borra los datos del navegador, cambia de navegador o pierde su iPad, el administrador debe revocar la clave anterior y crear una nueva.

**Importante:** el navegador no puede demostrar por sí solo que el hardware sea físicamente un iPad. Esta versión ata la clave a ese navegador mediante un identificador local; no es una huella infalible del equipo.

## Límites y siguientes mejoras

Esta es una primera versión funcional, no un servicio auditado para información sensible. Antes de abrirla al público conviene añadir HTTPS obligatorio, CSRF reforzado para administración, recuperación de cuentas, bloqueo/reporte, moderación, copias de seguridad, registro de auditoría y una base de datos de producción. No compartas información privada o sensible por una instalación de prueba.

El proyecto mantiene el código organizado y evita inflar artificialmente el número de líneas: una aplicación de chat pequeña no necesita 10,000 líneas para funcionar bien. Es preferible código legible, comprobable y mantenible a miles de líneas repetidas.
