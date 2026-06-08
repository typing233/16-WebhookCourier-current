import asyncio
import logging
from datetime import datetime, timezone

import httpx

from webhook_courier.database import SessionLocal
from webhook_courier.models import Endpoint, CircuitState as ModelCircuitState
from webhook_courier.config import settings
from webhook_courier.core.circuit_breaker import circuit_breaker, CircuitState

logger = logging.getLogger("webhook_courier.health_checker")

AUTO_DISABLE_THRESHOLD = 3


class HealthChecker:
    """Periodically probes endpoint health URLs.

    - Probes ALL endpoints with a health_check_url (including inactive ones for recovery).
    - On sustained failures: trips circuit breaker AND auto-disables endpoint.
    - On recovery success from OPEN/HALF_OPEN: re-enables endpoint and closes circuit.
    - Persists circuit_state, failure_count, success_count, is_active to DB.
    """

    def __init__(self):
        self._running = False
        self._task: asyncio.Task | None = None
        self._interval: int = settings.HEALTH_CHECK_INTERVAL

    @property
    def interval(self) -> int:
        return self._interval

    @interval.setter
    def interval(self, value: int):
        self._interval = value

    async def start(self):
        self._running = True
        self._interval = settings.HEALTH_CHECK_INTERVAL
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
            await asyncio.sleep(self._interval)

    async def _run_checks(self):
        db = SessionLocal()
        try:
            # Probe active endpoints AND inactive ones that have health_check_url
            # (inactive ones are probed for recovery)
            endpoints = db.query(Endpoint).filter(
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
        endpoint_id = endpoint.id
        was_active = endpoint.is_active

        try:
            if method == "GET":
                resp = await client.get(url)
            else:
                resp = await client.head(url)

            if 200 <= resp.status_code < 400:
                circuit_breaker.record_success(endpoint_id)
                self._persist_health(endpoint_id, success=True, was_active=was_active)
            else:
                circuit_breaker.record_failure(endpoint_id)
                self._persist_health(endpoint_id, success=False, was_active=was_active)
                logger.warning(f"Health check failed for {endpoint_id}: HTTP {resp.status_code}")
        except httpx.RequestError as e:
            circuit_breaker.record_failure(endpoint_id)
            self._persist_health(endpoint_id, success=False, was_active=was_active)
            logger.warning(f"Health check error for {endpoint_id}: {e}")

    def _persist_health(self, endpoint_id: str, success: bool, was_active: bool):
        db = SessionLocal()
        try:
            ep = db.query(Endpoint).filter(Endpoint.id == endpoint_id).first()
            if not ep:
                return

            state = circuit_breaker.get_state(endpoint_id)
            ep.circuit_state = ModelCircuitState(state.value)

            if success:
                ep.success_count += 1
                # Auto-recovery: if circuit closed back and endpoint was disabled by us
                if state == CircuitState.CLOSED and not ep.is_active:
                    ep.is_active = True
                    ep.circuit_opened_at = None
                    ep.failure_count = 0
                    logger.info(f"Endpoint {endpoint_id} auto-recovered, re-enabled")
            else:
                ep.failure_count += 1
                if state == CircuitState.OPEN:
                    ep.circuit_opened_at = datetime.now(timezone.utc)
                    # Auto-disable: endpoint should stop receiving traffic
                    if ep.is_active:
                        ep.is_active = False
                        logger.warning(f"Endpoint {endpoint_id} auto-disabled due to circuit OPEN")

            db.commit()
        finally:
            db.close()


health_checker = HealthChecker()
