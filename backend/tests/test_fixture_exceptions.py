import os
from decimal import Decimal
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import Entity, LedgerAccount, TrialBalanceSnapshot, AuditException
from app.ingestion.tally_parser import parse_tally_xml
from app.ingestion.tally_normalizer import normalize_tally_data
from app.rules.engine import run_scrutiny


def test_fixture_exceptions_matching_requirements():
    # 1. Read the newly created test fixture XML file
    xml_path = os.path.join(os.path.dirname(__file__), "../../sample_data/test_fixture_with_exceptions.xml")
    with open(xml_path, "rb") as f:
        xml_content = f.read()

    # 2. Parse XML content
    parsed_data = parse_tally_xml(xml_content)
    assert parsed_data["entity"]["name"] == "Test Fixture Corp"

    # 3. Create fresh DB
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # 4. Ingest and normalize
        # Case A: Materiality threshold at 150 (Petty Cash Variance at 120 should be filtered out)
        entity = normalize_tally_data(parsed_data, session, materiality_threshold=Decimal("150.00"))
        session.commit()

        accounts = session.query(LedgerAccount).filter_by(entity_id=entity.id).all()
        snapshots = session.query(TrialBalanceSnapshot).filter_by(entity_id=entity.id).all()

        exceptions = run_scrutiny(entity, accounts, snapshots)
        
        # Create a map of account_id -> name for quick lookup without requiring session relationship loads
        acc_name_map = {acc.id: acc.name for acc in accounts}

        # Verify exceptions at materiality >= 150
        # Rahul Enterprises (Sundry Creditors, Debit balance 12,000) and Verma Traders (Sundry Debtors, Credit balance -8,000)
        # should be present. Petty Cash (-120.00) is filtered out because variance 120 < 150 threshold.
        exc_names = {acc_name_map[e.ledger_account_id] for e in exceptions if e.ledger_account_id in acc_name_map}
        assert "Rahul Enterprises" in exc_names
        assert "Verma Traders" in exc_names
        assert "Petty Cash Variance" not in exc_names
        assert "Share Capital" not in exc_names
        assert "Furniture and Fixtures" not in exc_names
        assert "HDFC Bank Current Account" not in exc_names
        assert "Sales Account" not in exc_names
        assert "Purchase Account" not in exc_names
        assert "Office Rent" not in exc_names
        assert len(exceptions) == 2

        # Case B: Materiality threshold at 50 (Petty Cash Variance should be included)
        entity.materiality_threshold = Decimal("50.00")
        session.commit()
        
        exceptions_low_materiality = run_scrutiny(entity, accounts, snapshots)
        exc_names_low = {acc_name_map[e.ledger_account_id] for e in exceptions_low_materiality if e.ledger_account_id in acc_name_map}
        assert "Rahul Enterprises" in exc_names_low
        assert "Verma Traders" in exc_names_low
        assert "Petty Cash Variance" in exc_names_low
        assert len(exceptions_low_materiality) == 3

    finally:
        session.close()
        Base.metadata.drop_all(engine)
