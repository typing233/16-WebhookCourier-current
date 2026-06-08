import uuid
import enum
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Integer, Float, Text, DateTime, Boolean, Index,
    Enum as SAEnum, UniqueConstraint, ForeignKey
)
from webhook_courier.database import Base


def utcnow():
    return datetime.now(timezone.utc)


def gen_id():
    return str(uuid.uuid4())


class MessageStatus(enum.Enum):
    PENDING = "pending"
    IN_FLIGHT = "in_flight"
    DELIVERED = "delivered"
    FAILED = "failed"
    DEAD = "dead"


class CircuitState(enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


# --- Multi-tenancy ---

class Application(Base):
    __tablename__ = "applications"

    id = Column(String(36), primary_key=True, default=gen_id)
    name = Column(String(256), nullable=False, unique=True)
    signing_key = Column(String(256), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(String(36), primary_key=True, default=gen_id)
    app_id = Column(String(36), ForeignKey("applications.id"), nullable=False, index=True)
    key_hash = Column(String(128), nullable=False, unique=True)
    prefix = Column(String(8), nullable=False)
    label = Column(String(256), default="")
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


# --- Core Models ---

class Endpoint(Base):
    __tablename__ = "endpoints"

    id = Column(String(36), primary_key=True, default=gen_id)
    app_id = Column(String(36), ForeignKey("applications.id"), nullable=True, index=True)
    url = Column(String(2048), nullable=False)
    secret = Column(String(256), nullable=False)
    description = Column(String(512), default="")
    is_active = Column(Boolean, default=True, nullable=False)
    max_retries = Column(Integer, default=5, nullable=False)
    retry_base_interval = Column(Float, default=2.0, nullable=False)
    jitter_strategy = Column(String(20), default="full", nullable=False)
    per_attempt_timeout = Column(Float, default=10.0, nullable=False)
    max_backoff = Column(Float, default=3600.0, nullable=False)
    rate_limit_per_sec = Column(Integer, default=50, nullable=False)
    health_check_url = Column(String(2048), nullable=True)
    health_check_method = Column(String(10), default="HEAD", nullable=False)
    health_check_interval = Column(Integer, nullable=True)
    circuit_state = Column(SAEnum(CircuitState), default=CircuitState.CLOSED, nullable=False)
    circuit_opened_at = Column(DateTime(timezone=True), nullable=True)
    failure_count = Column(Integer, default=0, nullable=False)
    success_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_status_next_attempt", "status", "next_attempt_at"),
        Index("ix_messages_idempotency", "endpoint_id", "idempotency_key", unique=True),
    )

    id = Column(String(36), primary_key=True, default=gen_id)
    endpoint_id = Column(String(36), nullable=False, index=True)
    app_id = Column(String(36), ForeignKey("applications.id"), nullable=True, index=True)
    event_type = Column(String(256), nullable=True, index=True)
    idempotency_key = Column(String(256), nullable=False)
    payload = Column(Text, nullable=False)
    status = Column(SAEnum(MessageStatus), default=MessageStatus.PENDING, nullable=False)
    attempt_count = Column(Integer, default=0, nullable=False)
    max_retries = Column(Integer, default=5, nullable=False)
    next_attempt_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_response_code = Column(Integer, nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class DeadLetter(Base):
    __tablename__ = "dead_letters"

    id = Column(String(36), primary_key=True, default=gen_id)
    message_id = Column(String(36), nullable=False, index=True)
    endpoint_id = Column(String(36), nullable=False, index=True)
    app_id = Column(String(36), ForeignKey("applications.id"), nullable=True, index=True)
    idempotency_key = Column(String(256), nullable=False)
    event_type = Column(String(256), nullable=True)
    payload = Column(Text, nullable=False)
    attempt_count = Column(Integer, nullable=False)
    last_response_code = Column(Integer, nullable=True)
    last_error = Column(Text, nullable=True)
    replayed = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


# --- Delivery Logs ---

class DeliveryLog(Base):
    __tablename__ = "delivery_logs"
    __table_args__ = (
        Index("ix_delivery_logs_endpoint_created", "endpoint_id", "created_at"),
        Index("ix_delivery_logs_app_created", "app_id", "created_at"),
    )

    id = Column(String(36), primary_key=True, default=gen_id)
    message_id = Column(String(36), nullable=False, index=True)
    endpoint_id = Column(String(36), nullable=False)
    app_id = Column(String(36), nullable=True)
    attempt_number = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False)
    response_code = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    latency_ms = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


# --- Event Routing ---

class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("endpoint_id", "event_type", name="uq_subscription_endpoint_event"),
    )

    id = Column(String(36), primary_key=True, default=gen_id)
    app_id = Column(String(36), ForeignKey("applications.id"), nullable=True, index=True)
    endpoint_id = Column(String(36), ForeignKey("endpoints.id"), nullable=False, index=True)
    event_type = Column(String(256), nullable=False, index=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


# --- Schema Validation ---

class EventSchema(Base):
    __tablename__ = "event_schemas"
    __table_args__ = (
        UniqueConstraint("app_id", "event_type", "version", name="uq_event_schema_version"),
    )

    id = Column(String(36), primary_key=True, default=gen_id)
    app_id = Column(String(36), ForeignKey("applications.id"), nullable=True, index=True)
    event_type = Column(String(256), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    schema_json = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


# --- Alerts ---

class AlertConfig(Base):
    __tablename__ = "alert_configs"

    id = Column(String(36), primary_key=True, default=gen_id)
    app_id = Column(String(36), ForeignKey("applications.id"), nullable=True, index=True)
    endpoint_id = Column(String(36), ForeignKey("endpoints.id"), nullable=True, index=True)
    channel = Column(String(20), nullable=False)
    destination = Column(String(2048), nullable=False)
    failure_threshold = Column(Integer, default=3, nullable=False)
    cooldown_seconds = Column(Integer, default=300, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class AlertLog(Base):
    __tablename__ = "alert_logs"

    id = Column(String(36), primary_key=True, default=gen_id)
    config_id = Column(String(36), ForeignKey("alert_configs.id"), nullable=False, index=True)
    endpoint_id = Column(String(36), nullable=False, index=True)
    error_fingerprint = Column(String(64), nullable=False)
    sent_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


# --- Schema Version Tracking ---

class SchemaVersion(Base):
    __tablename__ = "schema_version"

    id = Column(Integer, primary_key=True, autoincrement=True)
    version = Column(String(20), nullable=False)
    applied_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
