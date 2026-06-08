import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from webhook_courier.database import engine, Base, SessionLocal
from webhook_courier.models import Endpoint
from webhook_courier.config import settings
from webhook_courier.logging_config import setup_logging
from webhook_courier.api.endpoints import router as endpoints_router
from webhook_courier.api.messages import router as messages_router
from webhook_courier.api.applications import router as applications_router
from webhook_courier.api.delivery_logs import router as delivery_logs_router
from webhook_courier.api.subscriptions import router as subscriptions_router
from webhook_courier.api.schemas import router as schemas_router
from webhook_courier.api.alerts import router as alerts_router
from webhook_courier.dlq.dead_letter import router as dlq_router
from webhook_courier.core.dispatcher import dispatcher
from webhook_courier.core.rate_limiter import rate_limiter
from webhook_courier.core.circuit_breaker import circuit_breaker
from webhook_courier.core.health_checker import health_checker
from webhook_courier.metrics.collector import metrics


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger = logging.getLogger("webhook_courier")
    logger.info("Initializing database tables")
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        for ep in db.query(Endpoint).filter(Endpoint.is_active.is_(True)).all():
            rate_limiter.configure(ep.id, ep.rate_limit_per_sec)
            circuit_breaker.load_from_db(
                ep.id,
                ep.circuit_state.value if ep.circuit_state else "closed",
                ep.failure_count,
                ep.success_count,
            )
    finally:
        db.close()

    await dispatcher.start()
    await health_checker.start()
    yield
    await health_checker.stop()
    await dispatcher.stop()


app = FastAPI(
    title="Webhook Courier",
    description="Production-grade webhook delivery system with at-least-once semantics",
    version="2.0.0",
    lifespan=lifespan,
)

app.include_router(endpoints_router)
app.include_router(messages_router)
app.include_router(applications_router)
app.include_router(delivery_logs_router)
app.include_router(subscriptions_router)
app.include_router(schemas_router)
app.include_router(alerts_router)
app.include_router(dlq_router)


@app.get("/health")
def health():
    return {"status": "ok", "version": "2.0.0"}


@app.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics():
    return metrics.export_prometheus()


@app.post("/admin/reload-config", tags=["admin"])
def reload_config():
    settings.reload()
    return {"status": "reloaded"}
