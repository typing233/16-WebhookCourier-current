import os
import json
import threading
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("webhook_courier.config")

_CONFIG_FILE = os.environ.get("WEBHOOK_COURIER_CONFIG_FILE", "config.json")


class Settings:
    """Central configuration with hot-reload support.

    Reads from environment variables and optional JSON config file.
    Supports runtime reload via reload() method.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._callbacks: list = []
        self._load()

    def _load(self):
        self.DATABASE_URL: str = os.environ.get("DATABASE_URL", "sqlite:///webhook_courier.db")
        self.AUTH_ENABLED: bool = os.environ.get("AUTH_ENABLED", "false").lower() == "true"

        self.DISPATCHER_CONCURRENCY: int = int(os.environ.get("DISPATCHER_CONCURRENCY", "10"))
        self.DISPATCHER_POLL_INTERVAL: float = float(os.environ.get("DISPATCHER_POLL_INTERVAL", "1.0"))
        self.DISPATCHER_BATCH_SIZE: int = int(os.environ.get("DISPATCHER_BATCH_SIZE", "100"))
        self.DELIVERY_TIMEOUT: float = float(os.environ.get("DELIVERY_TIMEOUT", "10.0"))

        self.GLOBAL_RATE_LIMIT_PER_SEC: int = int(os.environ.get("GLOBAL_RATE_LIMIT_PER_SEC", "1000"))

        self.DEFAULT_RETRY_JITTER: str = os.environ.get("DEFAULT_RETRY_JITTER", "full")
        self.DEFAULT_MAX_RETRIES: int = int(os.environ.get("DEFAULT_MAX_RETRIES", "5"))
        self.DEFAULT_RETRY_BASE_INTERVAL: float = float(os.environ.get("DEFAULT_RETRY_BASE_INTERVAL", "2.0"))
        self.DEFAULT_MAX_BACKOFF: float = float(os.environ.get("DEFAULT_MAX_BACKOFF", "3600.0"))

        self.HEALTH_CHECK_INTERVAL: int = int(os.environ.get("HEALTH_CHECK_INTERVAL", "60"))
        self.HEALTH_CHECK_TIMEOUT: float = float(os.environ.get("HEALTH_CHECK_TIMEOUT", "5.0"))

        self.CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = int(os.environ.get("CIRCUIT_BREAKER_FAILURE_THRESHOLD", "5"))
        self.CIRCUIT_BREAKER_RECOVERY_TIMEOUT: int = int(os.environ.get("CIRCUIT_BREAKER_RECOVERY_TIMEOUT", "30"))
        self.CIRCUIT_BREAKER_HALF_OPEN_REQUESTS: int = int(os.environ.get("CIRCUIT_BREAKER_HALF_OPEN_REQUESTS", "1"))

        self.ALERT_WEBHOOK_URL: str = os.environ.get("ALERT_WEBHOOK_URL", "")
        self.ALERT_EMAIL_SMTP_HOST: str = os.environ.get("ALERT_EMAIL_SMTP_HOST", "")
        self.ALERT_EMAIL_SMTP_PORT: int = int(os.environ.get("ALERT_EMAIL_SMTP_PORT", "587"))
        self.ALERT_EMAIL_FROM: str = os.environ.get("ALERT_EMAIL_FROM", "")
        self.ALERT_EMAIL_TO: str = os.environ.get("ALERT_EMAIL_TO", "")
        self.ALERT_RATE_LIMIT_WINDOW: int = int(os.environ.get("ALERT_RATE_LIMIT_WINDOW", "300"))

        self.LOG_RETENTION_DAYS: int = int(os.environ.get("LOG_RETENTION_DAYS", "30"))

        self._load_config_file()

    def _load_config_file(self):
        path = Path(_CONFIG_FILE)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            for key, value in data.items():
                upper_key = key.upper()
                if hasattr(self, upper_key):
                    current = getattr(self, upper_key)
                    if isinstance(current, bool):
                        setattr(self, upper_key, bool(value))
                    elif isinstance(current, int):
                        setattr(self, upper_key, int(value))
                    elif isinstance(current, float):
                        setattr(self, upper_key, float(value))
                    else:
                        setattr(self, upper_key, str(value))
        except Exception as e:
            logger.warning(f"Failed to load config file {path}: {e}")

    def reload(self):
        with self._lock:
            self._load()
            for cb in self._callbacks:
                try:
                    cb(self)
                except Exception as e:
                    logger.error(f"Config reload callback error: {e}")
        logger.info("Configuration reloaded")

    def on_reload(self, callback):
        self._callbacks.append(callback)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k.isupper()}


settings = Settings()
