"""Source-agnostic records and contract values for ledger ingestion."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping


class BatchKind(str, Enum):
    JOURNAL = "journal"
    BALANCE_CHECKPOINT = "balance_checkpoint"


class BatchStatus(str, Enum):
    STAGED = "STAGED"
    ACTIVE = "ACTIVE"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"


class SourceFamily(str, Enum):
    TALLY = "tally"
    GL_UPLOAD = "gl_upload"
    GL = "gl_upload"


class JournalLineSide(str, Enum):
    DEBIT = "debit"
    CREDIT = "credit"


CANONICAL_DEBIT = JournalLineSide.DEBIT.value
CANONICAL_CREDIT = JournalLineSide.CREDIT.value
GL_REQUIRED_HEADERS = (
    "Document Number",
    "G/L Account",
    "Posting Date",
    "Amount in local currency",
)
DOCUMENT_BALANCE_TOLERANCE = Decimal("0.01")
SUPPORTED_BATCH_KINDS = tuple(kind.value for kind in BatchKind)
SUPPORTED_BATCH_STATUSES = tuple(status.value for status in BatchStatus)


@dataclass(frozen=True, slots=True)
class JournalLineRecord:
    """Canonical signed source row; debit is positive and credit negative."""

    source_row_number: int
    ledger_account_code: str
    amount: Decimal
    side: JournalLineSide
    posting_key: str | None = None
    quantity: Decimal | None = None
    currency: str | None = None
    reference: str | None = None
    clearing_document: str | None = None
    profit_center: str | None = None
    cost_center: str | None = None
    text: str | None = None
    supplier: str | None = None
    wbs: str | None = None
    purchasing_document: str | None = None
    customer: str | None = None
    dimensions: Mapping[str, Any] = field(default_factory=dict)
    source_metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class JournalEntryRecord:
    """Canonical source document containing every source row."""

    source_document_id: str
    posting_date: date
    document_date: date | None = None
    document_type: str | None = None
    narration: str | None = None
    lines: tuple[JournalLineRecord, ...] = ()
    source_family: SourceFamily | None = None


@dataclass(frozen=True, slots=True)
class BalanceCheckpointRecord:
    """Canonical signed account balance at a stated date."""

    ledger_account_code: str
    balance_date: date
    balance: Decimal
    currency: str | None = None
    source_family: SourceFamily | None = None
    source_metadata: Mapping[str, Any] = field(default_factory=dict)


# Descriptive aliases for callers that prefer the canonical prefix.
CanonicalJournalEntry = JournalEntryRecord
CanonicalJournalLine = JournalLineRecord
CanonicalBalanceCheckpoint = BalanceCheckpointRecord
