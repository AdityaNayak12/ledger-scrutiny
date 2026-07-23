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
    assert parsed_data["entity"]["name"] == "Test Fixtures Pvt Ltd"

    # 3. Create fresh DB
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # 4. Ingest and normalize current period
        # Case A: Materiality threshold at 5000.00
        entity = normalize_tally_data(parsed_data, session, materiality_threshold=Decimal("5000.00"))
        session.commit()

        accounts = session.query(LedgerAccount).filter_by(entity_id=entity.id).all()
        snapshots = session.query(TrialBalanceSnapshot).filter_by(entity_id=entity.id).all()

        exceptions = run_scrutiny(entity, accounts, snapshots)
        
        # Create a map of account_id -> name for quick lookup without requiring session relationship loads
        acc_name_map = {acc.id: acc.name for acc in accounts}

        # Verify exceptions at materiality >= 5000
        # Under the opt-in design, normal_balance_check is categorical and not filtered by materiality.
        # Thus, Rahul Enterprises, Verma Traders, and Petty Cash Variance should all be present.
        exc_names = {acc_name_map[e.ledger_account_id] for e in exceptions if e.ledger_account_id in acc_name_map}
        assert "Rahul Enterprises" in exc_names
        assert "Verma Traders" in exc_names
        assert "Petty Cash Variance" in exc_names
        assert "Share Capital" not in exc_names
        assert "Furniture and Fixtures" not in exc_names
        assert "HDFC Bank Current Account" not in exc_names
        assert "Sales Account" not in exc_names
        assert "Purchase Account" not in exc_names
        assert "Office Rent" not in exc_names
        assert len(exceptions) == 3

        # Case B: Materiality threshold at 0 (Petty Cash Variance should be included)
        entity.materiality_threshold = Decimal("0.00")
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


def test_normal_balance_check_materiality_exempt():
    # Read XML file
    xml_path = os.path.join(os.path.dirname(__file__), "../../sample_data/test_fixture_with_exceptions.xml")
    with open(xml_path, "rb") as f:
        xml_content = f.read()

    parsed_data = parse_tally_xml(xml_content)
    
    # Run against three different materiality thresholds: 0, 15000, 999999
    for threshold in [Decimal("0.00"), Decimal("15000.00"), Decimal("999999.00")]:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()

        try:
            entity = normalize_tally_data(parsed_data, session, materiality_threshold=threshold)
            session.commit()

            accounts = session.query(LedgerAccount).filter_by(entity_id=entity.id).all()
            snapshots = session.query(TrialBalanceSnapshot).filter_by(entity_id=entity.id).all()

            exceptions = run_scrutiny(entity, accounts, snapshots)
            acc_name_map = {acc.id: acc.name for acc in accounts}

            # Gather all account names with exceptions
            exc_names = {acc_name_map[e.ledger_account_id] for e in exceptions if e.ledger_account_id in acc_name_map}
            
            # Assert exactly the 3 normal balance violation accounts are flagged in all scenarios
            assert len(exceptions) == 3, f"Expected 3 exceptions, got {len(exceptions)} at threshold {threshold}"
            assert "Rahul Enterprises" in exc_names
            assert "Verma Traders" in exc_names
            assert "Petty Cash Variance" in exc_names

        finally:
            session.close()
            Base.metadata.drop_all(engine)


def test_opening_balance_continuity_regression():
    xml_path_p1 = os.path.join(os.path.dirname(__file__), "../../sample_data/test_fixture_with_exceptions.xml")
    xml_path_p2 = os.path.join(os.path.dirname(__file__), "../../sample_data/test_fixture_period2_continuity.xml")
    
    with open(xml_path_p1, "rb") as f:
        xml_p1 = f.read()
    with open(xml_path_p2, "rb") as f:
        xml_p2 = f.read()

    parsed_p1 = parse_tally_xml(xml_p1)
    parsed_p2 = parse_tally_xml(xml_p2)
    
    # 1. Run at materiality = 15000.00
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        # Ingest Period 1
        normalize_tally_data(parsed_p1, session, materiality_threshold=Decimal("15000.00"))
        session.commit()
        
        # Ingest Period 2
        entity2 = normalize_tally_data(parsed_p2, session, materiality_threshold=Decimal("15000.00"))
        session.commit()
        
        accounts = session.query(LedgerAccount).filter_by(entity_id=entity2.id).all()
        snapshots = session.query(TrialBalanceSnapshot).all()
        
        exceptions = run_scrutiny(entity2, accounts, snapshots)
        acc_name_map = {acc.id: acc.name for acc in accounts}
        
        continuity_exceptions = [e for e in exceptions if e.rule_name == "opening_balance_continuity"]
        exc_names = {acc_name_map[e.ledger_account_id] for e in continuity_exceptions if e.ledger_account_id in acc_name_map}
        
        # Expect exactly 1 continuity exception: Verma Traders
        assert len(continuity_exceptions) == 1
        assert "Verma Traders" in exc_names
        assert "Rahul Enterprises" not in exc_names
        assert "Petty Cash Variance" not in exc_names
        assert "Sales Account" not in exc_names
        assert "Purchase Account" not in exc_names
        assert "Office Rent" not in exc_names
    finally:
        session.close()
        Base.metadata.drop_all(engine)

    # 2. Run at materiality = 0.00
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        # Ingest Period 1
        normalize_tally_data(parsed_p1, session, materiality_threshold=Decimal("0.00"))
        session.commit()
        
        # Ingest Period 2
        entity2 = normalize_tally_data(parsed_p2, session, materiality_threshold=Decimal("0.00"))
        session.commit()
        
        accounts = session.query(LedgerAccount).filter_by(entity_id=entity2.id).all()
        snapshots = session.query(TrialBalanceSnapshot).all()
        
        exceptions = run_scrutiny(entity2, accounts, snapshots)
        acc_name_map = {acc.id: acc.name for acc in accounts}
        
        continuity_exceptions = [e for e in exceptions if e.rule_name == "opening_balance_continuity"]
        exc_names = {acc_name_map[e.ledger_account_id] for e in continuity_exceptions if e.ledger_account_id in acc_name_map}
        
        # Expect exactly 2 continuity exceptions: Verma Traders, Rahul Enterprises
        assert len(continuity_exceptions) == 2
        assert "Verma Traders" in exc_names
        assert "Rahul Enterprises" in exc_names
        assert "Petty Cash Variance" not in exc_names
        assert "Sales Account" not in exc_names
        assert "Purchase Account" not in exc_names
        assert "Office Rent" not in exc_names
    finally:
        session.close()
        Base.metadata.drop_all(engine)
