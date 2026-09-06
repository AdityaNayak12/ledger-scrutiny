"""Read-only derivation of the active canonical ledger dataset."""

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import BalanceCheckpoint, ImportBatch, JournalEntry, JournalLine, LedgerAccount
from app.ingestion.batches import _batch_dates, _financial_year, _is_journal, _source_family
from app.ingestion.reconciliation import compute_dataset_fingerprint, _signed_amount
from app.ingestion.schema import BatchKind, BatchStatus


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value


def _batch_year(batch: ImportBatch) -> int | None:
    return _financial_year(_as_date(_batch_dates(batch)[0]))


def _year_window(financial_year: int) -> tuple[date, date]:
    return date(financial_year, 4, 1), date(financial_year + 1, 3, 31)


def _normalise_financial_year(value: int | str | date | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, date):
        return _financial_year(value)
    if isinstance(value, str):
        return int(value[:4])
    return int(value)


def _batch_label(batch: ImportBatch) -> str:
    return f"batch {batch.id}"


def _money(value: Decimal) -> str:
    return format(value, "f")


def _account_key(account: LedgerAccount | None, account_id: int) -> str:
    if account is None:
        return f"ledger_account:{account_id}"
    return str(account.external_code or account.name or f"ledger_account:{account_id}")


def _base_result(entity_id: int, financial_year: int | None) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "financial_year": financial_year,
        "source_family": None,
        "active_batch_ids": [],
        "selected_batch_ids": [],
        "source_batch_ids": [],
        "baseline_batch_ids": [],
        "replaced_batch_ids": [],
        "excluded_batch_ids": [],
        "coverage": [],
        "gaps": [],
        "baseline_coverage": {
            "present": False,
            "complete": False,
            "balance_date": None,
            "account_count": 0,
        },
        "account_movements": {},
        "opening_balances": {},
        "closing_balances": {},
        "readiness": "PARTIAL",
        "dataset_fingerprint": compute_dataset_fingerprint(({
            "kind": "dataset",
            "entity_id": entity_id,
            "financial_year": financial_year,
        },)),
        "warnings": [],
        "errors": [],
    }


def _finish(result: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    if result["errors"]:
        result["readiness"] = "INVALID"
    elif partial:
        result["readiness"] = "PARTIAL"
    elif result["warnings"]:
        result["readiness"] = "READY_WITH_WARNINGS"
    else:
        result["readiness"] = "READY"
    result["selected_batch_ids"] = sorted(
        set(result["active_batch_ids"]) | set(result["baseline_batch_ids"])
    )
    result["source_batch_ids"] = result["selected_batch_ids"]
    return result


def _gaps(
    selected: list[tuple[ImportBatch, date, date]],
    start: date,
    end: date,
) -> list[dict[str, str]]:
    cursor = start
    gaps: list[dict[str, str]] = []
    for _batch, batch_start, batch_end in selected:
        batch_start = max(start, batch_start)
        batch_end = min(end, batch_end)
        if batch_end < start or batch_start > end:
            continue
        if batch_start > cursor:
            gaps.append({"start": cursor.isoformat(), "end": (batch_start - timedelta(days=1)).isoformat()})
        cursor = max(cursor, batch_end + timedelta(days=1))
    if cursor <= end:
        gaps.append({"start": cursor.isoformat(), "end": end.isoformat()})
    return gaps


def _select_journal_batches(
    batches: list[ImportBatch],
    financial_year: int,
    family: str,
    result: dict[str, Any],
) -> list[tuple[ImportBatch, date, date]]:
    start, end = _year_window(financial_year)
    dated: list[tuple[ImportBatch, date, date]] = []
    for batch in batches:
        batch_start, batch_end = (_as_date(value) for value in _batch_dates(batch))
        if batch_start is None or batch_end is None or batch_start > batch_end:
            result["errors"].append(f"{_batch_label(batch)} has invalid coverage metadata.")
            continue
        if _source_family(batch.source, batch.source_family) != family:
            continue
        if batch_start < start or batch_end > end:
            result["errors"].append(
                f"{_batch_label(batch)} coverage falls outside financial year {financial_year}."
            )
            continue
        dated.append((batch, batch_start, batch_end))

    by_coverage: dict[tuple[date, date], list[tuple[ImportBatch, date, date]]] = defaultdict(list)
    for item in dated:
        by_coverage[(item[1], item[2])].append(item)
    winners: list[tuple[ImportBatch, date, date]] = []
    for coverage, candidates in by_coverage.items():
        candidates.sort(key=lambda item: item[0].id or 0)
        winners.append(candidates[-1])
        result["replaced_batch_ids"].extend(item[0].id for item in candidates[:-1])

    annual = [item for item in winners if item[1] == start and item[2] == end]
    if annual:
        winner = max(annual, key=lambda item: item[0].id or 0)
        result["excluded_batch_ids"].extend(
            item[0].id for item in winners if item[0].id != winner[0].id
        )
        winners = [winner]

    winners.sort(key=lambda item: (item[1], item[2], item[0].id or 0))
    for previous, current in zip(winners, winners[1:]):
        if current[1] <= previous[2]:
            result["errors"].append(
                f"Active journal coverage overlaps between {_batch_label(previous[0])} "
                f"and {_batch_label(current[0])}."
            )

    result["coverage"] = [
        {
            "batch_id": batch.id,
            "start": batch_start.isoformat(),
            "end": batch_end.isoformat(),
        }
        for batch, batch_start, batch_end in winners
    ]
    result["gaps"] = _gaps(winners, start, end)
    return winners


def _batch_report_state(
    batches: list[ImportBatch],
    result: dict[str, Any],
) -> bool:
    partial = False
    for batch in batches:
        report = dict(batch.validation_report or {})
        readiness = report.get("readiness")
        if readiness == "INVALID" or report.get("errors"):
            result["errors"].extend(str(error) for error in report.get("errors", ()))
            if readiness == "INVALID" and not report.get("errors"):
                result["errors"].append(f"{_batch_label(batch)} has invalid validation state.")
        elif readiness == "PARTIAL" or not readiness:
            baseline = report.get("baseline_coverage") or {}
            batch_start, batch_end = (_as_date(value) for value in _batch_dates(batch))
            baseline_only = (
                readiness == "PARTIAL"
                and baseline.get("complete") is False
                and batch_start is not None
                and batch_end is not None
                and report.get("coverage_start") == batch_start.isoformat()
                and report.get("coverage_end") == batch_end.isoformat()
                and report.get("coverage_complete") is True
            )
            if not baseline_only:
                partial = True
        result["warnings"].extend(str(warning) for warning in report.get("warnings", ()))
    return partial


def _load_account_keys(session: Session, entity_id: int, account_ids: set[int]) -> dict[int, str]:
    accounts = session.execute(
        select(LedgerAccount).where(
            LedgerAccount.entity_id == entity_id,
            LedgerAccount.id.in_(account_ids),
        )
    ).scalars().all()
    by_id = {account.id: account for account in accounts}
    return {account_id: _account_key(by_id.get(account_id), account_id) for account_id in account_ids}


def _resolve_baseline(
    session: Session,
    entity_id: int,
    financial_year: int,
    family: str,
    active_batches: list[ImportBatch],
    selected_journals: list[tuple[ImportBatch, date, date]],
    result: dict[str, Any],
) -> tuple[dict[int, Decimal], set[int], bool]:
    year_start, _year_end = _year_window(financial_year)
    target_date = year_start - timedelta(days=1)

    checkpoint_batches = [
        batch
        for batch in active_batches
        if batch.kind == BatchKind.BALANCE_CHECKPOINT.value
        and _source_family(batch.source, batch.source_family) == family
    ]
    checkpoint_rows = session.execute(
        select(BalanceCheckpoint).where(
            BalanceCheckpoint.import_batch_id.in_({batch.id for batch in checkpoint_batches} or {-1}),
        ).order_by(BalanceCheckpoint.id)
    ).scalars().all()
    target_checkpoint_batches = [
        batch for batch in checkpoint_batches
        if any(
            row.import_batch_id == batch.id and row.balance_date == target_date
            for row in checkpoint_rows
        )
    ]
    if not target_checkpoint_batches and family == "tally":
        target_checkpoint_batches = [
            batch for batch in checkpoint_batches
            if any(
                row.import_batch_id == batch.id and row.balance_date == year_start
                for row in checkpoint_rows
            )
        ]
    checkpoint_batch = (
        max(target_checkpoint_batches, key=lambda batch: batch.id or 0)
        if target_checkpoint_batches
        else None
    )
    checkpoint_batch_ids = [checkpoint_batch.id] if checkpoint_batch else []
    checkpoint_source_ids = set(checkpoint_batch_ids)
    source_ids = checkpoint_source_ids or {batch.id for batch, _start, _end in selected_journals}
    rows = [
        row for row in checkpoint_rows
        if row.import_batch_id in checkpoint_source_ids
    ] if checkpoint_source_ids else session.execute(
        select(BalanceCheckpoint).where(
            BalanceCheckpoint.import_batch_id.in_(source_ids or {-1}),
        ).order_by(BalanceCheckpoint.id)
    ).scalars().all()
    target_rows = [row for row in rows if row.balance_date == target_date]
    if not target_rows and family == "tally":
        target_rows = [row for row in rows if row.balance_date == year_start]

    for batch_id in checkpoint_batch_ids:
        result["baseline_batch_ids"].append(batch_id)

    baseline_report = dict(checkpoint_batch.validation_report or {}) if checkpoint_batch else {}
    coverage_report = dict(baseline_report.get("baseline_coverage") or {})
    row_account_ids = {row.ledger_account_id for row in rows}
    accounts = session.execute(
        select(LedgerAccount).where(LedgerAccount.id.in_(row_account_ids or {-1}))
    ).scalars().all()
    accounts_by_id = {account.id: account for account in accounts}
    baseline_errors = []
    for row in rows:
        if row.entity_id != entity_id:
            baseline_errors.append(
                f"Balance checkpoint {row.id} does not belong to entity {entity_id}."
            )
        account = accounts_by_id.get(row.ledger_account_id)
        if account is None or account.entity_id != entity_id:
            baseline_errors.append(
                f"Balance checkpoint {row.id} account {row.ledger_account_id} "
                f"does not belong to entity {entity_id}."
            )

    account_ids = {row.ledger_account_id for row in target_rows}
    entity_accounts = session.execute(
        select(LedgerAccount).where(LedgerAccount.entity_id == entity_id)
    ).scalars().all()
    expected_codes = {
        _account_key(account, account.id): account.id
        for account in entity_accounts
    }
    balances: dict[int, Decimal] = {}
    duplicate_ids: set[int] = set()
    account_keys: dict[int, str] = {}
    if baseline_errors:
        result["errors"].extend(baseline_errors)
        baseline_complete = False
        account_ids = set()
        missing_codes: list[str] = []
        duplicate_codes: list[str] = []
    else:
        account_keys = _load_account_keys(session, entity_id, account_ids)
        for row in target_rows:
            if row.ledger_account_id in balances:
                duplicate_ids.add(row.ledger_account_id)
            balances[row.ledger_account_id] = Decimal(str(row.balance))
        missing_codes = sorted(set(expected_codes) - set(account_keys.values()))
        duplicate_codes = sorted(account_keys[account_id] for account_id in duplicate_ids)
        baseline_complete = bool(target_rows) and not missing_codes and not duplicate_ids
    if coverage_report and coverage_report.get("complete") is False:
        baseline_complete = False
    if baseline_report.get("readiness") == "INVALID" or baseline_report.get("errors"):
        result["errors"].extend(str(error) for error in baseline_report.get("errors", ()))
        if baseline_report.get("readiness") == "INVALID" and not baseline_report.get("errors"):
            result["errors"].append(f"{_batch_label(checkpoint_batch)} has invalid baseline validation.")

    baseline_date = target_rows[0].balance_date.isoformat() if target_rows else None
    row_currencies = {row.currency for row in target_rows if row.currency is not None}
    baseline_currency = (
        next(iter(row_currencies))
        if len(row_currencies) == 1
        else coverage_report.get("currency")
    )
    result["baseline_coverage"] = {
        "present": bool(target_rows),
        "complete": baseline_complete,
        "balance_date": baseline_date,
        "account_count": len(account_ids),
    }
    if baseline_currency is not None or "currency" in coverage_report:
        result["baseline_coverage"]["currency"] = baseline_currency
    if missing_codes:
        result["baseline_coverage"]["missing_account_codes"] = missing_codes
    if duplicate_codes:
        result["baseline_coverage"]["duplicate_account_codes"] = duplicate_codes
    if coverage_report:
        for key in ("signed_total",):
            if key in coverage_report:
                result["baseline_coverage"][key] = coverage_report[key]
    return balances, account_ids, baseline_complete


def resolve_active_dataset(
    session: Session,
    entity_id: int,
    financial_year: int | str | date | None = None,
) -> dict[str, Any]:
    """Resolve one entity/FY into a JSON-safe, immutable dataset description.

    The resolver never changes batch state. When historical rows contain an
    exact-coverage correction or an annual file alongside quarters, the
    newest correction or annual file wins in the returned resolution.
    """
    requested_year = _normalise_financial_year(financial_year)
    result = _base_result(entity_id, requested_year)
    active = session.execute(
        select(ImportBatch).where(
            ImportBatch.entity_id == entity_id,
            ImportBatch.status == BatchStatus.ACTIVE.value,
        ).order_by(ImportBatch.id)
    ).scalars().all()
    journal_active = [batch for batch in active if _is_journal(batch)]
    years = {_batch_year(batch) for batch in journal_active if _batch_year(batch) is not None}
    if requested_year is None:
        if len(years) > 1:
            result["errors"].append("Active journal batches span multiple financial years; specify financial_year.")
            return _finish(result)
        requested_year = next(iter(years), None)
        result["financial_year"] = requested_year
    if requested_year is None:
        result["gaps"] = []
        return _finish(result, partial=True)

    year_start, year_end = _year_window(requested_year)
    candidate_journals = []
    for batch in journal_active:
        batch_start, batch_end = (_as_date(value) for value in _batch_dates(batch))
        if (
            batch_start is not None
            and batch_end is not None
            and batch_start <= year_end
            and batch_end >= year_start
        ):
            candidate_journals.append(batch)
    families = sorted({_source_family(batch.source, batch.source_family) for batch in candidate_journals})
    if len(families) > 1:
        result["active_batch_ids"] = sorted(batch.id for batch in candidate_journals)
        result["errors"].append(
            f"Active source family conflict for financial year {requested_year}: {', '.join(families)}."
        )
        return _finish(result)
    family = families[0] if families else None
    result["source_family"] = family
    selected_journals: list[tuple[ImportBatch, date, date]] = []
    baseline_balances: dict[int, Decimal] = {}
    baseline_account_ids: set[int] = set()
    baseline_complete = False
    partial = False
    if family is not None:
        selected_journals = _select_journal_batches(candidate_journals, requested_year, family, result)
        result["active_batch_ids"] = [batch.id for batch, _start, _end in selected_journals]
        partial = bool(result["gaps"])
        partial = _batch_report_state([batch for batch, _start, _end in selected_journals], result) or partial
        baseline_balances, baseline_account_ids, baseline_complete = _resolve_baseline(
            session,
            entity_id,
            requested_year,
            family,
            active,
            selected_journals,
            result,
        )
        partial = partial or not baseline_complete
    else:
        result["gaps"] = [{"start": year_start.isoformat(), "end": year_end.isoformat()}]
        partial = True

    selected_ids = [batch.id for batch, _start, _end in selected_journals]
    movements: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
    if selected_ids:
        entries = session.execute(
            select(JournalEntry)
            .where(JournalEntry.import_batch_id.in_(selected_ids))
            .order_by(JournalEntry.posting_date, JournalEntry.id)
        ).scalars().all()
        entry_by_id = {entry.id: entry for entry in entries}
        lines = session.execute(
            select(JournalLine)
            .join(JournalEntry, JournalEntry.id == JournalLine.journal_entry_id)
            .where(JournalEntry.import_batch_id.in_(selected_ids))
            .order_by(JournalEntry.posting_date, JournalEntry.id, JournalLine.source_row_number)
        ).scalars().all()
        journal_errors: list[str] = []
        for entry in entries:
            if entry.entity_id != entity_id:
                journal_errors.append(
                    f"Journal entry {entry.id} does not belong to entity {entity_id}."
                )
        account_ids_in_journal = {line.ledger_account_id for line in lines}
        journal_accounts = session.execute(
            select(LedgerAccount).where(LedgerAccount.id.in_(account_ids_in_journal or {-1}))
        ).scalars().all()
        journal_accounts_by_id = {account.id: account for account in journal_accounts}
        for line in lines:
            parent = entry_by_id.get(line.journal_entry_id)
            if parent is None or parent.entity_id != entity_id:
                journal_errors.append(
                    f"Journal line {line.id} has a parent entry outside entity {entity_id}."
                )
            account = journal_accounts_by_id.get(line.ledger_account_id)
            if account is None or account.entity_id != entity_id:
                journal_errors.append(
                    f"Journal line {line.id} account {line.ledger_account_id} "
                    f"does not belong to entity {entity_id}."
                )
        if journal_errors:
            result["errors"].extend(journal_errors)
            return _finish(result)
        for line in lines:
            posting_date = _as_date(entry_by_id[line.journal_entry_id].posting_date)
            if posting_date is not None and year_start <= posting_date <= year_end:
                movements[line.ledger_account_id] += _signed_amount(line)

    account_ids = set(movements) | set(baseline_balances) | baseline_account_ids
    account_keys = _load_account_keys(session, entity_id, account_ids)
    result["account_movements"] = {
        account_keys[account_id]: _money(movements[account_id])
        for account_id in sorted(movements, key=lambda account_id: account_keys[account_id])
    }
    result["opening_balances"] = {
        account_keys[account_id]: _money(balance)
        for account_id, balance in sorted(
            baseline_balances.items(), key=lambda item: account_keys[item[0]]
        )
    }
    closing_ids = sorted(account_ids, key=lambda account_id: account_keys[account_id])
    result["closing_balances"] = {
        account_keys[account_id]: (
            _money(baseline_balances[account_id] + movements[account_id])
            if account_id in baseline_balances
            else None
        )
        for account_id in closing_ids
    }

    baseline_pairs = sorted(
        (account_keys[account_id], _money(balance))
        for account_id, balance in baseline_balances.items()
    )
    fingerprint_items: list[Any] = [batch for batch, _start, _end in selected_journals]
    fingerprint_items.append({
        "kind": BatchKind.BALANCE_CHECKPOINT.value,
        "source_family": family,
        "balance_date": result["baseline_coverage"]["balance_date"],
        "currency": result["baseline_coverage"].get("currency"),
        "account_balance_pairs": baseline_pairs,
    })
    result["dataset_fingerprint"] = compute_dataset_fingerprint(fingerprint_items)
    return _finish(result, partial=partial)


resolve_dataset = resolve_active_dataset


__all__ = ["resolve_active_dataset", "resolve_dataset"]
