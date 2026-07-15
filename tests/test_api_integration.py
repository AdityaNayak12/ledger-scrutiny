import os
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from db.base import Base
from db.session import get_db
from main import app

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




def test_api_ingest_scrutinize_exceptions_flow():
    # 1. Upload Clean Sample Tally XML
    xml_path = os.path.join(os.path.dirname(__file__), "sample_tally_export.xml")
    with open(xml_path, "rb") as f:
        response = client.post(
            "/api/v1/ingest",
            files={"file": ("sample_tally_export.xml", f, "text/xml")},
            params={"materiality_threshold": 1000.00}
        )
    
    assert response.status_code == 201
    res_data = response.json()
    assert res_data["message"] == "Ingestion successful"
    assert res_data["entity_name"] == "Acme Audited Corp"
    entity_id = res_data["entity_id"]
    assert entity_id is not None

    # 2. Trigger Scrutiny for the clean entity
    scrutinize_response = client.post(f"/api/v1/entities/{entity_id}/scrutinize")
    assert scrutinize_response.status_code == 200
    exceptions = scrutinize_response.json()
    # Clean data should have 0 exceptions
    assert len(exceptions) == 0

    # 3. Check exceptions endpoint
    list_response = client.get(f"/api/v1/entities/{entity_id}/exceptions")
    assert list_response.status_code == 200
    assert len(list_response.json()) == 0


def test_api_scrutiny_with_violations():
    # 1. Create a Tally XML with engineered violations:
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
    
    # 2. Upload the violating XML
    response = client.post(
        "/api/v1/ingest",
        files={"file": ("violating_export.xml", violating_xml.encode("utf-8"), "text/xml")},
        params={"materiality_threshold": 5000.00} # Materiality = 5000
    )
    
    assert response.status_code == 201
    entity_id = response.json()["entity_id"]

    # 3. Trigger Scrutiny (Runs Rules + filters by materiality)
    # Cash-in-hand: opening=10000 Dr, credits=80000, closing=-70000 (Cr).
    # Normal balance check sees a credit balance of 70,000 on a Debit account.
    # Deviation is 70,000, which is >= 5000 (materiality), so it is kept!
    scrutiny_res = client.post(f"/api/v1/entities/{entity_id}/scrutinize")
    assert scrutiny_res.status_code == 200
    exceptions = scrutiny_res.json()
    
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

    # 4. Query exceptions filterable by severity
    list_res = client.get(f"/api/v1/entities/{entity_id}/exceptions", params={"severity": "error"})
    assert list_res.status_code == 200
    assert len(list_res.json()) == 2

    list_res_warn = client.get(f"/api/v1/entities/{entity_id}/exceptions", params={"severity": "warning"})
    assert list_res_warn.status_code == 200
    assert len(list_res_warn.json()) == 0

