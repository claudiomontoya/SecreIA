@echo off
setlocal enabledelayedexpansion

echo ========================================
echo Construyendo SecreIA para Windows...
echo ========================================

:: Verificar Python
python --version >nul 2>&1
if errorlevel 1 (
    echo Error: Python no está instalado o no está en PATH
    echo Descarga Python desde: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Obtener versión de Python
for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PY_VERSION=%%i
echo Usando Python !PY_VERSION!

:: Verificar que sea Python 3.8+
python -c "import sys; exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1
if errorlevel 1 (
    echo Error: Se requiere Python 3.8 o superior
    pause
    exit /b 1
)

:: Limpiar archivos anteriores
echo Limpiando archivos de construcciones anteriores...
if exist .venv rmdir /s /q .venv 2>nul
if exist build rmdir /s /q build 2>nul
if exist dist rmdir /s /q dist 2>nul
if exist __pycache__ rmdir /s /q __pycache__ 2>nul
if exist app\__pycache__ rmdir /s /q app\__pycache__ 2>nul
del *.spec 2>nul

:: Crear entorno virtual
echo Creando entorno virtual...
python -m venv .venv
if errorlevel 1 (
    echo Error: No se pudo crear el entorno virtual
    pause
    exit /b 1
)

:: Activar entorno virtual
echo Activando entorno virtual...
call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo Error: No se pudo activar el entorno virtual
    pause
    exit /b 1
)

:: Actualizar pip
echo Actualizando pip...
python -m pip install --upgrade pip wheel setuptools

:: Instalar dependencias
echo Instalando dependencias...
pip install -r requirements_windows.txt
if errorlevel 1 (
    echo Error: No se pudieron instalar las dependencias
    pause
    exit /b 1
)

:: Instalar PyInstaller
echo Instalando PyInstaller...
pip install pyinstaller
if errorlevel 1 (
    echo Error: No se pudo instalar PyInstaller
    pause
    exit /b 1
)

:: Verificar instalaciones críticas
echo Verificando instalaciones críticas...
python -c "import PySide6; import openai; import chromadb; import sounddevice; import speech_recognition; print('Todas las dependencias están instaladas')" 2>nul
if errorlevel 1 (
    echo Error: Falta alguna dependencia crítica
    pause
    exit /b 1
)

:: Verificar que existe el icono
if not exist "assets\icon.ico" (
    echo Advertencia: No se encuentra assets\icon.ico
    echo Copiando icono por defecto...
    if not exist assets mkdir assets
    echo. > assets\icon.ico
)

:: Construir aplicación
echo ========================================
echo Construyendo aplicación con PyInstaller...
echo ========================================

pyinstaller ^
  --noconfirm ^
  --windowed ^
  --name "SecreIA" ^
  --icon="assets\icon.ico" ^
  --add-data="assets;assets" ^
  --hidden-import=chromadb.telemetry.impl.noop ^
  --hidden-import=chromadb.api ^
  --hidden-import=chromadb.db ^
  --hidden-import=posthog ^
  --hidden-import=tqdm ^
  --hidden-import=requests ^
  --hidden-import=numpy ^
  --hidden-import=sqlite3 ^
  --hidden-import=sounddevice ^
  --hidden-import=soundfile ^
  --hidden-import=speech_recognition ^
  --hidden-import=pyaudio ^
  --hidden-import=pyperclip ^
  --hidden-import=pygame ^
  --hidden-import=pytz ^
  --collect-all chromadb ^
  --collect-all sounddevice ^
  --collect-all speech_recognition ^
  --collect-all numpy ^
  run_app.py

if errorlevel 1 (
    echo Error: Falló la construcción con PyInstaller
    pause
    exit /b 1
)

:: Verificar que se creó el ejecutable
if not exist "dist\SecreIA\SecreIA.exe" (
    echo Error: No se generó el ejecutable
    pause
    exit /b 1
)

echo ========================================
echo ¡Build completado exitosamente!
echo ========================================
echo.
echo Aplicación creada en: dist\SecreIA\
echo Ejecutable: dist\SecreIA\SecreIA.exe
echo.
echo Para probar la aplicación:
echo   cd dist\SecreIA
echo   SecreIA.exe
echo.
echo Para crear un instalador, puedes usar:
echo   - Inno Setup (recomendado)
echo   - NSIS
echo   - WiX Toolset
echo.
pause