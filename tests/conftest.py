import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.responses import PlainTextResponse
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from webhook_courier.database import Base, get_db
from webhook_courier.models import gen_id


@pytest.fixture
def db_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture
def db_session_factory(db_engine):
    return sessionmaker(bind=db_engine, autoflush=False, autocommit=False)


@pytest.fixture
def client(db_engine, db_session_factory):
    def override_get_db():
        session = db_session_factory()
        try:
            yield session
        finally:
            session.close()

    from webhook_courier.api.endpoints import router as endpoints_router
    from webhook_courier.api.messages import router as messages_router
    from webhook_courier.api.applications import router as applications_router
    from webhook_courier.api.delivery_logs import router as delivery_logs_router
    from webhook_courier.api.subscriptions import router as subscriptions_router
    from webhook_courier.api.schemas import router as schemas_router
    from webhook_courier.api.alerts import router as alerts_router
    from webhook_courier.dlq.dead_letter import router as dlq_router
    from webhook_courier.metrics.collector import metrics

    test_app = FastAPI()
    test_app.include_router(endpoints_router)
    test_app.include_router(messages_router)
    test_app.include_router(applications_router)
    test_app.include_router(delivery_logs_router)
    test_app.include_router(subscriptions_router)
    test_app.include_router(schemas_router)
    test_app.include_router(alerts_router)
    test_app.include_router(dlq_router)

    @test_app.get("/health")
    def health():
        return {"status": "ok", "version": "2.0.0"}

    @test_app.get("/metrics", response_class=PlainTextResponse)
    def prometheus_metrics():
        return metrics.export_prometheus()

    test_app.dependency_overrides[get_db] = override_get_db

    with TestClient(test_app) as c:
        yield c

    test_app.dependency_overrides.clear()


@pytest.fixture
def sample_endpoint(client):
    resp = client.post("/endpoints", json={
        "url": "http://localhost:9999/webhook",
        "secret": "testsecret1234567890",
        "description": "Test endpoint",
        "max_retries": 3,
        "retry_base_interval": 1.0,
    })
    assert resp.status_code == 201
    return resp.json()


@pytest.fixture
def sample_application(client):
    resp = client.post("/applications", json={
        "name": "test-app",
    })
    assert resp.status_code == 201
    return resp.json()
