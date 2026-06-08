import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from webhook_courier.database import engine, Base, SessionLocal
from webhook_courier.models import Endpoint
from webhook_courier.logging_config import setup_logging
from webhook_courier.api.endpoints import router as endpoints_router
from webhook_courier.api.messages import router as messages_router
from webhook_courier.dlq.dead_letter import router as dlq_router
from webhook_courier.core.dispatcher import dispatcher
from webhook_courier.core.rate_limiter import rate_limiter


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
    finally:
        db.close()

    await dispatcher.start()
    yield
    await dispatcher.stop()


app = FastAPI(
    title="Webhook Courier",
    description="Production-grade webhook delivery system with at-least-once semantics",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(endpoints_router)
app.include_router(messages_router)
app.include_router(dlq_router)


@app.get("/health")
def health():
    return {"status": "ok"}
