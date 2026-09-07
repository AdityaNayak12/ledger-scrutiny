import io
from datetime import date
from decimal import Decimal

import openpyxl
import pytest
from sqlalchemy import create_engine, select
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
from app.ingestion.batches import activate_import_batch, stage_import_batch
from app.ingestion.datasets import resolve_active_dataset
from app.ingestion.reconciliation import build_reconciliation_report
from app.ingestion.xlsx_normalizer import normalize_gl_xlsx


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


def _active_batch(
    session,
    entity_id,
    start,
    end,
    contents,
    *,
    kind="journal",
    lifecycle=False,
    source="gl_upload",
    source_family="gl_upload",
):
    batch = stage_import_batch(
        session,
        entity_id=entity_id,
        period_start=start,
        period_end=end,
        source=source,
        source_family=source_family,
        original_filename=f"{contents.decode()}.xlsx",
        contents=contents,
        uploaded_by_user_id=None,
        kind=kind,
        coverage_start=start,
        coverage_end=end,
    )
    batch.validation_report = {
        "readiness": "READY",
        "baseline_coverage": {"present": True, "complete": True},
    }
    if lifecycle:
        activate_import_batch(session, batch)
    else:
        batch.status = "ACTIVE"
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


def _gl_xlsx_bytes():
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.append([
        "Document Number",
        "G/L Account",
        "Posting Date",
        "Amount in local currency",
    ])
    worksheet.append(["NORMALIZER-1", "1000", date(2025, 4, 10), "25.00"])
    worksheet.append(["NORMALIZER-1", "2000", date(2025, 4, 10), "-25.00"])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_q1_q2_accumulate_signed_movement_and_report_gaps(session):
    db, entity_id = session
    account_ids = _account_ids(db, entity_id)
    baseline = _baseline(db, entity_id, account_ids)
    q1 = _active_batch(db, entity_id, FY_START, date(2025, 6, 30), b"q1")
    q2 = _active_batch(db, entity_id, date(2025, 7, 1), date(2025, 9, 30), b"q2")
    q1.validation_report = build_reconciliation_report((), coverage_complete=True)
    q1.validation_report.update({
        "coverage_start": FY_START.isoformat(),
        "coverage_end": date(2025, 6, 30).isoformat(),
    })
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
    baseline = _baseline(db, entity_id, account_ids, cash=Decimal("0.00"))
    original = _active_batch(db, entity_id, FY_START, date(2025, 6, 30), b"q1-original")
    correction = _active_batch(db, entity_id, FY_START, date(2025, 6, 30), b"q1-corrected")
    q2 = _active_batch(db, entity_id, date(2025, 7, 1), date(2025, 9, 30), b"q2")
    _entry(db, original, date(2025, 4, 10), "ORIGINAL", account_ids, Decimal("10.00"))
    _entry(db, correction, date(2025, 4, 10), "CORRECTED", account_ids, Decimal("40.00"))
    _entry(db, q2, date(2025, 7, 10), "Q2-1", account_ids, Decimal("20.00"))
    db.commit()

    result = resolve_active_dataset(db, entity_id, financial_year=2025)

    assert result["active_batch_ids"] == [correction.id, q2.id]
    assert result["source_batch_ids"] == sorted([baseline.id, correction.id, q2.id])
    assert original.id in result["replaced_batch_ids"]
    assert result["account_movements"]["1000"] == "60.00"
    assert db.scalar(
        select(JournalEntry.source_document_id).where(JournalEntry.import_batch_id == original.id)
    ) == "ORIGINAL"
    assert db.scalar(
        select(JournalEntry.source_document_id).where(JournalEntry.import_batch_id == q2.id)
    ) == "Q2-1"


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


def test_dataset_resolution_isolated_by_entity_year_and_source_family(session):
    db, entity_id = session
    account_ids = _account_ids(db, entity_id)
    organization_id = db.scalar(
        select(Entity.organization_id).where(Entity.id == entity_id)
    )
    other_entity = Entity(
        organization_id=organization_id,
        name="Other Isolated Entity",
        materiality_threshold=Decimal("0.00"),
    )
    db.add(other_entity)
    db.flush()

    gl_q1 = _active_batch(
        db,
        entity_id,
        FY_START,
        date(2025, 6, 30),
        b"isolated-gl-q1",
    )
    tally_q1 = _active_batch(
        db,
        entity_id,
        date(2026, 4, 1),
        date(2026, 6, 30),
        b"isolated-tally-q1",
        source="tally_xml",
        source_family="tally",
    )
    other_batch = _active_batch(
        db,
        other_entity.id,
        FY_START,
        date(2025, 6, 30),
        b"isolated-other-entity",
    )
    _entry(db, gl_q1, date(2025, 4, 10), "ISOLATED-GL", account_ids, Decimal("10.00"))
    _entry(db, tally_q1, date(2026, 4, 10), "ISOLATED-TALLY", account_ids, Decimal("20.00"))
    db.commit()

    fy_2025 = resolve_active_dataset(db, entity_id, financial_year=2025)
    fy_2026 = resolve_active_dataset(db, entity_id, financial_year=2026)

    assert fy_2025["source_family"] == "gl_upload"
    assert fy_2025["active_batch_ids"] == [gl_q1.id]
    assert fy_2025["source_batch_ids"] == [gl_q1.id]
    assert fy_2025["account_movements"] == {"1000": "10.00", "2000": "-10.00"}
    assert fy_2026["source_family"] == "tally"
    assert fy_2026["active_batch_ids"] == [tally_q1.id]
    assert fy_2026["source_batch_ids"] == [tally_q1.id]
    assert fy_2026["account_movements"] == {"1000": "20.00", "2000": "-20.00"}
    assert other_batch.id not in fy_2025["active_batch_ids"]
    assert other_batch.id not in fy_2026["active_batch_ids"]


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


def test_normalizer_partial_journal_becomes_ready_after_complete_baseline(session):
    db, entity_id = session
    account_ids = _account_ids(db, entity_id)
    _baseline(db, entity_id, account_ids)
    for account in db.query(LedgerAccount).filter_by(entity_id=entity_id):
        account.group_name = account.name
        account.normal_balance = "debit"
    contents = _gl_xlsx_bytes()
    annual = stage_import_batch(
        db,
        entity_id=entity_id,
        period_start=FY_START,
        period_end=FY_END,
        source="gl_upload",
        source_family="gl_upload",
        original_filename="normalizer-partial.xlsx",
        contents=contents,
        uploaded_by_user_id=None,
        coverage_start=FY_START,
        coverage_end=FY_END,
    )
    report = normalize_gl_xlsx(
        contents,
        FY_START,
        FY_END,
        entity_id,
        db,
        import_batch_id=annual.id,
    )
    activate_import_batch(db, annual)
    db.commit()

    result = resolve_active_dataset(db, entity_id, financial_year=2025)

    assert report["coverage_complete"] is True
    assert annual.status == "ACTIVE"
    assert annual.validation_report["readiness"] == "PARTIAL"
    assert result["baseline_coverage"]["complete"] is True
    assert result["readiness"] == "READY"


def test_missing_baseline_keeps_openings_unknown_and_dataset_partial(session):
    db, entity_id = session
    account_ids = _account_ids(db, entity_id)
    annual = _active_batch(db, entity_id, FY_START, FY_END, b"missing-baseline")
    _entry(db, annual, date(2025, 4, 10), "MISSING-BASELINE-1", account_ids, Decimal("25.00"))
    db.commit()

    result = resolve_active_dataset(db, entity_id, financial_year=2025)

    assert result["readiness"] == "PARTIAL"
    assert result["baseline_coverage"]["present"] is False
    assert result["baseline_coverage"]["complete"] is False
    assert result["baseline_coverage"]["balance_date"] is None
    assert result["baseline_coverage"]["account_count"] == 0
    assert result["baseline_coverage"]["missing_account_codes"] == ["1000", "2000"]
    assert result["opening_balances"] == {}
    assert result["closing_balances"] == {"1000": None, "2000": None}


def test_incomplete_period_coverage_remains_partial_after_complete_baseline(session):
    db, entity_id = session
    account_ids = _account_ids(db, entity_id)
    _baseline(db, entity_id, account_ids)
    annual = _active_batch(db, entity_id, FY_START, FY_END, b"incomplete-period")
    annual.validation_report = build_reconciliation_report((), coverage_complete=False)
    annual.validation_report.update({
        "coverage_start": FY_START.isoformat(),
        "coverage_end": FY_END.isoformat(),
    })
    _entry(db, annual, date(2025, 4, 10), "INCOMPLETE-1", account_ids, Decimal("25.00"))
    db.commit()

    result = resolve_active_dataset(db, entity_id, financial_year=2025)

    assert annual.validation_report["coverage_complete"] is False
    assert result["baseline_coverage"]["complete"] is True
    assert result["readiness"] == "PARTIAL"


def test_omitted_period_coverage_remains_partial_after_complete_baseline(session):
    db, entity_id = session
    account_ids = _account_ids(db, entity_id)
    _baseline(db, entity_id, account_ids)
    annual = _active_batch(db, entity_id, FY_START, FY_END, b"omitted-period")
    annual.validation_report = build_reconciliation_report(())
    annual.validation_report.update({
        "coverage_start": FY_START.isoformat(),
        "coverage_end": FY_END.isoformat(),
    })
    _entry(db, annual, date(2025, 4, 10), "OMITTED-1", account_ids, Decimal("25.00"))
    db.commit()

    result = resolve_active_dataset(db, entity_id, financial_year=2025)

    assert "coverage_complete" not in annual.validation_report
    assert result["baseline_coverage"]["complete"] is True
    assert result["readiness"] == "PARTIAL"


def test_baseline_prefers_target_date_rows_over_newer_wrong_date_checkpoint(session):
    db, entity_id = session
    account_ids = _account_ids(db, entity_id)
    valid = _baseline(db, entity_id, account_ids, cash=Decimal("100.00"))
    wrong_date = _active_batch(
        db,
        entity_id,
        FY_START,
        FY_END,
        b"wrong-date-baseline",
        kind="balance_checkpoint",
    )
    db.add_all([
        BalanceCheckpoint(
            import_batch_id=wrong_date.id,
            entity_id=entity_id,
            ledger_account_id=account_ids["1000"],
            balance_date=date(2025, 4, 30),
            balance=Decimal("999.00"),
        ),
        BalanceCheckpoint(
            import_batch_id=wrong_date.id,
            entity_id=entity_id,
            ledger_account_id=account_ids["2000"],
            balance_date=date(2025, 4, 30),
            balance=Decimal("-999.00"),
        ),
    ])
    annual = _active_batch(db, entity_id, FY_START, FY_END, b"wrong-date-journal")
    _entry(db, annual, date(2025, 4, 10), "WRONG-DATE-1", account_ids, Decimal("10.00"))
    db.commit()

    result = resolve_active_dataset(db, entity_id, financial_year=2025)

    assert result["baseline_batch_ids"] == [valid.id]
    assert result["opening_balances"]["1000"] == "100.00"
    assert result["readiness"] == "READY"


def test_tally_baseline_falls_back_to_financial_year_start(session):
    db, entity_id = session
    account_ids = _account_ids(db, entity_id)
    baseline = _active_batch(
        db,
        entity_id,
        FY_START,
        FY_END,
        b"tally-fy-start-baseline",
        kind="balance_checkpoint",
        source="tally_xml",
        source_family="tally",
    )
    db.add_all([
        BalanceCheckpoint(
            import_batch_id=baseline.id,
            entity_id=entity_id,
            ledger_account_id=account_ids["1000"],
            balance_date=FY_START,
            balance=Decimal("100.00"),
        ),
        BalanceCheckpoint(
            import_batch_id=baseline.id,
            entity_id=entity_id,
            ledger_account_id=account_ids["2000"],
            balance_date=FY_START,
            balance=Decimal("-100.00"),
        ),
    ])
    annual = _active_batch(
        db,
        entity_id,
        FY_START,
        FY_END,
        b"tally-fy-start-journal",
        source="tally_xml",
        source_family="tally",
    )
    _entry(db, annual, date(2025, 4, 10), "TALLY-FY-START-1", account_ids, Decimal("10.00"))
    db.commit()

    result = resolve_active_dataset(db, entity_id, financial_year=2025)

    assert result["baseline_batch_ids"] == [baseline.id]
    assert result["baseline_coverage"]["balance_date"] == FY_START.isoformat()
    assert result["opening_balances"] == {"1000": "100.00", "2000": "-100.00"}
    assert result["readiness"] == "READY"


def test_foreign_checkpoint_rows_invalidate_baseline_without_fallback_balances(session):
    db, entity_id = session
    account_ids = _account_ids(db, entity_id)
    organization_id = db.query(Organization).one().id
    other_entity = Entity(
        organization_id=organization_id,
        name="Other Baseline Entity",
        materiality_threshold=Decimal("0.00"),
    )
    db.add(other_entity)
    db.flush()
    foreign_account = LedgerAccount(
        entity_id=other_entity.id,
        external_code="3000",
        name="Other Cash",
    )
    wrong_entity_account = LedgerAccount(
        entity_id=other_entity.id,
        external_code="4000",
        name="Other Capital",
    )
    db.add_all([foreign_account, wrong_entity_account])
    db.flush()
    baseline = _baseline(db, entity_id, account_ids, cash=Decimal("0.00"))
    db.add_all([
        BalanceCheckpoint(
            import_batch_id=baseline.id,
            entity_id=entity_id,
            ledger_account_id=foreign_account.id,
            balance_date=BASELINE_DATE,
            balance=Decimal("999.00"),
        ),
        BalanceCheckpoint(
            import_batch_id=baseline.id,
            entity_id=other_entity.id,
            ledger_account_id=wrong_entity_account.id,
            balance_date=BASELINE_DATE,
            balance=Decimal("888.00"),
        ),
    ])
    _active_batch(db, entity_id, FY_START, FY_END, b"foreign-baseline-journal")
    db.commit()

    result = resolve_active_dataset(db, entity_id, financial_year=2025)

    assert result["readiness"] == "INVALID"
    assert result["opening_balances"] == {}
    assert result["closing_balances"] == {}
    assert f"ledger_account:{foreign_account.id}" not in repr(result)
    assert f"ledger_account:{wrong_entity_account.id}" not in repr(result)
    assert any("checkpoint" in error.lower() for error in result["errors"])
    assert any("entity" in error.lower() or "account" in error.lower() for error in result["errors"])


def test_cross_financial_year_journal_coverage_is_invalid_without_out_of_window_movement(session):
    db, entity_id = session
    account_ids = _account_ids(db, entity_id)
    _baseline(db, entity_id, account_ids, cash=Decimal("0.00"))
    cross_year = _active_batch(
        db,
        entity_id,
        FY_START,
        date(2026, 4, 30),
        b"cross-financial-year",
    )
    _entry(db, cross_year, date(2026, 4, 15), "CROSS-FY-1", account_ids, Decimal("80.00"))
    db.commit()

    result = resolve_active_dataset(db, entity_id, financial_year=2025)

    assert result["readiness"] == "INVALID"
    assert result["account_movements"] == {}
    assert any("outside financial year" in error.lower() for error in result["errors"])


def test_cross_entity_canonical_children_make_dataset_invalid_without_account_fallback(session):
    db, entity_id = session
    account_ids = _account_ids(db, entity_id)
    organization_id = db.query(Organization).one().id
    other_entity = Entity(
        organization_id=organization_id,
        name="Other Dataset Test Entity",
        materiality_threshold=Decimal("0.00"),
    )
    db.add(other_entity)
    db.flush()
    other_account = LedgerAccount(
        entity_id=other_entity.id,
        external_code="3000",
        name="Other Cash",
    )
    db.add(other_account)
    db.flush()
    _baseline(db, entity_id, account_ids, cash=Decimal("0.00"))
    annual = _active_batch(db, entity_id, FY_START, FY_END, b"cross-entity-journal")
    foreign_parent = JournalEntry(
        import_batch_id=annual.id,
        entity_id=other_entity.id,
        source_document_id="FOREIGN-PARENT",
        posting_date=date(2025, 4, 10),
    )
    foreign_parent.lines = [JournalLine(
        ledger_account_id=account_ids["1000"],
        source_row_number=1,
        amount=Decimal("20.00"),
        side="debit",
    )]
    local_parent_foreign_account = JournalEntry(
        import_batch_id=annual.id,
        entity_id=entity_id,
        source_document_id="FOREIGN-ACCOUNT",
        posting_date=date(2025, 4, 11),
    )
    local_parent_foreign_account.lines = [JournalLine(
        ledger_account_id=other_account.id,
        source_row_number=1,
        amount=Decimal("30.00"),
        side="debit",
    )]
    db.add_all([foreign_parent, local_parent_foreign_account])
    db.commit()

    result = resolve_active_dataset(db, entity_id, financial_year=2025)

    assert result["readiness"] == "INVALID"
    assert result["account_movements"] == {}
    assert f"ledger_account:{other_account.id}" not in repr(result)
    assert any("entity" in error.lower() or "account" in error.lower() for error in result["errors"])


def test_baseline_currency_is_part_of_dataset_fingerprint(session):
    db, entity_id = session
    account_ids = _account_ids(db, entity_id)
    baseline = _baseline(db, entity_id, account_ids)
    annual = _active_batch(db, entity_id, FY_START, FY_END, b"currency-fingerprint")
    _entry(db, annual, date(2025, 4, 10), "CURRENCY-1", account_ids, Decimal("10.00"))
    for row in baseline.balance_checkpoints:
        row.currency = "INR"
    db.commit()
    inr = resolve_active_dataset(db, entity_id, financial_year=2025)

    for row in baseline.balance_checkpoints:
        row.currency = "USD"
    db.commit()
    usd = resolve_active_dataset(db, entity_id, financial_year=2025)

    assert inr["baseline_coverage"]["currency"] == "INR"
    assert usd["baseline_coverage"]["currency"] == "USD"
    assert inr["dataset_fingerprint"] != usd["dataset_fingerprint"]
