import hashlib
import secrets
import logging
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from webhook_courier.database import get_db
from webhook_courier.models import ApiKey, Application
from webhook_courier.config import settings

logger = logging.getLogger("webhook_courier.auth")


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    """Generate a new API key. Returns (full_key, prefix, key_hash)."""
    key = f"whc_{secrets.token_urlsafe(32)}"
    prefix = key[:8]
    key_hash = hash_api_key(key)
    return key, prefix, key_hash


def get_current_app(request: Request, db: Session = Depends(get_db)):
    """Dependency that resolves the current application from API key.

    When AUTH_ENABLED=False, returns None (no scoping applied).
    When AUTH_ENABLED=True, requires a valid API key in the Authorization header.
    """
    if not settings.AUTH_ENABLED:
        return None

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")

    token = auth_header[7:]
    key_hash = hash_api_key(token)

    api_key = db.query(ApiKey).filter(
        ApiKey.key_hash == key_hash,
        ApiKey.is_active.is_(True),
    ).first()

    if not api_key:
        raise HTTPException(401, "Invalid API key")

    app = db.query(Application).filter(
        Application.id == api_key.app_id,
        Application.is_active.is_(True),
    ).first()

    if not app:
        raise HTTPException(403, "Application is inactive")

    return app
