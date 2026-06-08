from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from webhook_courier.database import get_db
from webhook_courier.models import Endpoint, Application, gen_id
from webhook_courier.schemas import EndpointCreate, EndpointUpdate, EndpointResponse
from webhook_courier.core.rate_limiter import rate_limiter
from webhook_courier.auth.dependencies import get_current_app

router = APIRouter(prefix="/endpoints", tags=["endpoints"])


def _scope_query(query, app: Application | None):
    if app is not None:
        return query.filter(Endpoint.app_id == app.id)
    return query


@router.post("", response_model=EndpointResponse, status_code=201)
def create_endpoint(
    body: EndpointCreate,
    db: Session = Depends(get_db),
    app: Application | None = Depends(get_current_app),
):
    ep = Endpoint(id=gen_id(), **body.model_dump())
    if app:
        ep.app_id = app.id
    db.add(ep)
    db.commit()
    db.refresh(ep)
    rate_limiter.configure(ep.id, ep.rate_limit_per_sec)
    return ep


@router.get("", response_model=list[EndpointResponse])
def list_endpoints(
    skip: int = 0,
    limit: int = 50,
    is_active: bool | None = None,
    db: Session = Depends(get_db),
    app: Application | None = Depends(get_current_app),
):
    query = _scope_query(db.query(Endpoint), app)
    if is_active is not None:
        query = query.filter(Endpoint.is_active == is_active)
    return query.offset(skip).limit(limit).all()


@router.get("/{endpoint_id}", response_model=EndpointResponse)
def get_endpoint(
    endpoint_id: str,
    db: Session = Depends(get_db),
    app: Application | None = Depends(get_current_app),
):
    query = _scope_query(db.query(Endpoint), app)
    ep = query.filter(Endpoint.id == endpoint_id).first()
    if not ep:
        raise HTTPException(404, "Endpoint not found")
    return ep


@router.patch("/{endpoint_id}", response_model=EndpointResponse)
def update_endpoint(
    endpoint_id: str,
    body: EndpointUpdate,
    db: Session = Depends(get_db),
    app: Application | None = Depends(get_current_app),
):
    query = _scope_query(db.query(Endpoint), app)
    ep = query.filter(Endpoint.id == endpoint_id).first()
    if not ep:
        raise HTTPException(404, "Endpoint not found")
    updates = body.model_dump(exclude_unset=True)
    for key, val in updates.items():
        setattr(ep, key, val)
    db.commit()
    db.refresh(ep)
    rate_limiter.configure(ep.id, ep.rate_limit_per_sec)
    return ep


@router.delete("/{endpoint_id}", status_code=204)
def delete_endpoint(
    endpoint_id: str,
    db: Session = Depends(get_db),
    app: Application | None = Depends(get_current_app),
):
    query = _scope_query(db.query(Endpoint), app)
    ep = query.filter(Endpoint.id == endpoint_id).first()
    if not ep:
        raise HTTPException(404, "Endpoint not found")
    db.delete(ep)
    db.commit()
    rate_limiter.remove(endpoint_id)
