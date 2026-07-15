import os
from decimal import Decimal
from datetime import date
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import Entity, LedgerAccount, Transaction, TrialBalanceSnapshot
from app.ingestion.tally_parser import parse_tally_xml
from app.ingestion.tally_normalizer import normalize_tally_data


def test_tally_ingestion_end_to_end():
    # 1. Read the sample XML file
    xml_path = os.path.join(os.path.dirname(__file__), "sample_tally_export.xml")
    with open(xml_path, "rb") as f:
        xml_content = f.read()

    # 2. Parse the XML file
    parsed_data = parse_tally_xml(xml_content)
    assert parsed_data["entity"]["name"] == "Acme Audited Corp"
    assert parsed_data["entity"]["financial_year_start"] == date(2025, 4, 1)
    assert parsed_data["entity"]["financial_year_end"] == date(2026, 3, 31)
    assert len(parsed_data["ledgers"]) == 5
    assert len(parsed_data["vouchers"]) == 2

    # 3. Create clean in-memory SQLite DB
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # 4. Normalize and persist parsed data
        materiality = Decimal("15000.00")
        entity = normalize_tally_data(parsed_data, session, materiality_threshold=materiality)
        session.commit()

        # 5. Assertions on Entity
        assert entity.id is not None
        assert entity.name == "Acme Audited Corp"
        assert entity.materiality_threshold == materiality

        # 6. Assertions on Ledger Accounts and normal balances
        accounts = session.query(LedgerAccount).filter_by(entity_id=entity.id).all()
        assert len(accounts) == 5
        
        acc_map = {acc.name: acc for acc in accounts}
        assert acc_map["Cash-in-hand"].normal_balance == "debit"
        assert acc_map["Cash-in-hand"].group_name == "Cash-in-hand"
        
        assert acc_map["Owner Capital"].normal_balance == "credit"
        assert acc_map["Owner Capital"].group_name == "Capital Account"
        
        assert acc_map["Machinery"].normal_balance == "debit"
        assert acc_map["Machinery"].group_name == "Fixed Assets"
        
        assert acc_map["Sales Account"].normal_balance == "credit"
        assert acc_map["Sales Account"].group_name == "Sales Accounts"
        
        assert acc_map["ACME Debtors"].normal_balance == "debit"
        assert acc_map["ACME Debtors"].group_name == "Sundry Debtors"

        # 7. Assertions on Transactions
        transactions = session.query(Transaction).filter_by(entity_id=entity.id).all()
        assert len(transactions) == 2
        
        txn_map = {txn.source_voucher_id: txn for txn in transactions}
        
        # Voucher 1: Receipt (debit Cash-in-hand, credit Owner Capital)
        vch1 = txn_map["VCH-0001"]
        assert vch1.voucher_type == "Receipt"
        assert vch1.amount == Decimal("50000.00")
        assert vch1.debit_account.name == "Cash-in-hand"
        assert vch1.credit_account.name == "Owner Capital"
        assert vch1.narration == "Capital introduced by owner"
        assert vch1.date == date(2025, 4, 1)

        # Voucher 2: Sales (debit ACME Debtors, credit Sales Account)
        vch2 = txn_map["VCH-0002"]
        assert vch2.voucher_type == "Sales"
        assert vch2.amount == Decimal("20000.00")
        assert vch2.debit_account.name == "ACME Debtors"
        assert vch2.credit_account.name == "Sales Account"
        assert vch2.narration == "Service sales to ACME Debtors"
        assert vch2.date == date(2025, 5, 12)

        # 8. Assertions on Trial Balance Snapshots
        snapshots = session.query(TrialBalanceSnapshot).filter_by(entity_id=entity.id).all()
        assert len(snapshots) == 5
        
        snap_map = {snap.ledger_account.name: snap for snap in snapshots}
        
        # Cash-in-hand: opening=10000 (Dr), debit=50000, credit=0, closing=60000 (Dr)
        cash_snap = snap_map["Cash-in-hand"]
        assert cash_snap.opening_balance == Decimal("10000.00")
        assert cash_snap.total_debits == Decimal("50000.00")
        assert cash_snap.total_credits == Decimal("0.00")
        assert cash_snap.closing_balance == Decimal("60000.00")
        assert cash_snap.period_start == date(2025, 4, 1)
        assert cash_snap.period_end == date(2026, 3, 31)

        # Owner Capital: opening=0, debit=0, credit=50000, closing=-50000 (Cr)
        capital_snap = snap_map["Owner Capital"]
        assert capital_snap.opening_balance == Decimal("0.00")
        assert capital_snap.total_debits == Decimal("0.00")
        assert capital_snap.total_credits == Decimal("50000.00")
        assert capital_snap.closing_balance == Decimal("-50000.00")

        # Machinery: opening=150000 (Dr), debit=0, credit=0, closing=150000 (Dr)
        machinery_snap = snap_map["Machinery"]
        assert machinery_snap.opening_balance == Decimal("150000.00")
        assert machinery_snap.total_debits == Decimal("0.00")
        assert machinery_snap.total_credits == Decimal("0.00")
        assert machinery_snap.closing_balance == Decimal("150000.00")

        # Sales Account: opening=0, debit=0, credit=20000, closing=-20000 (Cr)
        sales_snap = snap_map["Sales Account"]
        assert sales_snap.opening_balance == Decimal("0.00")
        assert sales_snap.total_debits == Decimal("0.00")
        assert sales_snap.total_credits == Decimal("20000.00")
        assert sales_snap.closing_balance == Decimal("-20000.00")

    finally:
        session.close()
        Base.metadata.drop_all(engine)
