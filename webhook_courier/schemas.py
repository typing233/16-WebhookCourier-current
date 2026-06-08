from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class EndpointCreate(BaseModel):
    url: str = Field(..., max_length=2048)
    secret: str = Field(..., min_length=16, max_length=256)
    description: str = Field(default="", max_length=512)
    max_retries: int = Field(default=5, ge=1, le=20)
    retry_base_interval: float = Field(default=2.0, ge=0.5, le=60.0)
    rate_limit_per_sec: int = Field(default=50, ge=1, le=1000)


class EndpointUpdate(BaseModel):
    url: Optional[str] = Field(None, max_length=2048)
    secret: Optional[str] = Field(None, min_length=16, max_length=256)
    description: Optional[str] = Field(None, max_length=512)
    is_active: Optional[bool] = None
    max_retries: Optional[int] = Field(None, ge=1, le=20)
    retry_base_interval: Optional[float] = Field(None, ge=0.5, le=60.0)
    rate_limit_per_sec: Optional[int] = Field(None, ge=1, le=1000)


class EndpointResponse(BaseModel):
    id: str
    url: str
    description: str
    is_active: bool
    max_retries: int
    retry_base_interval: float
    rate_limit_per_sec: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MessageIngest(BaseModel):
    endpoint_id: str
    idempotency_key: str = Field(..., min_length=1, max_length=256)
    payload: str


class MessageResponse(BaseModel):
    id: str
    endpoint_id: str
    idempotency_key: str
    status: str
    attempt_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class DeadLetterResponse(BaseModel):
    id: str
    message_id: str
    endpoint_id: str
    idempotency_key: str
    payload: str
    attempt_count: int
    last_response_code: Optional[int]
    last_error: Optional[str]
    replayed: bool
    created_at: datetime

    model_config = {"from_attributes": True}
