import json
import os
from typing import Optional

# Configuración específica para Windows
WINDOWS_DATA_DIR = os.path.join(os.path.expandvars('%LOCALAPPDATA%'), "SecreIA")

DEFAULT_CONFIG = {
    "data_dir": WINDOWS_DATA_DIR,
    "openai_api_key": "",
    "chat_model": "gpt-4o-mini",
    "embedding_model": "text-embedding-3-small", 
    "transcription_model": "whisper-1",
    "top_k": 5,
}

class Settings:
    """Configuraciones optimizadas para Windows"""
    
    def __init__(self, path: Optional[str] = None) -> None:
        if path is None:
            config_dir = os.path.join(os.path.expandvars('%LOCALAPPDATA%'), "SecreIA")
            os.makedirs(config_dir, exist_ok=True)
            self.config_path = os.path.join(config_dir, "config.json")
        else:
            self.config_path = path
            
        self._config: dict = {}
        self._ensure_dirs()
        self.load()

    def _ensure_dirs(self) -> None:
        """Asegurar que el directorio de configuración existe en Windows"""
        cfg_dir = os.path.dirname(self.config_path)
        os.makedirs(cfg_dir, exist_ok=True)

    def load(self) -> None:
        """Cargar configuración desde disco con manejo de errores Windows"""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self._config = json.load(f)
            except (json.JSONDecodeError, UnicodeDecodeError, PermissionError) as e:
                print(f"Error cargando configuración: {e}. Usando valores por defecto.")
                self._config = DEFAULT_CONFIG.copy()
        else:
            self._config = DEFAULT_CONFIG.copy()
            self.save()

        # Completar claves faltantes
        changed = False
        for key, value in DEFAULT_CONFIG.items():
            if key not in self._config:
                self._config[key] = value
                changed = True
        if changed:
            self.save()

        # Asegurar que el directorio de datos existe
        try:
            os.makedirs(self._config["data_dir"], exist_ok=True)
        except PermissionError:
            # Fallback a directorio temporal si hay problemas de permisos
            import tempfile
            fallback_dir = os.path.join(tempfile.gettempdir(), "SecreIA")
            os.makedirs(fallback_dir, exist_ok=True)
            self._config["data_dir"] = fallback_dir
            print(f"Usando directorio temporal: {fallback_dir}")

    def save(self) -> None:
        """Guardar configuración con manejo robusto para Windows"""
        try:
            # Crear directorio padre si no existe
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            
            # Escribir a archivo temporal primero
            temp_path = self.config_path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2, ensure_ascii=False)
            
            # Reemplazar archivo original
            if os.path.exists(self.config_path):
                os.remove(self.config_path)
            os.rename(temp_path, self.config_path)
            
        except (PermissionError, OSError) as e:
            print(f"Error guardando configuración: {e}")

    # Properties para acceso a configuración
    @property
    def data_dir(self) -> str:
        return self._config["data_dir"]

    @data_dir.setter
    def data_dir(self, value: str) -> None:
        try:
            os.makedirs(value, exist_ok=True)
            self._config["data_dir"] = value
            self.save()
        except (PermissionError, OSError) as e:
            print(f"Error configurando directorio de datos: {e}")

    @property
    def openai_api_key(self) -> str:
        return self._config["openai_api_key"]

    @openai_api_key.setter
    def openai_api_key(self, value: str) -> None:
        self._config["openai_api_key"] = value
        self.save()

    @property
    def chat_model(self) -> str:
        return self._config["chat_model"]

    @chat_model.setter
    def chat_model(self, value: str) -> None:
        self._config["chat_model"] = value
        self.save()

    @property
    def embedding_model(self) -> str:
        return self._config["embedding_model"]

    @embedding_model.setter
    def embedding_model(self, value: str) -> None:
        self._config["embedding_model"] = value
        self.save()

    @property
    def transcription_model(self) -> str:
        return self._config["transcription_model"]

    @transcription_model.setter
    def transcription_model(self, value: str) -> None:
        self._config["transcription_model"] = value
        self.save()

    @property
    def top_k(self) -> int:
        return int(self._config.get("top_k", 5))

    @top_k.setter
    def top_k(self, value: int) -> None:
        self._config["top_k"] = int(value)
        self.save()

    def get_logs_dir(self) -> str:
        """Directorio de logs específico para Windows"""
        logs_dir = os.path.join(self.data_dir, "logs")
        os.makedirs(logs_dir, exist_ok=True)
        return logs_dir

    def get_temp_dir(self) -> str:
        """Directorio temporal específico para Windows"""
        temp_dir = os.path.join(self.data_dir, "temp")
        os.makedirs(temp_dir, exist_ok=True)
        return temp_dir