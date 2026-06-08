from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from webhook_courier.database import get_db
from webhook_courier.models import AlertConfig, Application, gen_id
from webhook_courier.schemas import AlertConfigCreate, AlertConfigResponse
from webhook_courier.auth.dependencies import get_current_app

router = APIRouter(prefix="/alert-configs", tags=["alerts"])


@router.post("", response_model=AlertConfigResponse, status_code=201)
def create_alert_config(
    body: AlertConfigCreate,
    db: Session = Depends(get_db),
    app: Application | None = Depends(get_current_app),
):
    config = AlertConfig(
        id=gen_id(),
        app_id=app.id if app else None,
        endpoint_id=body.endpoint_id,
        channel=body.channel,
        destination=body.destination,
        failure_threshold=body.failure_threshold,
        cooldown_seconds=body.cooldown_seconds,
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    return config


@router.get("", response_model=list[AlertConfigResponse])
def list_alert_configs(
    endpoint_id: str | None = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    app: Application | None = Depends(get_current_app),
):
    query = db.query(AlertConfig)
    if app:
        query = query.filter(AlertConfig.app_id == app.id)
    if endpoint_id:
        query = query.filter(AlertConfig.endpoint_id == endpoint_id)
    return query.offset(skip).limit(limit).all()


@router.get("/{config_id}", response_model=AlertConfigResponse)
def get_alert_config(config_id: str, db: Session = Depends(get_db)):
    config = db.query(AlertConfig).filter(AlertConfig.id == config_id).first()
    if not config:
        raise HTTPException(404, "Alert config not found")
    return config


@router.patch("/{config_id}", response_model=AlertConfigResponse)
def update_alert_config(
    config_id: str,
    body: AlertConfigCreate,
    db: Session = Depends(get_db),
    app: Application | None = Depends(get_current_app),
):
    query = db.query(AlertConfig).filter(AlertConfig.id == config_id)
    if app:
        query = query.filter(AlertConfig.app_id == app.id)
    config = query.first()
    if not config:
        raise HTTPException(404, "Alert config not found")
    config.channel = body.channel
    config.destination = body.destination
    config.failure_threshold = body.failure_threshold
    config.cooldown_seconds = body.cooldown_seconds
    if body.endpoint_id is not None:
        config.endpoint_id = body.endpoint_id
    db.commit()
    db.refresh(config)
    return config


@router.delete("/{config_id}", status_code=204)
def delete_alert_config(
    config_id: str,
    db: Session = Depends(get_db),
    app: Application | None = Depends(get_current_app),
):
    query = db.query(AlertConfig).filter(AlertConfig.id == config_id)
    if app:
        query = query.filter(AlertConfig.app_id == app.id)
    config = query.first()
    if not config:
        raise HTTPException(404, "Alert config not found")
    db.delete(config)
    db.commit()
