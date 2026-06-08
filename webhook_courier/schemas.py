from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


# --- Endpoint Schemas ---

class EndpointCreate(BaseModel):
    url: str = Field(..., max_length=2048)
    secret: str = Field(..., min_length=16, max_length=256)
    description: str = Field(default="", max_length=512)
    max_retries: int = Field(default=5, ge=1, le=20)
    retry_base_interval: float = Field(default=2.0, ge=0.5, le=60.0)
    jitter_strategy: str = Field(default="full")
    per_attempt_timeout: float = Field(default=10.0, ge=1.0, le=120.0)
    max_backoff: float = Field(default=3600.0, ge=1.0, le=86400.0)
    rate_limit_per_sec: int = Field(default=50, ge=1, le=1000)
    health_check_url: Optional[str] = Field(None, max_length=2048)
    health_check_method: str = Field(default="HEAD")
    health_check_interval: Optional[int] = Field(None, ge=10, le=3600)


class EndpointUpdate(BaseModel):
    url: Optional[str] = Field(None, max_length=2048)
    secret: Optional[str] = Field(None, min_length=16, max_length=256)
    description: Optional[str] = Field(None, max_length=512)
    is_active: Optional[bool] = None
    max_retries: Optional[int] = Field(None, ge=1, le=20)
    retry_base_interval: Optional[float] = Field(None, ge=0.5, le=60.0)
    jitter_strategy: Optional[str] = None
    per_attempt_timeout: Optional[float] = Field(None, ge=1.0, le=120.0)
    max_backoff: Optional[float] = Field(None, ge=1.0, le=86400.0)
    rate_limit_per_sec: Optional[int] = Field(None, ge=1, le=1000)
    health_check_url: Optional[str] = Field(None, max_length=2048)
    health_check_method: Optional[str] = None
    health_check_interval: Optional[int] = Field(None, ge=10, le=3600)


class EndpointResponse(BaseModel):
    id: str
    app_id: Optional[str] = None
    url: str
    description: str
    is_active: bool
    max_retries: int
    retry_base_interval: float
    jitter_strategy: str
    per_attempt_timeout: float
    max_backoff: float
    rate_limit_per_sec: int
    health_check_url: Optional[str] = None
    health_check_method: str
    circuit_state: str
    failure_count: int
    success_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# --- Message Schemas ---

class MessageIngest(BaseModel):
    endpoint_id: str
    idempotency_key: str = Field(..., min_length=1, max_length=256)
    payload: str
    event_type: Optional[str] = Field(None, max_length=256)


class MessageRoute(BaseModel):
    event_type: str = Field(..., min_length=1, max_length=256)
    idempotency_key: str = Field(..., min_length=1, max_length=256)
    payload: str


class MessageResponse(BaseModel):
    id: str
    endpoint_id: str
    app_id: Optional[str] = None
    event_type: Optional[str] = None
    idempotency_key: str
    status: str
    attempt_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class MessageListResponse(BaseModel):
    items: list[MessageResponse]
    total: int
    skip: int
    limit: int


class BatchMessageRequest(BaseModel):
    ids: list[str] = Field(..., min_length=1, max_length=1000)


class BatchMessageResponse(BaseModel):
    affected_count: int
    ids: list[str]


# --- Dead Letter Schemas ---

class DeadLetterResponse(BaseModel):
    id: str
    message_id: str
    endpoint_id: str
    app_id: Optional[str] = None
    event_type: Optional[str] = None
    idempotency_key: str
    payload: str
    attempt_count: int
    last_response_code: Optional[int]
    last_error: Optional[str]
    replayed: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class BatchReplayRequest(BaseModel):
    ids: Optional[list[str]] = None
    filter_endpoint_id: Optional[str] = None
    filter_before: Optional[datetime] = None


class BatchReplayResponse(BaseModel):
    replayed_count: int
    new_message_ids: list[str]


class PurgeRequest(BaseModel):
    ids: Optional[list[str]] = None
    filter_endpoint_id: Optional[str] = None
    filter_before: Optional[datetime] = None


class DlqStatsResponse(BaseModel):
    total: int
    by_endpoint: dict[str, int]
    by_error: dict[str, int]
    replayed_count: int
    unreplayed_count: int


# --- Application Schemas ---

class ApplicationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    signing_key: Optional[str] = Field(None, min_length=16, max_length=256)


class ApplicationUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=256)
    signing_key: Optional[str] = Field(None, min_length=16, max_length=256)
    is_active: Optional[bool] = None


class ApplicationResponse(BaseModel):
    id: str
    name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ApiKeyCreate(BaseModel):
    label: str = Field(default="", max_length=256)


class ApiKeyResponse(BaseModel):
    id: str
    app_id: str
    prefix: str
    label: str
    is_active: bool
    created_at: datetime
    key: Optional[str] = None

    model_config = {"from_attributes": True}


# --- Delivery Log Schemas ---

class DeliveryLogResponse(BaseModel):
    id: str
    message_id: str
    endpoint_id: str
    app_id: Optional[str] = None
    attempt_number: int
    status: str
    response_code: Optional[int]
    error_message: Optional[str]
    latency_ms: Optional[float]
    created_at: datetime

    model_config = {"from_attributes": True}


class DeliveryLogListResponse(BaseModel):
    items: list[DeliveryLogResponse]
    total: int
    skip: int
    limit: int


class DeliveryStatsResponse(BaseModel):
    total_attempts: int
    success_count: int
    failure_count: int
    success_rate: float
    avg_latency_ms: Optional[float]
    p50_latency_ms: Optional[float]
    p95_latency_ms: Optional[float]
    p99_latency_ms: Optional[float]
    error_breakdown: dict[str, int]


# --- Subscription Schemas ---

class SubscriptionCreate(BaseModel):
    endpoint_id: str
    event_type: str = Field(..., min_length=1, max_length=256)


class SubscriptionResponse(BaseModel):
    id: str
    app_id: Optional[str] = None
    endpoint_id: str
    event_type: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# --- Event Schema Schemas ---

class EventSchemaCreate(BaseModel):
    event_type: str = Field(..., min_length=1, max_length=256)
    version: int = Field(default=1, ge=1)
    schema_definition: str = Field(serialization_alias="schema_json")


class EventSchemaResponse(BaseModel):
    id: str
    app_id: Optional[str] = None
    event_type: str
    version: int
    schema_definition: str = Field(validation_alias="schema_json", serialization_alias="schema_json")
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True, "populate_by_name": True}


# --- Alert Config Schemas ---

class AlertConfigCreate(BaseModel):
    endpoint_id: Optional[str] = None
    channel: str = Field(..., pattern="^(webhook|email)$")
    destination: str = Field(..., max_length=2048)
    failure_threshold: int = Field(default=3, ge=1, le=100)
    cooldown_seconds: int = Field(default=300, ge=60, le=86400)


class AlertConfigResponse(BaseModel):
    id: str
    app_id: Optional[str] = None
    endpoint_id: Optional[str] = None
    channel: str
    destination: str
    failure_threshold: int
    cooldown_seconds: int
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
