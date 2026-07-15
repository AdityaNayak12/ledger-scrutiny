from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Entity(Base):
    """
    Represents a client business entity.
    """
    __tablename__ = "entities"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    financial_year_start: Mapped[date] = mapped_column(Date, nullable=False)
    financial_year_end: Mapped[date] = mapped_column(Date, nullable=False)
    materiality_threshold: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)

    # Relationships
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


class LedgerAccount(Base):
    """
    Represents an individual ledger account belonging to an entity.
    """
    __tablename__ = "ledger_accounts"

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


class TrialBalanceSnapshot(Base):
    """
    Represents a trial balance snapshot for a specific period for continuity checks.
    """
    __tablename__ = "trial_balance_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
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


class AuditException(Base):
    """
    Represents a scrutiny exception found during audit rules execution.
    """
    __tablename__ = "exceptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    rule_name: Mapped[str] = mapped_column(String(100), nullable=False)
    ledger_account_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ledger_accounts.id", ondelete="CASCADE"), nullable=True
    )
    severity: Mapped[str] = mapped_column(String(50), nullable=False)  # 'error', 'warning', etc.
    message: Mapped[str] = mapped_column(String(1000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), default=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )

    # Relationships
    entity: Mapped["Entity"] = relationship("Entity", back_populates="exceptions")
    ledger_account: Mapped[Optional["LedgerAccount"]] = relationship("LedgerAccount", back_populates="exceptions")
