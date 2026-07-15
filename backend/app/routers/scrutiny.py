from typing import Optional, List
from decimal import Decimal
from datetime import datetime, date
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import select, delete
from pydantic import BaseModel, ConfigDict

from app.db.session import get_db
from app.db.models import Entity, LedgerAccount, TrialBalanceSnapshot, AuditException
from app.ingestion.tally_parser import parse_tally_xml
from app.ingestion.tally_normalizer import normalize_tally_data
from app.rules.engine import run_scrutiny

# Router without prefix to match the exact URL layout
router = APIRouter(
    tags=["scrutiny"]
)


# Pydantic schemas
class EntityCreate(BaseModel):
    name: str
    financial_year_start: date
    financial_year_end: date
    materiality_threshold: Decimal


class EntityResponse(BaseModel):
    id: int
    name: str
    financial_year_start: date
    financial_year_end: date
    materiality_threshold: Decimal

    model_config = ConfigDict(from_attributes=True)


class ExceptionResponse(BaseModel):
    id: int
    rule_name: str
    severity: str
    message: str
    ledger_account_name: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class IngestionResponse(BaseModel):
    message: str
    entity_id: int
    entity_name: str


class ScrutinyRunSummary(BaseModel):
    status: str
    exceptions_count: int


# --- Entity management endpoints ---

@router.get("/entities", response_model=List[EntityResponse])
def list_entities(db: Session = Depends(get_db)):
    """List all business entities."""
    return db.execute(select(Entity)).scalars().all()


@router.post("/entities", response_model=EntityResponse, status_code=status.HTTP_201_CREATED)
def create_entity(entity_in: EntityCreate, db: Session = Depends(get_db)):
    """Create a new business entity."""
    entity = Entity(
        name=entity_in.name,
        financial_year_start=entity_in.financial_year_start,
        financial_year_end=entity_in.financial_year_end,
        materiality_threshold=entity_in.materiality_threshold
    )
    db.add(entity)
    db.commit()
    db.refresh(entity)
    return entity


# --- Scrutiny and Ingestion endpoints ---

@router.post("/entities/{entity_id}/upload", response_model=IngestionResponse)
async def upload_tally_export(
    entity_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """
    Upload a Tally XML export for a specific entity, parsing and normalizing it.
    """
    if not file.filename.endswith(".xml"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only XML files are supported."
        )

    # Verify entity exists
    entity = db.execute(select(Entity).where(Entity.id == entity_id)).scalar_one_or_none()
    if not entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entity with ID {entity_id} not found."
        )

    try:
        contents = await file.read()
        parsed_data = parse_tally_xml(contents)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse XML content: {str(e)}"
        )

    try:
        # Normalize and clear existing data for this entity
        normalize_tally_data(
            parsed_data, 
            db, 
            materiality_threshold=entity.materiality_threshold, 
            entity_id=entity.id
        )
        db.commit()
        return IngestionResponse(
            message="Ingestion successful",
            entity_id=entity.id,
            entity_name=entity.name
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to normalize and save ledger data: {str(e)}"
        )


@router.post("/entities/{entity_id}/scrutiny-run", response_model=ScrutinyRunSummary)
def trigger_scrutiny_run(entity_id: int, db: Session = Depends(get_db)):
    """
    Trigger the rules engine scrutiny run for the specified entity,
    and return a summary containing the count of generated exceptions.
    """
    # 1. Fetch Entity
    entity = db.execute(select(Entity).where(Entity.id == entity_id)).scalar_one_or_none()
    if not entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entity with ID {entity_id} not found."
        )

    # 2. Fetch Accounts and Snapshots
    accounts = db.execute(
        select(LedgerAccount).where(LedgerAccount.entity_id == entity_id)
    ).scalars().all()
    
    snapshots = db.execute(
        select(TrialBalanceSnapshot).where(TrialBalanceSnapshot.entity_id == entity_id)
    ).scalars().all()

    # 3. Clear existing exceptions for this entity
    db.execute(delete(AuditException).where(AuditException.entity_id == entity_id))
    db.flush()

    # 4. Run rules engine
    exceptions = run_scrutiny(entity, accounts, snapshots)

    # 5. Persist exceptions to the database
    for exc in exceptions:
        db.add(exc)
    db.commit()

    return ScrutinyRunSummary(
        status="success",
        exceptions_count=len(exceptions)
    )


@router.get("/entities/{entity_id}/exceptions", response_model=List[ExceptionResponse])
def list_exceptions(
    entity_id: int,
    severity: Optional[str] = Query(None, description="Filter exceptions by severity"),
    db: Session = Depends(get_db)
):
    """
    Get the list of scrutiny exceptions persisted for the specified entity,
    optionally filtered by severity level.
    """
    # Verify entity exists
    entity_exists = db.execute(select(Entity.id).where(Entity.id == entity_id)).scalar_one_or_none()
    if not entity_exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entity with ID {entity_id} not found."
        )

    query = select(AuditException).options(joinedload(AuditException.ledger_account)).where(AuditException.entity_id == entity_id)
    if severity:
        query = query.where(AuditException.severity == severity)

    db_exceptions = db.execute(query).scalars().all()

    response = []
    for exc in db_exceptions:
        response.append(
            ExceptionResponse(
                id=exc.id,
                rule_name=exc.rule_name,
                severity=exc.severity,
                message=exc.message,
                ledger_account_name=exc.ledger_account.name if exc.ledger_account else None,
                created_at=exc.created_at
            )
        )
    return response
