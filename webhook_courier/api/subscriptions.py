from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from webhook_courier.database import get_db
from webhook_courier.models import Subscription, Endpoint, Application, gen_id
from webhook_courier.schemas import SubscriptionCreate, SubscriptionResponse
from webhook_courier.auth.dependencies import get_current_app

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


@router.post("", response_model=SubscriptionResponse, status_code=201)
def create_subscription(
    body: SubscriptionCreate,
    db: Session = Depends(get_db),
    app: Application | None = Depends(get_current_app),
):
    endpoint = db.query(Endpoint).filter(Endpoint.id == body.endpoint_id).first()
    if not endpoint:
        raise HTTPException(404, "Endpoint not found")
    if app and endpoint.app_id != app.id:
        raise HTTPException(403, "Endpoint does not belong to your application")

    existing = db.query(Subscription).filter(
        Subscription.endpoint_id == body.endpoint_id,
        Subscription.event_type == body.event_type,
    ).first()
    if existing:
        raise HTTPException(409, "Subscription already exists")

    sub = Subscription(
        id=gen_id(),
        app_id=app.id if app else None,
        endpoint_id=body.endpoint_id,
        event_type=body.event_type,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


@router.get("", response_model=list[SubscriptionResponse])
def list_subscriptions(
    endpoint_id: str | None = None,
    event_type: str | None = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    app: Application | None = Depends(get_current_app),
):
    query = db.query(Subscription)
    if app:
        query = query.filter(Subscription.app_id == app.id)
    if endpoint_id:
        query = query.filter(Subscription.endpoint_id == endpoint_id)
    if event_type:
        query = query.filter(Subscription.event_type == event_type)
    return query.offset(skip).limit(limit).all()


@router.get("/{subscription_id}", response_model=SubscriptionResponse)
def get_subscription(subscription_id: str, db: Session = Depends(get_db)):
    sub = db.query(Subscription).filter(Subscription.id == subscription_id).first()
    if not sub:
        raise HTTPException(404, "Subscription not found")
    return sub


@router.delete("/{subscription_id}", status_code=204)
def delete_subscription(
    subscription_id: str,
    db: Session = Depends(get_db),
    app: Application | None = Depends(get_current_app),
):
    query = db.query(Subscription).filter(Subscription.id == subscription_id)
    if app:
        query = query.filter(Subscription.app_id == app.id)
    sub = query.first()
    if not sub:
        raise HTTPException(404, "Subscription not found")
    db.delete(sub)
    db.commit()


@router.patch("/{subscription_id}", response_model=SubscriptionResponse)
def update_subscription(
    subscription_id: str,
    body: dict,
    db: Session = Depends(get_db),
    app: Application | None = Depends(get_current_app),
):
    query = db.query(Subscription).filter(Subscription.id == subscription_id)
    if app:
        query = query.filter(Subscription.app_id == app.id)
    sub = query.first()
    if not sub:
        raise HTTPException(404, "Subscription not found")
    if "event_type" in body:
        sub.event_type = body["event_type"]
    if "is_active" in body:
        sub.is_active = body["is_active"]
    db.commit()
    db.refresh(sub)
    return sub
