import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from webhook_courier.database import get_db
from webhook_courier.models import (
    Message, Endpoint, Subscription, Application, MessageStatus, gen_id
)
from webhook_courier.schemas import (
    MessageIngest, MessageRoute, MessageResponse, MessageListResponse
)
from webhook_courier.core.schema_validator import validate_payload
from webhook_courier.auth.dependencies import get_current_app

logger = logging.getLogger("webhook_courier.ingest")

router = APIRouter(prefix="/messages", tags=["messages"])


@router.post("", response_model=MessageResponse, status_code=202)
def ingest_message(
    body: MessageIngest,
    db: Session = Depends(get_db),
    app: Application | None = Depends(get_current_app),
):
    """Ingest a message for async delivery with idempotent deduplication."""
    endpoint = db.query(Endpoint).filter(Endpoint.id == body.endpoint_id).first()
    if not endpoint:
        raise HTTPException(404, "Endpoint not found")
    if not endpoint.is_active:
        raise HTTPException(400, "Endpoint is inactive")
    if app and endpoint.app_id and endpoint.app_id != app.id:
        raise HTTPException(403, "Endpoint does not belong to your application")

    if body.event_type:
        valid, err = validate_payload(body.payload, body.event_type, app.id if app else None, db)
        if not valid:
            raise HTTPException(422, err)

    existing = (
        db.query(Message)
        .filter(
            Message.endpoint_id == body.endpoint_id,
            Message.idempotency_key == body.idempotency_key,
        )
        .first()
    )
    if existing:
        logger.info("Deduplicated message", extra={"endpoint_id": body.endpoint_id, "message_id": existing.id})
        return _to_response(existing)

    msg = Message(
        id=gen_id(),
        endpoint_id=body.endpoint_id,
        app_id=app.id if app else None,
        event_type=body.event_type,
        idempotency_key=body.idempotency_key,
        payload=body.payload,
        max_retries=endpoint.max_retries,
    )
    db.add(msg)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(Message)
            .filter(
                Message.endpoint_id == body.endpoint_id,
                Message.idempotency_key == body.idempotency_key,
            )
            .first()
        )
        if existing:
            return _to_response(existing)
        raise HTTPException(500, "Unexpected integrity error")

    db.refresh(msg)
    logger.info("Message ingested", extra={"endpoint_id": body.endpoint_id, "message_id": msg.id})
    return _to_response(msg)


@router.post("/route", response_model=list[MessageResponse], status_code=202)
def route_message(
    body: MessageRoute,
    db: Session = Depends(get_db),
    app: Application | None = Depends(get_current_app),
):
    """Route a message to all endpoints subscribed to the event_type."""
    valid, err = validate_payload(body.payload, body.event_type, app.id if app else None, db)
    if not valid:
        raise HTTPException(422, err)

    query = db.query(Subscription).filter(
        Subscription.event_type == body.event_type,
        Subscription.is_active.is_(True),
    )
    if app:
        query = query.filter(Subscription.app_id == app.id)

    subscriptions = query.all()
    if not subscriptions:
        raise HTTPException(404, f"No subscriptions found for event_type '{body.event_type}'")

    created = []
    for sub in subscriptions:
        endpoint = db.query(Endpoint).filter(
            Endpoint.id == sub.endpoint_id,
            Endpoint.is_active.is_(True),
        ).first()
        if not endpoint:
            continue

        idem_key = f"{body.idempotency_key}__{sub.endpoint_id[:8]}"
        existing = db.query(Message).filter(
            Message.endpoint_id == sub.endpoint_id,
            Message.idempotency_key == idem_key,
        ).first()
        if existing:
            created.append(_to_response(existing))
            continue

        msg = Message(
            id=gen_id(),
            endpoint_id=sub.endpoint_id,
            app_id=app.id if app else None,
            event_type=body.event_type,
            idempotency_key=idem_key,
            payload=body.payload,
            max_retries=endpoint.max_retries,
        )
        db.add(msg)
        try:
            db.flush()
            created.append(_to_response(msg))
        except IntegrityError:
            db.rollback()

    db.commit()
    logger.info(f"Routed event '{body.event_type}' to {len(created)} endpoints")
    return created


@router.get("", response_model=MessageListResponse)
def list_messages(
    endpoint_id: str | None = None,
    status: str | None = None,
    event_type: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    skip: int = 0,
    limit: int = Query(default=50, le=200),
    db: Session = Depends(get_db),
    app: Application | None = Depends(get_current_app),
):
    query = db.query(Message)
    if app:
        query = query.filter(Message.app_id == app.id)
    if endpoint_id:
        query = query.filter(Message.endpoint_id == endpoint_id)
    if status:
        query = query.filter(Message.status == MessageStatus(status))
    if event_type:
        query = query.filter(Message.event_type == event_type)
    if date_from:
        query = query.filter(Message.created_at >= date_from)
    if date_to:
        query = query.filter(Message.created_at <= date_to)

    total = query.count()
    items = query.order_by(Message.created_at.desc()).offset(skip).limit(limit).all()
    return MessageListResponse(
        items=[_to_response(m) for m in items],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/{message_id}", response_model=MessageResponse)
def get_message(
    message_id: str,
    db: Session = Depends(get_db),
    app: Application | None = Depends(get_current_app),
):
    query = db.query(Message).filter(Message.id == message_id)
    if app:
        query = query.filter(Message.app_id == app.id)
    msg = query.first()
    if not msg:
        raise HTTPException(404, "Message not found")
    return _to_response(msg)


def _to_response(msg: Message) -> MessageResponse:
    return MessageResponse(
        id=msg.id,
        endpoint_id=msg.endpoint_id,
        app_id=msg.app_id,
        event_type=msg.event_type,
        idempotency_key=msg.idempotency_key,
        status=msg.status.value if isinstance(msg.status, MessageStatus) else msg.status,
        attempt_count=msg.attempt_count,
        created_at=msg.created_at,
    )
