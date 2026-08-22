from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from sqlalchemy import Date, DateTime, ForeignKey, JSON, Numeric, String, UniqueConstraint, func
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
    exceptions: Mapped[list["AuditException"]] = relationship(
        "AuditException", back_populates="entity", cascade="all, delete-orphan"
    )

    # Transient attributes fallback mapping
    @property
    def financial_year_start(self) -> date:
        if hasattr(self, "_transient_fy_start") and self._transient_fy_start is not None:
            return self._transient_fy_start
        raise RuntimeError(
            "Entity.financial_year_start accessed before being set for this scrutiny run. "
            "This indicates a code path that skipped explicit period attachment."
        )

    @financial_year_start.setter
    def financial_year_start(self, value: date):
        self._transient_fy_start = value

    @property
    def financial_year_end(self) -> date:
        if hasattr(self, "_transient_fy_end") and self._transient_fy_end is not None:
            return self._transient_fy_end
        raise RuntimeError(
            "Entity.financial_year_end accessed before being set for this scrutiny run. "
            "This indicates a code path that skipped explicit period attachment."
        )

    @financial_year_end.setter
    def financial_year_end(self, value: date):
        self._transient_fy_end = value


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

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True)
    financial_period_id: Mapped[int] = mapped_column(ForeignKey("financial_periods.id", ondelete="CASCADE"), nullable=False, index=True)
    uploaded_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    parser_version: Mapped[str] = mapped_column(String(50), nullable=False, default="1")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ACTIVE")
    validation_report: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    entity: Mapped["Entity"] = relationship("Entity")
    financial_period: Mapped["FinancialPeriod"] = relationship("FinancialPeriod", back_populates="import_batches")
    snapshots: Mapped[list["TrialBalanceSnapshot"]] = relationship("TrialBalanceSnapshot", back_populates="import_batch")
    transactions: Mapped[list["Transaction"]] = relationship("Transaction", back_populates="import_batch")
    scrutiny_runs: Mapped[list["ScrutinyRun"]] = relationship("ScrutinyRun", back_populates="import_batch")


class LedgerAccount(Base):
    """
    Represents an individual ledger account belonging to an entity.
    """
    __tablename__ = "ledger_accounts"
    __table_args__ = (UniqueConstraint("entity_id", "name", name="uq_ledger_account_entity_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    group_name: Mapped[str] = mapped_column(String(255), nullable=False)
    normal_balance: Mapped[str] = mapped_column(String(10), nullable=False)  # 'debit' or 'credit'

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
