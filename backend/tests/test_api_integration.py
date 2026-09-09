import os
import io
import base64
import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path
from decimal import Decimal
from datetime import date
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
import openpyxl

from app.main import app
from app.routers import scrutiny as scrutiny_router
from app.db.models import (
    AuditException,
    BalanceCheckpoint,
    FinancialPeriod,
    ImportBatch,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    ScrutinyRun,
    Transaction,
    TrialBalanceSnapshot,
)
from app.ingestion import baseline as baseline_module
from app.ingestion.datasets import resolve_active_dataset
from app.ingestion.tally_parser import TallyConnectorError, parse_tally_xml
from app.ingestion.xlsx_normalizer import validate_gl_xlsx_replay
from conftest import TestingSessionLocal

client = TestClient(app)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].upper()


def _canonical_tally_xml(contents: bytes, *, add_voucher_if_missing: bool = False) -> bytes:
    """Make legacy test exports satisfy the strict canonical Tally contract."""
    root = ET.fromstring(contents)
    ledgers = [node for node in root.iter() if _local_name(node.tag) == "LEDGER"]
    ledger_names = {node.attrib.get("NAME") or next(
        (child.text.strip() for child in node if _local_name(child.tag) == "NAME" and child.text), ""
    ) for node in ledgers}
    movements = {name: Decimal("0") for name in ledger_names}
    vouchers = [node for node in root.iter() if _local_name(node.tag) == "VOUCHER"]
    for voucher in vouchers:
        for entry in voucher.iter():
            if _local_name(entry.tag) not in {"ALLLEDGERENTRIES.LIST", "LEDGERENTRIES.LIST"}:
                continue
            ledger_name = next(
                (child.text.strip() for child in entry if _local_name(child.tag) in {"LEDGERNAME", "LEDGER"} and child.text),
                "",
            )
            amount_text = next(
                (child.text.strip() for child in entry if _local_name(child.tag) == "AMOUNT" and child.text),
                "0",
            )
            amount = abs(Decimal(amount_text.replace(",", "")))
            deemed = next(
                (child.text.strip().lower() for child in entry if _local_name(child.tag) == "ISDEEMEDPOSITIVE" and child.text),
                "yes" if Decimal(amount_text) >= 0 else "no",
            )
            movements[ledger_name] = movements.get(ledger_name, Decimal("0")) + (amount if deemed in {"yes", "y", "true", "1"} else -amount)

    opening_by_name: dict[str, Decimal] = {}
    closing_by_name: dict[str, Decimal] = {}
    for ledger in ledgers:
        name = ledger.attrib.get("NAME") or next(
            (child.text.strip() for child in ledger if _local_name(child.tag) == "NAME" and child.text), ""
        )
        opening = Decimal(next(
            (child.text.strip() for child in ledger if _local_name(child.tag) == "OPENINGBALANCE" and child.text), "0"
        ).replace(",", ""))
        opening_by_name[name] = opening
        closing_node = next((child for child in ledger if _local_name(child.tag) == "CLOSINGBALANCE"), None)
        if closing_node is None:
            closing = opening + movements.get(name, Decimal("0"))
            closing_node = ET.SubElement(ledger, "CLOSINGBALANCE")
            closing_node.text = format(closing, "f")
        else:
            closing = Decimal((closing_node.text or "0").replace(",", ""))
        closing_by_name[name] = closing

    if add_voucher_if_missing and not vouchers:
        request_data = next((node for node in root.iter() if _local_name(node.tag) == "REQUESTDATA"), root)
        message = ET.SubElement(request_data, "TALLYMESSAGE")
        voucher = ET.SubElement(message, "VOUCHER", {"VCHTYPE": "Journal"})
        ET.SubElement(voucher, "DATE").text = next(
            (child.text.strip() for node in root.iter() if _local_name(node.tag) in {"COMPANY", "STATICVARIABLES"}
             for child in node if _local_name(child.tag) in {"BOOKSFROM", "SVFROMDATE"} and child.text),
            "20250401",
        )
        ET.SubElement(voucher, "VOUCHERNUMBER").text = "compatibility-balancing-entry"
        ET.SubElement(voucher, "NARRATION").text = "Compatibility fixture balancing entry"
        deltas = {name: closing_by_name[name] - opening_by_name[name] for name in ledger_names}
        imbalance = sum(deltas.values(), Decimal("0"))
        if imbalance:
            first_name = next(iter(ledger_names), None)
            if first_name is not None:
                deltas[first_name] -= imbalance
        has_entries = False
        for name, delta in deltas.items():
            if not delta:
                continue
            has_entries = True
            entry = ET.SubElement(voucher, "ALLLEDGERENTRIES.LIST")
            ET.SubElement(entry, "LEDGERNAME").text = name
            ET.SubElement(entry, "ISDEEMEDPOSITIVE").text = "Yes" if delta > 0 else "No"
            ET.SubElement(entry, "AMOUNT").text = format(abs(delta), "f")
        if not has_entries:
            name = next(iter(ledger_names), "compatibility-zero")
            entry = ET.SubElement(voucher, "ALLLEDGERENTRIES.LIST")
            ET.SubElement(entry, "LEDGERNAME").text = name
            ET.SubElement(entry, "ISDEEMEDPOSITIVE").text = "Yes"
            ET.SubElement(entry, "AMOUNT").text = "0"

    return ET.tostring(root, encoding="utf-8")


def _strict_fixture_bytes(filename: str, *, add_voucher_if_missing: bool = False) -> bytes:
    path = Path(__file__).with_name(filename)
    return _canonical_tally_xml(path.read_bytes(), add_voucher_if_missing=add_voucher_if_missing)


def _gl_bytes() -> bytes:
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.append(["Document Number", "G/L Account", "Posting Date", "Amount in local currency"])
    worksheet.append(["GL-1", "1000", date(2025, 4, 1), 100])
    worksheet.append(["GL-1", "2000", date(2025, 4, 1), -100])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _baseline_bytes(first_balance: int = 50, second_balance: int | None = None) -> bytes:
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.append(["Account Code", "Balance Date", "Signed Balance", "Currency"])
    worksheet.append(["1000", date(2025, 3, 31), first_balance, "INR"])
    worksheet.append(["2000", date(2025, 3, 31), -first_balance if second_balance is None else second_balance, "INR"])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _upload_public_baseline(entity_id: int, headers: dict[str, str], contents: bytes, *, period_start: str = "2025-04-01", period_end: str = "2026-03-31", balance_date: str = "2025-03-31"):
    return client.post(
        f"/entities/{entity_id}/upload-xlsx/baseline",
        data={
            "target_period_start": period_start,
            "target_period_end": period_end,
            "expected_currency": "INR",
            "expected_balance_date": balance_date,
        },
        files={
            "file": (
                "opening-baseline.xlsx",
                io.BytesIO(contents),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
        },
        headers=headers,
    )


def test_public_upload_limits_reject_before_batch_staging(monkeypatch):
    monkeypatch.setattr(scrutiny_router, "MAX_UPLOAD_BYTES", 4)
    monkeypatch.setattr(scrutiny_router, "MAX_SIGNED_PDF_BYTES", 3)
    headers = get_auth_headers()
    entity = client.post(
        "/entities",
        json={"name": "Upload Limit Entity", "materiality_threshold": "0.00"},
        headers=dict(headers),
    ).json()

    xml_response = client.post(
        f"/entities/{entity['id']}/upload",
        files={"file": ("oversized.xml", b"12345", "text/xml")},
        headers=dict(headers),
    )
    assert xml_response.status_code == 413

    xlsx_response = client.post(
        f"/entities/{entity['id']}/upload-xlsx/confirm",
        data={
            "column_mapping": "{}",
            "target_period_start": "2025-04-01",
            "target_period_end": "2026-03-31",
        },
        files={"file": ("oversized.xlsx", b"12345", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=dict(headers),
    )
    assert xlsx_response.status_code == 413

    pdf_headers = get_auth_headers()
    pdf_entity = client.post(
        "/entities",
        json={"name": "PDF Upload Limit Entity", "materiality_threshold": "0.00"},
        headers=pdf_headers,
    ).json()
    pdf_response = client.post(
        f"/entities/{pdf_entity['id']}/upload-xlsx/baseline",
        data={
            "target_period_start": "2025-04-01",
            "target_period_end": "2026-03-31",
            "expected_currency": "INR",
            "expected_balance_date": "2025-03-31",
        },
        files={
            "file": ("baseline.xlsx", b"123", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "signed_pdf": ("evidence.pdf", b"1234", "application/pdf"),
        },
        headers=dict(pdf_headers),
    )
    assert pdf_response.status_code == 413

    with TestingSessionLocal() as session:
        assert session.scalar(
            select(func.count()).select_from(ImportBatch).where(ImportBatch.entity_id == entity["id"])
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(ImportBatch).where(ImportBatch.entity_id == pdf_entity["id"])
        ) == 0


def _model_snapshot(session, model, *criteria):
    return [
        tuple(getattr(row, column.name) for column in model.__table__.columns)
        for row in session.scalars(select(model).where(*criteria).order_by(model.id)).all()
    ]


def _batch_child_snapshot(session, batch_id):
    entry_ids = select(JournalEntry.id).where(JournalEntry.import_batch_id == batch_id)
    return {
        "journal_entries": _model_snapshot(session, JournalEntry, JournalEntry.import_batch_id == batch_id),
        "journal_lines": _model_snapshot(session, JournalLine, JournalLine.journal_entry_id.in_(entry_ids)),
        "balance_checkpoints": _model_snapshot(
            session, BalanceCheckpoint, BalanceCheckpoint.import_batch_id == batch_id
        ),
        "transactions": _model_snapshot(session, Transaction, Transaction.import_batch_id == batch_id),
        "trial_balance_snapshots": _model_snapshot(
            session, TrialBalanceSnapshot, TrialBalanceSnapshot.import_batch_id == batch_id
        ),
    }


def get_auth_headers():
    res = client.post("/auth/register", json={
        "organization_name": "Integration Test Firm",
        "email": f"test.{os.urandom(4).hex()}@integration.com",
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


@pytest.mark.parametrize("pack, sector, expected", [
    ("compliance_v1", None, 201),
    (None, None, 201),
    ("manufacturing_v1", "manufacturing", 201),
    ("manufacturing_v1", None, 400),
    ("unknown", None, 400),
])
def test_entity_rule_pack_contract(pack, sector, expected):
    response = client.post("/entities", headers=get_auth_headers(), json={
        "name": "Pack contract", "materiality_threshold": "100.00",
        "rule_pack": pack, "sector": sector,
    })
    assert response.status_code == expected, response.text
    if expected == 201:
        assert response.json()["rule_pack"] == pack


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
        contents = _canonical_tally_xml((fixture_dir / filename).read_bytes(), add_voucher_if_missing=True)
        response = client.post(
            f"/entities/{entity['id']}/upload",
            params={"clear_only_period": "true", "target_period_start": start, "target_period_end": end},
            files={"file": (filename, contents, "text/xml")},
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
    with TestingSessionLocal() as session:
        stored_run = session.get(ScrutinyRun, run.json()["scrutiny_run_id"])
        assert stored_run.rule_set_version == "core-v1+compliance-v1+manufacturing-v1"
        stored_findings = session.scalars(select(AuditException).where(
            AuditException.scrutiny_run_id == stored_run.id)).all()
        assert all(finding.rule_version == stored_run.rule_set_version
                   for finding in stored_findings)

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


def test_compliance_versions_and_review_survive_rerun():
    headers = get_auth_headers()
    created = client.post("/entities", headers=headers, json={
        "name": "Compliance history", "materiality_threshold": "100.00",
        "rule_pack": "compliance_v1",
    })
    assert created.status_code == 201, created.text
    entity_id = created.json()["id"]
    fixture = (Path(__file__).parents[2] / "sample_data" /
               "pitch_manufacturing_demo" / "meridian_fy2025_26.xml")
    contents = _canonical_tally_xml(fixture.read_bytes(), add_voucher_if_missing=True)
    upload = client.post(f"/entities/{entity_id}/upload", headers=headers,
                         files={"file": ("ready.xml",
                                contents,
                                "text/xml")})
    assert upload.status_code == 200, upload.text
    params = {"period_start": "2025-04-01", "period_end": "2026-03-31"}
    run_ids = []
    fingerprint = None
    for attempt in range(2):
        response = client.post(f"/entities/{entity_id}/scrutiny-run",
                               params=params, headers=headers)
        assert response.status_code == 200, response.text
        run_id = response.json()["scrutiny_run_id"]
        run_ids.append(run_id)
        with TestingSessionLocal() as session:
            run = session.get(ScrutinyRun, run_id)
            assert run.rule_set_version == "core-v1+compliance-v1"
            assert run.dataset_fingerprint == upload.json()["dataset_fingerprint"]
            assert run.source_batch_ids == upload.json()["source_batch_ids"]
            rows = session.scalars(select(AuditException).where(
                AuditException.scrutiny_run_id == run_id)).all()
            assert rows
            assert all(row.rule_version == run.rule_set_version for row in rows)
            tds_rows = [row for row in rows if row.rule_name == "tds_liability_check"]
            assert len(tds_rows) == 1
            finding = tds_rows[0]
            assert finding.fingerprint
            finding_id = finding.id
            if attempt == 0:
                fingerprint = finding.fingerprint
            else:
                assert finding.fingerprint == fingerprint
                assert finding.status == "CLEARED"
                assert finding.auditor_notes == "TDS evidence reviewed."
        if attempt == 0:
            reviewed = client.patch(f"/entities/{entity_id}/exceptions/{finding_id}", headers=headers,
                                    json={"status": "CLEARED",
                                          "auditor_notes": "TDS evidence reviewed."})
            assert reviewed.status_code == 200, reviewed.text
    assert run_ids[0] != run_ids[1]
    with TestingSessionLocal() as session:
        assert session.get(ScrutinyRun, run_ids[0]) is not None
        previous = session.scalars(select(AuditException).where(
            AuditException.scrutiny_run_id == run_ids[0],
            AuditException.rule_name == "tds_liability_check")).one()
        assert previous.status == "CLEARED"
        assert previous.auditor_notes == "TDS evidence reviewed."


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
    contents = _strict_fixture_bytes("sample_tally_export.xml")
    upload_res = client.post(
        f"/entities/{entity_id}/upload",
        files={"file": ("sample_tally_export.xml", contents, "text/xml")},
        headers=headers
    )

    assert upload_res.status_code == 200
    res_data = upload_res.json()
    assert res_data["message"] == "Ingestion successful"
    assert res_data["entity_id"] == entity_id
    assert res_data["status"] == "ACTIVE"
    assert res_data["readiness"] == "READY"
    assert res_data["validation_report"]["document_count"] == 2
    assert res_data["active_batch_ids"] == [res_data["import_batch_id"]]
    assert res_data["source_batch_ids"] == [res_data["import_batch_id"]]
    assert res_data["dataset_fingerprint"]

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
    contents = _strict_fixture_bytes("sample_tally_export.xml")

    first = client.post(
        f"/entities/{entity_id}/upload",
        files={"file": ("sample_tally_export.xml", contents, "text/xml")},
        headers=headers,
    )
    duplicate = client.post(
        f"/entities/{entity_id}/upload",
        files={"file": ("sample_tally_export.xml", contents, "text/xml")},
        headers=headers,
    )

    assert first.status_code == 200, first.text
    assert duplicate.status_code == 200, duplicate.text
    first_body = first.json()
    batch_id = first_body["import_batch_id"]
    assert duplicate.json()["import_batch_id"] == batch_id
    assert duplicate.json()["dataset_fingerprint"] == first_body["dataset_fingerprint"]
    assert duplicate.json()["source_batch_ids"] == first_body["source_batch_ids"]
    with TestingSessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(JournalEntry).where(JournalEntry.import_batch_id == batch_id)) == 2
        assert session.scalar(select(func.count()).select_from(JournalLine).join(JournalEntry).where(JournalEntry.import_batch_id == batch_id)) == 4
        assert session.scalar(select(func.count()).select_from(BalanceCheckpoint).where(BalanceCheckpoint.import_batch_id == batch_id)) == 10
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


@pytest.mark.parametrize("mutation", ["missing", "altered"])
def test_tally_duplicate_replay_rejects_corrupt_canonical_children(mutation):
    headers = get_auth_headers()
    entity = client.post(
        "/entities", json={"name": f"Tally Replay {mutation}", "materiality_threshold": "0.00"}, headers=headers
    ).json()
    contents = _strict_fixture_bytes("sample_tally_export.xml")
    first = client.post(
        f"/entities/{entity['id']}/upload",
        files={"file": ("sample_tally_export.xml", contents, "text/xml")},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    batch_id = first_body["import_batch_id"]

    with TestingSessionLocal() as session:
        line = session.scalars(
            select(JournalLine).join(JournalEntry).where(JournalEntry.import_batch_id == batch_id)
        ).first()
        assert line is not None
        if mutation == "missing":
            session.delete(line)
        else:
            line.amount = Decimal("999.00")
        session.flush()
        corrupted_snapshot = _batch_child_snapshot(session, batch_id)
        session.commit()

    duplicate = client.post(
        f"/entities/{entity['id']}/upload",
        files={"file": ("sample_tally_export.xml", contents, "text/xml")},
        headers=headers,
    )

    assert duplicate.status_code == 400, duplicate.text
    detail = duplicate.json()["detail"]
    assert detail["failed_batch_id"] == batch_id
    assert detail["failed_batch_status"] == "ACTIVE"
    assert detail["status"] == "ACTIVE"
    assert detail["validation_report"]["errors"]
    assert detail["active_batch_ids"] == [batch_id]
    assert detail["source_batch_ids"] == [batch_id]
    assert detail["dataset_fingerprint"]
    with TestingSessionLocal() as session:
        active_batch = session.get(ImportBatch, batch_id)
        assert active_batch.status == "ACTIVE"
        assert active_batch.validation_report == first_body["validation_report"]
        assert _batch_child_snapshot(session, batch_id) == corrupted_snapshot
        assert session.scalar(
            select(func.count()).select_from(JournalLine).join(JournalEntry).where(JournalEntry.import_batch_id == batch_id)
        ) == (3 if mutation == "missing" else 4)


def test_tally_duplicate_replay_with_no_children_rejects_without_repopulate():
    headers = get_auth_headers()
    entity = client.post(
        "/entities", json={"name": "Empty Tally Replay", "materiality_threshold": "0.00"}, headers=headers
    ).json()
    contents = _strict_fixture_bytes("sample_tally_export.xml")
    first = client.post(
        f"/entities/{entity['id']}/upload",
        files={"file": ("sample_tally_export.xml", contents, "text/xml")},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    batch_id = first.json()["import_batch_id"]

    with TestingSessionLocal() as session:
        batch = session.get(ImportBatch, batch_id)
        assert batch is not None
        for entry in list(batch.journal_entries):
            session.delete(entry)
        for checkpoint in list(batch.balance_checkpoints):
            session.delete(checkpoint)
        for transaction in list(batch.transactions):
            session.delete(transaction)
        for snapshot in list(batch.snapshots):
            session.delete(snapshot)
        session.commit()

    duplicate = client.post(
        f"/entities/{entity['id']}/upload",
        files={"file": ("sample_tally_export.xml", contents, "text/xml")},
        headers=headers,
    )

    assert duplicate.status_code == 400, duplicate.text
    detail = duplicate.json()["detail"]
    assert detail["failed_batch_id"] == batch_id
    assert detail["failed_batch_status"] == "ACTIVE"
    assert detail["active_batch_ids"] == [batch_id]
    assert detail["source_batch_ids"] == [batch_id]
    with TestingSessionLocal() as session:
        assert session.get(ImportBatch, batch_id).status == "ACTIVE"
        assert session.scalar(
            select(func.count()).select_from(JournalEntry).where(JournalEntry.import_batch_id == batch_id)
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(BalanceCheckpoint).where(BalanceCheckpoint.import_batch_id == batch_id)
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(Transaction).where(Transaction.import_batch_id == batch_id)
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(TrialBalanceSnapshot).where(
                TrialBalanceSnapshot.import_batch_id == batch_id
            )
        ) == 0


def test_tally_duplicate_replay_rejects_foreign_account_child_without_mutating_active_data():
    headers = get_auth_headers()
    entity = client.post(
        "/entities", json={"name": "Tally Foreign Child Replay", "materiality_threshold": "0.00"}, headers=headers
    ).json()
    foreign_entity = client.post(
        "/entities", json={"name": "Tally Foreign Owner", "materiality_threshold": "0.00"}, headers=headers
    ).json()
    contents = _strict_fixture_bytes("sample_tally_export.xml")
    first = client.post(
        f"/entities/{entity['id']}/upload",
        files={"file": ("sample_tally_export.xml", contents, "text/xml")},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    batch_id = first.json()["import_batch_id"]

    with TestingSessionLocal() as session:
        foreign_account = LedgerAccount(entity_id=foreign_entity["id"], name="Foreign Replay Account")
        session.add(foreign_account)
        session.flush()
        line = session.scalars(
            select(JournalLine).join(JournalEntry).where(JournalEntry.import_batch_id == batch_id)
        ).first()
        assert line is not None
        line.ledger_account_id = foreign_account.id
        session.commit()
        foreign_account_id = foreign_account.id

    duplicate = client.post(
        f"/entities/{entity['id']}/upload",
        files={"file": ("sample_tally_export.xml", contents, "text/xml")},
        headers=headers,
    )

    assert duplicate.status_code == 400, duplicate.text
    detail = duplicate.json()["detail"]
    assert detail["failed_batch_id"] == batch_id
    assert detail["failed_batch_status"] == "ACTIVE"
    assert detail["status"] == "ACTIVE"
    assert detail["active_batch_ids"] == [batch_id]
    assert detail["validation_report"]["errors"]
    with TestingSessionLocal() as session:
        assert session.get(ImportBatch, batch_id).status == "ACTIVE"
        assert session.scalar(select(func.count()).select_from(ImportBatch).where(ImportBatch.entity_id == entity["id"])) == 1
        persisted_line = session.scalars(
            select(JournalLine).join(JournalEntry).where(JournalEntry.import_batch_id == batch_id)
        ).first()
        assert persisted_line is not None
        assert persisted_line.ledger_account_id == foreign_account_id


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
    contents = _strict_fixture_bytes("sample_tally_export.xml")

    first = client.post(
        f"/entities/{entity_id}/upload",
        files={"file": ("sample_tally_export.xml", contents, "text/xml")},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    batch_id = first.json()["import_batch_id"]
    with TestingSessionLocal() as session:
        batch = session.get(ImportBatch, batch_id)
        batch.status = "FAILED"
        session.commit()

    duplicate = client.post(
        f"/entities/{entity_id}/upload",
        files={"file": ("sample_tally_export.xml", contents, "text/xml")},
        headers=headers,
    )

    assert duplicate.status_code == 400
    detail = duplicate.json()["detail"]
    assert detail["failed_batch_status"] == "FAILED"
    assert detail["validation_report"]["errors"]
    assert detail["active_batch_ids"] == []


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
        files={"file": ("violating_export.xml", _canonical_tally_xml(violating_xml.encode("utf-8")), "text/xml")},
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
        files={"file": ("violating_export.xml", _canonical_tally_xml(violating_xml.encode("utf-8")), "text/xml")},
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

    contents = _strict_fixture_bytes("sample_tally_export.xml")
    upload_res = client.post(
        f"/entities/{entity_id}/upload",
        files={"file": ("sample_tally_export.xml", contents, "text/xml")},
        headers=headers
    )
    assert upload_res.status_code == 200

    from app.db.models import AuditException

    def mock_run_scrutiny(entity, accounts, snapshots, period_start, period_end, rule_pack=None):
        del snapshots, period_start, period_end, rule_pack
        cash_acc = next(a for a in accounts if a.name == "Cash-in-hand")
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


def test_api_rejects_legacy_trial_balance_mapping_before_scrutiny():
    headers = get_auth_headers()

    res = client.post("/entities", json={"name": "Legacy Mapping Entity", "materiality_threshold": "0.0"}, headers=headers)
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
    assert upload_res.status_code == 409
    detail = upload_res.json()["detail"]
    assert detail["message"] == "Only the fixed canonical GL profile is supported for XLSX ingestion."
    assert detail["readiness"] == "INVALID"
    assert detail["errors"]
    assert "fixed canonical GL" in detail["errors"][0]

    scrutiny = client.post(
        f"/entities/{entity_id}/scrutiny-run",
        params={"period_start": "2025-04-01", "period_end": "2026-03-31"},
        headers=headers,
    )
    assert scrutiny.status_code == 409
    assert scrutiny.json()["detail"]["readiness"] == "PARTIAL"


def test_api_rejects_xls_for_xlsx_only_upload():
    headers = get_auth_headers()
    entity = client.post(
        "/entities", json={"name": "XLS Rejection Entity", "materiality_threshold": "0.00"}, headers=headers
    ).json()

    response = client.post(
        f"/entities/{entity['id']}/upload-xlsx/confirm",
        data={"column_mapping": "{}", "target_period_start": "2025-04-01", "target_period_end": "2026-03-31"},
        files={"file": ("legacy.xls", io.BytesIO(b"not an xlsx"), "application/vnd.ms-excel")},
        headers=headers,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Only XLSX files are supported."


def test_scrutiny_rejects_legacy_snapshot_only_active_batch():
    headers = get_auth_headers()
    entity = client.post(
        "/entities", json={"name": "Legacy Snapshot Entity", "materiality_threshold": "0.00"}, headers=headers
    ).json()
    with TestingSessionLocal() as session:
        period = FinancialPeriod(
            entity_id=entity["id"],
            period_start=date(2025, 4, 1),
            period_end=date(2026, 3, 31),
            source="xlsx_trial_balance",
        )
        account = LedgerAccount(entity_id=entity["id"], name="Legacy Cash")
        session.add_all([period, account])
        session.flush()
        batch = ImportBatch(
            entity_id=entity["id"],
            financial_period_id=period.id,
            source="xlsx_trial_balance",
            source_family="gl_upload",
            original_filename="legacy.xlsx",
            content_sha256="legacy-snapshot-only",
            parser_version="1",
            status="ACTIVE",
            raw_source_bytes=b"legacy",
            coverage_start=date(2025, 4, 1),
            coverage_end=date(2026, 3, 31),
            source_metadata={},
            validation_report={"parser": "xlsx_trial_balance", "coverage_complete": True},
        )
        session.add(batch)
        session.flush()
        batch_id = batch.id
        session.add(TrialBalanceSnapshot(
            import_batch_id=batch.id,
            entity_id=entity["id"],
            ledger_account_id=account.id,
            period_start=date(2025, 4, 1),
            period_end=date(2026, 3, 31),
            opening_balance=Decimal("0"),
            total_debits=Decimal("100"),
            total_credits=Decimal("0"),
            closing_balance=Decimal("100"),
        ))
        session.commit()

    response = client.post(
        f"/entities/{entity['id']}/scrutiny-run",
        params={"period_start": "2025-04-01", "period_end": "2026-03-31"},
        headers=headers,
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["readiness"] == "INVALID"
    assert "legacy trial-balance snapshots" in " ".join(detail["errors"])
    assert detail["active_batch_ids"] == [batch_id]


@pytest.mark.parametrize("mutation", ["missing", "altered"])
def test_fixed_gl_duplicate_replay_validates_canonical_children(mutation):
    headers = get_auth_headers()
    entity = client.post(
        "/entities", json={"name": f"GL Replay {mutation}", "materiality_threshold": "0.00"}, headers=headers
    ).json()
    contents = _gl_bytes()
    first = client.post(
        f"/entities/{entity['id']}/upload-xlsx/confirm",
        data={"column_mapping": "{}", "target_period_start": "2025-04-01", "target_period_end": "2026-03-31"},
        files={"file": ("replay.xlsx", io.BytesIO(contents), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    batch_id = first.json()["import_batch_id"]
    valid_duplicate = client.post(
        f"/entities/{entity['id']}/upload-xlsx/confirm",
        data={"column_mapping": "{}", "target_period_start": "2025-04-01", "target_period_end": "2026-03-31"},
        files={"file": ("replay.xlsx", io.BytesIO(contents), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )
    assert valid_duplicate.status_code == 200, valid_duplicate.text
    assert valid_duplicate.json()["import_batch_id"] == batch_id
    assert valid_duplicate.json()["dataset_fingerprint"] == first.json()["dataset_fingerprint"]

    with TestingSessionLocal() as session:
        line = session.scalars(
            select(JournalLine).join(JournalEntry).where(JournalEntry.import_batch_id == batch_id)
        ).first()
        assert line is not None
        if mutation == "missing":
            session.delete(line)
        else:
            line.amount = Decimal("999.00")
        session.flush()
        corrupted_snapshot = _batch_child_snapshot(session, batch_id)
        with pytest.raises(ValueError, match="missing or altered"):
            validate_gl_xlsx_replay(
                contents,
                date(2025, 4, 1),
                date(2026, 3, 31),
                entity["id"],
                session,
                import_batch_id=batch_id,
            )
        assert _batch_child_snapshot(session, batch_id) == corrupted_snapshot
        session.commit()

    invalid_duplicate = client.post(
        f"/entities/{entity['id']}/upload-xlsx/confirm",
        data={"column_mapping": "{}", "target_period_start": "2025-04-01", "target_period_end": "2026-03-31"},
        files={"file": ("replay.xlsx", io.BytesIO(contents), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )
    assert invalid_duplicate.status_code == 409
    detail = invalid_duplicate.json()["detail"]
    assert detail["failed_batch_id"] == batch_id
    assert detail["failed_batch_status"] == "ACTIVE"
    assert detail["errors"]
    assert detail["active_batch_ids"] == [batch_id]
    assert detail["dataset_fingerprint"] == first.json()["dataset_fingerprint"]


def test_tally_connector_response_is_complete_and_endpoint_metadata_is_safe(monkeypatch):
    headers = get_auth_headers()
    entity = client.post(
        "/entities",
        json={"name": "Connector Contract Entity", "materiality_threshold": "0.00"},
        headers=headers,
    ).json()
    contents = _strict_fixture_bytes("sample_tally_export.xml")
    parsed = parse_tally_xml(contents)
    monkeypatch.setattr(
        "app.routers.scrutiny.fetch_trial_balance",
        lambda **_kwargs: (parsed, contents),
    )

    response = client.post(
        f"/entities/{entity['id']}/import-from-tally",
        json={
            "endpoint": "http://user:secret@example.test:9001/private/export?token=hidden#fragment",
            "company_name": "Acme Audited Corp",
            "period_start": "2025-04-01",
            "period_end": "2026-03-31",
        },
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ACTIVE"
    assert body["readiness"] == "READY"
    assert body["validation_report"]["document_count"] == 2
    assert body["active_batch_ids"] == [body["import_batch_id"]]
    assert body["source_batch_ids"] == [body["import_batch_id"]]
    assert body["dataset_fingerprint"]
    with TestingSessionLocal() as session:
        batch = session.get(ImportBatch, body["import_batch_id"])
        assert batch.source_metadata == {
            "connector": "tally_http",
            "endpoint": {"scheme": "http", "host": "example.test", "port": 9001},
            "parser": "tally_xml",
            "ledger_count": 5,
            "voucher_count": 2,
        }
        assert "secret" not in str(batch.validation_report)
        assert "private" not in str(batch.validation_report)


def test_tally_connector_failure_does_not_echo_endpoint_or_credentials(monkeypatch):
    headers = get_auth_headers()
    entity = client.post(
        "/entities",
        json={"name": "Connector Failure Entity", "materiality_threshold": "0.00"},
        headers=headers,
    ).json()
    monkeypatch.setattr(
        "app.routers.scrutiny.fetch_trial_balance",
        lambda **_kwargs: (_ for _ in ()).throw(
            TallyConnectorError(
                "request failed http://user:secret@example.test:9001/private?token=hidden#fragment"
            )
        ),
    )

    response = client.post(
        f"/entities/{entity['id']}/import-from-tally",
        json={
            "endpoint": "http://user:secret@example.test:9001/private?token=hidden#fragment",
            "company_name": "Connector Failure Entity",
            "period_start": "2025-04-01",
            "period_end": "2026-03-31",
        },
        headers=headers,
    )

    assert response.status_code == 400
    assert "secret" not in response.text
    assert "private" not in response.text
    assert "hidden" not in response.text
    assert "Tally" in response.json()["detail"]["message"]
    assert response.json()["detail"]["failed_batch_id"] is None
    assert response.json()["detail"]["active_batch_ids"] == []


def test_malformed_tally_parse_failure_returns_safe_structured_detail():
    headers = get_auth_headers()
    entity = client.post(
        "/entities", json={"name": "Malformed Tally Entity", "materiality_threshold": "0.00"}, headers=headers
    ).json()
    raw_xml = b"<ENVELOPE><BROKEN password='do-not-echo'>"
    response = client.post(
        f"/entities/{entity['id']}/upload",
        files={"file": ("malformed.xml", raw_xml, "text/xml")},
        headers=headers,
    )
    assert response.status_code == 400
    assert raw_xml.decode() not in response.text
    detail = response.json()["detail"]
    assert detail["failed_batch_id"] is None
    assert detail["validation_report"]["errors"]
    assert detail["active_batch_ids"] == []


def test_tally_and_gl_canonical_reports_have_matching_journal_totals():
    tally_xml = b"""<ENVELOPE><COMPANY><RENAME>Parity Tally</RENAME><BOOKSFROM>20250401</BOOKSFROM><BOOKSTO>20260331</BOOKSTO></COMPANY>
      <TALLYMESSAGE><LEDGER NAME="1000"><PARENT>Fixed Assets</PARENT><OPENINGBALANCE>0</OPENINGBALANCE><CLOSINGBALANCE>100</CLOSINGBALANCE></LEDGER></TALLYMESSAGE>
      <TALLYMESSAGE><LEDGER NAME="2000"><PARENT>Capital Account</PARENT><OPENINGBALANCE>0</OPENINGBALANCE><CLOSINGBALANCE>-100</CLOSINGBALANCE></LEDGER></TALLYMESSAGE>
      <TALLYMESSAGE><VOUCHER VCHTYPE="Journal"><DATE>20250401</DATE><VOUCHERNUMBER>PARITY-1</VOUCHERNUMBER>
        <ALLLEDGERENTRIES.LIST><LEDGERNAME>1000</LEDGERNAME><ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>100</AMOUNT></ALLLEDGERENTRIES.LIST>
        <ALLLEDGERENTRIES.LIST><LEDGERNAME>2000</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>100</AMOUNT></ALLLEDGERENTRIES.LIST>
      </VOUCHER></TALLYMESSAGE></ENVELOPE>"""
    tally_headers = get_auth_headers()
    tally_entity = client.post(
        "/entities", json={"name": "Parity Tally", "materiality_threshold": "0.00"}, headers=tally_headers
    ).json()
    tally_response = client.post(
        f"/entities/{tally_entity['id']}/upload",
        files={"file": ("parity.xml", tally_xml, "text/xml")},
        headers=tally_headers,
    )
    assert tally_response.status_code == 200, tally_response.text

    gl_headers = get_auth_headers()
    gl_entity = client.post(
        "/entities", json={"name": "Parity GL", "materiality_threshold": "0.00"}, headers=gl_headers
    ).json()
    gl_response = client.post(
        f"/entities/{gl_entity['id']}/upload-xlsx/confirm",
        data={
            "column_mapping": "{}",
            "target_period_start": "2025-04-01",
            "target_period_end": "2026-03-31",
        },
        files={"file": ("parity.xlsx", io.BytesIO(_gl_bytes()), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=gl_headers,
    )
    assert gl_response.status_code == 200, gl_response.text

    tally_report = tally_response.json()["validation_report"]
    gl_report = gl_response.json()["validation_report"]
    for key in ("accepted_rows", "document_count", "debit_total", "credit_total", "net_total"):
        assert tally_report[key] == gl_report[key]
    assert tally_response.json()["readiness"] == "READY"
    assert gl_response.json()["readiness"] == "PARTIAL"

    with TestingSessionLocal() as session:
        tally_entries = session.scalars(
            select(JournalEntry).where(JournalEntry.import_batch_id == tally_response.json()["import_batch_id"])
        ).all()
        gl_entries = session.scalars(
            select(JournalEntry).where(JournalEntry.import_batch_id == gl_response.json()["import_batch_id"])
        ).all()
        assert [entry.source_document_id for entry in tally_entries] == ["PARITY-1"]
        assert [entry.source_document_id for entry in gl_entries] == ["GL-1"]
        assert all(entry.entity_id == tally_entity["id"] for entry in tally_entries)
        assert all(entry.entity_id == gl_entity["id"] for entry in gl_entries)

        def child_facts(entries):
            lines = [line for entry in entries for line in entry.lines]
            return sorted(
                (
                    line.source_row_number,
                    line.ledger_account.external_code or line.ledger_account.name,
                    line.ledger_account.entity_id,
                    Decimal(str(line.amount)),
                    line.side,
                )
                for line in lines
            )

        tally_facts = child_facts(tally_entries)
        gl_facts = child_facts(gl_entries)
        assert len(tally_facts) == len(gl_facts) == 2
        assert [(fact[3], fact[4]) for fact in tally_facts] == [
            (Decimal("100.00"), "debit"),
            (Decimal("-100.00"), "credit"),
        ]
        assert [(fact[3], fact[4]) for fact in gl_facts] == [
            (Decimal("100.00"), "debit"),
            (Decimal("-100.00"), "credit"),
        ]
        assert {fact[1] for fact in tally_facts} == {"1000", "2000"}
        assert {fact[1] for fact in gl_facts} == {"1000", "2000"}
        assert all(fact[2] == tally_entity["id"] for fact in tally_facts)
        assert all(fact[2] == gl_entity["id"] for fact in gl_facts)
        assert {fact[0] for fact in tally_facts} == {1, 2}
        assert {fact[0] for fact in gl_facts} == {2, 3}


def test_tally_and_gl_complete_parity_has_equal_scrutiny_inputs_and_findings():
    period_start = date(2025, 4, 1)
    period_end = date(2026, 3, 31)
    baseline_date = date(2025, 3, 31)
    tally_xml = b"""<ENVELOPE><COMPANY><RENAME>Complete Tally Parity</RENAME><BOOKSFROM>20250401</BOOKSFROM><BOOKSTO>20260331</BOOKSTO></COMPANY>
      <TALLYMESSAGE><LEDGER NAME="Trade Payable Control" GUID="1000"><PARENT>Sundry Creditors</PARENT><OPENINGBALANCE>50</OPENINGBALANCE><CLOSINGBALANCE>150</CLOSINGBALANCE></LEDGER></TALLYMESSAGE>
      <TALLYMESSAGE><LEDGER NAME="Retained Earnings Control" GUID="2000"><PARENT>Capital Account</PARENT><OPENINGBALANCE>-50</OPENINGBALANCE><CLOSINGBALANCE>-150</CLOSINGBALANCE></LEDGER></TALLYMESSAGE>
      <TALLYMESSAGE><VOUCHER VCHTYPE="Journal"><DATE>20250401</DATE><VOUCHERNUMBER>PARITY-TALLY-1</VOUCHERNUMBER>
        <ALLLEDGERENTRIES.LIST><LEDGERNAME>Trade Payable Control</LEDGERNAME><ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>100</AMOUNT></ALLLEDGERENTRIES.LIST>
        <ALLLEDGERENTRIES.LIST><LEDGERNAME>Retained Earnings Control</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>100</AMOUNT></ALLLEDGERENTRIES.LIST>
      </VOUCHER></TALLYMESSAGE></ENVELOPE>"""
    gl_bytes = _gl_bytes()

    baseline_workbook = openpyxl.Workbook()
    baseline_sheet = baseline_workbook.active
    baseline_sheet.append(["Account Code", "Balance Date", "Signed Balance", "Currency"])
    baseline_sheet.append(["1000", baseline_date, 50, "INR"])
    baseline_sheet.append(["2000", baseline_date, -50, "INR"])
    baseline_output = io.BytesIO()
    baseline_workbook.save(baseline_output)
    baseline_bytes = baseline_output.getvalue()
    signed_pdf = b"%PDF-1.7 signed baseline evidence"

    headers = get_auth_headers()
    tally_entity = client.post(
        "/entities",
        json={"name": "Complete Tally Parity", "materiality_threshold": "1000.00"},
        headers=headers,
    ).json()
    tally_upload = client.post(
        f"/entities/{tally_entity['id']}/upload",
        files={"file": ("complete-parity.xml", tally_xml, "text/xml")},
        headers=headers,
    )
    assert tally_upload.status_code == 200, tally_upload.text

    gl_entity = client.post(
        "/entities",
        json={"name": "Complete GL Parity", "materiality_threshold": "1000.00"},
        headers=headers,
    ).json()
    explicit_classification = {
        "1000": ("Sundry Creditors", "credit"),
        "2000": ("Capital Account", "credit"),
    }
    with TestingSessionLocal() as session:
        session.add_all([
            LedgerAccount(
                entity_id=gl_entity["id"],
                external_code=code,
                name=name,
                group_name=group_name,
                normal_balance=normal_balance,
            )
            for code, (name, group_name, normal_balance) in {
                "1000": ("Trade Payable Control", *explicit_classification["1000"]),
                "2000": ("Retained Earnings Control", *explicit_classification["2000"]),
            }.items()
        ])
        session.commit()

    gl_upload = client.post(
        f"/entities/{gl_entity['id']}/upload-xlsx/confirm",
        data={
            "column_mapping": "{}",
            "target_period_start": period_start.isoformat(),
            "target_period_end": period_end.isoformat(),
        },
        files={"file": ("complete-parity.xlsx", io.BytesIO(gl_bytes), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )
    assert gl_upload.status_code == 200, gl_upload.text

    baseline_upload = client.post(
        f"/entities/{gl_entity['id']}/upload-xlsx/baseline",
        data={
            "target_period_start": period_start.isoformat(),
            "target_period_end": period_end.isoformat(),
            "expected_currency": "INR",
            "expected_balance_date": baseline_date.isoformat(),
        },
        files={
            "file": (
                "complete-opening-baseline.xlsx",
                io.BytesIO(baseline_bytes),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            "signed_pdf": ("signed-baseline.pdf", signed_pdf, "application/pdf"),
        },
        headers=headers,
    )
    assert baseline_upload.status_code == 200, baseline_upload.text
    assert baseline_upload.json()["readiness"] in {"READY", "READY_WITH_WARNINGS"}
    assert baseline_upload.json()["baseline_coverage"]["complete"] is True
    gl_baseline_id = baseline_upload.json()["import_batch_id"]

    duplicate_baseline = client.post(
        f"/entities/{gl_entity['id']}/upload-xlsx/baseline",
        data={
            "target_period_start": period_start.isoformat(),
            "target_period_end": period_end.isoformat(),
            "expected_currency": "INR",
            "expected_balance_date": baseline_date.isoformat(),
        },
        files={
            "file": (
                "complete-opening-baseline.xlsx",
                io.BytesIO(baseline_bytes),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
        },
        headers=headers,
    )
    assert duplicate_baseline.status_code == 200, duplicate_baseline.text
    assert duplicate_baseline.json()["import_batch_id"] == gl_baseline_id

    with TestingSessionLocal() as session:
        tally_dataset = resolve_active_dataset(session, tally_entity["id"], financial_year=2025)
        gl_dataset = resolve_active_dataset(session, gl_entity["id"], financial_year=2025)

    def decimal_account_values(dataset, field):
        return {code: Decimal(value) for code, value in dataset[field].items()}

    assert decimal_account_values(tally_dataset, "account_movements") == decimal_account_values(
        gl_dataset, "account_movements"
    ) == {"1000": Decimal("100"), "2000": Decimal("-100")}
    assert decimal_account_values(tally_dataset, "opening_balances") == decimal_account_values(
        gl_dataset, "opening_balances"
    ) == {"1000": Decimal("50"), "2000": Decimal("-50")}
    assert decimal_account_values(tally_dataset, "closing_balances") == decimal_account_values(
        gl_dataset, "closing_balances"
    ) == {"1000": Decimal("150"), "2000": Decimal("-150")}
    assert tally_dataset["readiness"] in {"READY", "READY_WITH_WARNINGS"}
    assert gl_dataset["readiness"] in {"READY", "READY_WITH_WARNINGS"}
    assert tally_dataset["source_batch_ids"] == [tally_upload.json()["import_batch_id"]]
    assert gl_dataset["source_batch_ids"] == sorted([
        gl_upload.json()["import_batch_id"],
        gl_baseline_id,
    ])

    tally_run = client.post(
        f"/entities/{tally_entity['id']}/scrutiny-run",
        params={"period_start": period_start.isoformat(), "period_end": period_end.isoformat()},
        headers=headers,
    )
    gl_run = client.post(
        f"/entities/{gl_entity['id']}/scrutiny-run",
        params={"period_start": period_start.isoformat(), "period_end": period_end.isoformat()},
        headers=headers,
    )
    assert tally_run.status_code == 200, tally_run.text
    assert gl_run.status_code == 200, gl_run.text

    def deterministic_findings(entity_id):
        findings = client.get(
            f"/entities/{entity_id}/exceptions",
            params={"period_start": period_start.isoformat(), "period_end": period_end.isoformat()},
            headers=headers,
        )
        assert findings.status_code == 200, findings.text
        return sorted(
            tuple(item[field] for field in (
                "rule_name", "severity", "message", "ledger_account_name", "status", "auditor_notes",
            ))
            for item in findings.json()
        )

    tally_findings = deterministic_findings(tally_entity["id"])
    gl_findings = deterministic_findings(gl_entity["id"])
    assert tally_findings == gl_findings
    assert tally_findings == [(
        "creditor_debit_balance",
        "error",
        "Creditor account 'Trade Payable Control' has a debit closing balance of 150.00. "
        "This indicates an advance given to the supplier or an excess payment made.",
        "Trade Payable Control",
        "PENDING",
        None,
    )]

    with TestingSessionLocal() as session:
        for entity_id, run_id, dataset in (
            (tally_entity["id"], tally_run.json()["scrutiny_run_id"], tally_dataset),
            (gl_entity["id"], gl_run.json()["scrutiny_run_id"], gl_dataset),
        ):
            stored_run = session.get(ScrutinyRun, run_id)
            assert stored_run.status == "COMPLETED"
            assert stored_run.dataset_fingerprint == dataset["dataset_fingerprint"]
            assert stored_run.source_batch_ids == dataset["source_batch_ids"]
            assert stored_run.source_batch_ids == sorted(stored_run.source_batch_ids)
            assert stored_run.entity_id == entity_id

        tally_batch = session.get(ImportBatch, tally_upload.json()["import_batch_id"])
        gl_batch = session.get(ImportBatch, gl_upload.json()["import_batch_id"])
        gl_baseline = session.get(ImportBatch, gl_baseline_id)
        assert tally_batch.source_family == "tally"
        assert tally_batch.raw_source_bytes == tally_xml
        assert gl_batch.source_family == "gl_upload"
        assert gl_batch.raw_source_bytes == gl_bytes
        assert gl_baseline.kind == "balance_checkpoint"
        assert gl_baseline.source_family == "gl_upload"
        assert gl_baseline.raw_source_bytes == baseline_bytes
        assert gl_baseline.source_metadata["supporting_evidence"]["signed_pdf"]["bytes_base64"]
        assert gl_baseline.source_metadata["supporting_evidence"]["signed_pdf"]["content_sha256"]
        assert tally_dataset["dataset_fingerprint"] != gl_dataset["dataset_fingerprint"]


def test_public_baseline_failure_preserves_active_gl_journal():
    headers = get_auth_headers()
    entity = client.post(
        "/entities",
        json={"name": "Baseline Replacement Entity", "materiality_threshold": "0.00"},
        headers=headers,
    ).json()
    journal = _gl_bytes()
    journal_upload = client.post(
        f"/entities/{entity['id']}/upload-xlsx/confirm",
        data={
            "column_mapping": "{}",
            "target_period_start": "2025-04-01",
            "target_period_end": "2026-03-31",
        },
        files={"file": ("journal.xlsx", io.BytesIO(journal), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )
    assert journal_upload.status_code == 200, journal_upload.text
    journal_batch_id = journal_upload.json()["import_batch_id"]

    invalid_baseline = openpyxl.Workbook()
    sheet = invalid_baseline.active
    sheet.append(["Account Code", "Balance Date", "Signed Balance", "Currency"])
    sheet.append(["1000", date(2025, 3, 31), 100, "INR"])
    sheet.append(["2000", date(2025, 3, 31), -99, "INR"])
    output = io.BytesIO()
    invalid_baseline.save(output)
    invalid_baseline_bytes = output.getvalue()
    signed_pdf = b"%PDF-1.7 invalid baseline evidence"
    baseline_upload = client.post(
        f"/entities/{entity['id']}/upload-xlsx/baseline",
        data={
            "target_period_start": "2025-04-01",
            "target_period_end": "2026-03-31",
            "expected_currency": "INR",
            "expected_balance_date": "2025-03-31",
        },
        files={
            "file": (
                "invalid-baseline.xlsx",
                io.BytesIO(invalid_baseline_bytes),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
            "signed_pdf": ("signed-invalid-baseline.pdf", signed_pdf, "application/pdf"),
        },
        headers=headers,
    )

    assert baseline_upload.status_code == 400, baseline_upload.text
    detail = baseline_upload.json()["detail"]
    assert detail["failed_batch_status"] == "FAILED"
    assert detail["active_batch_ids"] == [journal_batch_id]
    assert detail["baseline_coverage"]["complete"] is False
    with TestingSessionLocal() as session:
        assert session.get(ImportBatch, journal_batch_id).status == "ACTIVE"
        failed = session.scalars(
            select(ImportBatch).where(
                ImportBatch.entity_id == entity["id"],
                ImportBatch.status == "FAILED",
            )
        ).one()
        assert failed.raw_source_bytes == invalid_baseline_bytes
        assert failed.validation_report["readiness"] == "INVALID"
        assert failed.validation_report["errors"]
        assert failed.validation_report["rejected_rows"] == failed.validation_report["input_rows"]
        signed_pdf_metadata = failed.source_metadata["supporting_evidence"]["signed_pdf"]
        assert signed_pdf_metadata == {
            "filename": "signed-invalid-baseline.pdf",
            "content_type": "application/pdf",
            "content_sha256": hashlib.sha256(signed_pdf).hexdigest(),
            "bytes_base64": base64.b64encode(signed_pdf).decode("ascii"),
        }
        assert session.scalar(
            select(func.count()).select_from(BalanceCheckpoint).where(
                BalanceCheckpoint.import_batch_id == failed.id,
            )
        ) == 0


def test_public_baseline_rejects_reversed_period_before_staging():
    headers = get_auth_headers()
    entity = client.post(
        "/entities",
        json={"name": "Reversed Baseline Period Entity", "materiality_threshold": "0.00"},
        headers=headers,
    ).json()
    journal_upload = client.post(
        f"/entities/{entity['id']}/upload-xlsx/confirm",
        data={
            "column_mapping": "{}",
            "target_period_start": "2025-04-01",
            "target_period_end": "2026-03-31",
        },
        files={"file": ("journal.xlsx", io.BytesIO(_gl_bytes()), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )
    assert journal_upload.status_code == 200, journal_upload.text
    journal_batch_id = journal_upload.json()["import_batch_id"]
    with TestingSessionLocal() as session:
        before_dataset = resolve_active_dataset(session, entity["id"], financial_year=2025)
    assert before_dataset["active_batch_ids"] == [journal_batch_id]

    baseline_upload = _upload_public_baseline(
        entity["id"],
        headers,
        _baseline_bytes(),
        period_start="2026-03-31",
        period_end="2025-04-01",
    )

    assert baseline_upload.status_code == 400, baseline_upload.text
    detail = baseline_upload.json()["detail"]
    assert detail["failed_batch_id"] is None
    with TestingSessionLocal() as session:
        after_dataset = resolve_active_dataset(session, entity["id"], financial_year=2025)
        assert after_dataset["active_batch_ids"] == before_dataset["active_batch_ids"]
        assert after_dataset["dataset_fingerprint"] == before_dataset["dataset_fingerprint"]
        batches = session.scalars(
            select(ImportBatch).where(ImportBatch.entity_id == entity["id"]).order_by(ImportBatch.id)
        ).all()
        assert [batch.id for batch in batches] == [journal_batch_id]
        assert all(batch.status == "ACTIVE" for batch in batches)
        assert session.scalar(
            select(func.count()).select_from(BalanceCheckpoint).where(
                BalanceCheckpoint.entity_id == entity["id"],
            )
        ) == 0


def test_public_baseline_replacement_supersedes_old_checkpoint_and_children():
    headers = get_auth_headers()
    entity = client.post(
        "/entities",
        json={"name": "Baseline Replacement Lifecycle Entity", "materiality_threshold": "0.00"},
        headers=headers,
    ).json()
    journal_upload = client.post(
        f"/entities/{entity['id']}/upload-xlsx/confirm",
        data={
            "column_mapping": "{}",
            "target_period_start": "2025-04-01",
            "target_period_end": "2026-03-31",
        },
        files={"file": ("journal.xlsx", io.BytesIO(_gl_bytes()), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )
    assert journal_upload.status_code == 200, journal_upload.text
    journal_batch_id = journal_upload.json()["import_batch_id"]

    first_upload = _upload_public_baseline(entity["id"], headers, _baseline_bytes(50))
    assert first_upload.status_code == 200, first_upload.text
    first_batch_id = first_upload.json()["import_batch_id"]
    with TestingSessionLocal() as session:
        first_children = _batch_child_snapshot(session, first_batch_id)

    replacement_upload = _upload_public_baseline(entity["id"], headers, _baseline_bytes(75))
    assert replacement_upload.status_code == 200, replacement_upload.text
    replacement_batch_id = replacement_upload.json()["import_batch_id"]
    assert replacement_batch_id != first_batch_id
    assert replacement_upload.json()["baseline_coverage"]["complete"] is True
    assert replacement_upload.json()["dataset_fingerprint"] != first_upload.json()["dataset_fingerprint"]

    with TestingSessionLocal() as session:
        first = session.get(ImportBatch, first_batch_id)
        replacement = session.get(ImportBatch, replacement_batch_id)
        assert first.status == "SUPERSEDED"
        assert replacement.status == "ACTIVE"
        assert _batch_child_snapshot(session, first_batch_id) == first_children
        replacement_checkpoints = session.scalars(
            select(BalanceCheckpoint).where(
                BalanceCheckpoint.import_batch_id == replacement_batch_id,
            ).order_by(BalanceCheckpoint.ledger_account_id)
        ).all()
        assert [row.balance for row in replacement_checkpoints] == [Decimal("75"), Decimal("-75")]
        active = session.scalars(
            select(ImportBatch).where(
                ImportBatch.entity_id == entity["id"],
                ImportBatch.status == "ACTIVE",
            ).order_by(ImportBatch.id)
        ).all()
        assert [batch.id for batch in active if batch.kind == "balance_checkpoint"] == [replacement_batch_id]
        assert session.get(ImportBatch, journal_batch_id).status == "ACTIVE"


def test_public_baseline_failed_replacement_keeps_old_checkpoint_and_dataset():
    headers = get_auth_headers()
    entity = client.post(
        "/entities",
        json={"name": "Failed Baseline Replacement Entity", "materiality_threshold": "0.00"},
        headers=headers,
    ).json()
    journal_upload = client.post(
        f"/entities/{entity['id']}/upload-xlsx/confirm",
        data={
            "column_mapping": "{}",
            "target_period_start": "2025-04-01",
            "target_period_end": "2026-03-31",
        },
        files={"file": ("journal.xlsx", io.BytesIO(_gl_bytes()), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )
    assert journal_upload.status_code == 200, journal_upload.text
    journal_batch_id = journal_upload.json()["import_batch_id"]

    first_upload = _upload_public_baseline(entity["id"], headers, _baseline_bytes(50))
    assert first_upload.status_code == 200, first_upload.text
    first_batch_id = first_upload.json()["import_batch_id"]
    before_fingerprint = first_upload.json()["dataset_fingerprint"]
    with TestingSessionLocal() as session:
        first_children = _batch_child_snapshot(session, first_batch_id)

    failed_upload = _upload_public_baseline(entity["id"], headers, _baseline_bytes(75, -74))

    assert failed_upload.status_code == 400, failed_upload.text
    detail = failed_upload.json()["detail"]
    assert detail["dataset_fingerprint"] == before_fingerprint
    assert detail["active_batch_ids"] == [journal_batch_id]
    failed_batch_id = detail["failed_batch_id"]
    assert failed_batch_id is not None
    with TestingSessionLocal() as session:
        first = session.get(ImportBatch, first_batch_id)
        failed = session.get(ImportBatch, failed_batch_id)
        assert first.status == "ACTIVE"
        assert failed.status == "FAILED"
        assert failed.validation_report["readiness"] == "INVALID"
        assert failed.validation_report["errors"]
        assert _batch_child_snapshot(session, first_batch_id) == first_children
        assert session.scalar(
            select(func.count()).select_from(BalanceCheckpoint).where(
                BalanceCheckpoint.import_batch_id == failed_batch_id,
            )
        ) == 0
        assert session.get(ImportBatch, journal_batch_id).status == "ACTIVE"


def test_public_baseline_activation_failure_preserves_old_checkpoint_and_dataset(monkeypatch):
    headers = get_auth_headers()
    entity = client.post(
        "/entities",
        json={"name": "Injected Baseline Replacement Failure Entity", "materiality_threshold": "0.00"},
        headers=headers,
    ).json()
    journal_upload = client.post(
        f"/entities/{entity['id']}/upload-xlsx/confirm",
        data={
            "column_mapping": "{}",
            "target_period_start": "2025-04-01",
            "target_period_end": "2026-03-31",
        },
        files={"file": ("journal.xlsx", io.BytesIO(_gl_bytes()), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )
    assert journal_upload.status_code == 200, journal_upload.text

    first_upload = _upload_public_baseline(entity["id"], headers, _baseline_bytes(50))
    assert first_upload.status_code == 200, first_upload.text
    first_batch_id = first_upload.json()["import_batch_id"]
    with TestingSessionLocal() as session:
        before_dataset = resolve_active_dataset(session, entity["id"], financial_year=2025)
        first_children = _batch_child_snapshot(session, first_batch_id)

    real_activate = baseline_module.activate_import_batch

    def fail_after_activation(session, batch_or_id, **kwargs):
        candidate = real_activate(session, batch_or_id, **kwargs)
        assert candidate.status == "ACTIVE"
        assert session.scalar(
            select(ImportBatch.status).where(ImportBatch.id == first_batch_id)
        ) == "SUPERSEDED"
        assert session.scalar(
            select(BalanceCheckpoint.id).where(
                BalanceCheckpoint.import_batch_id == candidate.id,
            )
        ) is not None
        raise RuntimeError("injected baseline activation failure")

    monkeypatch.setattr(baseline_module, "activate_import_batch", fail_after_activation)
    failed_upload = _upload_public_baseline(entity["id"], headers, _baseline_bytes(75))

    assert failed_upload.status_code == 400, failed_upload.text
    detail = failed_upload.json()["detail"]
    assert detail["failed_batch_id"] is not None
    assert detail["failed_batch_status"] == "FAILED"
    assert detail["dataset_fingerprint"] == before_dataset["dataset_fingerprint"]
    assert detail["active_batch_ids"] == before_dataset["active_batch_ids"]
    failed_batch_id = detail["failed_batch_id"]
    with TestingSessionLocal() as session:
        after_dataset = resolve_active_dataset(session, entity["id"], financial_year=2025)
        first = session.get(ImportBatch, first_batch_id)
        failed = session.get(ImportBatch, failed_batch_id)
        assert after_dataset["dataset_fingerprint"] == before_dataset["dataset_fingerprint"]
        assert first.status == "ACTIVE"
        assert _batch_child_snapshot(session, first_batch_id) == first_children
        assert failed.status == "FAILED"
        assert failed.validation_report["readiness"] == "INVALID"
        assert failed.validation_report["errors"]
        assert session.scalar(
            select(func.count()).select_from(BalanceCheckpoint).where(
                BalanceCheckpoint.import_batch_id == failed_batch_id,
            )
        ) == 0


def test_first_failed_tally_batch_is_retained_without_exposing_an_orphan_period():
    headers = get_auth_headers()
    entity = client.post(
        "/entities", json={"name": "Initial Tally Failure Entity", "materiality_threshold": "0.00"}, headers=headers
    ).json()
    valid = _strict_fixture_bytes("sample_tally_export.xml")
    malformed = valid.replace(b"<LEDGERNAME>Sales Account</LEDGERNAME>", b"<LEDGERNAME>Unknown Account</LEDGERNAME>", 1)
    response = client.post(
        f"/entities/{entity['id']}/upload",
        params={"clear_only_period": "true", "target_period_start": "2025-04-01", "target_period_end": "2026-03-31"},
        files={"file": ("malformed.xml", malformed, "text/xml")},
        headers=headers,
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["failed_batch_id"] is not None
    assert detail["failed_batch_status"] == "FAILED"
    assert detail["validation_report"]["errors"]
    assert detail["readiness"] in {"INVALID", "PARTIAL"}
    assert detail["active_batch_ids"] == []
    assert detail["source_batch_ids"] == []
    with TestingSessionLocal() as session:
        batch = session.get(ImportBatch, detail["failed_batch_id"])
        assert batch is not None
        assert batch.status == "FAILED"
        assert batch.raw_source_bytes == malformed
        assert session.scalar(select(func.count()).select_from(JournalEntry).where(JournalEntry.import_batch_id == batch.id)) == 0
    periods = client.get(f"/entities/{entity['id']}/periods", headers=headers)
    assert periods.status_code == 200
    assert periods.json() == []


def test_malformed_tally_replacement_retains_previous_active_dataset():
    headers = get_auth_headers()
    entity = client.post(
        "/entities", json={"name": "Tally Replacement Entity", "materiality_threshold": "0.00"}, headers=headers
    ).json()
    valid = _strict_fixture_bytes("sample_tally_export.xml")
    first = client.post(
        f"/entities/{entity['id']}/upload",
        params={"clear_only_period": "true", "target_period_start": "2025-04-01", "target_period_end": "2026-03-31"},
        files={"file": ("valid.xml", valid, "text/xml")},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    malformed = valid.replace(b"<LEDGERNAME>Sales Account</LEDGERNAME>", b"<LEDGERNAME>Unknown Account</LEDGERNAME>", 1)
    replacement = client.post(
        f"/entities/{entity['id']}/upload",
        params={"clear_only_period": "true", "target_period_start": "2025-04-01", "target_period_end": "2026-03-31"},
        files={"file": ("malformed.xml", malformed, "text/xml")},
        headers=headers,
    )
    assert replacement.status_code == 400
    replacement_detail = replacement.json()["detail"]
    assert replacement_detail["failed_batch_status"] == "FAILED"
    assert replacement_detail["validation_report"]["errors"]
    assert replacement_detail["active_batch_ids"] == [first_body["import_batch_id"]]
    assert replacement_detail["dataset_fingerprint"] == first_body["dataset_fingerprint"]

    with TestingSessionLocal() as session:
        batches = session.scalars(
            select(ImportBatch).where(ImportBatch.entity_id == entity["id"]).order_by(ImportBatch.id)
        ).all()
        assert [batch.status for batch in batches] == ["ACTIVE", "FAILED"]
        assert batches[0].id == first_body["import_batch_id"]
        assert batches[0].validation_report == first_body["validation_report"]
        assert batches[1].raw_source_bytes == malformed
        assert batches[1].validation_report["readiness"] == "INVALID"
        assert batches[1].validation_report["errors"]
        assert session.scalar(
            select(func.count()).select_from(JournalEntry).where(JournalEntry.import_batch_id == batches[0].id)
        ) == 2
        assert session.scalar(
            select(func.count()).select_from(JournalEntry).where(JournalEntry.import_batch_id == batches[1].id)
        ) == 0
        assert session.scalar(select(func.count()).select_from(JournalLine)) == 4


def test_fixed_gl_api_persists_complete_golden_file_rows_and_documents():
    headers = get_auth_headers()
    entity = client.post(
        "/entities", json={"name": "Complete GL Fixture Entity", "materiality_threshold": "0.00"}, headers=headers
    ).json()
    contents = (Path(__file__).parent / "fixtures" / "golden_gl.xlsx").read_bytes()
    response = client.post(
        f"/entities/{entity['id']}/upload-xlsx/confirm",
        data={"column_mapping": "{}", "target_period_start": "2025-04-01", "target_period_end": "2026-03-31"},
        files={"file": ("golden_gl.xlsx", io.BytesIO(contents), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["validation_report"]["accepted_rows"] == 6
    assert body["validation_report"]["document_count"] == 3
    with TestingSessionLocal() as session:
        entries = session.scalars(
            select(JournalEntry).where(JournalEntry.import_batch_id == body["import_batch_id"])
        ).all()
        lines = session.scalars(
            select(JournalLine).join(JournalEntry).where(JournalEntry.import_batch_id == body["import_batch_id"])
        ).all()
        assert len(entries) == 3
        assert len(lines) == 6
        assert {entry.source_document_id for entry in entries} == {"DOC-1001", "DOC-1002", "DOC-1003"}
        assert {line.source_row_number for line in lines} == set(range(2, 8))
        assert {line.ledger_account.external_code for line in lines} == {
            "110000", "120000", "210000", "400000", "500000"
        }
        assert all(line.journal_entry.entity_id == entity["id"] for line in lines)


@pytest.mark.parametrize("sign_convention", ["positive_is_credit", "separate_dr_cr_columns"])
def test_xlsx_confirm_rejects_unsupported_sign_convention_before_staging(sign_convention):
    headers = get_auth_headers()
    entity = client.post(
        "/entities", json={"name": "Unsupported Sign Entity", "materiality_threshold": "0.00"}, headers=headers
    ).json()
    response = client.post(
        f"/entities/{entity['id']}/upload-xlsx/confirm",
        data={
            "column_mapping": "{}",
            "sign_convention": sign_convention,
            "target_period_start": "2025-04-01",
            "target_period_end": "2026-03-31",
        },
        files={"file": ("unsupported-sign.xlsx", io.BytesIO(_gl_bytes()), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert "signed amounts" in detail["message"]
    assert detail["readiness"] == "INVALID"
    assert detail["active_batch_ids"] == []
    assert detail["errors"]
    with TestingSessionLocal() as session:
        assert session.scalar(
            select(func.count()).select_from(ImportBatch).where(ImportBatch.entity_id == entity["id"])
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(JournalEntry).where(JournalEntry.entity_id == entity["id"])
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(JournalLine).join(JournalEntry).where(JournalEntry.entity_id == entity["id"])
        ) == 0


def test_scrutiny_reports_readiness_block_and_persists_ready_dataset_lineage():
    headers = get_auth_headers()
    partial_entity = client.post(
        "/entities", json={"name": "Partial Scrutiny Entity", "materiality_threshold": "0.00"}, headers=headers
    ).json()
    partial_upload = client.post(
        f"/entities/{partial_entity['id']}/upload-xlsx/confirm",
        data={"column_mapping": "{}", "target_period_start": "2025-04-01", "target_period_end": "2026-03-31"},
        files={"file": ("partial.xlsx", io.BytesIO(_gl_bytes()), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=headers,
    )
    assert partial_upload.status_code == 200
    assert partial_upload.json()["readiness"] == "PARTIAL"
    blocked = client.post(
        f"/entities/{partial_entity['id']}/scrutiny-run",
        params={"period_start": "2025-04-01", "period_end": "2026-03-31"},
        headers=headers,
    )
    assert blocked.status_code == 409
    blocked_detail = blocked.json()["detail"]
    assert blocked_detail["readiness"] == "PARTIAL"
    assert blocked_detail["baseline_coverage"]["complete"] is False
    assert "baseline" in blocked_detail["message"].lower()

    ready_entity = client.post(
        "/entities", json={"name": "Ready Scrutiny Entity", "materiality_threshold": "0.00"}, headers=headers
    ).json()
    ready_contents = _strict_fixture_bytes("sample_tally_export.xml")
    ready_upload = client.post(
        f"/entities/{ready_entity['id']}/upload",
        files={"file": ("ready.xml", ready_contents, "text/xml")},
        headers=headers,
    )
    assert ready_upload.status_code == 200, ready_upload.text
    ready_body = ready_upload.json()
    run = client.post(
        f"/entities/{ready_entity['id']}/scrutiny-run",
        params={"period_start": "2025-04-01", "period_end": "2026-03-31"},
        headers=headers,
    )
    assert run.status_code == 200, run.text
    with TestingSessionLocal() as session:
        scrutiny_run = session.get(ScrutinyRun, run.json()["scrutiny_run_id"])
        assert scrutiny_run.dataset_fingerprint == ready_body["dataset_fingerprint"]
        assert scrutiny_run.source_batch_ids == ready_body["source_batch_ids"]
        assert scrutiny_run.import_batch_id == ready_body["import_batch_id"]
