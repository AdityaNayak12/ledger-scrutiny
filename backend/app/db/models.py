from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from sqlalchemy import Date, DateTime, ForeignKey, Integer, JSON, LargeBinary, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Organization(Base):
    """
    Represents a CA firm / accounting organization.
    """
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )

    # Relationships
    users: Mapped[list["User"]] = relationship("User", back_populates="organization", cascade="all, delete-orphan")
    entities: Mapped[list["Entity"]] = relationship("Entity", back_populates="organization", cascade="all, delete-orphan")


class User(Base):
    """
    Represents an auditor / user belonging to an Organization.
    """
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )

    # Relationships
    organization: Mapped["Organization"] = relationship("Organization", back_populates="users")


class Entity(Base):
    """
    Represents a client business entity belonging to an Organization.
    """
    __tablename__ = "entities"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    materiality_threshold: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    gstin: Mapped[Optional[str]] = mapped_column(String(15), nullable=True)
    sector: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    rule_pack: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Relationships
    organization: Mapped["Organization"] = relationship("Organization", back_populates="entities")
    accounts: Mapped[list["LedgerAccount"]] = relationship(
        "LedgerAccount", back_populates="entity", cascade="all, delete-orphan"
    )
    transactions: Mapped[list["Transaction"]] = relationship(
        "Transaction", back_populates="entity", cascade="all, delete-orphan"
    )
    snapshots: Mapped[list["TrialBalanceSnapshot"]] = relationship(
        "TrialBalanceSnapshot", back_populates="entity", cascade="all, delete-orphan"
    )
    journal_entries: Mapped[list["JournalEntry"]] = relationship(
        "JournalEntry", back_populates="entity", cascade="all, delete-orphan"
    )
    balance_checkpoints: Mapped[list["BalanceCheckpoint"]] = relationship(
        "BalanceCheckpoint", back_populates="entity", cascade="all, delete-orphan"
    )
    exceptions: Mapped[list["AuditException"]] = relationship(
        "AuditException", back_populates="entity", cascade="all, delete-orphan"
    )

class FinancialPeriod(Base):
    """
    Represents a financial period for an entity and its data source.
    """
    __tablename__ = "financial_periods"
    __table_args__ = (
        UniqueConstraint("entity_id", "period_start", "period_end", name="uq_financial_period_entity_dates"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False) # e.g. "tally_xml", "xlsx_trial_balance", "sap_gl_dump"

    # Relationships
    entity: Mapped["Entity"] = relationship("Entity")
    import_batches: Mapped[list["ImportBatch"]] = relationship(
        "ImportBatch", back_populates="financial_period", cascade="all, delete-orphan"
    )


class ImportBatch(Base):
    """Immutable record of one source-file ingestion attempt."""
    __tablename__ = "import_batches"
    __table_args__ = (
        UniqueConstraint(
            "entity_id",
            "content_sha256",
            name="uq_import_batch_entity_content_sha256",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True)
    financial_period_id: Mapped[int] = mapped_column(ForeignKey("financial_periods.id", ondelete="CASCADE"), nullable=False, index=True)
    uploaded_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    parser_version: Mapped[str] = mapped_column(String(50), nullable=False, default="1")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ACTIVE")
    kind: Mapped[str] = mapped_column(String(30), nullable=False, default="journal", server_default="journal")
    raw_source_bytes: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    coverage_start: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    coverage_end: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    source_family: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    source_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    validation_report: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    entity: Mapped["Entity"] = relationship("Entity")
    financial_period: Mapped["FinancialPeriod"] = relationship("FinancialPeriod", back_populates="import_batches")
    snapshots: Mapped[list["TrialBalanceSnapshot"]] = relationship("TrialBalanceSnapshot", back_populates="import_batch")
    transactions: Mapped[list["Transaction"]] = relationship("Transaction", back_populates="import_batch")
    scrutiny_runs: Mapped[list["ScrutinyRun"]] = relationship("ScrutinyRun", back_populates="import_batch")
    journal_entries: Mapped[list["JournalEntry"]] = relationship(
        "JournalEntry", back_populates="import_batch", cascade="all, delete-orphan"
    )
    balance_checkpoints: Mapped[list["BalanceCheckpoint"]] = relationship(
        "BalanceCheckpoint", back_populates="import_batch", cascade="all, delete-orphan"
    )


class LedgerAccount(Base):
    """
    Represents an individual ledger account belonging to an entity.
    """
    __tablename__ = "ledger_accounts"
    __table_args__ = (
        UniqueConstraint("entity_id", "name", name="uq_ledger_account_entity_name"),
        UniqueConstraint("entity_id", "external_code", name="uq_ledger_account_entity_external_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    external_code: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    group_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    normal_balance: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # 'debit' or 'credit'

    # Relationships
    entity: Mapped["Entity"] = relationship("Entity", back_populates="accounts")
    debit_transactions: Mapped[list["Transaction"]] = relationship(
        "Transaction", foreign_keys="[Transaction.debit_account_id]", back_populates="debit_account"
    )
    credit_transactions: Mapped[list["Transaction"]] = relationship(
        "Transaction", foreign_keys="[Transaction.credit_account_id]", back_populates="credit_account"
    )
    snapshots: Mapped[list["TrialBalanceSnapshot"]] = relationship(
        "TrialBalanceSnapshot", back_populates="ledger_account", cascade="all, delete-orphan"
    )
    exceptions: Mapped[list["AuditException"]] = relationship(
        "AuditException", back_populates="ledger_account"
    )
    journal_lines: Mapped[list["JournalLine"]] = relationship(
        "JournalLine", back_populates="ledger_account"
    )
    balance_checkpoints: Mapped[list["BalanceCheckpoint"]] = relationship(
        "BalanceCheckpoint", back_populates="ledger_account", cascade="all, delete-orphan"
    )


class JournalEntry(Base):
    """One source document in the canonical journal representation."""
    __tablename__ = "journal_entries"
    __table_args__ = (
        UniqueConstraint("import_batch_id", "source_document_id", name="uq_journal_entry_batch_document"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    import_batch_id: Mapped[int] = mapped_column(
        ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True)
    source_document_id: Mapped[str] = mapped_column(String(255), nullable=False)
    posting_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    document_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    document_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    narration: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)

    import_batch: Mapped["ImportBatch"] = relationship("ImportBatch", back_populates="journal_entries")
    entity: Mapped["Entity"] = relationship("Entity", back_populates="journal_entries")
    lines: Mapped[list["JournalLine"]] = relationship(
        "JournalLine", back_populates="journal_entry", cascade="all, delete-orphan"
    )


class JournalLine(Base):
    """One source row in a canonical journal entry."""
    __tablename__ = "journal_lines"
    __table_args__ = (
        UniqueConstraint("journal_entry_id", "source_row_number", name="uq_journal_line_entry_row"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    journal_entry_id: Mapped[int] = mapped_column(
        ForeignKey("journal_entries.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ledger_account_id: Mapped[int] = mapped_column(
        ForeignKey("ledger_accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    posting_key: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    quantity: Mapped[Optional[Decimal]] = mapped_column(Numeric(20, 4), nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    reference: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    clearing_document: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    profit_center: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    cost_center: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    text: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    supplier: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    wbs: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    purchasing_document: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    customer: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    dimensions: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    source_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    journal_entry: Mapped["JournalEntry"] = relationship("JournalEntry", back_populates="lines")
    ledger_account: Mapped["LedgerAccount"] = relationship("LedgerAccount", back_populates="journal_lines")


class BalanceCheckpoint(Base):
    """One signed account balance at a source-provided balance date."""
    __tablename__ = "balance_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "import_batch_id", "ledger_account_id", "balance_date", name="uq_balance_checkpoint_batch_account_date"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    import_batch_id: Mapped[int] = mapped_column(
        ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True)
    ledger_account_id: Mapped[int] = mapped_column(
        ForeignKey("ledger_accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    balance_date: Mapped[date] = mapped_column(Date, nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    currency: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    source_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    import_batch: Mapped["ImportBatch"] = relationship("ImportBatch", back_populates="balance_checkpoints")
    entity: Mapped["Entity"] = relationship("Entity", back_populates="balance_checkpoints")
    ledger_account: Mapped["LedgerAccount"] = relationship(
        "LedgerAccount", back_populates="balance_checkpoints"
    )


class Transaction(Base):
    """
    Represents a double-entry transaction record.
    """
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    import_batch_id: Mapped[Optional[int]] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=True, index=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    debit_account_id: Mapped[int] = mapped_column(
        ForeignKey("ledger_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    credit_account_id: Mapped[int] = mapped_column(
        ForeignKey("ledger_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    narration: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    voucher_type: Mapped[str] = mapped_column(String(100), nullable=False)
    source_voucher_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Relationships
    entity: Mapped["Entity"] = relationship("Entity", back_populates="transactions")
    debit_account: Mapped["LedgerAccount"] = relationship(
        "LedgerAccount", foreign_keys=[debit_account_id], back_populates="debit_transactions"
    )
    credit_account: Mapped["LedgerAccount"] = relationship(
        "LedgerAccount", foreign_keys=[credit_account_id], back_populates="credit_transactions"
    )
    import_batch: Mapped[Optional["ImportBatch"]] = relationship("ImportBatch", back_populates="transactions")


class TrialBalanceSnapshot(Base):
    """
    Represents a trial balance snapshot for a specific period for continuity checks.
    """
    __tablename__ = "trial_balance_snapshots"
    __table_args__ = (UniqueConstraint("import_batch_id", "ledger_account_id", name="uq_snapshot_batch_account"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    import_batch_id: Mapped[Optional[int]] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=True, index=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    ledger_account_id: Mapped[int] = mapped_column(
        ForeignKey("ledger_accounts.id", ondelete="CASCADE"), nullable=False
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    total_debits: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    total_credits: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    closing_balance: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)

    # Relationships
    entity: Mapped["Entity"] = relationship("Entity", back_populates="snapshots")
    ledger_account: Mapped["LedgerAccount"] = relationship("LedgerAccount", back_populates="snapshots")
    import_batch: Mapped[Optional["ImportBatch"]] = relationship("ImportBatch", back_populates="snapshots")


class ScrutinyRun(Base):
    """An immutable execution record for a versioned rule set over one import batch."""
    __tablename__ = "scrutiny_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True)
    financial_period_id: Mapped[int] = mapped_column(ForeignKey("financial_periods.id", ondelete="CASCADE"), nullable=False)
    import_batch_id: Mapped[Optional[int]] = mapped_column(ForeignKey("import_batches.id", ondelete="SET NULL"), nullable=True)
    triggered_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    rule_set_version: Mapped[str] = mapped_column(String(50), nullable=False, default="1")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="COMPLETED")
    summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    dataset_fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source_batch_ids: Mapped[Optional[list[int]]] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    import_batch: Mapped[Optional["ImportBatch"]] = relationship("ImportBatch", back_populates="scrutiny_runs")
    findings: Mapped[list["AuditException"]] = relationship("AuditException", back_populates="scrutiny_run")


class AuditException(Base):
    """
    Represents a scrutiny exception found during audit rules execution.
    """
    __tablename__ = "exceptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    scrutiny_run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("scrutiny_runs.id", ondelete="CASCADE"), nullable=True, index=True)
    fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    rule_version: Mapped[str] = mapped_column(String(50), nullable=False, default="1")
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    rule_name: Mapped[str] = mapped_column(String(100), nullable=False)
    ledger_account_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ledger_accounts.id", ondelete="CASCADE"), nullable=True
    )
    severity: Mapped[str] = mapped_column(String(50), nullable=False)  # 'error', 'warning', etc.
    message: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="PENDING", default="PENDING")
    auditor_notes: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )

    # Relationships
    entity: Mapped["Entity"] = relationship("Entity", back_populates="exceptions")
    ledger_account: Mapped[Optional["LedgerAccount"]] = relationship("LedgerAccount", back_populates="exceptions")
    scrutiny_run: Mapped[Optional["ScrutinyRun"]] = relationship("ScrutinyRun", back_populates="findings")
    review_actions: Mapped[list["ReviewAction"]] = relationship("ReviewAction", back_populates="exception", cascade="all, delete-orphan")


class ReviewAction(Base):
    """Append-only reviewer decision history for a scrutiny finding."""
    __tablename__ = "review_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    exception_id: Mapped[int] = mapped_column(ForeignKey("exceptions.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    auditor_notes: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    exception: Mapped["AuditException"] = relationship("AuditException", back_populates="review_actions")
