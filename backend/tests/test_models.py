from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import (
    AuditException,
    BalanceCheckpoint,
    Entity,
    FinancialPeriod,
    ImportBatch,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    Organization,
    ScrutinyRun,
    Transaction,
    TrialBalanceSnapshot,
    User,
)
from app.ingestion.schema import (
    BatchKind,
    BalanceCheckpointRecord,
    JournalEntryRecord,
    JournalLineRecord,
    JournalLineSide,
    SourceFamily,
)


def test_database_models_lifecycle():
    # Set up in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # 0. Create an Organization and User
        org = Organization(name="Test CA Firm")
        session.add(org)
        session.flush()

        user = User(
            organization_id=org.id,
            email="auditor@cafirm.com",
            hashed_password="hashed_pass_secret"
        )
        session.add(user)
        session.commit()

        assert org.id is not None
        assert user.id is not None
        assert user.organization.name == "Test CA Firm"

        # 1. Create and persist an Entity
        entity = Entity(
            organization_id=org.id,
            name="Acme Corp",
            materiality_threshold=Decimal("10000.00"),
        )
        session.add(entity)
        session.commit()

        assert entity.id is not None
        assert entity.name == "Acme Corp"
        assert entity.organization_id == org.id
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
            period_start=date(2025, 4, 1),
            period_end=date(2026, 3, 31),
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


def test_entity_transient_properties_fail_loud():
    import pytest
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        org = Organization(name="Test Org")
        session.add(org)
        session.flush()

        entity = Entity(organization_id=org.id, name="Fail Loud Corp", materiality_threshold=Decimal("10000.00"))
        session.add(entity)
        session.commit()
        entity_id = entity.id

        session.close()
        session = Session()

        loaded_entity = session.query(Entity).filter_by(id=entity_id).first()
        assert loaded_entity is not None

        assert not hasattr(loaded_entity, "financial_year_start")
        assert not hasattr(loaded_entity, "financial_year_end")

    finally:
        session.close()
        Base.metadata.drop_all(engine)


def test_canonical_models_preserve_multiline_source_and_lineage():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        org = Organization(name="Canonical Org")
        session.add(org)
        session.flush()
        entity = Entity(
            organization_id=org.id,
            name="Canonical Corp",
            materiality_threshold=Decimal("100.00"),
        )
        session.add(entity)
        session.flush()
        account = LedgerAccount(
            entity_id=entity.id,
            external_code="110000",
            name="Cash",
            group_name=None,
            normal_balance=None,
        )
        revenue_account = LedgerAccount(
            entity_id=entity.id,
            external_code="400000",
            name="Revenue",
            group_name=None,
            normal_balance=None,
        )
        period = FinancialPeriod(
            entity_id=entity.id,
            period_start=date(2025, 4, 1),
            period_end=date(2026, 3, 31),
            source="gl_upload",
        )
        session.add_all([account, revenue_account, period])
        session.flush()
        batch = ImportBatch(
            entity_id=entity.id,
            financial_period_id=period.id,
            source="gl_upload",
            source_family=SourceFamily.GL_UPLOAD.value,
            kind=BatchKind.JOURNAL.value,
            original_filename="gl.xlsx",
            content_sha256="a" * 64,
            parser_version="2",
            status="STAGED",
            raw_source_bytes=b"xlsx-bytes",
            coverage_start=date(2025, 4, 1),
            coverage_end=date(2025, 4, 30),
            source_metadata={"profile": "sap_gl"},
            validation_report={},
        )
        entry = JournalEntry(
            entity_id=entity.id,
            import_batch=batch,
            source_document_id="DOC-1",
            posting_date=date(2025, 4, 1),
            document_type="SA",
            narration="Cash sale",
        )
        entry.lines = [
            JournalLine(
                ledger_account=account,
                source_row_number=2,
                amount=Decimal("100.00"),
                side=JournalLineSide.DEBIT.value,
                posting_key="40",
                currency="INR",
                source_metadata={"text": "Cash sale"},
            ),
            JournalLine(
                ledger_account=revenue_account,
                source_row_number=3,
                amount=Decimal("-100.00"),
                side=JournalLineSide.CREDIT.value,
                posting_key="50",
                currency="INR",
                source_metadata={"text": "Cash sale"},
            ),
        ]
        session.add(batch)
        session.flush()
        checkpoint = BalanceCheckpoint(
            entity_id=entity.id,
            import_batch=batch,
            ledger_account=account,
            balance_date=date(2025, 3, 31),
            balance=Decimal("100.00"),
            currency="INR",
        )
        run = ScrutinyRun(
            entity_id=entity.id,
            financial_period_id=period.id,
            dataset_fingerprint="b" * 64,
            source_batch_ids=[batch.id],
            rule_set_version="1",
            status="COMPLETED",
            summary={},
        )
        session.add_all([checkpoint, run])
        session.commit()

        loaded_lines = session.query(JournalLine).order_by(JournalLine.source_row_number).all()
        loaded_line = loaded_lines[0]
        assert loaded_line.journal_entry.source_document_id == "DOC-1"
        assert loaded_line.ledger_account.external_code == "110000"
        assert loaded_line.amount == Decimal("100.00")
        assert loaded_line.source_metadata == {"text": "Cash sale"}
        assert len(loaded_lines) == 2
        assert {line.source_row_number for line in loaded_line.journal_entry.lines} == {2, 3}
        assert session.query(BalanceCheckpoint).one().balance == Decimal("100.00")
        loaded_batch = session.query(ImportBatch).one()
        assert loaded_batch.raw_source_bytes == b"xlsx-bytes"
        assert loaded_batch.coverage_start == date(2025, 4, 1)
        assert session.query(ScrutinyRun).one().source_batch_ids == [batch.id]
    finally:
        session.close()
        Base.metadata.drop_all(engine)


def test_canonical_schema_records_use_signed_decimal_contract():
    line = JournalLineRecord(
        source_row_number=2,
        ledger_account_code="110000",
        amount=Decimal("100.00"),
        side=JournalLineSide.DEBIT,
        currency="INR",
    )
    entry = JournalEntryRecord(
        source_document_id="DOC-1",
        posting_date=date(2025, 4, 1),
        lines=(line,),
        source_family=SourceFamily.GL_UPLOAD,
    )
    checkpoint = BalanceCheckpointRecord(
        ledger_account_code="110000",
        balance_date=date(2025, 3, 31),
        balance=Decimal("100.00"),
        source_family=SourceFamily.GL_UPLOAD,
    )

    assert entry.lines[0].amount == Decimal("100.00")
    assert entry.lines[0].side is JournalLineSide.DEBIT
    assert checkpoint.balance == Decimal("100.00")
