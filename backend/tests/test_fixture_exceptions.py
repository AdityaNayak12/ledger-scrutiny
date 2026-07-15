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
        # 4. Ingest and normalize current period
        # Case A: Materiality threshold at 15000.00
        entity = normalize_tally_data(parsed_data, session, materiality_threshold=Decimal("15000.00"))
        session.commit()

        # Find Petty Cash Variance ledger account to attach prior period snapshot
        petty_cash_acc = session.query(LedgerAccount).filter_by(entity_id=entity.id, name="Petty Cash Variance").one()

        # Insert a prior period snapshot for Petty Cash Variance with closing balance 250.00
        # Given current period opening is 100.00, this creates an opening balance continuity variance of exactly 150.00
        prior_snapshot = TrialBalanceSnapshot(
            entity_id=entity.id,
            ledger_account_id=petty_cash_acc.id,
            period_start=date(2024, 4, 1),
            period_end=date(2025, 3, 31),
            opening_balance=Decimal("0.00"),
            total_debits=Decimal("250.00"),
            total_credits=Decimal("0.00"),
            closing_balance=Decimal("250.00")
        )
        session.add(prior_snapshot)
        session.commit()

        accounts = session.query(LedgerAccount).filter_by(entity_id=entity.id).all()
        snapshots = session.query(TrialBalanceSnapshot).filter_by(entity_id=entity.id).all()

        exceptions = run_scrutiny(entity, accounts, snapshots)
        
        # Create a map of account_id -> name for quick lookup without requiring session relationship loads
        acc_name_map = {acc.id: acc.name for acc in accounts}

        # Verify exceptions at materiality >= 15000
        # Rahul Enterprises (Sundry Creditors, Debit balance 12,000) and Verma Traders (Sundry Debtors, Credit balance -8,000)
        # should be present as they are normal balance checks (categorical errors not subject to materiality).
        # Petty Cash Variance (continuity check, variance 150.00 < 15000) is filtered out.
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
