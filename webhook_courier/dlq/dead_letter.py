import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from webhook_courier.database import get_db
from webhook_courier.models import DeadLetter, Message, Endpoint, MessageStatus, gen_id
from webhook_courier.schemas import DeadLetterResponse

logger = logging.getLogger("webhook_courier.dlq")

router = APIRouter(prefix="/dlq", tags=["dead-letter-queue"])


@router.get("", response_model=list[DeadLetterResponse])
def list_dead_letters(
    endpoint_id: str | None = None,
    replayed: bool | None = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    query = db.query(DeadLetter)
    if endpoint_id:
        query = query.filter(DeadLetter.endpoint_id == endpoint_id)
    if replayed is not None:
        query = query.filter(DeadLetter.replayed == replayed)
    return query.order_by(DeadLetter.created_at.desc()).offset(skip).limit(limit).all()


@router.post("/{dead_letter_id}/replay", response_model=dict, status_code=200)
def replay_dead_letter(dead_letter_id: str, db: Session = Depends(get_db)):
    """Manually replay a dead-lettered message by re-enqueuing it."""
    dl = db.query(DeadLetter).filter(DeadLetter.id == dead_letter_id).first()
    if not dl:
        raise HTTPException(404, "Dead letter not found")
    if dl.replayed:
        raise HTTPException(409, "Already replayed")

    endpoint = db.query(Endpoint).filter(Endpoint.id == dl.endpoint_id).first()
    if not endpoint:
        raise HTTPException(400, "Endpoint no longer exists")

    new_msg = Message(
        id=gen_id(),
        endpoint_id=dl.endpoint_id,
        idempotency_key=f"{dl.idempotency_key}__replay_{dl.id[:8]}",
        payload=dl.payload,
        max_retries=endpoint.max_retries,
        status=MessageStatus.PENDING,
        next_attempt_at=datetime.now(timezone.utc),
    )
    db.add(new_msg)
    dl.replayed = True
    db.commit()

    logger.info(
        "Dead letter replayed",
        extra={"endpoint_id": dl.endpoint_id, "message_id": new_msg.id},
    )
    return {"new_message_id": new_msg.id, "status": "re-enqueued"}
