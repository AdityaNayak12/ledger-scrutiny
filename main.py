from typing import Optional, List
from decimal import Decimal
from fastapi import FastAPI, Depends, UploadFile, File, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import select, delete
from pydantic import BaseModel, ConfigDict
from datetime import datetime

from db.session import get_db
from db.models import Entity, LedgerAccount, TrialBalanceSnapshot, AuditException
from ingestion.tally_parser import parse_tally_xml
from ingestion.tally_normalizer import normalize_tally_data
from rules.engine import run_scrutiny

app = FastAPI(
    title="Audit Scrutiny Engine API",
    description="API for Tally XML ingestion and trial balance scrutiny engine.",
    version="1.0.0"
)


# Pydantic schemas for responses
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


@app.post("/api/v1/ingest", response_model=IngestionResponse, status_code=status.HTTP_201_CREATED)
async def ingest_tally_export(
    file: UploadFile = File(...),
    materiality_threshold: Decimal = Query(Decimal("0.00"), description="Materiality threshold for the entity"),
    db: Session = Depends(get_db)
):
    """
    Upload a Tally XML export, parse it, and normalize it into the database.
    """
    if not file.filename.endswith(".xml"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only XML files are supported."
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
        entity = normalize_tally_data(parsed_data, db, materiality_threshold=materiality_threshold)
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


@app.post("/api/v1/entities/{entity_id}/scrutinize", response_model=List[ExceptionResponse])
def trigger_scrutiny(entity_id: int, db: Session = Depends(get_db)):
    """
    Trigger a scrutiny run for the specified entity, running all registered rules,
    persisting the resulting exceptions to the database, and returning them.
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

    # 3. Clear existing exceptions for this entity (to avoid duplicates on rerun)
    db.execute(delete(AuditException).where(AuditException.entity_id == entity_id))
    db.flush()

    # 4. Run rules engine
    exceptions = run_scrutiny(entity, accounts, snapshots)

    # 5. Persist exceptions to the database
    for exc in exceptions:
        db.add(exc)
    db.commit()

    # 6. Re-query exceptions with relationship loaded to return correct response
    db_exceptions = db.execute(
        select(AuditException)
        .options(joinedload(AuditException.ledger_account))
        .where(AuditException.entity_id == entity_id)
    ).scalars().all()

    # Build response objects mapping the ledger account name manually to match schema
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


@app.get("/api/v1/entities/{entity_id}/exceptions", response_model=List[ExceptionResponse])
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
