import asyncio
import logging
from datetime import datetime, timezone

import httpx

from webhook_courier.database import SessionLocal
from webhook_courier.models import Endpoint
from webhook_courier.config import settings
from webhook_courier.core.circuit_breaker import circuit_breaker, CircuitState

logger = logging.getLogger("webhook_courier.health_checker")


class HealthChecker:
    """Periodically probes endpoint health URLs.

    Successful probes reset circuit breaker failure counters.
    Failed probes increment failure counters and can trip the circuit.
    """

    def __init__(self):
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._check_loop())
        logger.info("Health checker started")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Health checker stopped")

    async def _check_loop(self):
        while self._running:
            try:
                await self._run_checks()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check loop error: {e}", exc_info=True)
            await asyncio.sleep(settings.HEALTH_CHECK_INTERVAL)

    async def _run_checks(self):
        db = SessionLocal()
        try:
            endpoints = db.query(Endpoint).filter(
                Endpoint.is_active.is_(True),
                Endpoint.health_check_url.isnot(None),
            ).all()
        finally:
            db.close()

        if not endpoints:
            return

        async with httpx.AsyncClient(timeout=settings.HEALTH_CHECK_TIMEOUT) as client:
            tasks = [self._probe(client, ep) for ep in endpoints]
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _probe(self, client: httpx.AsyncClient, endpoint: Endpoint):
        url = endpoint.health_check_url
        method = (endpoint.health_check_method or "HEAD").upper()

        try:
            if method == "GET":
                resp = await client.get(url)
            else:
                resp = await client.head(url)

            if 200 <= resp.status_code < 400:
                circuit_breaker.record_success(endpoint.id)
                self._persist_health(endpoint.id, success=True)
            else:
                circuit_breaker.record_failure(endpoint.id)
                self._persist_health(endpoint.id, success=False)
                logger.warning(f"Health check failed for {endpoint.id}: HTTP {resp.status_code}")
        except httpx.RequestError as e:
            circuit_breaker.record_failure(endpoint.id)
            self._persist_health(endpoint.id, success=False)
            logger.warning(f"Health check error for {endpoint.id}: {e}")

    def _persist_health(self, endpoint_id: str, success: bool):
        db = SessionLocal()
        try:
            ep = db.query(Endpoint).filter(Endpoint.id == endpoint_id).first()
            if not ep:
                return
            state = circuit_breaker.get_state(endpoint_id)
            ep.circuit_state = state
            if success:
                ep.success_count += 1
            else:
                ep.failure_count += 1
                if state == CircuitState.OPEN:
                    ep.circuit_opened_at = datetime.now(timezone.utc)
            db.commit()
        finally:
            db.close()


health_checker = HealthChecker()
