import hashlib
import logging
import smtplib
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from webhook_courier.database import SessionLocal
from webhook_courier.models import AlertConfig, AlertLog, gen_id
from webhook_courier.config import settings

logger = logging.getLogger("webhook_courier.alerter")


class Alerter:
    """Dispatches failure alerts with noise reduction.

    Supports webhook and email channels.
    Deduplicates alerts within a configurable cooldown window per endpoint+error.
    """

    async def maybe_alert(
        self,
        endpoint_id: str,
        error: str,
        consecutive_failures: int,
        db: Optional[Session] = None,
        app_id: Optional[str] = None,
    ):
        """Evaluate alert configs and send if threshold met and not suppressed.

        Args:
            endpoint_id: The endpoint that failed
            error: Error description
            consecutive_failures: Number of consecutive failures (total attempts)
            db: Optional session for testing; if None, creates its own
            app_id: If provided, only match configs belonging to this app or global
        """
        owns_session = db is None
        if owns_session:
            db = SessionLocal()
        try:
            query = db.query(AlertConfig).filter(
                AlertConfig.is_active.is_(True),
                (AlertConfig.endpoint_id == endpoint_id) | (AlertConfig.endpoint_id.is_(None)),
            )
            if app_id:
                query = query.filter(
                    (AlertConfig.app_id == app_id) | (AlertConfig.app_id.is_(None))
                )
            configs = query.all()

            sent_count = 0
            for config in configs:
                if consecutive_failures < config.failure_threshold:
                    continue
                if self._is_suppressed(db, config.id, endpoint_id, error, config.cooldown_seconds):
                    continue
                await self._send_alert(config, endpoint_id, error, consecutive_failures)
                self._record_alert(db, config.id, endpoint_id, error)
                sent_count += 1
            db.commit()
            return sent_count
        except Exception as e:
            logger.error(f"Alert dispatch error: {e}", exc_info=True)
            return 0
        finally:
            if owns_session:
                db.close()

    def _is_suppressed(self, db: Session, config_id: str, endpoint_id: str, error: str, cooldown: int) -> bool:
        fingerprint = self._error_fingerprint(error)
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=cooldown)
        existing = db.query(AlertLog).filter(
            AlertLog.config_id == config_id,
            AlertLog.endpoint_id == endpoint_id,
            AlertLog.error_fingerprint == fingerprint,
            AlertLog.sent_at >= cutoff,
        ).first()
        return existing is not None

    def _record_alert(self, db: Session, config_id: str, endpoint_id: str, error: str):
        log = AlertLog(
            id=gen_id(),
            config_id=config_id,
            endpoint_id=endpoint_id,
            error_fingerprint=self._error_fingerprint(error),
        )
        db.add(log)

    def _error_fingerprint(self, error: str) -> str:
        normalized = error.strip().split(":")[0] if ":" in error else error.strip()
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    async def _send_alert(self, config: AlertConfig, endpoint_id: str, error: str, failures: int):
        if config.channel == "webhook":
            await self._send_webhook(config.destination, endpoint_id, error, failures)
        elif config.channel == "email":
            self._send_email(config.destination, endpoint_id, error, failures)

    async def _send_webhook(self, url: str, endpoint_id: str, error: str, failures: int):
        payload = {
            "type": "webhook_courier.delivery_failure",
            "endpoint_id": endpoint_id,
            "error": error,
            "consecutive_failures": failures,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code >= 400:
                    logger.warning(f"Alert webhook returned {resp.status_code}")
        except Exception as e:
            logger.error(f"Failed to send alert webhook: {e}")

    def _send_email(self, to_addr: str, endpoint_id: str, error: str, failures: int):
        if not settings.ALERT_EMAIL_SMTP_HOST:
            logger.warning("Email alert configured but SMTP host not set")
            return
        subject = f"[WebhookCourier] Delivery failure: endpoint {endpoint_id[:8]}..."
        body = (
            f"Endpoint: {endpoint_id}\n"
            f"Error: {error}\n"
            f"Consecutive failures: {failures}\n"
            f"Time: {datetime.now(timezone.utc).isoformat()}\n"
        )
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = settings.ALERT_EMAIL_FROM
        msg["To"] = to_addr
        try:
            with smtplib.SMTP(settings.ALERT_EMAIL_SMTP_HOST, settings.ALERT_EMAIL_SMTP_PORT) as server:
                server.starttls()
                server.send_message(msg)
        except Exception as e:
            logger.error(f"Failed to send alert email: {e}")


alerter = Alerter()
