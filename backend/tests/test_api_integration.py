import os
from decimal import Decimal
from datetime import date
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.session import get_db
from app.main import app

# Create persistent connection for in-memory SQLite database
engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False)
_connection = None


@pytest.fixture(autouse=True)
def setup_db():
    global _connection
    _connection = engine.connect()
    # Enable foreign keys in SQLite
    _connection.execute(text("PRAGMA foreign_keys=ON"))
    TestingSessionLocal.configure(bind=_connection)
    Base.metadata.create_all(bind=_connection)
    yield
    Base.metadata.drop_all(bind=_connection)
    _connection.close()


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


# Apply the dependency override to the app
app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)


def test_api_entities_lifecycle_flow():
    # 1. Create an Entity
    entity_payload = {
        "name": "Acme Audited Corp",
        "financial_year_start": "2025-04-01",
        "financial_year_end": "2026-03-31",
        "materiality_threshold": "1000.00"
    }
    create_res = client.post("/entities", json=entity_payload)
    assert create_res.status_code == 201
    entity = create_res.json()
    assert entity["name"] == "Acme Audited Corp"
    entity_id = entity["id"]

    # 2. List Entities
    list_entities_res = client.get("/entities")
    assert list_entities_res.status_code == 200
    assert len(list_entities_res.json()) == 1
    assert list_entities_res.json()[0]["id"] == entity_id

    # 3. Upload Clean Sample Tally XML
    xml_path = os.path.join(os.path.dirname(__file__), "sample_tally_export.xml")
    with open(xml_path, "rb") as f:
        upload_res = client.post(
            f"/entities/{entity_id}/upload",
            files={"file": ("sample_tally_export.xml", f, "text/xml")}
        )
    
    assert upload_res.status_code == 200
    res_data = upload_res.json()
    assert res_data["message"] == "Ingestion successful"
    assert res_data["entity_id"] == entity_id

    # 4. Trigger Scrutiny Run
    run_res = client.post(f"/entities/{entity_id}/scrutiny-run")
    assert run_res.status_code == 200
    summary = run_res.json()
    assert summary["status"] == "success"
    assert summary["exceptions_count"] == 0

    # 5. Check exceptions endpoint
    list_exceptions_res = client.get(f"/entities/{entity_id}/exceptions")
    assert list_exceptions_res.status_code == 200
    assert len(list_exceptions_res.json()) == 0


def test_api_scrutiny_with_violations():
    # 1. Create the entity
    entity_payload = {
        "name": "Violating Company Ltd",
        "financial_year_start": "2025-04-01",
        "financial_year_end": "2026-03-31",
        "materiality_threshold": "5000.00"
    }
    create_res = client.post("/entities", json=entity_payload)
    assert create_res.status_code == 201
    entity_id = create_res.json()["id"]

    # 2. Create a Tally XML with engineered violations:
    # We record a payment of 80,000 from Cash-in-hand (opening 10,000), 
    # leaving a Credit closing balance of 70,000 (which violates normal Debit balance check).
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
                <OPENINGBALANCE>-10000.00</OPENINGBALANCE>
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
        files={"file": ("violating_export.xml", violating_xml.encode("utf-8"), "text/xml")}
    )
    assert upload_res.status_code == 200

    # 4. Trigger Scrutiny
    run_res = client.post(f"/entities/{entity_id}/scrutiny-run")
    assert run_res.status_code == 200
    summary = run_res.json()
    assert summary["status"] == "success"
    assert summary["exceptions_count"] == 2

    # 5. Query exceptions filterable by severity
    list_res = client.get(f"/entities/{entity_id}/exceptions", params={"severity": "error"})
    assert list_res.status_code == 200
    exceptions = list_res.json()
    assert len(exceptions) == 2
    
    exc_accounts = {e["ledger_account_name"]: e for e in exceptions}
    assert "Cash-in-hand" in exc_accounts
    assert "Owner Capital" in exc_accounts

    cash_exc = exc_accounts["Cash-in-hand"]
    assert cash_exc["rule_name"] == "normal_balance_check"
    assert cash_exc["severity"] == "error"
    assert "credit closing balance of 70000" in cash_exc["message"]

    capital_exc = exc_accounts["Owner Capital"]
    assert capital_exc["rule_name"] == "normal_balance_check"
    assert capital_exc["severity"] == "error"
    assert "debit closing balance of 80000" in capital_exc["message"]

    list_res_warn = client.get(f"/entities/{entity_id}/exceptions", params={"severity": "warning"})
    assert list_res_warn.status_code == 200
    assert len(list_res_warn.json()) == 0
