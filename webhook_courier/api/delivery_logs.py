import csv
import io
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from webhook_courier.database import get_db
from webhook_courier.models import DeliveryLog, Application
from webhook_courier.schemas import DeliveryLogResponse, DeliveryLogListResponse, DeliveryStatsResponse
from webhook_courier.auth.dependencies import get_current_app

logger = logging.getLogger("webhook_courier.api.delivery_logs")

router = APIRouter(prefix="/delivery-logs", tags=["delivery-logs"])


def _scope_query(query, app: Application | None):
    if app is not None:
        return query.filter(DeliveryLog.app_id == app.id)
    return query


@router.get("", response_model=DeliveryLogListResponse)
def list_delivery_logs(
    endpoint_id: str | None = None,
    message_id: str | None = None,
    status: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    skip: int = 0,
    limit: int = Query(default=50, le=200),
    db: Session = Depends(get_db),
    app: Application | None = Depends(get_current_app),
):
    query = _scope_query(db.query(DeliveryLog), app)
    if endpoint_id:
        query = query.filter(DeliveryLog.endpoint_id == endpoint_id)
    if message_id:
        query = query.filter(DeliveryLog.message_id == message_id)
    if status:
        query = query.filter(DeliveryLog.status == status)
    if date_from:
        query = query.filter(DeliveryLog.created_at >= date_from)
    if date_to:
        query = query.filter(DeliveryLog.created_at <= date_to)

    total = query.count()
    items = query.order_by(DeliveryLog.created_at.desc()).offset(skip).limit(limit).all()
    return DeliveryLogListResponse(items=items, total=total, skip=skip, limit=limit)


@router.get("/stats", response_model=DeliveryStatsResponse)
def get_delivery_stats(
    endpoint_id: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    db: Session = Depends(get_db),
    app: Application | None = Depends(get_current_app),
):
    query = _scope_query(db.query(DeliveryLog), app)
    if endpoint_id:
        query = query.filter(DeliveryLog.endpoint_id == endpoint_id)
    if date_from:
        query = query.filter(DeliveryLog.created_at >= date_from)
    if date_to:
        query = query.filter(DeliveryLog.created_at <= date_to)

    total = query.count()
    if total == 0:
        return DeliveryStatsResponse(
            total_attempts=0, success_count=0, failure_count=0,
            success_rate=0.0, avg_latency_ms=None,
            p50_latency_ms=None, p95_latency_ms=None, p99_latency_ms=None,
            error_breakdown={},
        )

    success_count = query.filter(DeliveryLog.status == "success").count()
    failure_count = total - success_count
    success_rate = success_count / total if total > 0 else 0.0

    avg_latency = db.query(func.avg(DeliveryLog.latency_ms)).filter(
        DeliveryLog.id.in_(query.with_entities(DeliveryLog.id))
    ).scalar()

    latencies = [
        row[0] for row in
        query.with_entities(DeliveryLog.latency_ms)
        .filter(DeliveryLog.latency_ms.isnot(None))
        .order_by(DeliveryLog.latency_ms)
        .all()
    ]

    p50 = _percentile(latencies, 50)
    p95 = _percentile(latencies, 95)
    p99 = _percentile(latencies, 99)

    error_rows = (
        query.filter(DeliveryLog.status != "success")
        .with_entities(DeliveryLog.error_message, func.count())
        .group_by(DeliveryLog.error_message)
        .all()
    )
    error_breakdown = {(err or "unknown"): cnt for err, cnt in error_rows}

    return DeliveryStatsResponse(
        total_attempts=total,
        success_count=success_count,
        failure_count=failure_count,
        success_rate=round(success_rate, 4),
        avg_latency_ms=round(avg_latency, 2) if avg_latency else None,
        p50_latency_ms=round(p50, 2) if p50 else None,
        p95_latency_ms=round(p95, 2) if p95 else None,
        p99_latency_ms=round(p99, 2) if p99 else None,
        error_breakdown=error_breakdown,
    )


@router.get("/export")
def export_delivery_logs(
    endpoint_id: str | None = None,
    message_id: str | None = None,
    status: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    format: str = Query(default="csv", pattern="^(csv|json)$"),
    db: Session = Depends(get_db),
    app: Application | None = Depends(get_current_app),
):
    query = _scope_query(db.query(DeliveryLog), app)
    if endpoint_id:
        query = query.filter(DeliveryLog.endpoint_id == endpoint_id)
    if message_id:
        query = query.filter(DeliveryLog.message_id == message_id)
    if status:
        query = query.filter(DeliveryLog.status == status)
    if date_from:
        query = query.filter(DeliveryLog.created_at >= date_from)
    if date_to:
        query = query.filter(DeliveryLog.created_at <= date_to)

    logs = query.order_by(DeliveryLog.created_at.desc()).limit(10000).all()

    if format == "json":
        import json
        data = [
            {
                "id": l.id, "message_id": l.message_id, "endpoint_id": l.endpoint_id,
                "attempt_number": l.attempt_number, "status": l.status,
                "response_code": l.response_code, "error_message": l.error_message,
                "latency_ms": l.latency_ms, "created_at": l.created_at.isoformat(),
            }
            for l in logs
        ]
        return StreamingResponse(
            io.BytesIO(json.dumps(data, indent=2).encode()),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=delivery_logs.json"},
        )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "message_id", "endpoint_id", "attempt_number", "status",
                     "response_code", "error_message", "latency_ms", "created_at"])
    for l in logs:
        writer.writerow([
            l.id, l.message_id, l.endpoint_id, l.attempt_number, l.status,
            l.response_code, l.error_message, l.latency_ms,
            l.created_at.isoformat() if l.created_at else "",
        ])
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=delivery_logs.csv"},
    )


def _percentile(sorted_data: list[float], pct: int) -> float | None:
    if not sorted_data:
        return None
    idx = int(len(sorted_data) * pct / 100)
    idx = min(idx, len(sorted_data) - 1)
    return sorted_data[idx]
