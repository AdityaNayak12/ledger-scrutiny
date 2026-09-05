from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import (
    BalanceCheckpoint,
    Entity,
    ImportBatch,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    Organization,
)
from app.ingestion.batches import stage_import_batch
from app.ingestion.datasets import resolve_active_dataset


FY_START = date(2025, 4, 1)
FY_END = date(2026, 3, 31)
BASELINE_DATE = date(2025, 3, 31)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        organization = Organization(name="Dataset Test Org")
        session.add(organization)
        session.flush()
        entity = Entity(
            organization_id=organization.id,
            name="Dataset Test Entity",
            materiality_threshold=Decimal("0.00"),
        )
        session.add(entity)
        session.flush()
        session.add_all([
            LedgerAccount(entity_id=entity.id, external_code="1000", name="Cash"),
            LedgerAccount(entity_id=entity.id, external_code="2000", name="Capital"),
        ])
        session.commit()
        yield session, entity.id
    Base.metadata.drop_all(engine)


def _account_ids(session, entity_id):
    return {
        account.external_code: account.id
        for account in session.query(LedgerAccount).filter_by(entity_id=entity_id)
    }


def _active_batch(session, entity_id, start, end, contents, *, kind="journal"):
    batch = stage_import_batch(
        session,
        entity_id=entity_id,
        period_start=start,
        period_end=end,
        source="gl_upload",
        source_family="gl_upload",
        original_filename=f"{contents.decode()}.xlsx",
        contents=contents,
        uploaded_by_user_id=None,
        kind=kind,
        coverage_start=start,
        coverage_end=end,
    )
    batch.status = "ACTIVE"
    batch.validation_report = {
        "readiness": "READY",
        "baseline_coverage": {"present": True, "complete": True},
    }
    return batch


def _entry(session, batch, posting_date, document_id, account_ids, amount):
    entry = JournalEntry(
        import_batch_id=batch.id,
        entity_id=batch.entity_id,
        source_document_id=document_id,
        posting_date=posting_date,
    )
    entry.lines = [
        JournalLine(
            ledger_account_id=account_ids["1000"],
            source_row_number=1,
            amount=amount,
            side="debit",
        ),
        JournalLine(
            ledger_account_id=account_ids["2000"],
            source_row_number=2,
            amount=-amount,
            side="credit",
        ),
    ]
    session.add(entry)


def _baseline(session, entity_id, account_ids, cash=Decimal("100.00")):
    batch = _active_batch(
        session,
        entity_id,
        FY_START,
        FY_END,
        b"baseline",
        kind="balance_checkpoint",
    )
    session.add_all([
        BalanceCheckpoint(
            import_batch_id=batch.id,
            entity_id=entity_id,
            ledger_account_id=account_ids["1000"],
            balance_date=BASELINE_DATE,
            balance=cash,
        ),
        BalanceCheckpoint(
            import_batch_id=batch.id,
            entity_id=entity_id,
            ledger_account_id=account_ids["2000"],
            balance_date=BASELINE_DATE,
            balance=-cash,
        ),
    ])
    return batch


def test_q1_q2_accumulate_signed_movement_and_report_gaps(session):
    db, entity_id = session
    account_ids = _account_ids(db, entity_id)
    baseline = _baseline(db, entity_id, account_ids)
    q1 = _active_batch(db, entity_id, FY_START, date(2025, 6, 30), b"q1")
    q2 = _active_batch(db, entity_id, date(2025, 7, 1), date(2025, 9, 30), b"q2")
    _entry(db, q1, date(2025, 4, 10), "Q1-1", account_ids, Decimal("25.00"))
    _entry(db, q2, date(2025, 7, 10), "Q2-1", account_ids, Decimal("50.00"))
    db.commit()

    result = resolve_active_dataset(db, entity_id, financial_year=2025)

    assert result["active_batch_ids"] == [q1.id, q2.id]
    assert result["baseline_batch_ids"] == [baseline.id]
    assert result["account_movements"] == {"1000": "75.00", "2000": "-75.00"}
    assert result["opening_balances"] == {"1000": "100.00", "2000": "-100.00"}
    assert result["closing_balances"] == {"1000": "175.00", "2000": "-175.00"}
    assert result["gaps"] == [{"start": "2025-10-01", "end": "2026-03-31"}]
    assert result["readiness"] == "PARTIAL"
    assert len(result["dataset_fingerprint"]) == 64
    assert result["dataset_fingerprint"] == resolve_active_dataset(
        db, entity_id, financial_year=2025
    )["dataset_fingerprint"]


def test_corrected_quarter_replaces_same_coverage_in_resolution(session):
    db, entity_id = session
    account_ids = _account_ids(db, entity_id)
    _baseline(db, entity_id, account_ids, cash=Decimal("0.00"))
    original = _active_batch(db, entity_id, FY_START, date(2025, 6, 30), b"q1-original")
    correction = _active_batch(db, entity_id, FY_START, date(2025, 6, 30), b"q1-corrected")
    q2 = _active_batch(db, entity_id, date(2025, 7, 1), date(2025, 9, 30), b"q2")
    _entry(db, original, date(2025, 4, 10), "ORIGINAL", account_ids, Decimal("10.00"))
    _entry(db, correction, date(2025, 4, 10), "CORRECTED", account_ids, Decimal("40.00"))
    _entry(db, q2, date(2025, 7, 10), "Q2-1", account_ids, Decimal("20.00"))
    db.commit()

    result = resolve_active_dataset(db, entity_id, financial_year=2025)

    assert result["active_batch_ids"] == [correction.id, q2.id]
    assert original.id in result["replaced_batch_ids"]
    assert result["account_movements"]["1000"] == "60.00"


def test_annual_coverage_excludes_quarterly_batches_for_same_family(session):
    db, entity_id = session
    account_ids = _account_ids(db, entity_id)
    baseline = _baseline(db, entity_id, account_ids, cash=Decimal("0.00"))
    q1 = _active_batch(db, entity_id, FY_START, date(2025, 6, 30), b"annual-q1")
    q2 = _active_batch(db, entity_id, date(2025, 7, 1), date(2025, 9, 30), b"annual-q2")
    annual = _active_batch(db, entity_id, FY_START, FY_END, b"annual")
    _entry(db, q1, date(2025, 4, 10), "Q1-1", account_ids, Decimal("10.00"))
    _entry(db, q2, date(2025, 7, 10), "Q2-1", account_ids, Decimal("20.00"))
    _entry(db, annual, date(2025, 4, 10), "ANNUAL-1", account_ids, Decimal("100.00"))
    db.commit()

    result = resolve_active_dataset(db, entity_id, financial_year=2025)

    assert result["active_batch_ids"] == [annual.id]
    assert result["excluded_batch_ids"] == [q1.id, q2.id]
    assert result["baseline_batch_ids"] == [baseline.id]
    assert result["gaps"] == []
    assert result["readiness"] == "READY"
    assert result["account_movements"]["1000"] == "100.00"


def test_baseline_closing_seeds_account_opening_continuity(session):
    db, entity_id = session
    account_ids = _account_ids(db, entity_id)
    _baseline(db, entity_id, account_ids, cash=Decimal("125.00"))
    annual = _active_batch(db, entity_id, FY_START, FY_END, b"continuity")
    _entry(db, annual, date(2025, 4, 10), "CONTINUITY-1", account_ids, Decimal("25.00"))
    db.commit()

    result = resolve_active_dataset(db, entity_id, financial_year=2025)

    assert result["baseline_coverage"] == {
        "present": True,
        "complete": True,
        "balance_date": "2025-03-31",
        "account_count": 2,
    }
    assert result["opening_balances"]["1000"] == "125.00"
    assert result["closing_balances"]["1000"] == "150.00"
    assert result["closing_balances"]["2000"] == "-150.00"
    assert result["readiness"] == "READY"
