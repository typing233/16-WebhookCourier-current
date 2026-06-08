import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from webhook_courier.database import get_db
from webhook_courier.models import DeadLetter, Message, Endpoint, Application, MessageStatus, gen_id
from webhook_courier.schemas import (
    DeadLetterResponse, BatchReplayRequest, BatchReplayResponse,
    PurgeRequest, DlqStatsResponse,
)
from webhook_courier.auth.dependencies import get_current_app

logger = logging.getLogger("webhook_courier.dlq")

router = APIRouter(prefix="/dlq", tags=["dead-letter-queue"])


def _scope_query(query, app: Application | None):
    if app is not None:
        return query.filter(DeadLetter.app_id == app.id)
    return query


@router.get("", response_model=list[DeadLetterResponse])
def list_dead_letters(
    endpoint_id: str | None = None,
    replayed: bool | None = None,
    event_type: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    error_contains: str | None = None,
    skip: int = 0,
    limit: int = Query(default=50, le=200),
    db: Session = Depends(get_db),
    app: Application | None = Depends(get_current_app),
):
    query = _scope_query(db.query(DeadLetter), app)
    if endpoint_id:
        query = query.filter(DeadLetter.endpoint_id == endpoint_id)
    if replayed is not None:
        query = query.filter(DeadLetter.replayed == replayed)
    if event_type:
        query = query.filter(DeadLetter.event_type == event_type)
    if date_from:
        query = query.filter(DeadLetter.created_at >= date_from)
    if date_to:
        query = query.filter(DeadLetter.created_at <= date_to)
    if error_contains:
        query = query.filter(DeadLetter.last_error.contains(error_contains))
    return query.order_by(DeadLetter.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/stats", response_model=DlqStatsResponse)
def get_dlq_stats(
    db: Session = Depends(get_db),
    app: Application | None = Depends(get_current_app),
):
    query = _scope_query(db.query(DeadLetter), app)
    total = query.count()
    replayed_count = query.filter(DeadLetter.replayed.is_(True)).count()
    unreplayed_count = total - replayed_count

    by_endpoint_rows = (
        query.with_entities(DeadLetter.endpoint_id, func.count())
        .group_by(DeadLetter.endpoint_id)
        .all()
    )
    by_endpoint = {ep_id: cnt for ep_id, cnt in by_endpoint_rows}

    by_error_rows = (
        query.with_entities(DeadLetter.last_error, func.count())
        .group_by(DeadLetter.last_error)
        .limit(20)
        .all()
    )
    by_error = {(err or "unknown"): cnt for err, cnt in by_error_rows}

    return DlqStatsResponse(
        total=total,
        by_endpoint=by_endpoint,
        by_error=by_error,
        replayed_count=replayed_count,
        unreplayed_count=unreplayed_count,
    )


@router.post("/{dead_letter_id}/replay", response_model=dict, status_code=200)
def replay_dead_letter(
    dead_letter_id: str,
    db: Session = Depends(get_db),
    app: Application | None = Depends(get_current_app),
):
    """Replay a single dead-lettered message."""
    query = _scope_query(db.query(DeadLetter), app)
    dl = query.filter(DeadLetter.id == dead_letter_id).first()
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
        app_id=dl.app_id,
        event_type=dl.event_type,
        idempotency_key=f"{dl.idempotency_key}__replay_{dl.id[:8]}",
        payload=dl.payload,
        max_retries=endpoint.max_retries,
        status=MessageStatus.PENDING,
        next_attempt_at=datetime.now(timezone.utc),
    )
    db.add(new_msg)
    dl.replayed = True
    db.commit()

    logger.info("Dead letter replayed", extra={"endpoint_id": dl.endpoint_id, "message_id": new_msg.id})
    return {"new_message_id": new_msg.id, "status": "re-enqueued"}


@router.post("/batch-replay", response_model=BatchReplayResponse, status_code=200)
def batch_replay(
    body: BatchReplayRequest,
    db: Session = Depends(get_db),
    app: Application | None = Depends(get_current_app),
):
    """Batch replay multiple dead-lettered messages."""
    query = _scope_query(db.query(DeadLetter), app).filter(DeadLetter.replayed.is_(False))

    if body.ids:
        query = query.filter(DeadLetter.id.in_(body.ids))
    if body.filter_endpoint_id:
        query = query.filter(DeadLetter.endpoint_id == body.filter_endpoint_id)
    if body.filter_before:
        query = query.filter(DeadLetter.created_at <= body.filter_before)

    dead_letters = query.limit(1000).all()
    new_ids = []

    for dl in dead_letters:
        endpoint = db.query(Endpoint).filter(Endpoint.id == dl.endpoint_id).first()
        if not endpoint:
            continue

        new_msg = Message(
            id=gen_id(),
            endpoint_id=dl.endpoint_id,
            app_id=dl.app_id,
            event_type=dl.event_type,
            idempotency_key=f"{dl.idempotency_key}__replay_{dl.id[:8]}",
            payload=dl.payload,
            max_retries=endpoint.max_retries,
            status=MessageStatus.PENDING,
            next_attempt_at=datetime.now(timezone.utc),
        )
        db.add(new_msg)
        dl.replayed = True
        new_ids.append(new_msg.id)

    db.commit()
    logger.info(f"Batch replayed {len(new_ids)} dead letters")
    return BatchReplayResponse(replayed_count=len(new_ids), new_message_ids=new_ids)


@router.post("/purge", status_code=200)
def purge_dead_letters(
    body: PurgeRequest,
    db: Session = Depends(get_db),
    app: Application | None = Depends(get_current_app),
):
    """Purge (hard delete) dead letters matching criteria."""
    query = _scope_query(db.query(DeadLetter), app)

    if body.ids:
        query = query.filter(DeadLetter.id.in_(body.ids))
    elif body.filter_endpoint_id or body.filter_before:
        if body.filter_endpoint_id:
            query = query.filter(DeadLetter.endpoint_id == body.filter_endpoint_id)
        if body.filter_before:
            query = query.filter(DeadLetter.created_at <= body.filter_before)
    else:
        raise HTTPException(400, "Must specify ids or filter criteria")

    count = query.delete(synchronize_session=False)
    db.commit()
    logger.info(f"Purged {count} dead letters")
    return {"purged_count": count}
