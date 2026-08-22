import os
import io
import json
import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

SAMPLE_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../sample_data"))


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
    assert run_res.json()["exceptions_count"] == 4

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
