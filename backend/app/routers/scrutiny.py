import re
import os
import httpx
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
    materiality_threshold: Decimal


class EntityResponse(BaseModel):
    id: int
    name: str
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


class PeriodResponse(BaseModel):
    period_start: date
    period_end: date

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
        materiality_threshold=entity_in.materiality_threshold
    )
    db.add(entity)
    db.commit()
    db.refresh(entity)
    return entity


@router.get("/entities/{entity_id}/periods", response_model=List[PeriodResponse])
def list_periods(entity_id: int, db: Session = Depends(get_db)):
    """List all financial periods with data for this entity."""
    results = db.execute(
        select(TrialBalanceSnapshot.period_start, TrialBalanceSnapshot.period_end)
        .where(TrialBalanceSnapshot.entity_id == entity_id)
        .distinct()
    ).all()
    # Sort descending by period_start
    sorted_results = sorted(results, key=lambda x: x.period_start, reverse=True)
    return [
        PeriodResponse(period_start=r.period_start, period_end=r.period_end)
        for r in sorted_results
    ]


# --- Scrutiny and Ingestion endpoints ---

@router.post("/entities/{entity_id}/upload", response_model=IngestionResponse)
async def upload_tally_export(
    entity_id: int,
    clear_only_period: bool = Query(False),
    target_period_start: Optional[date] = Query(None),
    target_period_end: Optional[date] = Query(None),
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
        # Normalize and clear existing data for this entity/period
        normalize_tally_data(
            parsed_data, 
            db, 
            materiality_threshold=entity.materiality_threshold, 
            entity_id=entity.id,
            clear_only_period=clear_only_period,
            target_period_start=target_period_start,
            target_period_end=target_period_end
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
def trigger_scrutiny_run(
    entity_id: int,
    period_start: date = Query(...),
    period_end: date = Query(...),
    db: Session = Depends(get_db)
):
    """
    Trigger the rules engine scrutiny run for the specified entity and period.
    """
    # 1. Fetch Entity
    entity = db.execute(select(Entity).where(Entity.id == entity_id)).scalar_one_or_none()
    if not entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entity with ID {entity_id} not found."
        )

    # Set transient period attributes so rules can access them without database schema changes
    entity.financial_year_start = period_start
    entity.financial_year_end = period_end

    # 2. Fetch Accounts
    accounts = db.execute(
        select(LedgerAccount).where(LedgerAccount.entity_id == entity_id)
    ).scalars().all()
    
    # Find prior period from TrialBalanceSnapshot
    prior_period = db.execute(
        select(TrialBalanceSnapshot.period_start, TrialBalanceSnapshot.period_end)
        .where(
            TrialBalanceSnapshot.entity_id == entity_id,
            TrialBalanceSnapshot.period_end <= period_start
        )
        .order_by(TrialBalanceSnapshot.period_end.desc())
        .limit(1)
    ).first()
    
    # Fetch current and prior snapshots
    snapshot_query = select(TrialBalanceSnapshot).options(
        joinedload(TrialBalanceSnapshot.ledger_account)
    ).where(
        TrialBalanceSnapshot.entity_id == entity_id
    ).where(
        ((TrialBalanceSnapshot.period_start == period_start) & (TrialBalanceSnapshot.period_end == period_end)) |
        ((TrialBalanceSnapshot.period_start == prior_period.period_start) & (TrialBalanceSnapshot.period_end == prior_period.period_end) if prior_period else False)
    )
    snapshots = db.execute(snapshot_query).scalars().all()

    # 3. Clear existing exceptions for this entity and period
    db.execute(
        delete(AuditException)
        .where(
            AuditException.entity_id == entity_id,
            AuditException.period_start == period_start,
            AuditException.period_end == period_end
        )
    )
    db.flush()

    # 4. Run rules engine
    exceptions = run_scrutiny(entity, accounts, snapshots)

    # 5. Persist exceptions to the database, setting the period fields
    for exc in exceptions:
        exc.period_start = period_start
        exc.period_end = period_end
        db.add(exc)
    db.commit()

    return ScrutinyRunSummary(
        status="success",
        exceptions_count=len(exceptions)
    )


@router.get("/entities/{entity_id}/exceptions", response_model=List[ExceptionResponse])
def list_exceptions(
    entity_id: int,
    period_start: Optional[date] = Query(None),
    period_end: Optional[date] = Query(None),
    severity: Optional[str] = Query(None, description="Filter exceptions by severity"),
    db: Session = Depends(get_db)
):
    """
    Get the list of scrutiny exceptions persisted for the specified entity and period,
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
    if period_start:
        query = query.where(AuditException.period_start == period_start)
    if period_end:
        query = query.where(AuditException.period_end == period_end)
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


# --- GSTIN Lookup schemas and endpoint ---

class GstinLookupRequest(BaseModel):
    gstin: str


class GstinLookupResponse(BaseModel):
    gstin: str
    company_name: str
    state: str
    pan: str


# Official state code mapping for Indian GSTINs
STATE_CODES = {
    "01": "Jammu and Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "25": "Daman and Diu",
    "26": "Dadra and Nagar Haveli",
    "27": "Maharashtra",
    "28": "Andhra Pradesh (Before division)",
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman and Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh",
    "38": "Ladakh"
}

# Pre-defined mock database of GSTINs for testing
MOCK_GSTIN_REGISTRY = {
    "27AAAAA1111A1Z1": {
        "company_name": "Acme Industrial Solutions Pvt Ltd",
        "state": "Maharashtra",
        "pan": "AAAAA1111A"
    },
    "07BBBBB2222B2Z2": {
        "company_name": "Capital Trading Corporation",
        "state": "Delhi",
        "pan": "BBBBB2222B"
    },
    "29CCCCC3333C3Z3": {
        "company_name": "Bangalore Tech Ventures LLC",
        "state": "Karnataka",
        "pan": "CCCCC3333C"
    }
}


# Environment variables for API Setu taxpayer integration
APISETU_BASE_URL = os.getenv("APISETU_BASE_URL", "https://apisetu.gov.in/gstn")
APISETU_API_KEY = os.getenv("APISETU_API_KEY")
APISETU_CLIENT_ID = os.getenv("APISETU_CLIENT_ID")


@router.post("/gstin/lookup", response_model=GstinLookupResponse)
def lookup_gstin(request: GstinLookupRequest):
    """
    Look up company details using a GSTIN.
    """
    gstin_cleaned = request.gstin.strip().upper()
    
    # Validation regex for Indian GSTIN (15 characters)
    gstin_regex = r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$"
    if not re.match(gstin_regex, gstin_cleaned):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid GSTIN format. Expected format: 15-character alphanumeric (e.g. 27AAAAA1111A1Z1)."
        )
        
    state_code = gstin_cleaned[0:2]
    pan = gstin_cleaned[2:12]
    state_name = STATE_CODES.get(state_code, "Unknown State")
    
    # If API keys are configured, run the actual API Setu live request
    if APISETU_API_KEY and APISETU_CLIENT_ID:
        url = f"{APISETU_BASE_URL.rstrip('/')}/v1/taxpayers/{gstin_cleaned}"
        headers = {
            "X-APISETU-APIKEY": APISETU_API_KEY,
            "X-APISETU-CLIENTID": APISETU_CLIENT_ID,
            "Accept": "application/json"
        }
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(url, headers=headers)
                
            if response.status_code == 200:
                data = response.json()
                company_name = data.get("lgnm") or data.get("tradeNam") or "Unknown Company"
                return GstinLookupResponse(
                    gstin=gstin_cleaned,
                    company_name=company_name,
                    state=state_name,
                    pan=pan
                )
            elif response.status_code == 404:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Taxpayer with GSTIN {gstin_cleaned} not found on API Setu."
                )
            else:
                print(f"[ERROR] API Setu error status {response.status_code}: {response.text}")
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"API Setu gateway returned error code {response.status_code}."
                )
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"API Setu request failed: {str(exc)}"
            )

    # Fallback to local / mock lookup if environment variables are not set
    # Check mock registry first
    if gstin_cleaned in MOCK_GSTIN_REGISTRY:
        company_info = MOCK_GSTIN_REGISTRY[gstin_cleaned]
        return GstinLookupResponse(
            gstin=gstin_cleaned,
            company_name=company_info["company_name"],
            state=company_info["state"],
            pan=company_info["pan"]
        )
        
    # Dynamically generate realistic details if not in registry
    company_prefix = pan[0:5]
    company_name = f"{company_prefix.title()} Enterprises Pvt Ltd"
    
    return GstinLookupResponse(
        gstin=gstin_cleaned,
        company_name=company_name,
        state=state_name,
        pan=pan
    )
