import os
from pathlib import Path
from decimal import Decimal
from datetime import date
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import app
from app.db.models import ImportBatch, Transaction, TrialBalanceSnapshot
from conftest import TestingSessionLocal

client = TestClient(app)


def get_auth_headers():
    res = client.post("/auth/register", json={
        "organization_name": "Integration Test Firm",
        "email": "test@integration.com",
        "password": "Password123"
    })
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_error_responses_include_local_cors_headers():
    response = client.get(
        "/entities",
        headers={"Origin": "http://localhost:5173"},
    )

    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_demo_gst_profile_selects_manufacturing_pack(monkeypatch):
    headers = get_auth_headers()
    monkeypatch.setenv("GST_LOOKUP_MODE", "demo")

    lookup = client.post("/gst-profile/lookup", json={"gstin": "27DEMOX0000D1Z0"}, headers=headers)
    assert lookup.status_code == 200
    profile = lookup.json()
    assert profile["simulated"] is True
    assert profile["core_business_activity"] == "Manufacturer"
    assert profile["suggested_rule_pack"] == "manufacturing_v1"

    created = client.post("/entities", json={
        "name": profile["legal_name"],
        "materiality_threshold": "100000.00",
        "gstin": profile["gstin"],
        "sector": profile["suggested_sector"],
        "rule_pack": profile["suggested_rule_pack"],
    }, headers=headers)
    assert created.status_code == 201
    assert created.json()["rule_pack"] == "manufacturing_v1"


def test_manufacturing_pitch_files_produce_five_expected_findings(monkeypatch):
    headers = get_auth_headers()
    monkeypatch.setenv("GST_LOOKUP_MODE", "demo")
    entity = client.post("/entities", json={
        "name": "Meridian Components Private Limited — Fictional Demo",
        "materiality_threshold": "100000.00",
        "gstin": "27DEMOX0000D1Z0",
        "sector": "manufacturing",
        "rule_pack": "manufacturing_v1",
    }, headers=headers).json()
    fixture_dir = Path(__file__).parents[2] / "sample_data" / "pitch_manufacturing_demo"

    for filename, start, end in (
        ("meridian_fy2024_25.xml", "2024-04-01", "2025-03-31"),
        ("meridian_fy2025_26.xml", "2025-04-01", "2026-03-31"),
    ):
        with (fixture_dir / filename).open("rb") as file_handle:
            response = client.post(
                f"/entities/{entity['id']}/upload",
                params={"clear_only_period": "true", "target_period_start": start, "target_period_end": end},
                files={"file": (filename, file_handle, "text/xml")},
                headers=headers,
            )
        assert response.status_code == 200, response.text

    run = client.post(
        f"/entities/{entity['id']}/scrutiny-run",
        params={"period_start": "2025-04-01", "period_end": "2026-03-31"},
        headers=headers,
    )
    assert run.status_code == 200, run.text
    assert run.json()["exceptions_count"] == 8

    findings = client.get(
        f"/entities/{entity['id']}/exceptions",
        params={"period_start": "2025-04-01", "period_end": "2026-03-31"},
        headers=headers,
    ).json()
    assert {finding["rule_name"] for finding in findings} == {
        "current_account_credit_balance",
        "opening_balance_continuity",
        "suspense_account_nonzero",
        "manufacturing_low_inventory_movement",
        "manufacturing_gross_margin_shift", "tds_liability_check", "creditor_debit_balance", "debtor_credit_balance",
    }
    margin = next(finding for finding in findings if finding["rule_name"] == "manufacturing_gross_margin_shift")
    assert "27.5%" in margin["message"]
    assert "10.2%" in margin["message"]
    inventory = next(finding for finding in findings if finding["rule_name"] == "manufacturing_low_inventory_movement")
    assert "1% of COGS" in inventory["message"]


def test_api_entities_lifecycle_flow():
    headers = get_auth_headers()

    # 1. Create an Entity
    entity_payload = {
        "name": "Acme Audited Corp",
        "materiality_threshold": "1000.00"
    }
    create_res = client.post("/entities", json=entity_payload, headers=headers)
    assert create_res.status_code == 201
    entity = create_res.json()
    assert entity["name"] == "Acme Audited Corp"
    entity_id = entity["id"]

    # 2. List Entities
    list_entities_res = client.get("/entities", headers=headers)
    assert list_entities_res.status_code == 200
    assert len(list_entities_res.json()) == 1
    assert list_entities_res.json()[0]["id"] == entity_id

    # 3. Upload Clean Sample Tally XML
    xml_path = os.path.join(os.path.dirname(__file__), "sample_tally_export.xml")
    with open(xml_path, "rb") as f:
        upload_res = client.post(
            f"/entities/{entity_id}/upload",
            files={"file": ("sample_tally_export.xml", f, "text/xml")},
            headers=headers
        )

    assert upload_res.status_code == 200
    res_data = upload_res.json()
    assert res_data["message"] == "Ingestion successful"
    assert res_data["entity_id"] == entity_id

    # 4. Trigger Scrutiny Run
    run_res = client.post(f"/entities/{entity_id}/scrutiny-run?period_start=2025-04-01&period_end=2026-03-31", headers=headers)
    assert run_res.status_code == 200
    summary = run_res.json()
    assert summary["status"] == "success"
    assert summary["exceptions_count"] == 1  # trial_balance_balances exception

    # 5. Check exceptions endpoint
    list_exceptions_res = client.get(f"/entities/{entity_id}/exceptions?period_start=2025-04-01&period_end=2026-03-31", headers=headers)
    assert list_exceptions_res.status_code == 200
    exceptions = list_exceptions_res.json()
    assert len(exceptions) == 1
    assert exceptions[0]["rule_name"] == "trial_balance_balances"


def test_duplicate_tally_upload_is_idempotent_for_child_records():
    registration = client.post("/auth/register", json={
        "organization_name": "Duplicate Import Firm",
        "email": f"duplicate.{os.urandom(4).hex()}@integration.com",
        "password": "Password123",
    })
    assert registration.status_code == 201
    headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}
    entity = client.post(
        "/entities",
        json={"name": "Duplicate Import Entity", "materiality_threshold": "0.00"},
        headers=headers,
    )
    assert entity.status_code == 201
    entity_id = entity.json()["id"]
    xml_path = os.path.join(os.path.dirname(__file__), "sample_tally_export.xml")

    with open(xml_path, "rb") as file_handle:
        first = client.post(
            f"/entities/{entity_id}/upload",
            files={"file": ("sample_tally_export.xml", file_handle, "text/xml")},
            headers=headers,
        )
    with open(xml_path, "rb") as file_handle:
        duplicate = client.post(
            f"/entities/{entity_id}/upload",
            files={"file": ("sample_tally_export.xml", file_handle, "text/xml")},
            headers=headers,
        )

    assert first.status_code == 200, first.text
    assert duplicate.status_code == 200, duplicate.text
    batch_id = first.json()["import_batch_id"]
    assert duplicate.json()["import_batch_id"] == batch_id
    with TestingSessionLocal() as session:
        assert session.scalar(
            select(func.count()).select_from(Transaction).where(Transaction.import_batch_id == batch_id)
        ) == 2
        assert session.scalar(
            select(func.count()).select_from(TrialBalanceSnapshot).where(
                TrialBalanceSnapshot.import_batch_id == batch_id
            )
        ) == 5
        assert session.scalar(
            select(func.count()).select_from(ImportBatch).where(ImportBatch.entity_id == entity_id)
        ) == 1


def test_duplicate_tally_upload_of_failed_batch_is_rejected_without_new_rows():
    registration = client.post("/auth/register", json={
        "organization_name": "Failed Duplicate Firm",
        "email": f"failed-duplicate.{os.urandom(4).hex()}@integration.com",
        "password": "Password123",
    })
    assert registration.status_code == 201
    headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}
    entity = client.post(
        "/entities",
        json={"name": "Failed Duplicate Entity", "materiality_threshold": "0.00"},
        headers=headers,
    )
    entity_id = entity.json()["id"]
    xml_path = os.path.join(os.path.dirname(__file__), "sample_tally_export.xml")

    with open(xml_path, "rb") as file_handle:
        first = client.post(
            f"/entities/{entity_id}/upload",
            files={"file": ("sample_tally_export.xml", file_handle, "text/xml")},
            headers=headers,
        )
    assert first.status_code == 200, first.text
    batch_id = first.json()["import_batch_id"]
    with TestingSessionLocal() as session:
        batch = session.get(ImportBatch, batch_id)
        batch.status = "FAILED"
        session.commit()

    with open(xml_path, "rb") as file_handle:
        duplicate = client.post(
            f"/entities/{entity_id}/upload",
            files={"file": ("sample_tally_export.xml", file_handle, "text/xml")},
            headers=headers,
        )

    assert duplicate.status_code == 400
    assert "no active import" in duplicate.json()["detail"]


def test_api_scrutiny_with_violations():
    headers = get_auth_headers()

    # 1. Create the entity
    entity_payload = {
        "name": "Violating Company Ltd",
        "materiality_threshold": "5000.00"
    }
    create_res = client.post("/entities", json=entity_payload, headers=headers)
    assert create_res.status_code == 201
    entity_id = create_res.json()["id"]

    # 2. Create a Tally XML with engineered violations:
    violating_xml = """<ENVELOPE>
      <BODY>
        <IMPORTDATA>
          <REQUESTDESC>
            <REPORTNAME>All Ledger Entries</REPORTNAME>
          </REQUESTDESC>
          <REQUESTDATA>
            <COMPANY>
              <RENAME>Violating Company Ltd</RENAME>
              <BOOKSFROM>20250401</BOOKSFROM>
              <BOOKSTO>20260331</BOOKSTO>
            </COMPANY>
            <TALLYMESSAGE>
              <LEDGER NAME="Cash-in-hand">
                <PARENT>Cash-in-hand</PARENT>
                <OPENINGBALANCE>10000.00</OPENINGBALANCE>
              </LEDGER>
            </TALLYMESSAGE>
            <TALLYMESSAGE>
              <LEDGER NAME="Owner Capital">
                <PARENT>Capital Account</PARENT>
                <OPENINGBALANCE>0.00</OPENINGBALANCE>
              </LEDGER>
            </TALLYMESSAGE>
            <TALLYMESSAGE>
              <VOUCHER VCHTYPE="Payment">
                <DATE>20250410</DATE>
                <VOUCHERNUMBER>VCH-0003</VOUCHERNUMBER>
                <NARRATION>Excess drawing by owner</NARRATION>
                <ALLLEDGERENTRIES.LIST>
                  <LEDGERNAME>Owner Capital</LEDGERNAME>
                  <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
                  <AMOUNT>-80000.00</AMOUNT>
                </ALLLEDGERENTRIES.LIST>
                <ALLLEDGERENTRIES.LIST>
                  <LEDGERNAME>Cash-in-hand</LEDGERNAME>
                  <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
                  <AMOUNT>80000.00</AMOUNT>
                </ALLLEDGERENTRIES.LIST>
              </VOUCHER>
            </TALLYMESSAGE>
          </REQUESTDATA>
        </IMPORTDATA>
      </BODY>
    </ENVELOPE>
    """

    # 3. Upload the violating XML
    upload_res = client.post(
        f"/entities/{entity_id}/upload",
        files={"file": ("violating_export.xml", violating_xml.encode("utf-8"), "text/xml")},
        headers=headers
    )
    assert upload_res.status_code == 200

    # 4. Trigger Scrutiny
    run_res = client.post(f"/entities/{entity_id}/scrutiny-run?period_start=2025-04-01&period_end=2026-03-31", headers=headers)
    assert run_res.status_code == 200
    summary = run_res.json()
    assert summary["status"] == "success"
    assert summary["exceptions_count"] == 4

    # 5. Query exceptions filterable by severity
    list_res = client.get(f"/entities/{entity_id}/exceptions", params={"severity": "error", "period_start": "2025-04-01", "period_end": "2026-03-31"}, headers=headers)
    assert list_res.status_code == 200
    exceptions = list_res.json()
    assert len(exceptions) == 4

    exc_accounts = {e["ledger_account_name"]: e for e in exceptions if e["ledger_account_name"]}
    assert "Cash-in-hand" in exc_accounts
    assert "Owner Capital" in exc_accounts

    cash_excs = [e for e in exceptions if e["ledger_account_name"] == "Cash-in-hand"]
    assert len(cash_excs) == 2  # normal_balance_check and negative_cash_balance

    cash_normal_exc = next(e for e in cash_excs if e["rule_name"] == "normal_balance_check")
    assert cash_normal_exc["severity"] == "error"
    assert "credit closing balance of 70000" in cash_normal_exc["message"]

    cash_neg_exc = next(e for e in cash_excs if e["rule_name"] == "negative_cash_balance")
    assert cash_neg_exc["severity"] == "error"
    assert "Cash balance cannot be negative" in cash_neg_exc["message"]

    capital_exc = exc_accounts["Owner Capital"]
    assert capital_exc["rule_name"] == "normal_balance_check"
    assert capital_exc["severity"] == "error"
    assert "debit closing balance of 80000" in capital_exc["message"]

    list_res_warn = client.get(f"/entities/{entity_id}/exceptions", params={"severity": "warning"}, headers=headers)
    assert list_res_warn.status_code == 200
    assert len(list_res_warn.json()) == 0


def test_api_exception_review_workflow():
    headers = get_auth_headers()

    # 1. Create Entity
    entity_payload = {
        "name": "Acme Workflow Corp",
        "materiality_threshold": "1000.00"
    }
    create_res = client.post("/entities", json=entity_payload, headers=headers)
    assert create_res.status_code == 201
    entity_id = create_res.json()["id"]

    # 2. Upload Violating XML
    violating_xml = """<ENVELOPE>
      <BODY>
        <IMPORTDATA>
          <REQUESTDESC>
            <REPORTNAME>All Ledger Entries</REPORTNAME>
          </REQUESTDESC>
          <REQUESTDATA>
            <COMPANY>
              <RENAME>Acme Workflow Corp</RENAME>
              <BOOKSFROM>20250401</BOOKSFROM>
              <BOOKSTO>20260331</BOOKSTO>
            </COMPANY>
            <TALLYMESSAGE>
              <LEDGER NAME="Cash-in-hand">
                <PARENT>Cash-in-hand</PARENT>
                <OPENINGBALANCE>10000.00</OPENINGBALANCE>
              </LEDGER>
            </TALLYMESSAGE>
            <TALLYMESSAGE>
              <LEDGER NAME="Owner Capital">
                <PARENT>Capital Account</PARENT>
                <OPENINGBALANCE>0.00</OPENINGBALANCE>
              </LEDGER>
            </TALLYMESSAGE>
            <TALLYMESSAGE>
              <VOUCHER VCHTYPE="Payment">
                <DATE>20250410</DATE>
                <VOUCHERNUMBER>VCH-0003</VOUCHERNUMBER>
                <NARRATION>Excess drawing by owner</NARRATION>
                <ALLLEDGERENTRIES.LIST>
                  <LEDGERNAME>Owner Capital</LEDGERNAME>
                  <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
                  <AMOUNT>-80000.00</AMOUNT>
                </ALLLEDGERENTRIES.LIST>
                <ALLLEDGERENTRIES.LIST>
                  <LEDGERNAME>Cash-in-hand</LEDGERNAME>
                  <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
                  <AMOUNT>80000.00</AMOUNT>
                </ALLLEDGERENTRIES.LIST>
              </VOUCHER>
            </TALLYMESSAGE>
          </REQUESTDATA>
        </IMPORTDATA>
      </BODY>
    </ENVELOPE>
    """
    upload_res = client.post(
        f"/entities/{entity_id}/upload",
        files={"file": ("violating_export.xml", violating_xml.encode("utf-8"), "text/xml")},
        headers=headers
    )
    assert upload_res.status_code == 200

    # 3. Trigger Scrutiny Run
    run_res = client.post(f"/entities/{entity_id}/scrutiny-run?period_start=2025-04-01&period_end=2026-03-31", headers=headers)
    assert run_res.status_code == 200
    assert run_res.json()["exceptions_count"] == 4

    # 4. List exceptions and verify default status and empty notes
    list_res = client.get(f"/entities/{entity_id}/exceptions", params={"period_start": "2025-04-01", "period_end": "2026-03-31"}, headers=headers)
    assert list_res.status_code == 200
    exceptions = list_res.json()
    assert len(exceptions) == 4

    cash_exc = next(e for e in exceptions if e["ledger_account_name"] == "Cash-in-hand" and e["rule_name"] == "normal_balance_check")
    assert cash_exc["status"] == "PENDING"
    assert cash_exc["auditor_notes"] is None

    capital_exc = next(e for e in exceptions if e["ledger_account_name"] == "Owner Capital")
    assert capital_exc["status"] == "PENDING"
    assert capital_exc["auditor_notes"] is None

    # 5. Update Cash Exception to CLEARED with notes
    patch_payload = {
        "status": "CLEARED",
        "auditor_notes": "Verified drawing, approved by board of directors."
    }
    patch_res = client.patch(
        f"/entities/{entity_id}/exceptions/{cash_exc['id']}",
        json=patch_payload,
        headers=headers
    )
    assert patch_res.status_code == 200
    updated_cash_exc = patch_res.json()
    assert updated_cash_exc["status"] == "CLEARED"
    assert updated_cash_exc["auditor_notes"] == "Verified drawing, approved by board of directors."

    # 6. Re-run Scrutiny Run
    run_res_2 = client.post(f"/entities/{entity_id}/scrutiny-run?period_start=2025-04-01&period_end=2026-03-31", headers=headers)
    assert run_res_2.status_code == 200
    assert run_res_2.json()["exceptions_count"] == 4

    # 7. List exceptions and verify status/notes are preserved
    list_res_2 = client.get(f"/entities/{entity_id}/exceptions", params={"period_start": "2025-04-01", "period_end": "2026-03-31"}, headers=headers)
    assert list_res_2.status_code == 200
    exceptions_2 = list_res_2.json()

    cash_exc_2 = next(e for e in exceptions_2 if e["ledger_account_name"] == "Cash-in-hand")
    assert cash_exc_2["status"] == "CLEARED"
    assert cash_exc_2["auditor_notes"] == "Verified drawing, approved by board of directors."

    capital_exc_2 = next(e for e in exceptions_2 if e["ledger_account_name"] == "Owner Capital")
    assert capital_exc_2["status"] == "PENDING"
    assert capital_exc_2["auditor_notes"] is None


def test_api_entity_delete():
    headers = get_auth_headers()

    # 1. Create an Entity
    res = client.post("/entities", json={"name": "Delete Me Inc", "materiality_threshold": "10000.00"}, headers=headers)
    assert res.status_code == 201
    entity_id = res.json()["id"]

    # 2. Verify it is listed
    list_res = client.get("/entities", headers=headers)
    assert any(e["id"] == entity_id for e in list_res.json())

    # 3. Delete it
    delete_res = client.delete(f"/entities/{entity_id}", headers=headers)
    assert delete_res.status_code == 204

    # 4. Verify it is no longer listed
    list_res_after = client.get("/entities", headers=headers)
    assert not any(e["id"] == entity_id for e in list_res_after.json())


def test_api_preserve_notes_multiple_exceptions_for_same_account(monkeypatch):
    headers = get_auth_headers()

    # Create entity
    res = client.post("/entities", json={"name": "Preserve Test Inc", "materiality_threshold": "0.0"}, headers=headers)
    assert res.status_code == 201
    entity_id = res.json()["id"]

    import io
    import json
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Ledger Name", "Group Name", "Opening Balance", "Closing Balance"])
    ws.append(["Cash", "Cash-in-hand", "1000.00", "1000.00"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    column_mapping = json.dumps({
        "ledger_name": "Ledger Name",
        "group_name": "Group Name",
        "opening_balance": "Opening Balance",
        "closing_balance": "Closing Balance"
    })
    upload_res = client.post(
        f"/entities/{entity_id}/upload-xlsx/confirm",
        data={
            "column_mapping": column_mapping,
            "sign_convention": "negative_is_credit",
            "target_period_start": "2025-04-01",
            "target_period_end": "2026-03-31"
        },
        files={"file": ("dummy.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers
    )
    assert upload_res.status_code == 200

    from app.db.models import AuditException

    def mock_run_scrutiny(entity, accounts, snapshots, period_start, period_end, rule_pack=None):
        del snapshots, period_start, period_end, rule_pack
        cash_acc = next(a for a in accounts if a.name == "Cash")
        return [
            AuditException(
                entity_id=entity.id,
                rule_name="rule_a",
                ledger_account_id=cash_acc.id,
                severity="error",
                message="First exception message"
            ),
            AuditException(
                entity_id=entity.id,
                rule_name="rule_a",
                ledger_account_id=cash_acc.id,
                severity="error",
                message="Second exception message"
            )
        ]

    monkeypatch.setattr("app.routers.scrutiny.run_scrutiny", mock_run_scrutiny)

    # Trigger scrutiny run
    run_res = client.post(f"/entities/{entity_id}/scrutiny-run?period_start=2025-04-01&period_end=2026-03-31", headers=headers)
    assert run_res.status_code == 200
    assert run_res.json()["exceptions_count"] == 2

    # Get exceptions list
    list_res = client.get(f"/entities/{entity_id}/exceptions", params={"period_start": "2025-04-01", "period_end": "2026-03-31"}, headers=headers)
    exceptions = list_res.json()
    assert len(exceptions) == 2

    exc_1 = next(e for e in exceptions if e["message"] == "First exception message")

    # Update exception 1 to CLEARED with notes
    patch_res = client.patch(
        f"/entities/{entity_id}/exceptions/{exc_1['id']}",
        json={"status": "CLEARED", "auditor_notes": "Notes for first exception"},
        headers=headers
    )
    assert patch_res.status_code == 200

    # Re-run scrutiny run to verify status preservation
    run_res_2 = client.post(f"/entities/{entity_id}/scrutiny-run?period_start=2025-04-01&period_end=2026-03-31", headers=headers)
    assert run_res_2.status_code == 200

    # List exceptions again and verify exception 1 is CLEARED (with notes), and exception 2 is still PENDING
    list_res_2 = client.get(f"/entities/{entity_id}/exceptions", params={"period_start": "2025-04-01", "period_end": "2026-03-31"}, headers=headers)
    exceptions_2 = list_res_2.json()

    exc_1_after = next(e for e in exceptions_2 if e["message"] == "First exception message")
    exc_2_after = next(e for e in exceptions_2 if e["message"] == "Second exception message")

    assert exc_1_after["status"] == "CLEARED"
    assert exc_1_after["auditor_notes"] == "Notes for first exception"
    assert exc_2_after["status"] == "PENDING"
    assert exc_2_after["auditor_notes"] is None
