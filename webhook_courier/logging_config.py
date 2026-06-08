import logging
import logging.handlers
import json
import sys
from pathlib import Path


class StructuredFormatter(logging.Formatter):
    """JSON structured log formatter for container-friendly output."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%f"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "endpoint_id"):
            log_entry["endpoint_id"] = record.endpoint_id
        if hasattr(record, "message_id"):
            log_entry["message_id"] = record.message_id
        if hasattr(record, "attempt"):
            log_entry["attempt"] = record.attempt
        return json.dumps(log_entry, ensure_ascii=False)


def setup_logging(log_dir: str = "logs", level: int = logging.INFO) -> logging.Logger:
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("webhook_courier")
    root.setLevel(level)

    if root.handlers:
        return root

    formatter = StructuredFormatter()

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    root.addHandler(stdout_handler)

    file_handler = logging.handlers.RotatingFileHandler(
        filename=f"{log_dir}/webhook_courier.log",
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    return root
