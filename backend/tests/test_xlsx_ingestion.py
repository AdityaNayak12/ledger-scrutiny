import os
import io
import json
from datetime import date
from decimal import Decimal
import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.main import app
from app.db.base import Base
from app.db.models import Entity, ImportBatch, JournalEntry, JournalLine, LedgerAccount, Organization
from app.ingestion.batches import stage_import_batch
from app.ingestion.xlsx_normalizer import normalize_gl_xlsx, parse_gl_xlsx

client = TestClient(app)

SAMPLE_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../sample_data"))
GOLDEN_GL = os.path.join(os.path.dirname(__file__), "fixtures/golden_gl.xlsx")


def _xlsx_bytes(headers, rows, preamble=()):
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    for row in preamble:
        worksheet.append(row)
    worksheet.append(headers)
    for row in rows:
        worksheet.append(row)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


@pytest.fixture
def canonical_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        organization = Organization(name="GL Test Org")
        session.add(organization)
        session.flush()
        entity = Entity(
            organization_id=organization.id,
            name="GL Test Entity",
            materiality_threshold=Decimal("0.00"),
        )
        session.add(entity)
        session.flush()
        yield session, entity.id
    Base.metadata.drop_all(engine)


def _stage_gl_batch(session, entity_id, contents):
    return stage_import_batch(
        session,
        entity_id=entity_id,
        period_start=date(2025, 4, 1),
        period_end=date(2026, 3, 31),
        source="gl_upload",
        source_family="gl_upload",
        original_filename="golden_gl.xlsx",
        contents=contents,
        uploaded_by_user_id=None,
        coverage_start=date(2025, 4, 1),
        coverage_end=date(2026, 3, 31),
    )


def test_fixed_gl_normalizer_persists_golden_canonical_counts_and_report(canonical_session):
    session, entity_id = canonical_session
    contents = open(GOLDEN_GL, "rb").read()
    batch = _stage_gl_batch(session, entity_id, contents)

    report = normalize_gl_xlsx(
        contents,
        target_period_start=date(2025, 4, 1),
        target_period_end=date(2026, 3, 31),
        entity_id=entity_id,
        session=session,
        import_batch_id=batch.id,
    )
    session.commit()

    assert session.scalar(select(func.count()).select_from(JournalLine)) == 6
    assert session.scalar(select(func.count()).select_from(JournalEntry)) == 3
    assert session.scalar(select(func.count()).select_from(LedgerAccount)) == 5
    assert [line.side for line in session.scalars(select(JournalLine).order_by(JournalLine.source_row_number))] == [
        "debit", "credit", "debit", "credit", "debit", "credit"
    ]
    assert report["accepted_rows"] == 6
    assert report["document_count"] == 3
    assert report["unbalanced_document_count"] == 0
    assert Decimal(report["net"]) == Decimal("0")
    assert batch.validation_report["readiness"] == "PARTIAL"
    assert batch.validation_report["account_codes"] == ["110000", "120000", "210000", "400000", "500000"]


def test_fixed_gl_parser_uses_data_only_and_scans_only_first_fifteen_rows(monkeypatch):
    headers = ["Document Number", "G/L Account", "Posting Date", "Amount in local currency"]
    contents = _xlsx_bytes(
        headers,
        [
            ["DOC-1", "1000", date(2025, 4, 1), 1],
            ["DOC-1", "2000", date(2025, 4, 1), -1],
        ],
        preamble=[["Report title"] for _ in range(14)],
    )
    import app.ingestion.xlsx_normalizer as normalizer

    calls = []
    original_load_workbook = normalizer.openpyxl.load_workbook

    def load_workbook(*args, **kwargs):
        calls.append(kwargs.copy())
        return original_load_workbook(*args, **kwargs)

    monkeypatch.setattr(normalizer.openpyxl, "load_workbook", load_workbook)
    entries = parse_gl_xlsx(contents, date(2025, 4, 1), date(2025, 4, 30))

    assert len(entries) == 1
    assert calls[0]["data_only"] is True

    after_fifteen_rows = _xlsx_bytes(
        headers,
        [
            ["DOC-1", "1000", date(2025, 4, 1), 1],
            ["DOC-1", "2000", date(2025, 4, 1), -1],
        ],
        preamble=[["Report title"] for _ in range(15)],
    )
    with pytest.raises(ValueError, match="first 15 rows"):
        parse_gl_xlsx(after_fifteen_rows, date(2025, 4, 1), date(2025, 4, 30))

    wrong_header = _xlsx_bytes(
        ["Document Number", "G/L account", "Posting Date", "Amount in local currency"],
        [["DOC-1", "1000", date(2025, 4, 1), 1], ["DOC-1", "2000", date(2025, 4, 1), -1]],
    )
    with pytest.raises(ValueError, match="exact required GL headers"):
        parse_gl_xlsx(wrong_header, date(2025, 4, 1), date(2025, 4, 30))


def test_fixed_gl_parser_never_evaluates_formula_values():
    contents = _xlsx_bytes(
        ["Document Number", "G/L Account", "Posting Date", "Amount in local currency"],
        [["DOC-1", "1000", date(2025, 4, 1), "=1+1"]],
    )

    with pytest.raises(ValueError, match="Amount in local currency.*required"):
        parse_gl_xlsx(contents, date(2025, 4, 1), date(2025, 4, 30))


def test_fixed_gl_parser_preserves_dimensions_and_source_row(canonical_session):
    session, entity_id = canonical_session
    headers = [
        "Document Number", "G/L Account", "Posting Date", "Amount in local currency",
        "Document Date", "Document Type", "Posting Key", "Invoice Reference",
        "Clearing Document", "Profit Centre", "Cost Centre", "Text", "Supplier",
        "WBS Element", "Purchasing Document", "Customer", "Business Area",
    ]
    contents = _xlsx_bytes(headers, [
        [
            "DOC-1", "1000", date(2025, 4, 1), 100,
            date(2025, 4, 1), "SA", "40", "INV-1", "CLR-1", "PC-1", "CC-1",
            "Narration", "V-1", "WBS-1", "PO-1", "C-1", "BA-1",
        ],
        [
            "DOC-1", "2000", date(2025, 4, 1), -100,
            date(2025, 4, 1), "SA", "50", "INV-1", "CLR-1", "PC-1", "CC-1",
            "Narration", "V-1", "WBS-1", "PO-1", "C-1", "BA-1",
        ],
    ])
    entries = parse_gl_xlsx(contents, date(2025, 4, 1), date(2025, 4, 30))
    entry = entries[0]
    line = entry.lines[0]

    assert line.source_row_number == 2
    assert entry.document_date == date(2025, 4, 1)
    assert entry.document_type == "SA"
    assert entry.narration == "Narration"
    assert line.posting_key == "40"
    assert line.reference == "INV-1"
    assert line.clearing_document == "CLR-1"
    assert line.profit_center == "PC-1"
    assert line.cost_center == "CC-1"
    assert line.text == "Narration"
    assert line.supplier == "V-1"
    assert line.wbs == "WBS-1"
    assert line.purchasing_document == "PO-1"
    assert line.customer == "C-1"
    assert line.dimensions["Business Area"] == "BA-1"
    assert line.source_metadata["Business Area"] == "BA-1"

    batch = _stage_gl_batch(session, entity_id, contents)
    normalize_gl_xlsx(
        contents,
        date(2025, 4, 1),
        date(2025, 4, 30),
        entity_id,
        session,
        import_batch_id=batch.id,
    )
    persisted = session.scalar(select(JournalLine))
    assert persisted.source_row_number == 2
    assert persisted.dimensions["Business Area"] == "BA-1"
    assert persisted.source_metadata["G/L Account"] == "1000"


@pytest.mark.parametrize(
    ("document", "account", "posting_date", "amount", "message"),
    [
        (None, "1000", date(2025, 4, 1), 100, "Document Number.*required"),
        ("DOC-1", None, date(2025, 4, 1), 100, "G/L Account.*required"),
        ("DOC-1", "1000", "not-a-date", 100, "Posting Date.*date"),
        ("DOC-1", "1000", date(2025, 4, 1), "not-a-decimal", "Amount in local currency.*decimal"),
    ],
)
def test_fixed_gl_parser_rejects_malformed_required_values(
    document, account, posting_date, amount, message
):
    contents = _xlsx_bytes(
        ["Document Number", "G/L Account", "Posting Date", "Amount in local currency"],
        [[document, account, posting_date, amount]],
    )

    with pytest.raises(ValueError, match=message):
        parse_gl_xlsx(contents, date(2025, 4, 1), date(2025, 4, 30))


def test_fixed_gl_parser_rejects_unbalanced_documents():
    contents = _xlsx_bytes(
        ["Document Number", "G/L Account", "Posting Date", "Amount in local currency"],
        [
            ["DOC-1", "1000", date(2025, 4, 1), 100],
            ["DOC-1", "2000", date(2025, 4, 1), -99.98],
        ],
    )

    with pytest.raises(ValueError, match="DOC-1.*balance"):
        parse_gl_xlsx(contents, date(2025, 4, 1), date(2025, 4, 30))


def test_fixed_gl_parser_rejects_rows_outside_declared_coverage():
    contents = _xlsx_bytes(
        ["Document Number", "G/L Account", "Posting Date", "Amount in local currency"],
        [["DOC-1", "1000", date(2025, 5, 1), 100]],
    )

    with pytest.raises(ValueError, match="outside declared coverage"):
        parse_gl_xlsx(contents, date(2025, 4, 1), date(2025, 4, 30))


def test_fixed_gl_normalizer_reports_skips_formula_cache_warnings_and_unclassified_accounts(canonical_session):
    session, entity_id = canonical_session
    headers = [
        "Document Number", "G/L Account", "Posting Date", "Amount in local currency", "Text",
    ]
    contents = _xlsx_bytes(headers, [
        ["DOC-1", "1000", date(2025, 4, 1), 100, '=CONCAT("not", "cached")'],
        [None, None, None, None, None],
        ["DOC-1", "2000", date(2025, 4, 1), -100, '=CONCAT("not", "cached")'],
    ])
    batch = _stage_gl_batch(session, entity_id, contents)

    report = normalize_gl_xlsx(
        contents,
        date(2025, 4, 1),
        date(2025, 4, 30),
        entity_id,
        session,
        import_batch_id=batch.id,
    )

    assert report["accepted_rows"] == 2
    assert report["skipped_rows"] == 1
    assert report["skip_reasons"] == [{"row": 3, "reason": "blank source row"}]
    assert any("missing cached formula" in warning for warning in report["warnings"])
    assert report["unmapped_account_codes"] == ["1000", "2000"]
    assert all(account.group_name is None for account in session.scalars(select(LedgerAccount)))


def test_fixed_gl_parser_allows_repeated_account_codes_within_a_document():
    contents = _xlsx_bytes(
        ["Document Number", "G/L Account", "Posting Date", "Amount in local currency"],
        [
            ["DOC-1", "1000", date(2025, 4, 1), 60],
            ["DOC-1", "1000", date(2025, 4, 1), 40],
            ["DOC-1", "2000", date(2025, 4, 1), -100],
        ],
    )

    entries = parse_gl_xlsx(contents, date(2025, 4, 1), date(2025, 4, 30))

    assert len(entries) == 1
    assert [line.ledger_account_code for line in entries[0].lines] == ["1000", "1000", "2000"]


@pytest.fixture
def auth_headers():
    res = client.post("/auth/register", json={
        "organization_name": "XLSX Test Firm",
        "email": f"xlsx.auditor.{os.urandom(4).hex()}@firm.com",
        "password": "Password123!"
    })
    assert res.status_code == 201
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def test_entity(auth_headers):
    res = client.post("/entities", json={
        "name": "XLSX Test Entity",
        "materiality_threshold": 0.0
    }, headers=auth_headers)
    assert res.status_code == 201
    return res.json()["id"]


def test_xlsx_confirm_produces_identical_exceptions_to_xml_fixture(auth_headers, test_entity):
    file_path = os.path.join(SAMPLE_DATA_DIR, "test_fixture_1.xlsx")
    column_mapping = json.dumps({
        "ledger_name": "Particulars",
        "group_name": "Grp",
        "opening_balance": "Op Bal",
        "closing_balance": "Cl Bal"
    })

    with open(file_path, "rb") as f:
        confirm_res = client.post(
            f"/entities/{test_entity}/upload-xlsx/confirm",
            data={
                "column_mapping": column_mapping,
                "sign_convention": "negative_is_credit",
                "target_period_start": "2025-04-01",
                "target_period_end": "2026-03-31",
                "clear_only_period": "true"
            },
            files={"file": ("test_fixture_1.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            headers=auth_headers
        )

    assert confirm_res.status_code == 200, f"Confirm failed: {confirm_res.text}"
    assert confirm_res.json()["message"] == "XLSX ingestion successful"

    # Trigger scrutiny run
    run_res = client.post(
        f"/entities/{test_entity}/scrutiny-run?period_start=2025-04-01&period_end=2026-03-31",
        headers=auth_headers
    )
    assert run_res.status_code == 200, f"Scrutiny run failed: {run_res.text}"
    assert run_res.json()["exceptions_count"] == 5

    # Fetch exceptions
    exc_res = client.get(
        f"/entities/{test_entity}/exceptions?period_start=2025-04-01&period_end=2026-03-31",
        headers=auth_headers
    )
    assert exc_res.status_code == 200, f"Fetch exceptions failed: {exc_res.text}"
    exceptions = exc_res.json()

    account_names = [e["ledger_account_name"] for e in exceptions if e.get("ledger_account_name")]
    assert "Rahul Enterprises" in account_names
    assert "Verma Traders" in account_names
    assert "Petty Cash Variance" in account_names


def test_xlsx_fail_loud_validations_blank_ledger(auth_headers, test_entity):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Ledger Name", "Group Name", "Opening Balance", "Closing Balance"])
    ws.append(["", "Fixed Assets", "1000.00", "1000.00"])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    column_mapping = json.dumps({
        "ledger_name": "Ledger Name",
        "group_name": "Group Name",
        "opening_balance": "Opening Balance",
        "closing_balance": "Closing Balance"
    })

    res = client.post(
        f"/entities/{test_entity}/upload-xlsx/confirm",
        data={
            "column_mapping": column_mapping,
            "sign_convention": "negative_is_credit",
            "target_period_start": "2025-04-01",
            "target_period_end": "2026-03-31"
        },
        files={"file": ("blank_ledger.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=auth_headers
    )

    assert res.status_code == 400, f"Expected 400, got: {res.status_code} {res.text}"
    assert "Ledger account name cannot be blank" in res.json()["detail"]


def test_xlsx_fail_loud_validations_unrecognized_group(auth_headers, test_entity):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Ledger Name", "Group Name", "Opening Balance", "Closing Balance"])
    ws.append(["Valid Account", "Invalid Group Name Here", "1000.00", "1000.00"])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    column_mapping = json.dumps({
        "ledger_name": "Ledger Name",
        "group_name": "Group Name",
        "opening_balance": "Opening Balance",
        "closing_balance": "Closing Balance"
    })

    res = client.post(
        f"/entities/{test_entity}/upload-xlsx/confirm",
        data={
            "column_mapping": column_mapping,
            "sign_convention": "negative_is_credit",
            "target_period_start": "2025-04-01",
            "target_period_end": "2026-03-31"
        },
        files={"file": ("invalid_group.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=auth_headers
    )

    assert res.status_code == 400, f"Expected 400, got: {res.status_code} {res.text}"
    assert "Unrecognized" in res.json()["detail"]


def test_xlsx_sign_conventions(auth_headers, test_entity):
    # Test positive_is_credit
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Ledger", "Group", "Opening", "Closing"])
    ws.append(["Share Capital", "Capital Account", "100000.00", "100000.00"])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    column_mapping = json.dumps({
        "ledger_name": "Ledger",
        "group_name": "Group",
        "opening_balance": "Opening",
        "closing_balance": "Closing"
    })

    res = client.post(
        f"/entities/{test_entity}/upload-xlsx/confirm",
        data={
            "column_mapping": column_mapping,
            "sign_convention": "positive_is_credit",
            "target_period_start": "2025-04-01",
            "target_period_end": "2026-03-31"
        },
        files={"file": ("pos_credit.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=auth_headers
    )
    assert res.status_code == 200, f"Sign convention test failed: {res.status_code} {res.text}"

    # Test separate_dr_cr_columns
    wb2 = openpyxl.Workbook()
    ws2 = wb2.active
    ws2.append(["Ledger", "Group", "Op Dr", "Op Cr", "Cl Dr", "Cl Cr"])
    ws2.append(["Office Equipment", "Fixed Assets", "50000.00", "0.00", "45000.00", "0.00"])

    buf2 = io.BytesIO()
    wb2.save(buf2)
    buf2.seek(0)

    mapping2 = json.dumps({
        "ledger_name": "Ledger",
        "group_name": "Group",
        "opening_debit": "Op Dr",
        "opening_credit": "Op Cr",
        "closing_debit": "Cl Dr",
        "closing_credit": "Cl Cr"
    })

    res2 = client.post(
        f"/entities/{test_entity}/upload-xlsx/confirm",
        data={
            "column_mapping": mapping2,
            "sign_convention": "separate_dr_cr_columns",
            "target_period_start": "2025-04-01",
            "target_period_end": "2026-03-31"
        },
        files={"file": ("separate_drcr.xlsx", buf2, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=auth_headers
    )
    assert res2.status_code == 200, f"Separate dr/cr test failed: {res2.status_code} {res2.text}"

def test_xlsx_skips_blank_lines(auth_headers, test_entity):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Ledger Name", "Group Name", "Opening Balance", "Closing Balance"])
    ws.append(["Ledger A", "Fixed Assets", "1000.00", "1000.00"])
    ws.append(["", "", "", ""])  # blank row
    ws.append(["Ledger B", "Capital Account", "2000.00", "2000.00"])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    column_mapping = json.dumps({
        "ledger_name": "Ledger Name",
        "group_name": "Group Name",
        "opening_balance": "Opening Balance",
        "closing_balance": "Closing Balance"
    })

    res = client.post(
        f"/entities/{test_entity}/upload-xlsx/confirm",
        data={
            "column_mapping": column_mapping,
            "sign_convention": "negative_is_credit",
            "target_period_start": "2025-04-01",
            "target_period_end": "2026-03-31"
        },
        files={"file": ("blank_lines.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=auth_headers
    )
    assert res.status_code == 200, f"Failed to ingest: {res.text}"

    # Trigger scrutiny run
    run_res = client.post(
        f"/entities/{test_entity}/scrutiny-run?period_start=2025-04-01&period_end=2026-03-31",
        headers=auth_headers
    )
    assert run_res.status_code == 200, f"Scrutiny run failed: {run_res.text}"

    # Fetch exceptions
    exc_res = client.get(
        f"/entities/{test_entity}/exceptions?period_start=2025-04-01&period_end=2026-03-31",
        headers=auth_headers
    )
    assert exc_res.status_code == 200, f"Fetch exceptions failed: {exc_res.text}"
    exceptions = exc_res.json()

    # Assert Ledger B triggers normal balance check exception (proving it was ingested)
    account_names = [e["ledger_account_name"] for e in exceptions if e.get("ledger_account_name")]
    assert "Ledger B" in account_names
