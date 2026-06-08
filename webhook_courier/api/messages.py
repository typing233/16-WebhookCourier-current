import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from webhook_courier.database import get_db
from webhook_courier.models import Message, Endpoint, MessageStatus, gen_id
from webhook_courier.schemas import MessageIngest, MessageResponse

logger = logging.getLogger("webhook_courier.ingest")

router = APIRouter(prefix="/messages", tags=["messages"])


@router.post("", response_model=MessageResponse, status_code=202)
def ingest_message(body: MessageIngest, db: Session = Depends(get_db)):
    """Ingest a message for async delivery with idempotent deduplication.

    If a message with the same (endpoint_id, idempotency_key) already exists,
    returns the existing message instead of creating a duplicate.
    """
    endpoint = db.query(Endpoint).filter(Endpoint.id == body.endpoint_id).first()
    if not endpoint:
        raise HTTPException(404, "Endpoint not found")
    if not endpoint.is_active:
        raise HTTPException(400, "Endpoint is inactive")

    existing = (
        db.query(Message)
        .filter(
            Message.endpoint_id == body.endpoint_id,
            Message.idempotency_key == body.idempotency_key,
        )
        .first()
    )
    if existing:
        logger.info(
            "Deduplicated message",
            extra={"endpoint_id": body.endpoint_id, "message_id": existing.id},
        )
        return _to_response(existing)

    msg = Message(
        id=gen_id(),
        endpoint_id=body.endpoint_id,
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
    logger.info(
        "Message ingested",
        extra={"endpoint_id": body.endpoint_id, "message_id": msg.id},
    )
    return _to_response(msg)


@router.get("/{message_id}", response_model=MessageResponse)
def get_message(message_id: str, db: Session = Depends(get_db)):
    msg = db.query(Message).filter(Message.id == message_id).first()
    if not msg:
        raise HTTPException(404, "Message not found")
    return _to_response(msg)


def _to_response(msg: Message) -> MessageResponse:
    return MessageResponse(
        id=msg.id,
        endpoint_id=msg.endpoint_id,
        idempotency_key=msg.idempotency_key,
        status=msg.status.value,
        attempt_count=msg.attempt_count,
        created_at=msg.created_at,
    )
