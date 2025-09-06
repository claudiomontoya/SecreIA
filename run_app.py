#!/usr/bin/env python3
"""
Punto de entrada principal para SecreIA
"""
import sys
import os

# Configurar path para imports
if hasattr(sys, '_MEIPASS'):
    # Ejecutándose como PyInstaller bundle
    sys.path.insert(0, sys._MEIPASS)

# Importar y ejecutar main
if __name__ == "__main__":
    try:
        from app.main import main
        main()
    except ImportError as e:
        print(f"Error importando la aplicación: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error ejecutando la aplicación: {e}")
        sys.exit(1)