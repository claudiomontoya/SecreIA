#!/usr/bin/env bash
set -euo pipefail

echo "🚀 Construyendo SecreIA para macOS..."

# Verificar dependencias del sistema
echo "🔍 Verificando dependencias del sistema..."
if ! command -v brew &> /dev/null; then
    echo "❌ Homebrew no está instalado. Instálalo desde https://brew.sh"
    exit 1
fi

if ! brew list portaudio &> /dev/null; then
    echo "📦 Instalando PortAudio..."
    brew install portaudio
fi

# Limpiar procesos existentes
echo "📋 Limpiando procesos anteriores..."
if pgrep -f "SecreIA" > /dev/null; then
    pkill -f "SecreIA" || true
    sleep 2
fi

# Limpiar archivos de construcción
echo "🗑️  Limpiando archivos de construcción..."
sudo rm -rf .venv build/ dist/ __pycache__/ app/__pycache__/ *.spec 2>/dev/null || true

# Limpiar base de datos incompatible
echo "🗄️  Limpiando base de datos ChromaDB incompatible..."
rm -rf ~/.secretaria_ai/chroma/ 2>/dev/null || true

# Crear entorno virtual
echo "🐍 Creando entorno virtual con Python 3.11..."
python3.11 -m venv .venv
source .venv/bin/activate

# Configurar variables de entorno para pyaudio
echo "⚙️  Configurando variables de entorno para pyaudio..."
export LDFLAGS="-L$(brew --prefix portaudio)/lib"
export CPPFLAGS="-I$(brew --prefix portaudio)/include"

# Instalar dependencias básicas primero
echo "📦 Instalando dependencias básicas..."
python -m pip install --upgrade pip wheel setuptools

# Instalar pyaudio con configuración específica
echo "🎤 Instalando pyaudio..."
pip install --global-option="build_ext" \
           --global-option="-I$(brew --prefix portaudio)/include" \
           --global-option="-L$(brew --prefix portaudio)/lib" \
           pyaudio

# Instalar el resto de dependencias
echo "📦 Instalando resto de dependencias..."
pip install -r requirements.txt pyinstaller

# Verificar instalación crítica
echo "✅ Verificando instalaciones críticas..."
python -c "
import chromadb
import PySide6
import openai
import sounddevice
import soundfile
import speech_recognition
import pyaudio
print('✅ Todas las dependencias críticas están instaladas')
" || {
    echo "❌ Error en dependencias críticas"
    exit 1
}

APPNAME="SecreIA"

echo "🔨 Construyendo aplicación..."
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
  --osx-bundle-identifier="com.SecreIA.app" \
  run_app.py

echo "🎨 Configurando bundle de macOS..."

# Asegura que el icono esté dentro del bundle
mkdir -p "dist/$APPNAME.app/Contents/Resources"
cp -f "assets/icon.icns" "dist/$APPNAME.app/Contents/Resources/icon.icns"

# Crear Info.plist con permisos de micrófono CORREGIDO
echo "📝 Creando Info.plist con permisos correctos..."
cat > "dist/$APPNAME.app/Contents/Info.plist" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>Spanish</string>
    <key>CFBundleExecutable</key>
    <string>SecreIA</string>
    <key>CFBundleIdentifier</key>
    <string>com.SecreIA.app</string>
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
    <key>CSResourcesFileMapped</key>
    <true/>
    <key>LSRequiresCarbon</key>
    <true/>
    <key>CFBundleIconFile</key>
    <string>icon.icns</string>
    <key>CFBundleIconName</key>
    <string>icon</string>
    <key>LSMinimumSystemVersion</key>
    <string>11.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSMicrophoneUsageDescription</key>
    <string>SecreIA necesita acceso al micrófono para transcribir audio en tiempo real y proporcionar asistencia inteligente.</string>
    <key>NSAudioCaptureUsageDescription</key>
    <string>SecreIA requiere captura de audio para procesar y transcribir conversaciones en tiempo real.</string>
    <key>NSSpeechRecognitionUsageDescription</key>
    <string>SecreIA utiliza reconocimiento de voz local para transcripción rápida y precisa.</string>
    <key>LSHasLocalizedDisplayName</key>
    <true/>
    <key>LSMultipleInstancesProhibited</key>
    <true/>
    <key>LSUIElement</key>
    <false/>
    <key>LSApplicationCategoryType</key>
    <string>public.app-category.productivity</string>
    <key>LSBackgroundOnly</key>
    <false/>
</dict>
</plist>
EOF

# Verificar que el Info.plist se creó correctamente
if [ ! -f "dist/$APPNAME.app/Contents/Info.plist" ]; then
    echo "❌ Error: No se pudo crear Info.plist"
    exit 1
fi

echo "🔐 Configurando permisos del bundle..."
chmod -R 755 "dist/$APPNAME.app"


# Al final de tu script, después de todo, añade esto:

echo "🔧 Registrando aplicación en el sistema..."

# Mover a Applications (obligatorio para permisos)
if [ -d "/Applications/SecreIA.app" ]; then
    rm -rf "/Applications/SecreIA.app"
fi

cp -R "dist/$APPNAME.app" "/Applications/"

# Forzar registro en Launch Services
echo "📋 Registrando en Launch Services..."
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "/Applications/SecreIA.app"

# Tocar la base de datos TCC para forzar reconocimiento
echo "🔐 Preparando base de datos de permisos..."
sudo sqlite3 /Library/Application\ Support/com.apple.TCC/TCC.db "DELETE FROM access WHERE client = 'com.SecreIA.app';" 2>/dev/null || true

echo "✅ Aplicación registrada. Ahora ejecuta:"
echo "   1. Abre SecreIA desde /Applications"
echo "   2. La app aparecerá automáticamente en Preferencias > Micrófono"
echo "   3. Activa los permisos manualmente si no aparece el diálogo"

echo "✨ Build completado exitosamente!"
echo "📍 Ubicación: dist/$APPNAME.app"
echo "💡 Para instalar: arrastra la app a /Applications"
echo "🚀 Para ejecutar: open dist/$APPNAME.app"
echo ""
echo "⚠️  IMPORTANTE: La primera vez que ejecutes la app:"
echo "   1. macOS AHORA SÍ pedirá permisos de micrófono - acepta"
echo "   2. Si aparece 'desarrollador no identificado', ve a:"
echo "      Sistema > Privacidad y Seguridad > Permitir de todas formas"
echo "   3. Después de dar permisos, reinicia la app para que funcione correctamente"