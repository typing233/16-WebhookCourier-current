import json
import logging

from sqlalchemy.orm import Session

from webhook_courier.models import EventSchema

logger = logging.getLogger("webhook_courier.schema_validator")

try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False


def validate_payload(payload: str, event_type: str | None, app_id: str | None, db: Session) -> tuple[bool, str | None]:
    """Validate payload against the latest active schema for event_type.

    Returns (is_valid, error_message).
    If no schema registered or jsonschema not installed, passes through.
    """
    if not event_type:
        return True, None

    if not HAS_JSONSCHEMA:
        return True, None

    query = db.query(EventSchema).filter(
        EventSchema.event_type == event_type,
        EventSchema.is_active.is_(True),
    )
    if app_id:
        query = query.filter((EventSchema.app_id == app_id) | (EventSchema.app_id.is_(None)))
    else:
        query = query.filter(EventSchema.app_id.is_(None))

    schema_record = query.order_by(EventSchema.version.desc()).first()
    if not schema_record:
        return True, None

    try:
        schema = json.loads(schema_record.schema_json)
        payload_data = json.loads(payload)
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON: {e}"

    try:
        jsonschema.validate(instance=payload_data, schema=schema)
        return True, None
    except jsonschema.ValidationError as e:
        return False, f"Schema validation failed: {e.message}"
