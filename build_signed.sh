#!/usr/bin/env bash
set -euo pipefail

echo "Construyendo SecreIA con firma para macOS..."

# Verificar dependencias del sistema
echo "Verificando dependencias del sistema..."
if ! command -v brew &> /dev/null; then
    echo "Error: Homebrew no está instalado. Instálalo desde https://brew.sh"
    exit 1
fi

if ! brew list portaudio &> /dev/null; then
    echo "Instalando PortAudio..."
    brew install portaudio
fi

# Verificar certificado ANTES de empezar el build
CERT_NAME="secreia"
echo "Verificando certificado de firma..."
if ! security find-identity -v -p codesigning | grep "$CERT_NAME" > /dev/null; then
    echo ""
    echo "Error: No se encuentra el certificado '$CERT_NAME'"
    echo ""
    echo "CREAR CERTIFICADO PASO A PASO:"
    echo "1. Abre 'Acceso a Llaveros' (Keychain Access)"
    echo "2. Menu: Asistente de Certificados > Crear un Certificado"
    echo "3. Nombre: SecreIA Developer"
    echo "4. Tipo de Certificado: Firma de Código"
    echo "5. Dejar resto por defecto y hacer clic en 'Crear'"
    echo "6. Ejecutar este script de nuevo"
    echo ""
    exit 1
fi

echo "Certificado encontrado: $CERT_NAME"

# Limpiar procesos existentes
echo "Limpiando procesos anteriores..."
if pgrep -f "SecreIA" > /dev/null; then
    pkill -f "SecreIA" || true
    sleep 2
fi

# Limpiar archivos de construcción
echo "Limpiando archivos de construcción..."
rm -rf .venv build/ dist/ __pycache__/ app/__pycache__/ *.spec entitlements.plist 2>/dev/null || true

# Limpiar base de datos incompatible
echo "Limpiando base de datos ChromaDB incompatible..."
rm -rf ~/.secretaria_ai/chroma/ 2>/dev/null || true

# Crear entorno virtual
echo "Creando entorno virtual con Python 3.11..."
python3.11 -m venv .venv
source .venv/bin/activate

# Configurar variables de entorno para pyaudio
echo "Configurando variables de entorno para pyaudio..."
export PORTAUDIO_PATH=$(brew --prefix portaudio)
export LDFLAGS="-L$PORTAUDIO_PATH/lib"
export CPPFLAGS="-I$PORTAUDIO_PATH/include"

# Instalar dependencias básicas primero
echo "Instalando dependencias básicas..."
python -m pip install --upgrade pip wheel setuptools

# Instalar pyaudio
echo "Instalando pyaudio..."
pip install pyaudio

# Instalar el resto de dependencias
echo "Instalando resto de dependencias..."
pip install -r requirements.txt pyinstaller

# Verificar instalación crítica
echo "Verificando instalaciones críticas..."
python -c "
import chromadb
import PySide6
import openai
import sounddevice
import soundfile
import speech_recognition
import pyaudio
print('Todas las dependencias críticas están instaladas')
" || {
    echo "Error en dependencias críticas"
    exit 1
}

APPNAME="SecreIA"

echo "Construyendo aplicación..."
pyinstaller \
  --noconfirm \
  --windowed \
  --name "$APPNAME" \
  --icon="assets/icon.icns" \
  --hidden-import=chromadb.telemetry.impl.noop \
  --hidden-import=chromadb.api \
  --hidden-import=chromadb.db \
  --hidden-import=posthog \
  --hidden-import=tqdm \
  --hidden-import=requests \
  --hidden-import=numpy \
  --hidden-import=sqlite3 \
  --hidden-import=sounddevice \
  --hidden-import=soundfile \
  --hidden-import=speech_recognition \
  --hidden-import=pyaudio \
  --collect-all chromadb \
  --collect-all sounddevice \
  --collect-all speech_recognition \
  --osx-bundle-identifier="com.secretaria.SecreIA" \
  run_app.py

echo "Configurando bundle de macOS..."

APP_PATH="dist/$APPNAME.app"

# Asegurar estructura de directorios
mkdir -p "$APP_PATH/Contents/Resources"
mkdir -p "$APP_PATH/Contents/MacOS"

# Copiar icono
if [ -f "assets/icon.icns" ]; then
    cp -f "assets/icon.icns" "$APP_PATH/Contents/Resources/icon.icns"
fi

# Crear Info.plist con permisos
echo "Creando Info.plist con permisos correctos..."
cat > "$APP_PATH/Contents/Info.plist" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>es</string>
    <key>CFBundleExecutable</key>
    <string>SecreIA</string>
    <key>CFBundleIdentifier</key>
    <string>com.secretaria.SecreIA</string>
    <key>CFBundleName</key>
    <string>SecreIA</string>
    <key>CFBundleDisplayName</key>
    <string>SecreIA</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>CFBundleVersion</key>
    <string>1.0.0</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleIconFile</key>
    <string>icon.icns</string>
    <key>LSMinimumSystemVersion</key>
    <string>11.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSMicrophoneUsageDescription</key>
    <string>SecreIA necesita acceso al micrófono para transcribir audio en tiempo real y proporcionar asistencia inteligente.</string>
    <key>NSAudioCaptureUsageDescription</key>
    <string>SecreIA requiere captura de audio para procesar y transcribir conversaciones en tiempo real.</string>
    <key>NSSpeechRecognitionUsageDescription</key>
    <string>SecreIA utiliza reconocimiento de voz para transcripción rápida y precisa.</string>
    <key>LSApplicationCategoryType</key>
    <string>public.app-category.productivity</string>
</dict>
</plist>
EOF

# Configurar permisos básicos
echo "Configurando permisos del bundle..."
chmod -R 755 "$APP_PATH"

# Crear entitlements para firma
echo "Creando entitlements..."
cat > entitlements.plist << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.security.device.microphone</key>
    <true/>
    <key>com.apple.security.device.audio-input</key>
    <true/>
    <key>com.apple.security.cs.allow-jit</key>
    <true/>
    <key>com.apple.security.cs.allow-unsigned-executable-memory</key>
    <true/>
    <key>com.apple.security.cs.disable-library-validation</key>
    <true/>
</dict>
</plist>
EOF

# Firmar frameworks y librerías primero
echo "Firmando frameworks internos..."
find "$APP_PATH" -name "*.dylib" -exec codesign --force --sign "$CERT_NAME" {} \; 2>/dev/null || true
find "$APP_PATH" -name "*.so" -exec codesign --force --sign "$CERT_NAME" {} \; 2>/dev/null || true

# Firmar aplicación principal
echo "Firmando aplicación principal..."
codesign --force --sign "$CERT_NAME" --entitlements entitlements.plist --deep "$APP_PATH"

# Verificar firma
echo "Verificando firma..."
if codesign --verify --deep --strict "$APP_PATH"; then
    echo "Firma exitosa"
else
    echo "Error en firma - continuando de todas formas"
fi

# Remover quarantine antes de instalar
echo "Removiendo quarantine attributes..."
xattr -rd com.apple.quarantine "$APP_PATH" 2>/dev/null || true

# Instalar en Applications
echo "Instalando en /Applications..."
if [ -d "/Applications/SecreIA.app" ]; then
    rm -rf "/Applications/SecreIA.app"
fi
cp -R "$APP_PATH" "/Applications/"

# Remover quarantine de la copia instalada
xattr -rd com.apple.quarantine "/Applications/SecreIA.app" 2>/dev/null || true

# Registrar aplicación
echo "Registrando aplicación..."
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "/Applications/SecreIA.app"

# Limpiar archivos temporales
rm -f entitlements.plist

echo ""
echo "=== BUILD Y FIRMA COMPLETADOS ==="
echo ""
echo "App instalada en: /Applications/SecreIA.app"
echo ""
echo "PASOS SIGUIENTES:"
echo "1. Ejecutar: open /Applications/SecreIA.app"
echo "2. Si aparece dialogo de permisos, aceptar"
echo "3. Ve a: Sistema > Privacidad y Seguridad > Micrófono"
echo "4. Asegúrate de que SecreIA esté activado"
echo "5. Reinicia la app si es necesario"
echo ""
echo "Para debug: /Applications/SecreIA.app/Contents/MacOS/SecreIA"