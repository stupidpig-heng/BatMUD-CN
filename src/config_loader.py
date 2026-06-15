"""
Configuration loader — reads config.yaml
"""

import sys
from pathlib import Path
from typing import Any, Dict

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not found. Run: pip install pyyaml")
    sys.exit(1)


class Config:
    def __init__(self, data: Dict[str, Any]):
        self._data = data

    # -- Server --
    @property
    def server_host(self) -> str:
        return self._data.get("server", {}).get("host", "batmud.bat.org")

    @property
    def server_port(self) -> int:
        return int(self._data.get("server", {}).get("port", 23))

    # -- Web --
    @property
    def web_host(self) -> str:
        return self._data.get("web", {}).get("host", "127.0.0.1")

    @property
    def web_port(self) -> int:
        return int(self._data.get("web", {}).get("port", 8080))

    # -- Baidu API --
    @property
    def baidu_app_id(self) -> str:
        return self._data.get("baidu", {}).get("app_id", "")

    @property
    def baidu_secret_key(self) -> str:
        return self._data.get("baidu", {}).get("secret_key", "")

    @property
    def baidu_timeout(self) -> int:
        return int(self._data.get("baidu", {}).get("timeout", 15))

    @property
    def baidu_model_type(self) -> str:
        return self._data.get("baidu", {}).get("model_type", "llm")

    # -- Translation --
    @property
    def translation_enabled(self) -> bool:
        return bool(self._data.get("translation", {}).get("enabled", True))

    @property
    def cache_size(self) -> int:
        return int(self._data.get("translation", {}).get("cache_size", 2000))

    @property
    def cache_file(self) -> str:
        return self._data.get("translation", {}).get("cache_file", "translation_cache.db")

    @property
    def min_chars(self) -> int:
        return int(self._data.get("translation", {}).get("min_chars", 4))

    @property
    def max_batch(self) -> int:
        return int(self._data.get("translation", {}).get("max_batch", 3))

    # -- Logging --
    @property
    def log_level(self) -> str:
        return self._data.get("logging", {}).get("level", "INFO")

    @property
    def log_file(self) -> str:
        return self._data.get("logging", {}).get("file", "")

    @property
    def show_translations(self) -> bool:
        return bool(self._data.get("logging", {}).get("show_translations", True))

    def validate(self) -> list[str]:
        errors = []
        if not self.baidu_app_id:
            errors.append("config.yaml: baidu.app_id is required")
        if not self.baidu_secret_key:
            errors.append("config.yaml: baidu.secret_key is required")
        return errors


def load_config(config_path: str = None) -> Config:
    if config_path is None:
        candidates = [
            Path.cwd() / "config.yaml",
            Path(__file__).resolve().parent.parent / "config.yaml",
        ]
        for p in candidates:
            if p.exists():
                config_path = str(p)
                break
        else:
            print("ERROR: config.yaml not found")
            sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if data is None:
        print("ERROR: config.yaml is empty or invalid")
        sys.exit(1)

    config = Config(data)
    errors = config.validate()
    if errors:
        print("=" * 50)
        print("Config errors:")
        for e in errors:
            print(f"  x {e}")
        print("=" * 50)
        sys.exit(1)

    return config
