import re
import json
import hashlib
import os
from typing import Optional, List
from decimal import Decimal
from datetime import datetime, date, timezone
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, Query, Form, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import select, delete
from pydantic import BaseModel, ConfigDict

from app.db.session import get_db
from app.db.models import AuditException, Entity, FinancialPeriod, ImportBatch, LedgerAccount, ReviewAction, ScrutinyRun, TrialBalanceSnapshot, User
from app.ingestion.batches import create_import_batch, is_duplicate_import_batch
from app.ingestion.tally_parser import parse_tally_xml
from app.ingestion.tally_http import TallyConnectorError, fetch_trial_balance
from app.ingestion.tally_normalizer import normalize_tally_data
from app.ingestion.xlsx_normalizer import normalize_xlsx_confirm
from app.rules.engine import rule_set_version, run_scrutiny
from app.auth.security import get_current_user

# Router without prefix to match the exact URL layout
router = APIRouter(
    tags=["scrutiny"]
)


# Pydantic schemas
class EntityCreate(BaseModel):
    name: str
    materiality_threshold: Decimal
    gstin: Optional[str] = None
    sector: Optional[str] = None
    rule_pack: Optional[str] = None


class EntityResponse(BaseModel):
    id: int
    name: str
    materiality_threshold: Decimal
    gstin: Optional[str] = None
    sector: Optional[str] = None
    rule_pack: Optional[str] = None

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
    import_batch_id: Optional[int] = None


class TallyConnectorImportRequest(BaseModel):
    endpoint: str = "http://localhost:9000"
    company_name: str
    period_start: date
    period_end: date


class ScrutinyRunSummary(BaseModel):
    status: str
    exceptions_count: int
    scrutiny_run_id: Optional[int] = None


class GSTProfileLookupRequest(BaseModel):
    gstin: str


class GSTProfileResponse(BaseModel):
    gstin: str
    legal_name: str
    trade_name: str
    registration_status: str
    constitution: str
    nature_of_business: List[str]
    core_business_activity: str
    suggested_sector: str
    suggested_rule_pack: str
    source: str
    simulated: bool


def finding_fingerprint(exception: AuditException) -> str:
    """Stable identity across reruns while allowing amounts in messages to change."""
    canonical_message = re.sub(r"[-+]?\d[\d,]*(?:\.\d+)?", "#", exception.message.lower())
    material = f"{exception.rule_name}|{exception.ledger_account_id or 'entity'}|{canonical_message}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# --- Entity management endpoints ---

DEMO_GSTIN = "27DEMOX0000D1Z0"


@router.post("/gst-profile/lookup", response_model=GSTProfileResponse)
def lookup_gst_profile(
    request: GSTProfileLookupRequest,
    current_user: User = Depends(get_current_user),
):
    """Return the explicit fictional GST profile used by the pitch environment."""
    del current_user
    if os.getenv("GST_LOOKUP_MODE", "").lower() != "demo":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GST lookup provider is not configured. Set GST_LOOKUP_MODE=demo only for the fictional pitch profile.",
        )
    if request.gstin.strip().upper() != DEMO_GSTIN:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Demo GST lookup only supports the fictional identifier {DEMO_GSTIN}.",
        )
    return GSTProfileResponse(
        gstin=DEMO_GSTIN,
        legal_name="Meridian Components Private Limited — Fictional Demo",
        trade_name="Meridian Components",
        registration_status="ACTIVE (SIMULATED)",
        constitution="Private Limited Company",
        nature_of_business=["Factory / Manufacturing", "Wholesale Business"],
        core_business_activity="Manufacturer",
        suggested_sector="manufacturing",
        suggested_rule_pack="manufacturing_v1",
        source="FICTIONAL_DEMO_REGISTRY",
        simulated=True,
    )

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
    if entity_in.rule_pack not in (None, "manufacturing_v1"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported scrutiny rule pack.")
    if entity_in.rule_pack == "manufacturing_v1" and entity_in.sector != "manufacturing":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Manufacturing v1 requires the manufacturing sector.")
    entity = Entity(
        organization_id=current_user.organization_id,
        name=entity_in.name,
        materiality_threshold=entity_in.materiality_threshold,
        gstin=entity_in.gstin.strip().upper() if entity_in.gstin else None,
        sector=entity_in.sector,
        rule_pack=entity_in.rule_pack,
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

@router.post("/entities/{entity_id}/import-from-tally", response_model=IngestionResponse)
def import_from_tally_connector(
    entity_id: int,
    request: TallyConnectorImportRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Read a trial balance from a running TallyPrime HTTP server (default port 9000)."""
    entity = db.execute(select(Entity).where(
        Entity.id == entity_id,
        Entity.organization_id == current_user.organization_id,
    )).scalar_one_or_none()
    if not entity:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Entity with ID {entity_id} not found.")

    try:
        parsed_data, response_xml = fetch_trial_balance(
            endpoint=request.endpoint,
            company_name=request.company_name,
            period_start=request.period_start,
            period_end=request.period_end,
        )
        batch = create_import_batch(
            db,
            entity_id=entity.id,
            period_start=request.period_start,
            period_end=request.period_end,
            source="tally_http",
            original_filename=f"tally-http-{request.period_end.isoformat()}.xml",
            contents=response_xml,
            uploaded_by_user_id=current_user.id,
            validation_report={"connector": "tally_http", "endpoint": request.endpoint, "ledger_count": len(parsed_data["ledgers"])},
        )
        if not is_duplicate_import_batch(batch):
            normalize_tally_data(
                parsed_data,
                db,
                entity_id=entity.id,
                materiality_threshold=entity.materiality_threshold,
                clear_only_period=True,
                target_period_start=request.period_start,
                target_period_end=request.period_end,
                import_batch_id=batch.id,
            )
        db.commit()
        return IngestionResponse(
            message="TallyPrime import successful",
            entity_id=entity.id,
            entity_name=entity.name,
            import_batch_id=batch.id,
        )
    except TallyConnectorError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to import from TallyPrime: {exc}")

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
        period_start = target_period_start if clear_only_period and target_period_start else parsed_data["entity"]["financial_year_start"]
        period_end = target_period_end if clear_only_period and target_period_end else parsed_data["entity"]["financial_year_end"]
        batch = create_import_batch(
            db,
            entity_id=entity.id,
            period_start=period_start,
            period_end=period_end,
            source="tally_xml",
            original_filename=file.filename,
            contents=contents,
            uploaded_by_user_id=current_user.id,
            validation_report={"parser": "tally_xml", "voucher_count": len(parsed_data["vouchers"])},
        )
        if not is_duplicate_import_batch(batch):
            normalize_tally_data(
                parsed_data,
                db,
                materiality_threshold=entity.materiality_threshold,
                entity_id=entity.id,
                organization_id=current_user.organization_id,
                clear_only_period=clear_only_period,
                target_period_start=target_period_start,
                target_period_end=target_period_end,
                import_batch_id=batch.id,
            )
        db.commit()
        return IngestionResponse(
            message="Ingestion successful",
            entity_id=entity.id,
            entity_name=entity.name,
            import_batch_id=batch.id,
        )
    except ValueError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to normalize and save ledger data: {str(e)}"
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
        batch = create_import_batch(
            db,
            entity_id=entity.id,
            period_start=target_period_start,
            period_end=target_period_end,
            source="xlsx_trial_balance",
            original_filename=file.filename,
            contents=contents,
            uploaded_by_user_id=current_user.id,
            validation_report={"parser": "xlsx_trial_balance", "sign_convention": sign_convention},
        )
        if not is_duplicate_import_batch(batch):
            normalize_xlsx_confirm(
                file_bytes=contents,
                column_mapping=mapping_dict,
                sign_convention=sign_convention,
                target_period_start=str(target_period_start),
                target_period_end=str(target_period_end),
                entity_id=entity.id,
                session=db,
                clear_only_period=clear_only_period,
                import_batch_id=batch.id,
            )
        db.commit()
        return IngestionResponse(
            message="XLSX ingestion successful",
            entity_id=entity.id,
            entity_name=entity.name,
            import_batch_id=batch.id,
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

    accounts = db.execute(
        select(LedgerAccount).where(LedgerAccount.entity_id == entity_id)
    ).scalars().all()
    
    financial_period = db.execute(select(FinancialPeriod).where(
        FinancialPeriod.entity_id == entity_id,
        FinancialPeriod.period_start == period_start,
        FinancialPeriod.period_end == period_end,
    ).order_by(FinancialPeriod.id.desc())).scalars().first()
    if not financial_period:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="No uploaded import exists for this period.")

    active_batch = db.execute(select(ImportBatch).where(
        ImportBatch.financial_period_id == financial_period.id,
        ImportBatch.status == "ACTIVE",
    ).order_by(ImportBatch.id.desc())).scalar_one_or_none()
    if not active_batch:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This period has no active import batch.")

    prior_period = db.execute(
        select(TrialBalanceSnapshot.period_start, TrialBalanceSnapshot.period_end)
        .where(
            TrialBalanceSnapshot.entity_id == entity_id,
            TrialBalanceSnapshot.period_end <= period_start
        )
        .order_by(TrialBalanceSnapshot.period_end.desc())
        .limit(1)
    ).first()
    
    prior_batch = None
    if prior_period:
        previous = db.execute(select(FinancialPeriod).where(
            FinancialPeriod.entity_id == entity_id,
            FinancialPeriod.period_start == prior_period.period_start,
            FinancialPeriod.period_end == prior_period.period_end,
        ).order_by(FinancialPeriod.id.desc())).scalars().first()
        if previous:
            prior_batch = db.execute(select(ImportBatch).where(
                ImportBatch.financial_period_id == previous.id,
                ImportBatch.status == "ACTIVE",
            ).order_by(ImportBatch.id.desc())).scalar_one_or_none()

    snapshot_query = select(TrialBalanceSnapshot).options(
        joinedload(TrialBalanceSnapshot.ledger_account)
    ).where(TrialBalanceSnapshot.import_batch_id.in_([active_batch.id] + ([prior_batch.id] if prior_batch else [])))
    snapshots = db.execute(snapshot_query).scalars().all()

    previous_run = db.execute(select(ScrutinyRun).where(
        ScrutinyRun.financial_period_id == financial_period.id,
        ScrutinyRun.status == "COMPLETED",
    ).order_by(ScrutinyRun.id.desc())).scalars().first()
    existing_exceptions = []
    if previous_run:
        existing_exceptions = db.execute(select(AuditException).where(
            AuditException.scrutiny_run_id == previous_run.id
        )).scalars().all()
    
    preserve_map = {}
    for old_exc in existing_exceptions:
        key = old_exc.fingerprint or finding_fingerprint(old_exc)
        if old_exc.status != "PENDING" or old_exc.auditor_notes:
            preserve_map[key] = (old_exc.status, old_exc.auditor_notes)

    scrutiny_run = ScrutinyRun(
        entity_id=entity_id,
        financial_period_id=financial_period.id,
        import_batch_id=active_batch.id,
        triggered_by_user_id=current_user.id,
        rule_set_version=rule_set_version(entity.rule_pack),
        status="RUNNING",
    )
    db.add(scrutiny_run)
    db.flush()

    exceptions = run_scrutiny(entity, accounts, snapshots, period_start, period_end, entity.rule_pack)

    for exc in exceptions:
        exc.period_start = period_start
        exc.period_end = period_end
        exc.scrutiny_run_id = scrutiny_run.id
        exc.rule_version = scrutiny_run.rule_set_version
        exc.fingerprint = finding_fingerprint(exc)
        
        key = exc.fingerprint
        if key in preserve_map:
            old_status, old_notes = preserve_map[key]
            exc.status = old_status
            exc.auditor_notes = old_notes
        else:
            exc.status = "PENDING"
            exc.auditor_notes = None

        db.add(exc)
    scrutiny_run.status = "COMPLETED"
    scrutiny_run.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    scrutiny_run.summary = {
        "exceptions_count": len(exceptions),
        "rule_set_version": scrutiny_run.rule_set_version,
        "rule_pack": entity.rule_pack,
    }
    db.commit()

    return ScrutinyRunSummary(
        status="success",
        exceptions_count=len(exceptions),
        scrutiny_run_id=scrutiny_run.id,
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
    if period_start and period_end:
        latest_run = db.execute(select(ScrutinyRun).join(FinancialPeriod).where(
            FinancialPeriod.entity_id == entity_id,
            FinancialPeriod.period_start == period_start,
            FinancialPeriod.period_end == period_end,
            ScrutinyRun.status == "COMPLETED",
        ).order_by(ScrutinyRun.id.desc())).scalars().first()
        if latest_run:
            query = query.where(AuditException.scrutiny_run_id == latest_run.id)
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
    db.add(ReviewAction(
        exception_id=exc.id,
        user_id=current_user.id,
        status=exception_update.status,
        auditor_notes=exception_update.auditor_notes,
    ))
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
