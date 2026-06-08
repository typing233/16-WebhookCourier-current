import hashlib
import hmac
import time


def sign_payload(payload: str, secret: str, timestamp: int | None = None) -> dict[str, str]:
    """Generate HMAC-SHA256 signature headers for a webhook payload.

    Returns headers dict with timestamp and signature for the receiver to verify.
    """
    ts = timestamp or int(time.time())
    signed_content = f"{ts}.{payload}"
    signature = hmac.HMAC(
        secret.encode("utf-8"),
        signed_content.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Webhook-Timestamp": str(ts),
        "X-Webhook-Signature": f"sha256={signature}",
    }


def verify_signature(payload: str, secret: str, timestamp: str, signature: str) -> bool:
    ts = int(timestamp)
    signed_content = f"{ts}.{payload}"
    expected = hmac.HMAC(
        secret.encode("utf-8"),
        signed_content.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)
