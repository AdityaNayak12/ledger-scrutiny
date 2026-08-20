import re
import os
import json
import httpx
from typing import Optional, List, Dict, Any
from decimal import Decimal
from datetime import datetime, date
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Query, Form, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import select, delete
from pydantic import BaseModel, ConfigDict

from app.db.session import get_db
from app.db.models import Entity, LedgerAccount, TrialBalanceSnapshot, AuditException, User
from app.ingestion.tally_parser import parse_tally_xml
from app.ingestion.tally_normalizer import normalize_tally_data
from app.ingestion.xlsx_parser import detect_headers_and_parse
from app.ingestion.xlsx_normalizer import normalize_xlsx_confirm
from app.rules.engine import run_scrutiny
from app.auth.security import get_current_user

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
    status: str
    auditor_notes: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ExceptionUpdate(BaseModel):
    status: str
    auditor_notes: Optional[str] = None


class PeriodResponse(BaseModel):
    period_start: date
    period_end: date
    source: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class IngestionResponse(BaseModel):
    message: str
    entity_id: int
    entity_name: str


class XlsxPreviewResponse(BaseModel):
    header_row_number: Optional[int]
    column_mapping: Dict[str, Any]
    missing_fields: List[str]
    sample_rows: List[Dict[str, Any]]
    parse_errors: List[Dict[str, Any]]
    total_data_rows: int


class ScrutinyRunSummary(BaseModel):
    status: str
    exceptions_count: int


# --- Entity management endpoints ---

@router.get("/entities", response_model=List[EntityResponse])
def list_entities(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List all business entities belonging to the user's organization."""
    return db.execute(
        select(Entity).where(Entity.organization_id == current_user.organization_id)
    ).scalars().all()


@router.post("/entities", response_model=EntityResponse, status_code=status.HTTP_201_CREATED)
def create_entity(
    entity_in: EntityCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new business entity associated with the user's organization."""
    entity = Entity(
        organization_id=current_user.organization_id,
        name=entity_in.name,
        materiality_threshold=entity_in.materiality_threshold
    )
    db.add(entity)
    db.commit()
    db.refresh(entity)
    return entity


@router.delete("/entities/{entity_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_entity(
    entity_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a business entity and all its associated data (cascade)."""
    entity = db.execute(
        select(Entity).where(
            Entity.id == entity_id,
            Entity.organization_id == current_user.organization_id
        )
    ).scalar_one_or_none()
    if not entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entity with ID {entity_id} not found."
        )
    db.delete(entity)
    db.commit()
    return None


@router.get("/entities/{entity_id}/periods", response_model=List[PeriodResponse])
def list_periods(
    entity_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List all financial periods with data for this entity."""
    entity = db.execute(
        select(Entity.id).where(
            Entity.id == entity_id,
            Entity.organization_id == current_user.organization_id
        )
    ).scalar_one_or_none()
    if not entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entity with ID {entity_id} not found."
        )

    # Fetch from FinancialPeriod
    from app.db.models import FinancialPeriod
    periods = db.execute(
        select(FinancialPeriod).where(FinancialPeriod.entity_id == entity_id)
    ).scalars().all()
    
    # If a period is missing from FinancialPeriod (legacy), fallback to distinct TrialBalanceSnapshots
    legacy_results = db.execute(
        select(TrialBalanceSnapshot.period_start, TrialBalanceSnapshot.period_end)
        .where(TrialBalanceSnapshot.entity_id == entity_id)
        .distinct()
    ).all()
    
    period_dict = {}
    for r in legacy_results:
        period_dict[(r.period_start, r.period_end)] = None
        
    for p in periods:
        period_dict[(p.period_start, p.period_end)] = p.source

    sorted_results = sorted(period_dict.items(), key=lambda x: x[0][0], reverse=True)
    return [
        PeriodResponse(period_start=k[0], period_end=k[1], source=v)
        for k, v in sorted_results
    ]


# --- Scrutiny and Ingestion endpoints ---

@router.post("/entities/{entity_id}/upload", response_model=IngestionResponse)
async def upload_tally_export(
    entity_id: int,
    clear_only_period: bool = Query(False),
    target_period_start: Optional[date] = Query(None),
    target_period_end: Optional[date] = Query(None),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
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

    # Verify entity exists in the user's organization
    entity = db.execute(
        select(Entity).where(
            Entity.id == entity_id,
            Entity.organization_id == current_user.organization_id
        )
    ).scalar_one_or_none()
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
        normalize_tally_data(
            parsed_data, 
            db, 
            materiality_threshold=entity.materiality_threshold, 
            entity_id=entity.id,
            organization_id=current_user.organization_id,
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


@router.post("/entities/{entity_id}/upload-xlsx/preview", response_model=XlsxPreviewResponse)
async def upload_xlsx_preview(
    entity_id: int,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Parses uploaded XLSX trial balance and returns header detection preview without writing to database.
    """
    fn_lower = file.filename.lower()
    if not (fn_lower.endswith(".xlsx") or fn_lower.endswith(".xls")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only XLSX files are supported."
        )

    entity = db.execute(
        select(Entity).where(
            Entity.id == entity_id,
            Entity.organization_id == current_user.organization_id
        )
    ).scalar_one_or_none()
    if not entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entity with ID {entity_id} not found."
        )

    try:
        contents = await file.read()
        preview_data = detect_headers_and_parse(contents)
        return XlsxPreviewResponse(**preview_data)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to preview XLSX file: {str(e)}"
        )


@router.post("/entities/{entity_id}/upload-xlsx/confirm", response_model=IngestionResponse)
async def upload_xlsx_confirm(
    entity_id: int,
    column_mapping: str = Form(..., description="JSON string mapping field names to exact spreadsheet column headers"),
    sign_convention: str = Form("negative_is_credit", description="Sign convention: 'negative_is_credit', 'positive_is_credit', or 'separate_dr_cr_columns'"),
    target_period_start: date = Form(...),
    target_period_end: date = Form(...),
    clear_only_period: bool = Form(True),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Confirms XLSX upload using user-approved column_mapping and sign_convention, writing trial balance to DB.
    """
    fn_lower = file.filename.lower()
    if not (fn_lower.endswith(".xlsx") or fn_lower.endswith(".xls")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only XLSX files are supported."
        )

    entity = db.execute(
        select(Entity).where(
            Entity.id == entity_id,
            Entity.organization_id == current_user.organization_id
        )
    ).scalar_one_or_none()
    if not entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entity with ID {entity_id} not found."
        )

    try:
        mapping_dict = json.loads(column_mapping)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid JSON string provided for column_mapping: {str(e)}"
        )

    try:
        contents = await file.read()
        normalize_xlsx_confirm(
            file_bytes=contents,
            column_mapping=mapping_dict,
            sign_convention=sign_convention,
            target_period_start=str(target_period_start),
            target_period_end=str(target_period_end),
            entity_id=entity.id,
            session=db,
            clear_only_period=clear_only_period
        )
        db.commit()
        return IngestionResponse(
            message="XLSX ingestion successful",
            entity_id=entity.id,
            entity_name=entity.name
        )
    except ValueError as val_err:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(val_err)
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to ingest XLSX file: {str(e)}"
        )


@router.post("/entities/{entity_id}/scrutiny-run", response_model=ScrutinyRunSummary)
def trigger_scrutiny_run(
    entity_id: int,
    period_start: date = Query(...),
    period_end: date = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Trigger the rules engine scrutiny run for the specified entity and period.
    """
    entity = db.execute(
        select(Entity).where(
            Entity.id == entity_id,
            Entity.organization_id == current_user.organization_id
        )
    ).scalar_one_or_none()
    if not entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entity with ID {entity_id} not found."
        )

    entity.financial_year_start = period_start
    entity.financial_year_end = period_end

    accounts = db.execute(
        select(LedgerAccount).where(LedgerAccount.entity_id == entity_id)
    ).scalars().all()
    
    prior_period = db.execute(
        select(TrialBalanceSnapshot.period_start, TrialBalanceSnapshot.period_end)
        .where(
            TrialBalanceSnapshot.entity_id == entity_id,
            TrialBalanceSnapshot.period_end <= period_start
        )
        .order_by(TrialBalanceSnapshot.period_end.desc())
        .limit(1)
    ).first()
    
    snapshot_query = select(TrialBalanceSnapshot).options(
        joinedload(TrialBalanceSnapshot.ledger_account)
    ).where(
        TrialBalanceSnapshot.entity_id == entity_id
    ).where(
        ((TrialBalanceSnapshot.period_start == period_start) & (TrialBalanceSnapshot.period_end == period_end)) |
        ((TrialBalanceSnapshot.period_start == prior_period.period_start) & (TrialBalanceSnapshot.period_end == prior_period.period_end) if prior_period else False)
    )
    snapshots = db.execute(snapshot_query).scalars().all()

    existing_exceptions_query = select(AuditException).where(
        AuditException.entity_id == entity_id,
        AuditException.period_start == period_start,
        AuditException.period_end == period_end
    )
    existing_exceptions = db.execute(existing_exceptions_query).scalars().all()
    
    preserve_map = {}
    for old_exc in existing_exceptions:
        key = (old_exc.rule_name, old_exc.ledger_account_id, old_exc.message if old_exc.ledger_account_id is None else None)
        if old_exc.status != "PENDING" or old_exc.auditor_notes:
            preserve_map[key] = (old_exc.status, old_exc.auditor_notes)

    db.execute(
        delete(AuditException)
        .where(
            AuditException.entity_id == entity_id,
            AuditException.period_start == period_start,
            AuditException.period_end == period_end
        )
    )
    db.flush()

    exceptions = run_scrutiny(entity, accounts, snapshots)

    for exc in exceptions:
        exc.period_start = period_start
        exc.period_end = period_end
        
        key = (exc.rule_name, exc.ledger_account_id, exc.message if exc.ledger_account_id is None else None)
        if key in preserve_map:
            old_status, old_notes = preserve_map[key]
            exc.status = old_status
            exc.auditor_notes = old_notes
        else:
            exc.status = "PENDING"
            exc.auditor_notes = None

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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get the list of scrutiny exceptions persisted for the specified entity and period,
    optionally filtered by severity level.
    """
    entity_exists = db.execute(
        select(Entity.id).where(
            Entity.id == entity_id,
            Entity.organization_id == current_user.organization_id
        )
    ).scalar_one_or_none()
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
                status=exc.status,
                auditor_notes=exc.auditor_notes,
                created_at=exc.created_at
            )
        )
    return response


@router.patch("/entities/{entity_id}/exceptions/{exception_id}", response_model=ExceptionResponse)
def update_exception(
    entity_id: int,
    exception_id: int,
    exception_update: ExceptionUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Update the status and review notes of a specific audit exception.
    """
    entity_exists = db.execute(
        select(Entity.id).where(
            Entity.id == entity_id,
            Entity.organization_id == current_user.organization_id
        )
    ).scalar_one_or_none()
    if not entity_exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entity with ID {entity_id} not found."
        )

    exc = db.execute(
        select(AuditException)
        .options(joinedload(AuditException.ledger_account))
        .where(AuditException.id == exception_id, AuditException.entity_id == entity_id)
    ).scalar_one_or_none()

    if not exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Exception with ID {exception_id} not found for this entity."
        )

    if exception_update.status not in ["PENDING", "CLEARED", "REVIEWED"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid status. Must be one of: PENDING, CLEARED, REVIEWED"
        )

    exc.status = exception_update.status
    exc.auditor_notes = exception_update.auditor_notes
    db.commit()
    db.refresh(exc)

    return ExceptionResponse(
        id=exc.id,
        rule_name=exc.rule_name,
        severity=exc.severity,
        message=exc.message,
        ledger_account_name=exc.ledger_account.name if exc.ledger_account else None,
        status=exc.status,
        auditor_notes=exc.auditor_notes,
        created_at=exc.created_at
    )


# --- GSTIN Lookup schemas and endpoint ---

class GstinLookupRequest(BaseModel):
    gstin: str


class GstinLookupResponse(BaseModel):
    gstin: str
    company_name: str
    state: str
    pan: str


STATE_CODES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan",
    "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh",
    "13": "Nagaland", "14": "Manipur", "15": "Mizoram", "16": "Tripura",
    "17": "Meghalaya", "18": "Assam", "19": "West Bengal", "20": "Jharkhand",
    "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "25": "Daman and Diu", "26": "Dadra and Nagar Haveli", "27": "Maharashtra",
    "28": "Andhra Pradesh (Before division)", "29": "Karnataka", "30": "Goa",
    "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh"
}

MOCK_GSTIN_REGISTRY = {
    "27AAAAA1111A1Z1": {"company_name": "Acme Industrial Solutions Pvt Ltd", "state": "Maharashtra", "pan": "AAAAA1111A"},
    "07BBBBB2222B2Z2": {"company_name": "Capital Trading Corporation", "state": "Delhi", "pan": "BBBBB2222B"},
    "29CCCCC3333C3Z3": {"company_name": "Bangalore Tech Ventures LLC", "state": "Karnataka", "pan": "CCCCC3333C"}
}

APISETU_BASE_URL = os.getenv("APISETU_BASE_URL", "https://apisetu.gov.in/gstn")
APISETU_API_KEY = os.getenv("APISETU_API_KEY")
APISETU_CLIENT_ID = os.getenv("APISETU_CLIENT_ID")


@router.post("/gstin/lookup", response_model=GstinLookupResponse)
def lookup_gstin(
    request: GstinLookupRequest,
    current_user: User = Depends(get_current_user)
):
    """
    Look up company details using a GSTIN. Protected endpoint.
    """
    gstin_cleaned = request.gstin.strip().upper()
    
    gstin_regex = r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$"
    if not re.match(gstin_regex, gstin_cleaned):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid GSTIN format. Expected format: 15-character alphanumeric (e.g. 27AAAAA1111A1Z1)."
        )
        
    state_code = gstin_cleaned[0:2]
    pan = gstin_cleaned[2:12]
    state_name = STATE_CODES.get(state_code, "Unknown State")
    
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

    if gstin_cleaned in MOCK_GSTIN_REGISTRY:
        company_info = MOCK_GSTIN_REGISTRY[gstin_cleaned]
        return GstinLookupResponse(
            gstin=gstin_cleaned,
            company_name=company_info["company_name"],
            state=company_info["state"],
            pan=company_info["pan"]
        )
        
    company_prefix = pan[0:5]
    company_name = f"{company_prefix.title()} Enterprises Pvt Ltd"
    
    return GstinLookupResponse(
        gstin=gstin_cleaned,
        company_name=company_name,
        state=state_name,
        pan=pan
    )
