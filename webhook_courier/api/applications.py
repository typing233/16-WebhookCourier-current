import secrets
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from webhook_courier.database import get_db
from webhook_courier.models import Application, ApiKey, gen_id
from webhook_courier.schemas import (
    ApplicationCreate, ApplicationUpdate, ApplicationResponse,
    ApiKeyCreate, ApiKeyResponse,
)
from webhook_courier.auth.dependencies import get_current_app, generate_api_key

router = APIRouter(prefix="/applications", tags=["applications"])


@router.post("", response_model=ApplicationResponse, status_code=201)
def create_application(body: ApplicationCreate, db: Session = Depends(get_db)):
    existing = db.query(Application).filter(Application.name == body.name).first()
    if existing:
        raise HTTPException(409, "Application name already exists")

    signing_key = body.signing_key or f"sk_{secrets.token_urlsafe(32)}"
    app = Application(id=gen_id(), name=body.name, signing_key=signing_key)
    db.add(app)
    db.commit()
    db.refresh(app)
    return app


@router.get("", response_model=list[ApplicationResponse])
def list_applications(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    return db.query(Application).offset(skip).limit(limit).all()


@router.get("/{app_id}", response_model=ApplicationResponse)
def get_application(app_id: str, db: Session = Depends(get_db)):
    app = db.query(Application).filter(Application.id == app_id).first()
    if not app:
        raise HTTPException(404, "Application not found")
    return app


@router.patch("/{app_id}", response_model=ApplicationResponse)
def update_application(app_id: str, body: ApplicationUpdate, db: Session = Depends(get_db)):
    app = db.query(Application).filter(Application.id == app_id).first()
    if not app:
        raise HTTPException(404, "Application not found")
    updates = body.model_dump(exclude_unset=True)
    for key, val in updates.items():
        setattr(app, key, val)
    db.commit()
    db.refresh(app)
    return app


@router.delete("/{app_id}", status_code=204)
def delete_application(app_id: str, db: Session = Depends(get_db)):
    app = db.query(Application).filter(Application.id == app_id).first()
    if not app:
        raise HTTPException(404, "Application not found")
    db.delete(app)
    db.commit()


@router.post("/{app_id}/keys", response_model=ApiKeyResponse, status_code=201)
def create_api_key(app_id: str, body: ApiKeyCreate, db: Session = Depends(get_db)):
    app = db.query(Application).filter(Application.id == app_id).first()
    if not app:
        raise HTTPException(404, "Application not found")

    full_key, prefix, key_hash = generate_api_key()
    api_key = ApiKey(
        id=gen_id(),
        app_id=app_id,
        key_hash=key_hash,
        prefix=prefix,
        label=body.label,
    )
    db.add(api_key)
    db.commit()
    db.refresh(api_key)

    return ApiKeyResponse(
        id=api_key.id,
        app_id=api_key.app_id,
        prefix=api_key.prefix,
        label=api_key.label,
        is_active=api_key.is_active,
        created_at=api_key.created_at,
        key=full_key,
    )


@router.get("/{app_id}/keys", response_model=list[ApiKeyResponse])
def list_api_keys(app_id: str, db: Session = Depends(get_db)):
    app = db.query(Application).filter(Application.id == app_id).first()
    if not app:
        raise HTTPException(404, "Application not found")
    return db.query(ApiKey).filter(ApiKey.app_id == app_id).all()


@router.delete("/{app_id}/keys/{key_id}", status_code=204)
def delete_api_key(app_id: str, key_id: str, db: Session = Depends(get_db)):
    api_key = db.query(ApiKey).filter(ApiKey.id == key_id, ApiKey.app_id == app_id).first()
    if not api_key:
        raise HTTPException(404, "API key not found")
    db.delete(api_key)
    db.commit()
