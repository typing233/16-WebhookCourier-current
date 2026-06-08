import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from webhook_courier.database import SessionLocal
from webhook_courier.models import Message, Endpoint, DeadLetter, MessageStatus, gen_id
from webhook_courier.core.rate_limiter import rate_limiter
from webhook_courier.core.signer import sign_payload

logger = logging.getLogger("webhook_courier.dispatcher")

POLL_INTERVAL = 1.0
BATCH_SIZE = 100
DELIVERY_TIMEOUT = 10.0


class Dispatcher:
    """Core delivery executor implementing at-least-once semantics.

    On startup, reclaims any IN_FLIGHT messages (crash recovery).
    Polls PENDING messages from persistent storage and delivers them
    with exponential backoff retry and per-endpoint rate limiting.

    Claim strategy uses atomic UPDATE...WHERE status='PENDING' so that
    concurrent workers/instances sharing the same database will never
    claim the same message twice. For SQLite this relies on its write
    serialization; for PostgreSQL use SELECT...FOR UPDATE SKIP LOCKED.
    """

    def __init__(self):
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self):
        self._running = True
        self._recover_in_flight()
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Dispatcher started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Dispatcher stopped")

    def _recover_in_flight(self):
        """Crash recovery: reset IN_FLIGHT messages back to PENDING."""
        db = SessionLocal()
        try:
            count = (
                db.query(Message)
                .filter(Message.status == MessageStatus.IN_FLIGHT)
                .update({"status": MessageStatus.PENDING, "next_attempt_at": datetime.now(timezone.utc)})
            )
            db.commit()
            if count > 0:
                logger.info(f"Recovered {count} in-flight messages after restart")
        finally:
            db.close()

    async def _poll_loop(self):
        while self._running:
            try:
                messages = self._fetch_pending_batch()
                if messages:
                    async with httpx.AsyncClient(timeout=DELIVERY_TIMEOUT) as client:
                        tasks = [self._deliver(client, m) for m in messages]
                        await asyncio.gather(*tasks, return_exceptions=True)
                else:
                    await asyncio.sleep(POLL_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Poll loop error: {e}", exc_info=True)
                await asyncio.sleep(POLL_INTERVAL)

    def _fetch_pending_batch(self) -> list[dict]:
        """Atomically claim a batch of pending messages.

        Uses UPDATE...WHERE status='PENDING' to prevent multiple workers from
        claiming the same message. Only rows actually transitioned from PENDING
        to IN_FLIGHT by THIS statement are returned.
        """
        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)
            # Step 1: identify candidates
            candidate_ids = [
                row[0]
                for row in db.execute(
                    select(Message.id)
                    .where(
                        Message.status == MessageStatus.PENDING,
                        Message.next_attempt_at <= now,
                    )
                    .order_by(Message.next_attempt_at)
                    .limit(BATCH_SIZE)
                ).fetchall()
            ]
            if not candidate_ids:
                return []

            # Step 2: atomic claim — only transitions rows still in PENDING state
            db.execute(
                update(Message)
                .where(
                    Message.id.in_(candidate_ids),
                    Message.status == MessageStatus.PENDING,
                )
                .values(status=MessageStatus.IN_FLIGHT)
            )
            db.commit()

            # Step 3: read back only the rows we actually claimed
            claimed = (
                db.query(Message)
                .filter(
                    Message.id.in_(candidate_ids),
                    Message.status == MessageStatus.IN_FLIGHT,
                )
                .all()
            )
            return [
                {
                    "id": msg.id,
                    "endpoint_id": msg.endpoint_id,
                    "payload": msg.payload,
                    "attempt_count": msg.attempt_count,
                    "max_retries": msg.max_retries,
                }
                for msg in claimed
            ]
        finally:
            db.close()

    async def _deliver(self, client: httpx.AsyncClient, msg: dict):
        endpoint_id = msg["endpoint_id"]

        if not rate_limiter.acquire(endpoint_id):
            self._reschedule_for_rate_limit(msg["id"])
            return

        db = SessionLocal()
        try:
            endpoint = db.query(Endpoint).filter(Endpoint.id == endpoint_id).first()
            if not endpoint or not endpoint.is_active:
                self._mark_dead(db, msg, None, "Endpoint inactive or deleted")
                return

            headers = sign_payload(msg["payload"], endpoint.secret)
            headers["Content-Type"] = "application/json"

            try:
                response = await client.post(
                    endpoint.url,
                    content=msg["payload"],
                    headers=headers,
                )
                status_code = response.status_code
            except httpx.RequestError as e:
                status_code = None
                error_msg = f"{type(e).__name__}: {e}"
                self._handle_failure(db, msg, status_code, error_msg, endpoint)
                return

            if 200 <= status_code < 300:
                self._mark_delivered(db, msg["id"], status_code)
                logger.info(
                    "Delivered successfully",
                    extra={"endpoint_id": endpoint_id, "message_id": msg["id"], "attempt": msg["attempt_count"] + 1},
                )
            else:
                error_msg = f"HTTP {status_code}"
                self._handle_failure(db, msg, status_code, error_msg, endpoint)
        finally:
            db.close()

    def _mark_delivered(self, db: Session, message_id: str, status_code: int):
        db.query(Message).filter(Message.id == message_id).update({
            "status": MessageStatus.DELIVERED,
            "last_response_code": status_code,
            "attempt_count": Message.attempt_count + 1,
        })
        db.commit()

    def _handle_failure(self, db: Session, msg: dict, status_code: int | None, error: str, endpoint: Endpoint):
        new_attempt = msg["attempt_count"] + 1
        retries_used = new_attempt - 1  # first attempt is not a retry

        logger.warning(
            f"Delivery failed: {error}",
            extra={"endpoint_id": msg["endpoint_id"], "message_id": msg["id"], "attempt": new_attempt},
        )

        if retries_used >= msg["max_retries"]:
            self._mark_dead(db, msg, status_code, error)
        else:
            delay = endpoint.retry_base_interval * (2 ** retries_used)
            next_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            db.query(Message).filter(Message.id == msg["id"]).update({
                "status": MessageStatus.PENDING,
                "attempt_count": new_attempt,
                "next_attempt_at": next_at,
                "last_response_code": status_code,
                "last_error": error,
            })
            db.commit()

    def _mark_dead(self, db: Session, msg: dict, status_code: int | None, error: str):
        new_attempt = msg["attempt_count"] + 1
        db.query(Message).filter(Message.id == msg["id"]).update({
            "status": MessageStatus.DEAD,
            "attempt_count": new_attempt,
            "last_response_code": status_code,
            "last_error": error,
        })

        dead = DeadLetter(
            id=gen_id(),
            message_id=msg["id"],
            endpoint_id=msg["endpoint_id"],
            idempotency_key="",
            payload=msg["payload"],
            attempt_count=new_attempt,
            last_response_code=status_code,
            last_error=error,
        )
        original = db.query(Message).filter(Message.id == msg["id"]).first()
        if original:
            dead.idempotency_key = original.idempotency_key

        db.add(dead)
        db.commit()
        logger.error(
            "Message moved to dead letter queue",
            extra={"endpoint_id": msg["endpoint_id"], "message_id": msg["id"], "attempt": new_attempt},
        )

    def _reschedule_for_rate_limit(self, message_id: str):
        db = SessionLocal()
        try:
            next_at = datetime.now(timezone.utc) + timedelta(seconds=0.5)
            db.query(Message).filter(Message.id == message_id).update({
                "status": MessageStatus.PENDING,
                "next_attempt_at": next_at,
            })
            db.commit()
        finally:
            db.close()


dispatcher = Dispatcher()
