from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.base import Base
from db.models import Entity, LedgerAccount, Transaction, TrialBalanceSnapshot, AuditException


def test_database_models_lifecycle():
    # Set up in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # 1. Create and persist an Entity
        entity = Entity(
            name="Acme Corp",
            financial_year_start=date(2025, 4, 1),
            financial_year_end=date(2026, 3, 31),
            materiality_threshold=Decimal("10000.00"),
        )
        session.add(entity)
        session.commit()

        assert entity.id is not None
        assert entity.name == "Acme Corp"
        assert entity.materiality_threshold == Decimal("10000.00")

        # 2. Create Ledger Accounts
        cash_account = LedgerAccount(
            entity_id=entity.id,
            name="Cash-in-hand",
            group_name="Cash-in-hand",
            normal_balance="debit",
        )
        capital_account = LedgerAccount(
            entity_id=entity.id,
            name="Owner Capital",
            group_name="Capital Account",
            normal_balance="credit",
        )
        session.add_all([cash_account, capital_account])
        session.commit()

        assert cash_account.id is not None
        assert capital_account.id is not None
        assert len(entity.accounts) == 2

        # 3. Create a Transaction (Capital introduction)
        txn = Transaction(
            entity_id=entity.id,
            date=date(2025, 4, 1),
            debit_account_id=cash_account.id,
            credit_account_id=capital_account.id,
            amount=Decimal("50000.00"),
            narration="Capital introduced by owner",
            voucher_type="Receipt",
            source_voucher_id="VCH-0001",
        )
        session.add(txn)
        session.commit()

        assert txn.id is not None
        assert txn.amount == Decimal("50000.00")
        assert txn.debit_account.name == "Cash-in-hand"
        assert txn.credit_account.name == "Owner Capital"
        assert len(entity.transactions) == 1

        # 4. Create a Trial Balance Snapshot
        snapshot = TrialBalanceSnapshot(
            entity_id=entity.id,
            ledger_account_id=cash_account.id,
            period_start=date(2025, 4, 1),
            period_end=date(2025, 4, 30),
            opening_balance=Decimal("0.00"),
            total_debits=Decimal("50000.00"),
            total_credits=Decimal("0.00"),
            closing_balance=Decimal("50000.00"),
        )
        session.add(snapshot)
        session.commit()

        assert snapshot.id is not None
        assert snapshot.closing_balance == Decimal("50000.00")
        assert len(entity.snapshots) == 1
        assert len(cash_account.snapshots) == 1

        # 5. Create an AuditException
        exception = AuditException(
            entity_id=entity.id,
            rule_name="normal_balance_check",
            ledger_account_id=cash_account.id,
            severity="error",
            message="Cash-in-hand has credit balance",
        )
        session.add(exception)
        session.commit()

        assert exception.id is not None
        assert exception.rule_name == "normal_balance_check"
        assert exception.created_at is not None
        assert len(entity.exceptions) == 1
        assert len(cash_account.exceptions) == 1

    finally:
        session.close()
        Base.metadata.drop_all(engine)
