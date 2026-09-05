"""Source-agnostic validation totals and dataset identity."""

from collections.abc import Iterable, Mapping
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
import hashlib
import json
from typing import Any

from app.ingestion.schema import DOCUMENT_BALANCE_TOLERANCE, JournalLineSide


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (set, frozenset)):
        return sorted((_jsonable(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "__dict__"):
        return _jsonable({key: val for key, val in vars(value).items() if not key.startswith("_")})
    return value


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _batch_fingerprint_value(batch: Any) -> Any:
    content_hash = _field(batch, "content_sha256")
    if content_hash is None:
        return _jsonable(batch)
    period = _field(batch, "financial_period")
    return {
        "content_sha256": content_hash,
        "coverage_start": _field(batch, "coverage_start") or _field(period, "period_start"),
        "coverage_end": _field(batch, "coverage_end") or _field(period, "period_end"),
        "kind": _field(batch, "kind", "journal"),
        "source_family": _field(batch, "source_family"),
    }


def compute_dataset_fingerprint(items: Iterable[Any]) -> str:
    """Return stable identity independent of batch or record insertion order."""
    values = [_batch_fingerprint_value(item) for item in items]
    canonical = json.dumps(
        sorted((_jsonable(value) for value in values), key=lambda value: json.dumps(value, sort_keys=True)),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(f"ledger-dataset-v1:{canonical}".encode("utf-8")).hexdigest()


dataset_fingerprint = compute_dataset_fingerprint


def _lines(entry: Any) -> tuple[Any, ...]:
    return tuple(_field(entry, "lines", ()) or ())


def _signed_amount(line: Any) -> Decimal:
    amount = Decimal(str(_field(line, "amount", 0)))
    side = _field(line, "side")
    side = side.value if isinstance(side, Enum) else side
    if side == JournalLineSide.CREDIT.value:
        return -abs(amount)
    if side == JournalLineSide.DEBIT.value:
        return abs(amount)
    return amount


def _coverage_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {"present": False, "complete": False}
    if isinstance(value, Mapping):
        result = _jsonable(dict(value))
        result.setdefault("present", True)
        result.setdefault("complete", False)
        return result
    records = list(value)
    dates = [_field(record, "balance_date") for record in records if _field(record, "balance_date") is not None]
    return {
        "present": bool(records),
        "complete": bool(records),
        "account_count": len({_field(record, "ledger_account_code") for record in records}),
        "balance_date_min": min(dates).isoformat() if dates else None,
        "balance_date_max": max(dates).isoformat() if dates else None,
    }


def _readiness(
    *,
    rejected: int,
    unbalanced_documents: int,
    errors: list[Any],
    baseline_coverage: Mapping[str, Any],
    coverage_complete: bool | None,
    warnings: list[Any],
) -> str:
    if rejected or unbalanced_documents or errors:
        return "INVALID"
    period_complete = True if coverage_complete is None else coverage_complete
    if not baseline_coverage.get("complete") or not period_complete:
        return "PARTIAL"
    return "READY_WITH_WARNINGS" if warnings else "READY"


def build_reconciliation_report(
    entries: Iterable[Any] = (),
    *,
    baseline_coverage: Any = None,
    active_batch_ids: Iterable[int] = (),
    skipped: int = 0,
    rejected: int = 0,
    unmapped_account_codes: Iterable[str] = (),
    warnings: Iterable[Any] = (),
    errors: Iterable[Any] = (),
    input_rows: int | None = None,
    coverage_complete: bool | None = None,
    fingerprint: str | None = None,
) -> dict[str, Any]:
    """Build one JSON-safe report shared by Tally and GL ingestion."""
    entries = tuple(entries)
    lines = tuple(line for entry in entries for line in _lines(entry))
    input_count = len(lines) if input_rows is None else input_rows
    skipped = int(skipped)
    rejected = int(rejected)
    accepted = max(0, input_count - skipped - rejected)

    signed_amounts = tuple(_signed_amount(line) for line in lines)
    debit = sum((amount for amount in signed_amounts if amount > 0), Decimal("0"))
    credit = sum((-amount for amount in signed_amounts if amount < 0), Decimal("0"))
    net = debit - credit
    unbalanced = sum(
        abs(sum((_signed_amount(line) for line in _lines(entry)), Decimal("0"))) > DOCUMENT_BALANCE_TOLERANCE
        for entry in entries
    )
    account_codes = {
        _field(line, "ledger_account_code")
        for line in lines
        if _field(line, "ledger_account_code") not in (None, "")
    }
    unmapped = set(unmapped_account_codes)
    unmapped.update(
        _field(line, "ledger_account_code")
        for line in lines
        if _field(line, "ledger_account_code") in (None, "")
    )
    dates = [_field(entry, "posting_date") for entry in entries if _field(entry, "posting_date") is not None]
    warnings = list(warnings)
    errors = list(errors)
    baseline = _coverage_value(baseline_coverage)
    readiness = _readiness(
        rejected=rejected,
        unbalanced_documents=unbalanced,
        errors=errors,
        baseline_coverage=baseline,
        coverage_complete=coverage_complete,
        warnings=warnings,
    )
    active_ids = sorted(set(active_batch_ids))
    if fingerprint is None:
        fingerprint = compute_dataset_fingerprint(tuple(entries) + (baseline,))

    report = {
        "input": input_count,
        "accepted": accepted,
        "skipped": skipped,
        "rejected": rejected,
        "debit": format(debit, "f"),
        "credit": format(credit, "f"),
        "net": format(net, "f"),
        "document_count": len(entries),
        "unbalanced_document_count": int(unbalanced),
        "account_count": len(account_codes),
        "unmapped_account_count": len(unmapped),
        "account_codes": sorted(account_codes, key=str),
        "unmapped_account_codes": sorted(unmapped, key=str),
        "posting_date_min": min(dates).isoformat() if dates else None,
        "posting_date_max": max(dates).isoformat() if dates else None,
        "baseline_coverage": baseline,
        "active_batch_ids": active_ids,
        "dataset_fingerprint": fingerprint,
        "readiness": readiness,
        "warnings": [_jsonable(item) for item in warnings],
        "errors": [_jsonable(item) for item in errors],
    }
    if coverage_complete is not None:
        report["coverage_complete"] = coverage_complete
    report.update({
        "input_rows": input_count,
        "accepted_rows": accepted,
        "skipped_rows": skipped,
        "rejected_rows": rejected,
        "debit_total": report["debit"],
        "credit_total": report["credit"],
        "net_total": report["net"],
    })
    return report


def _batch_coverage(batch: Any) -> tuple[date | None, date | None]:
    period = _field(batch, "financial_period")
    start = _field(batch, "coverage_start") or _field(period, "period_start")
    end = _field(batch, "coverage_end") or _field(period, "period_end")
    if isinstance(start, datetime):
        start = start.date()
    if isinstance(end, datetime):
        end = end.date()
    return start, end


def _canonical_account_identities(session: Any, entity_id: int, batches: Iterable[Any]) -> set[str]:
    """Find distinct stored account identities for active and legacy batch rows."""
    from sqlalchemy import and_, or_, select
    from app.db.models import (
        BalanceCheckpoint,
        JournalEntry,
        JournalLine,
        LedgerAccount,
        Transaction,
        TrialBalanceSnapshot,
    )

    batches = tuple(batches)
    batch_ids = [batch.id for batch in batches]
    account_ids: set[int] = set()

    if batch_ids:
        account_ids.update(session.execute(
            select(JournalLine.ledger_account_id)
            .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
            .where(JournalEntry.import_batch_id.in_(batch_ids))
        ).scalars())

    legacy_transaction_filters = []
    legacy_snapshot_filters = []
    legacy_checkpoint_filters = []
    for batch in batches:
        start, end = _batch_coverage(batch)
        if start is None or end is None:
            legacy_transaction_filters.append(and_(
                Transaction.entity_id == entity_id,
                Transaction.import_batch_id.is_(None),
            ))
            legacy_snapshot_filters.append(and_(
                TrialBalanceSnapshot.entity_id == entity_id,
                TrialBalanceSnapshot.import_batch_id.is_(None),
            ))
            legacy_checkpoint_filters.append(and_(
                BalanceCheckpoint.entity_id == entity_id,
                BalanceCheckpoint.import_batch_id.is_(None),
            ))
            continue
        legacy_transaction_filters.append(and_(
            Transaction.entity_id == entity_id,
            Transaction.import_batch_id.is_(None),
            Transaction.date >= start,
            Transaction.date <= end,
        ))
        legacy_snapshot_filters.append(and_(
            TrialBalanceSnapshot.entity_id == entity_id,
            TrialBalanceSnapshot.import_batch_id.is_(None),
            TrialBalanceSnapshot.period_start <= end,
            TrialBalanceSnapshot.period_end >= start,
        ))
        legacy_checkpoint_filters.append(and_(
            BalanceCheckpoint.entity_id == entity_id,
            BalanceCheckpoint.import_batch_id.is_(None),
            BalanceCheckpoint.balance_date >= start,
            BalanceCheckpoint.balance_date <= end,
        ))

    if batch_ids or legacy_snapshot_filters:
        snapshot_filters = []
        if batch_ids:
            snapshot_filters.append(TrialBalanceSnapshot.import_batch_id.in_(batch_ids))
        snapshot_filters.extend(legacy_snapshot_filters)
        account_ids.update(session.execute(
            select(TrialBalanceSnapshot.ledger_account_id).where(
                TrialBalanceSnapshot.entity_id == entity_id,
                or_(*snapshot_filters),
            )
        ).scalars())

    if batch_ids or legacy_checkpoint_filters:
        checkpoint_filters = []
        if batch_ids:
            checkpoint_filters.append(BalanceCheckpoint.import_batch_id.in_(batch_ids))
        checkpoint_filters.extend(legacy_checkpoint_filters)
        account_ids.update(session.execute(
            select(BalanceCheckpoint.ledger_account_id).where(
                BalanceCheckpoint.entity_id == entity_id,
                or_(*checkpoint_filters),
            )
        ).scalars())

    transaction_filters = []
    if batch_ids:
        transaction_filters.append(Transaction.import_batch_id.in_(batch_ids))
    transaction_filters.extend(legacy_transaction_filters)
    if transaction_filters:
        for debit_id, credit_id in session.execute(
            select(Transaction.debit_account_id, Transaction.credit_account_id).where(
                Transaction.entity_id == entity_id,
                or_(*transaction_filters),
            )
        ):
            account_ids.update(account_id for account_id in (debit_id, credit_id) if account_id is not None)

    if not account_ids:
        return set()
    identities = session.execute(
        select(LedgerAccount.id, LedgerAccount.external_code, LedgerAccount.name).where(
            LedgerAccount.id.in_(account_ids)
        )
    )
    return {
        str(external_code or name or f"ledger_account:{account_id}")
        for account_id, external_code, name in identities
    }


def build_active_dataset_report(session: Any, entity_id: int) -> dict[str, Any]:
    """Summarize active journal batches for one entity."""
    from sqlalchemy import select
    from app.db.models import ImportBatch

    batches = session.execute(
        select(ImportBatch).where(
            ImportBatch.entity_id == entity_id,
            ImportBatch.status == "ACTIVE",
        ).order_by(ImportBatch.id)
    ).scalars().all()
    batches = [batch for batch in batches if batch.kind in (None, "journal")]
    reports = [batch.validation_report or {} for batch in batches]
    baseline = next(
        (report.get("baseline_coverage") for report in reports if report.get("baseline_coverage")),
        None,
    )
    report = build_reconciliation_report(
        (),
        baseline_coverage=baseline,
        active_batch_ids=[batch.id for batch in batches],
        fingerprint=compute_dataset_fingerprint(batches),
        coverage_complete=all(
            item.get("readiness") in ("READY", "READY_WITH_WARNINGS") for item in reports
        ) if reports else False,
        warnings=[warning for item in reports for warning in item.get("warnings", [])],
        errors=[error for item in reports for error in item.get("errors", [])],
    )
    account_codes = set()
    unmapped_account_codes = set()
    legacy_account_counts = []
    legacy_unmapped_counts = []
    for item in reports:
        if "account_codes" in item and item.get("account_codes") is not None:
            account_codes.update(item.get("account_codes", ()))
        else:
            legacy_account_counts.append(int(item.get("account_count", 0)))
        if "unmapped_account_codes" in item and item.get("unmapped_account_codes") is not None:
            unmapped_account_codes.update(item.get("unmapped_account_codes", ()))
        else:
            legacy_unmapped_counts.append(int(item.get("unmapped_account_count", 0)))
    needs_legacy_accounts = any(
        "account_codes" not in item or item.get("account_codes") is None
        for item in reports
    )
    stored_account_codes = (
        _canonical_account_identities(session, entity_id, batches)
        if needs_legacy_accounts
        else set()
    )
    account_codes.update(stored_account_codes)
    report["account_codes"] = sorted(account_codes, key=str)
    report["unmapped_account_codes"] = sorted(unmapped_account_codes, key=str)
    for key in (
        "input", "accepted", "skipped", "rejected", "document_count",
        "unbalanced_document_count",
    ):
        report[key] = sum(int(item.get(key, 0)) for item in reports)
    report["account_count"] = max(len(account_codes), max(legacy_account_counts, default=0))
    report["unmapped_account_count"] = max(
        len(unmapped_account_codes), max(legacy_unmapped_counts, default=0)
    )
    for key in ("debit", "credit"):
        report[key] = format(sum((Decimal(str(item.get(key, "0"))) for item in reports), Decimal("0")), "f")
    report["net"] = format(Decimal(report["debit"]) - Decimal(report["credit"]), "f")
    for key in ("debit_total", "credit_total", "net_total"):
        report[key] = report[key.removesuffix("_total")]
    dates = [
        item.get(key)
        for item in reports
        for key in ("posting_date_min", "posting_date_max")
        if item.get(key)
    ]
    report["posting_date_min"] = min(dates) if dates else None
    report["posting_date_max"] = max(dates) if dates else None
    report.update({
        "input_rows": report["input"],
        "accepted_rows": report["accepted"],
        "skipped_rows": report["skipped"],
        "rejected_rows": report["rejected"],
    })
    if any(item.get("readiness") == "INVALID" for item in reports) or report["unbalanced_document_count"]:
        report["readiness"] = "INVALID"
    elif any(item.get("readiness") == "PARTIAL" for item in reports) or not baseline:
        report["readiness"] = "PARTIAL"
    elif report["warnings"]:
        report["readiness"] = "READY_WITH_WARNINGS"
    else:
        report["readiness"] = "READY"
    return report


reconcile_records = build_reconciliation_report
