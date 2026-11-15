@echo off
cd /d "%~dp0"
title DirectLine - Servidor local

if not exist .venv (
  echo [1/3] Creando entorno de Python...
  py -m venv .venv
  if errorlevel 1 goto error
)

call .venv\Scripts\activate.bat

echo [2/3] Instalando o actualizando dependencias...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 goto error

if not exist .env (
  copy .env.example .env >nul
  echo [AVISO] Se creo .env con valores de prueba.
  echo Se abrira .env. Cambia SECRET_KEY y ADMIN_PASSWORD y guarda el archivo.
  start "" /wait notepad.exe .env
)

echo [3/3] Iniciando DirectLine...
echo Abre http://127.0.0.1:5000 en este equipo.
echo Para probar desde otro dispositivo de la misma red usa la IP local de esta PC y el puerto 5000.
python app.py
pause
goto end

:error
echo.
echo Ocurrio un error. Revisa que Python este instalado y agregado al PATH.
pause
:end
