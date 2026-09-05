import base64
import io
import json
from datetime import date
from decimal import Decimal

import openpyxl
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import BalanceCheckpoint, Entity, ImportBatch, LedgerAccount, Organization
from app.ingestion.baseline import (
    normalize_balance_checkpoint_xlsx,
    parse_baseline_xlsx,
)
from app.ingestion.batches import stage_import_batch


HEADERS = ["Account Code", "Balance Date", "Signed Balance", "Currency"]
BALANCE_DATE = date(2025, 3, 31)


def _xlsx_bytes(rows):
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.append(HEADERS)
    for row in rows:
        worksheet.append(row)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


@pytest.fixture
def baseline_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        organization = Organization(name="Baseline Test Org")
        session.add(organization)
        session.flush()
        entity = Entity(
            organization_id=organization.id,
            name="Baseline Test Entity",
            materiality_threshold=Decimal("0.00"),
        )
        session.add(entity)
        session.flush()
        session.add_all([
            LedgerAccount(entity_id=entity.id, external_code="1000", name="Cash"),
            LedgerAccount(entity_id=entity.id, external_code="2000", name="Capital"),
            LedgerAccount(entity_id=entity.id, external_code="3000", name="Revenue"),
        ])
        session.commit()
        yield session, entity.id
    Base.metadata.drop_all(engine)


def _stage(session, entity_id, contents):
    return stage_import_batch(
        session,
        entity_id=entity_id,
        period_start=date(2025, 4, 1),
        period_end=date(2026, 3, 31),
        source="gl_upload",
        source_family="gl_upload",
        original_filename="closing-baseline.xlsx",
        contents=contents,
        uploaded_by_user_id=None,
        kind="balance_checkpoint",
        coverage_start=date(2025, 4, 1),
        coverage_end=date(2026, 3, 31),
    )


def _valid_bytes():
    return _xlsx_bytes([
        ["1000", BALANCE_DATE, "100.00", "INR"],
        ["2000", BALANCE_DATE, "-60.00", "INR"],
        ["3000", BALANCE_DATE, "-40.00", "INR"],
    ])


def test_parser_reads_signed_decimal_rows_with_data_only(monkeypatch):
    calls = []
    original_load_workbook = openpyxl.load_workbook

    def load_workbook(*args, **kwargs):
        calls.append(kwargs.copy())
        return original_load_workbook(*args, **kwargs)

    monkeypatch.setattr("app.ingestion.baseline.openpyxl.load_workbook", load_workbook)

    records = parse_baseline_xlsx(_valid_bytes())

    assert calls[0]["data_only"] is True
    assert [record.ledger_account_code for record in records] == ["1000", "2000", "3000"]
    assert [record.balance for record in records] == [
        Decimal("100.00"), Decimal("-60.00"), Decimal("-40.00")
    ]
    assert all(record.balance_date == BALANCE_DATE for record in records)
    assert all(record.currency == "INR" for record in records)


def test_valid_baseline_persists_checkpoints_reports_and_activates(baseline_session):
    session, entity_id = baseline_session
    contents = _valid_bytes()
    batch = _stage(session, entity_id, contents)

    report = normalize_balance_checkpoint_xlsx(
        contents,
        entity_id,
        session,
        import_batch_id=batch.id,
        expected_currency="INR",
    )
    session.commit()

    checkpoints = session.scalars(
        select(BalanceCheckpoint).order_by(BalanceCheckpoint.ledger_account_id)
    ).all()
    assert batch.status == "ACTIVE"
    assert batch.raw_source_bytes == contents
    assert [checkpoint.balance for checkpoint in checkpoints] == [
        Decimal("100.00"), Decimal("-60.00"), Decimal("-40.00")
    ]
    assert all(checkpoint.import_batch_id == batch.id for checkpoint in checkpoints)
    assert report["readiness"] == "READY"
    assert report["signed_total"] == "0.00"
    assert report["baseline_coverage"]["complete"] is True
    json.dumps(report)


def test_duplicate_account_codes_are_rejected():
    contents = _xlsx_bytes([
        ["1000", BALANCE_DATE, "50.00", "INR"],
        ["1000", BALANCE_DATE, "-50.00", "INR"],
    ])

    with pytest.raises(ValueError, match="[Dd]uplicate account code"):
        parse_baseline_xlsx(contents)


@pytest.mark.parametrize(
    ("header_index", "unsupported_header"),
    [
        (0, "G/L Account"),
        (0, "G/L Account Code"),
        (2, "Closing Balance"),
        (2, "Balance"),
        (3, "Currency Code"),
    ],
)
def test_unsupported_baseline_header_aliases_are_rejected(header_index, unsupported_header):
    headers = HEADERS.copy()
    headers[header_index] = unsupported_header
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.append(headers)
    worksheet.append(["1000", BALANCE_DATE, "100.00", "INR"])
    output = io.BytesIO()
    workbook.save(output)

    with pytest.raises(ValueError, match="required baseline headers"):
        parse_baseline_xlsx(output.getvalue())


def test_baseline_headers_beyond_fixed_profile_scan_window_are_rejected():
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    for _ in range(15):
        worksheet.append(["Report title"])
    worksheet.append(HEADERS)
    worksheet.append(["1000", BALANCE_DATE, "100.00", "INR"])
    output = io.BytesIO()
    workbook.save(output)

    with pytest.raises(ValueError, match="first 15 rows"):
        parse_baseline_xlsx(output.getvalue())


def test_non_balanced_signed_total_is_rejected():
    contents = _xlsx_bytes([
        ["1000", BALANCE_DATE, "100.00", "INR"],
        ["2000", BALANCE_DATE, "-99.98", "INR"],
    ])

    with pytest.raises(ValueError, match="signed total"):
        parse_baseline_xlsx(contents)


def test_date_and_currency_inconsistency_are_rejected():
    with pytest.raises(ValueError, match="one balance date"):
        parse_baseline_xlsx(_xlsx_bytes([
            ["1000", BALANCE_DATE, "100.00", "INR"],
            ["2000", date(2025, 3, 30), "-100.00", "INR"],
        ]))

    with pytest.raises(ValueError, match="same currency"):
        parse_baseline_xlsx(_xlsx_bytes([
            ["1000", BALANCE_DATE, "100.00", "INR"],
            ["2000", BALANCE_DATE, "-100.00", "USD"],
        ]))


def test_unknown_and_missing_master_accounts_are_rejected(baseline_session):
    session, entity_id = baseline_session
    contents = _xlsx_bytes([
        ["1000", BALANCE_DATE, "100.00", "INR"],
        ["9999", BALANCE_DATE, "-100.00", "INR"],
    ])
    batch = _stage(session, entity_id, contents)

    with pytest.raises(ValueError, match="Unknown account code.*9999"):
        normalize_balance_checkpoint_xlsx(
            contents,
            entity_id,
            session,
            import_batch_id=batch.id,
            expected_currency="INR",
        )

    assert batch.status == "FAILED"
    assert session.scalar(select(BalanceCheckpoint.id)) is None
    assert batch.raw_source_bytes == contents
    errors = " ".join(batch.validation_report["errors"])
    assert "Missing expected account code" in errors
    assert "2000" in errors and "3000" in errors


def test_pdf_only_input_is_rejected_but_retained_as_evidence(baseline_session):
    session, entity_id = baseline_session
    pdf_bytes = b"%PDF-1.7 signed closing trial balance"
    batch = _stage(session, entity_id, pdf_bytes)

    with pytest.raises(ValueError, match="PDF.*structured XLSX"):
        normalize_balance_checkpoint_xlsx(
            pdf_bytes,
            entity_id,
            session,
            import_batch_id=batch.id,
        )

    assert batch.status == "FAILED"
    assert batch.raw_source_bytes == pdf_bytes
    evidence = batch.source_metadata["supporting_evidence"]["signed_pdf"]
    assert base64.b64decode(evidence["bytes_base64"]) == pdf_bytes
    assert session.scalar(select(BalanceCheckpoint.id)) is None


def test_invalid_staged_batch_keeps_workbook_and_optional_pdf_bytes(baseline_session):
    session, entity_id = baseline_session
    contents = _xlsx_bytes([
        ["1000", BALANCE_DATE, "10.00", "INR"],
        ["2000", BALANCE_DATE, "-9.00", "INR"],
    ])
    pdf_bytes = b"%PDF-1.7 signed evidence"
    batch = _stage(session, entity_id, contents)

    with pytest.raises(ValueError, match="signed total"):
        normalize_balance_checkpoint_xlsx(
            contents,
            entity_id,
            session,
            import_batch_id=batch.id,
            signed_pdf_bytes=pdf_bytes,
            expected_currency="INR",
        )

    assert batch.status == "FAILED"
    assert batch.raw_source_bytes == contents
    evidence = batch.source_metadata["supporting_evidence"]["signed_pdf"]
    assert base64.b64decode(evidence["bytes_base64"]) == pdf_bytes


def test_baseline_fingerprint_includes_account_balance_pairs(baseline_session):
    session, entity_id = baseline_session
    first_contents = _valid_bytes()
    second_contents = _xlsx_bytes([
        ["1000", BALANCE_DATE, "90.00", "INR"],
        ["2000", BALANCE_DATE, "-50.00", "INR"],
        ["3000", BALANCE_DATE, "-40.00", "INR"],
    ])
    first = _stage(session, entity_id, first_contents)
    second = _stage(session, entity_id, second_contents)

    first_report = normalize_balance_checkpoint_xlsx(
        first_contents, entity_id, session, import_batch_id=first.id, expected_currency="INR"
    )
    second_report = normalize_balance_checkpoint_xlsx(
        second_contents, entity_id, session, import_batch_id=second.id, expected_currency="INR"
    )

    assert first_report["signed_total"] == second_report["signed_total"] == "0.00"
    assert first_report["dataset_fingerprint"] != second_report["dataset_fingerprint"]
