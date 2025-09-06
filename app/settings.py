import json
import os
from typing import Optional

# Default configuration for the application.
DEFAULT_CONFIG = {
    "data_dir": os.path.expanduser("~/.secretaria_ai"),
    "openai_api_key": "",
    "chat_model": "gpt-4o-mini",
    "embedding_model": "text-embedding-3-small",
    "transcription_model": "whisper-1",
    "top_k": 5,
}


class Settings:
    """Simple settings manager that stores configuration in JSON."""

    def __init__(self, path: Optional[str] = None) -> None:
        # Use a user-specific path if none provided
        self.config_path = path or os.path.expanduser("~/.secretaria_ai/config.json")
        self._config: dict = {}
        self._ensure_dirs()
        self.load()

    def _ensure_dirs(self) -> None:
        """Ensure the directory for the config file exists."""
        cfg_dir = os.path.dirname(self.config_path)
        os.makedirs(cfg_dir, exist_ok=True)

    def load(self) -> None:
        """Load configuration from disk; falls back to defaults."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self._config = json.load(f)
            except Exception:
                # If JSON fails to parse, reset to defaults
                self._config = DEFAULT_CONFIG.copy()
        else:
            self._config = DEFAULT_CONFIG.copy()
            self.save()

        # Backfill missing keys
        changed = False
        for key, value in DEFAULT_CONFIG.items():
            if key not in self._config:
                self._config[key] = value
                changed = True
        if changed:
            self.save()

        # Ensure data directory exists
        os.makedirs(self._config["data_dir"], exist_ok=True)

    def save(self) -> None:
        """Persist current configuration to disk."""
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self._config, f, indent=2)

    # Properties for each config key to simplify access
    @property
    def data_dir(self) -> str:
        return self._config["data_dir"]

    @data_dir.setter
    def data_dir(self, value: str) -> None:
        self._config["data_dir"] = value
        os.makedirs(value, exist_ok=True)
        self.save()

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