import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from webhook_courier.database import get_db
from webhook_courier.models import EventSchema, Application, gen_id
from webhook_courier.schemas import EventSchemaCreate, EventSchemaResponse
from webhook_courier.auth.dependencies import get_current_app

router = APIRouter(prefix="/schemas", tags=["schemas"])


@router.post("", response_model=EventSchemaResponse, status_code=201)
def create_schema(
    body: EventSchemaCreate,
    db: Session = Depends(get_db),
    app: Application | None = Depends(get_current_app),
):
    try:
        json.loads(body.schema_definition)
    except json.JSONDecodeError:
        raise HTTPException(422, "schema_json must be valid JSON")

    existing = db.query(EventSchema).filter(
        EventSchema.event_type == body.event_type,
        EventSchema.version == body.version,
        EventSchema.app_id == (app.id if app else None),
    ).first()
    if existing:
        raise HTTPException(409, f"Schema version {body.version} already exists for event_type '{body.event_type}'")

    schema = EventSchema(
        id=gen_id(),
        app_id=app.id if app else None,
        event_type=body.event_type,
        version=body.version,
        schema_json=body.schema_definition,
    )
    db.add(schema)
    db.commit()
    db.refresh(schema)
    return schema


@router.get("", response_model=list[EventSchemaResponse])
def list_schemas(
    event_type: str | None = None,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    app: Application | None = Depends(get_current_app),
):
    query = db.query(EventSchema)
    if app:
        query = query.filter((EventSchema.app_id == app.id) | (EventSchema.app_id.is_(None)))
    if event_type:
        query = query.filter(EventSchema.event_type == event_type)
    return query.order_by(EventSchema.event_type, EventSchema.version.desc()).offset(skip).limit(limit).all()


@router.get("/{schema_id}", response_model=EventSchemaResponse)
def get_schema(schema_id: str, db: Session = Depends(get_db)):
    schema = db.query(EventSchema).filter(EventSchema.id == schema_id).first()
    if not schema:
        raise HTTPException(404, "Schema not found")
    return schema


@router.delete("/{schema_id}", status_code=204)
def delete_schema(
    schema_id: str,
    db: Session = Depends(get_db),
    app: Application | None = Depends(get_current_app),
):
    query = db.query(EventSchema).filter(EventSchema.id == schema_id)
    if app:
        query = query.filter(EventSchema.app_id == app.id)
    schema = query.first()
    if not schema:
        raise HTTPException(404, "Schema not found")
    schema.is_active = False
    db.commit()
