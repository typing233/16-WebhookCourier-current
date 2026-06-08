import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Integer, Float, Text, DateTime, Boolean, Index, Enum as SAEnum
)
from webhook_courier.database import Base
import enum


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


class Endpoint(Base):
    __tablename__ = "endpoints"

    id = Column(String(36), primary_key=True, default=gen_id)
    url = Column(String(2048), nullable=False)
    secret = Column(String(256), nullable=False)
    description = Column(String(512), default="")
    is_active = Column(Boolean, default=True, nullable=False)
    max_retries = Column(Integer, default=5, nullable=False)
    retry_base_interval = Column(Float, default=2.0, nullable=False)
    rate_limit_per_sec = Column(Integer, default=50, nullable=False)
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
    idempotency_key = Column(String(256), nullable=False)
    payload = Column(Text, nullable=False)
    attempt_count = Column(Integer, nullable=False)
    last_response_code = Column(Integer, nullable=True)
    last_error = Column(Text, nullable=True)
    replayed = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
