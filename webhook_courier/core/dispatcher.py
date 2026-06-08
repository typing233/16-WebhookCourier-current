import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta

import httpx
from sqlalchemy import update

from webhook_courier.database import SessionLocal
from webhook_courier.models import (
    Message, Endpoint, Application, DeadLetter, DeliveryLog, MessageStatus, gen_id
)
from webhook_courier.config import settings
from webhook_courier.core.rate_limiter import rate_limiter
from webhook_courier.core.signer import sign_payload
from webhook_courier.core.retry import calculate_backoff
from webhook_courier.core.circuit_breaker import circuit_breaker, CircuitState
from webhook_courier.core.alerter import alerter
from webhook_courier.metrics.collector import metrics

logger = logging.getLogger("webhook_courier.dispatcher")


class Dispatcher:
    """Core delivery executor with at-least-once semantics.

    Features: exponential backoff with jitter, circuit breaker integration,
    per-attempt delivery logs, concurrent worker pool via semaphore,
    alert triggering on sustained failures.
    """

    def __init__(self):
        self._running = False
        self._task: asyncio.Task | None = None
        self._semaphore: asyncio.Semaphore | None = None

    def update_concurrency(self, concurrency: int):
        self._semaphore = asyncio.Semaphore(concurrency)

    async def start(self):
        self._running = True
        self._semaphore = asyncio.Semaphore(settings.DISPATCHER_CONCURRENCY)
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
                    timeout = settings.DELIVERY_TIMEOUT
                    async with httpx.AsyncClient(timeout=timeout) as client:
                        tasks = [self._deliver_with_semaphore(client, m) for m in messages]
                        await asyncio.gather(*tasks, return_exceptions=True)
                else:
                    await asyncio.sleep(settings.DISPATCHER_POLL_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Poll loop error: {e}", exc_info=True)
                await asyncio.sleep(settings.DISPATCHER_POLL_INTERVAL)

    async def _deliver_with_semaphore(self, client: httpx.AsyncClient, msg: dict):
        async with self._semaphore:
            metrics.gauge_set("dispatcher_active_deliveries", 1, increment=True)
            try:
                await self._deliver(client, msg)
            finally:
                metrics.gauge_set("dispatcher_active_deliveries", -1, increment=True)

    def _fetch_pending_batch(self) -> list[dict]:
        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc)
            from sqlalchemy import select
            candidate_ids = [
                row[0]
                for row in db.execute(
                    select(Message.id)
                    .where(
                        Message.status == MessageStatus.PENDING,
                        Message.next_attempt_at <= now,
                    )
                    .order_by(Message.next_attempt_at)
                    .limit(settings.DISPATCHER_BATCH_SIZE)
                ).fetchall()
            ]
            if not candidate_ids:
                return []

            db.execute(
                update(Message)
                .where(
                    Message.id.in_(candidate_ids),
                    Message.status == MessageStatus.PENDING,
                )
                .values(status=MessageStatus.IN_FLIGHT)
            )
            db.commit()

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
                    "app_id": msg.app_id,
                    "payload": msg.payload,
                    "attempt_count": msg.attempt_count,
                    "max_retries": msg.max_retries,
                    "event_type": msg.event_type,
                }
                for msg in claimed
            ]
        finally:
            db.close()

    async def _deliver(self, client: httpx.AsyncClient, msg: dict):
        endpoint_id = msg["endpoint_id"]

        if not circuit_breaker.can_attempt(endpoint_id):
            self._reschedule_for_circuit_open(msg["id"])
            self._log_attempt(msg, 0, "circuit_open", None, None, 0)
            return

        if not rate_limiter.acquire(endpoint_id):
            self._reschedule_for_rate_limit(msg["id"])
            return

        db = SessionLocal()
        try:
            endpoint = db.query(Endpoint).filter(Endpoint.id == endpoint_id).first()
            if not endpoint or not endpoint.is_active:
                self._mark_dead(db, msg, None, "Endpoint inactive or deleted")
                return

            # Use application signing_key if endpoint belongs to an app, else endpoint secret
            signing_secret = endpoint.secret
            if endpoint.app_id:
                app_obj = db.query(Application).filter(Application.id == endpoint.app_id).first()
                if app_obj and app_obj.signing_key:
                    signing_secret = app_obj.signing_key

            headers = sign_payload(msg["payload"], signing_secret)
            headers["Content-Type"] = "application/json"

            timeout = endpoint.per_attempt_timeout or settings.DELIVERY_TIMEOUT
            start_time = time.perf_counter()
            try:
                response = await client.post(
                    endpoint.url,
                    content=msg["payload"],
                    headers=headers,
                    timeout=timeout,
                )
                status_code = response.status_code
                latency = (time.perf_counter() - start_time) * 1000
            except httpx.RequestError as e:
                latency = (time.perf_counter() - start_time) * 1000
                status_code = None
                error_msg = f"{type(e).__name__}: {e}"
                self._handle_failure(db, msg, status_code, error_msg, endpoint, latency)
                return

            if 200 <= status_code < 300:
                self._mark_delivered(db, msg, status_code, latency)
                circuit_breaker.record_success(endpoint_id)
                metrics.counter_inc("messages_total", labels={"status": "delivered"})
                logger.info(
                    "Delivered successfully",
                    extra={"endpoint_id": endpoint_id, "message_id": msg["id"], "attempt": msg["attempt_count"] + 1},
                )
            else:
                error_msg = f"HTTP {status_code}"
                self._handle_failure(db, msg, status_code, error_msg, endpoint, latency)
        finally:
            db.close()

    def _mark_delivered(self, db, message_id_or_msg, status_code: int, latency_ms: float):
        if isinstance(message_id_or_msg, dict):
            msg = message_id_or_msg
            message_id = msg["id"]
        else:
            message_id = message_id_or_msg
            msg = {"id": message_id, "endpoint_id": "", "app_id": None, "attempt_count": 0}

        db.query(Message).filter(Message.id == message_id).update({
            "status": MessageStatus.DELIVERED,
            "last_response_code": status_code,
            "attempt_count": Message.attempt_count + 1,
        })
        db.commit()

        self._log_attempt(msg, msg["attempt_count"] + 1, "success", status_code, None, latency_ms)
        metrics.histogram_observe("delivery_latency_seconds", latency_ms / 1000)

    def _handle_failure(self, db, msg: dict, status_code: int | None, error: str, endpoint: Endpoint, latency_ms: float):
        new_attempt = msg["attempt_count"] + 1
        retries_used = new_attempt - 1

        logger.warning(
            f"Delivery failed: {error}",
            extra={"endpoint_id": msg["endpoint_id"], "message_id": msg["id"], "attempt": new_attempt},
        )

        circuit_breaker.record_failure(msg["endpoint_id"])
        metrics.counter_inc("messages_total", labels={"status": "failed"})

        if retries_used >= msg["max_retries"]:
            self._mark_dead(db, msg, status_code, error)
            self._log_attempt(msg, new_attempt, "dead", status_code, error, latency_ms)
            # Alert with the actual total attempts (which equals max_retries + 1)
            asyncio.get_event_loop().create_task(
                alerter.maybe_alert(msg["endpoint_id"], error, new_attempt)
            )
        else:
            jitter = endpoint.jitter_strategy or "full"
            max_backoff = endpoint.max_backoff or settings.DEFAULT_MAX_BACKOFF
            delay = calculate_backoff(
                base_interval=endpoint.retry_base_interval,
                attempt=retries_used,
                jitter=jitter,
                max_backoff=max_backoff,
            )
            next_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            db.query(Message).filter(Message.id == msg["id"]).update({
                "status": MessageStatus.PENDING,
                "attempt_count": new_attempt,
                "next_attempt_at": next_at,
                "last_response_code": status_code,
                "last_error": error,
            })
            db.commit()
            self._log_attempt(msg, new_attempt, "failure", status_code, error, latency_ms)

            if retries_used >= 2:
                asyncio.get_event_loop().create_task(
                    alerter.maybe_alert(msg["endpoint_id"], error, retries_used)
                )

    def _mark_dead(self, db, msg: dict, status_code: int | None, error: str):
        new_attempt = msg["attempt_count"] + 1
        db.query(Message).filter(Message.id == msg["id"]).update({
            "status": MessageStatus.DEAD,
            "attempt_count": new_attempt,
            "last_response_code": status_code,
            "last_error": error,
        })

        original = db.query(Message).filter(Message.id == msg["id"]).first()
        idempotency_key = original.idempotency_key if original else ""

        dead = DeadLetter(
            id=gen_id(),
            message_id=msg["id"],
            endpoint_id=msg["endpoint_id"],
            app_id=msg.get("app_id"),
            idempotency_key=idempotency_key,
            event_type=msg.get("event_type"),
            payload=msg["payload"],
            attempt_count=new_attempt,
            last_response_code=status_code,
            last_error=error,
        )
        db.add(dead)
        db.commit()

        metrics.counter_inc("messages_total", labels={"status": "dead"})
        logger.error(
            "Message moved to dead letter queue",
            extra={"endpoint_id": msg["endpoint_id"], "message_id": msg["id"], "attempt": new_attempt},
        )

    def _log_attempt(self, msg: dict, attempt_number: int, status: str,
                     response_code: int | None, error: str | None, latency_ms: float):
        db = SessionLocal()
        try:
            log = DeliveryLog(
                id=gen_id(),
                message_id=msg["id"],
                endpoint_id=msg.get("endpoint_id", ""),
                app_id=msg.get("app_id"),
                attempt_number=attempt_number,
                status=status,
                response_code=response_code,
                error_message=error,
                latency_ms=latency_ms,
            )
            db.add(log)
            db.commit()
        except Exception as e:
            logger.error(f"Failed to write delivery log: {e}")
        finally:
            db.close()

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

    def _reschedule_for_circuit_open(self, message_id: str):
        db = SessionLocal()
        try:
            next_at = datetime.now(timezone.utc) + timedelta(seconds=settings.CIRCUIT_BREAKER_RECOVERY_TIMEOUT)
            db.query(Message).filter(Message.id == message_id).update({
                "status": MessageStatus.PENDING,
                "next_attempt_at": next_at,
            })
            db.commit()
        finally:
            db.close()


dispatcher = Dispatcher()
