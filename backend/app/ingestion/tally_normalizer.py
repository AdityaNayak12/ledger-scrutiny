"""Normalize Tally data into legacy rows or canonical journal records."""

from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    BalanceCheckpoint,
    Entity,
    FinancialPeriod,
    ImportBatch,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    Organization,
    Transaction,
    TrialBalanceSnapshot,
)
from app.ingestion.reconciliation import build_reconciliation_report
from app.ingestion.schema import (
    DOCUMENT_BALANCE_TOLERANCE,
    JournalEntryRecord,
    JournalLineRecord,
    JournalLineSide,
    SourceFamily,
)
from app.ingestion.tally_parser import TallyConnectorError
from app.rules.account_groups import get_normal_balance


def decompose_entries(debits: List[Dict[str, Any]], credits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pair entries for the legacy two-sided Transaction compatibility table."""
    debit_list = [{"ledger_name": d["ledger_name"], "amount": d["amount"]} for d in debits if d["amount"] > 0]
    credit_list = [{"ledger_name": c["ledger_name"], "amount": c["amount"]} for c in credits if c["amount"] > 0]

    pairs = []
    d_idx, c_idx = 0, 0
    while d_idx < len(debit_list) and c_idx < len(credit_list):
        debit = debit_list[d_idx]
        credit = credit_list[c_idx]
        match_amount = min(debit["amount"], credit["amount"])
        if match_amount <= 0:
            break
        pairs.append({
            "debit_ledger": debit["ledger_name"],
            "credit_ledger": credit["ledger_name"],
            "amount": match_amount,
        })
        debit["amount"] -= match_amount
        credit["amount"] -= match_amount
        if debit["amount"] <= Decimal("0.0001"):
            d_idx += 1
        if credit["amount"] <= Decimal("0.0001"):
            c_idx += 1
    return pairs


def _as_date(value: Any, label: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise TallyConnectorError(f"Tally {label} must be a valid date.")


def _as_decimal(value: Any, label: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise TallyConnectorError(f"Tally {label} must be a valid amount.")
    try:
        amount = Decimal(str(value))
    except Exception:
        raise TallyConnectorError(f"Tally {label} must be a valid amount.") from None
    if not amount.is_finite():
        raise TallyConnectorError(f"Tally {label} must be a finite amount.")
    return amount


def _external_id(record: dict[str, Any]) -> str | None:
    for key in ("external_id", "external_code", "ledger_id", "guid", "master_id"):
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _normal_balance(group_name: str) -> str:
    try:
        return get_normal_balance(group_name)
    except ValueError as error:
        raise TallyConnectorError(str(error)) from error


def _generated_voucher_id(voucher: dict[str, Any], index: int) -> str:
    entries = tuple(
        (entry.get("ledger_name"), entry.get("type"), str(entry.get("amount")))
        for entry in voucher.get("entries", ())
    )
    value = repr((voucher.get("date"), voucher.get("voucher_type"), voucher.get("narration"), entries))
    return f"generated:{sha256(value.encode("utf-8")).hexdigest()}"


def _legacy_source_voucher_id(voucher: dict[str, Any]) -> str:
    source_id = str(voucher["source_voucher_id"])
    voucher_number = voucher.get("voucher_number")
    if voucher_number is not None and str(voucher_number).strip() and str(voucher_number).strip() != source_id:
        return f"{source_id}|VOUCHERNUMBER:{str(voucher_number).strip()}"
    return source_id


def _prepare_records(
    parsed_data: Dict[str, Any],
    *,
    period_start: date,
    period_end: date,
    canonical: bool,
    allow_legacy_missing_closing: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], tuple[JournalEntryRecord, ...]]:
    entity_data = parsed_data.get("entity")
    if not isinstance(entity_data, dict):
        raise TallyConnectorError("Tally data omitted the entity section.")
    if not str(entity_data.get("name") or "").strip():
        raise TallyConnectorError("Tally data omitted the company/entity name.")
    _as_date(entity_data.get("financial_year_start"), "financial year start")
    _as_date(entity_data.get("financial_year_end"), "financial year end")

    raw_ledgers = parsed_data.get("ledgers")
    if not isinstance(raw_ledgers, list) or not raw_ledgers:
        raise TallyConnectorError("Tally data returned no ledger accounts.")
    ledgers: list[dict[str, Any]] = []
    ledger_names: set[str] = set()
    ledger_ids: dict[str, str] = {}
    for ledger in raw_ledgers:
        if not isinstance(ledger, dict):
            raise TallyConnectorError("Tally data contains a malformed ledger record.")
        name = str(ledger.get("name") or "").strip()
        group_name = str(ledger.get("group_name") or "").strip()
        if not name:
            raise TallyConnectorError("Tally data contains a ledger without a name.")
        if not group_name:
            raise TallyConnectorError(f"Ledger '{name}' is missing its group hierarchy.")
        if name in ledger_names:
            raise TallyConnectorError(f"Tally data contains duplicate ledger name '{name}'.")
        ledger_names.add(name)
        external_id = _external_id(ledger)
        if external_id and external_id in ledger_ids and ledger_ids[external_id] != name:
            raise TallyConnectorError(f"Tally data contains duplicate ledger identifier '{external_id}'.")
        if external_id:
            ledger_ids[external_id] = name
        _normal_balance(group_name)
        item = dict(ledger)
        item.update({
            "name": name,
            "group_name": group_name,
            "external_id": external_id,
            "opening_balance": _as_decimal(ledger.get("opening_balance"), f"opening balance for ledger '{name}'"),
            "closing_balance": (
                None
                if ledger.get("closing_balance") is None
                else _as_decimal(ledger.get("closing_balance"), f"closing balance for ledger '{name}'")
            ),
        })
        if canonical and not allow_legacy_missing_closing and item["closing_balance"] is None:
            raise TallyConnectorError("Tally ledger data is missing a required closing balance.")
        ledgers.append(item)

    raw_vouchers = parsed_data.get("vouchers", [])
    if not isinstance(raw_vouchers, list):
        raise TallyConnectorError("Tally data contains malformed voucher data.")
    vouchers: list[dict[str, Any]] = []
    voucher_ids: set[str] = set()
    canonical_entries: list[JournalEntryRecord] = []
    ledgers_by_name = {ledger["name"]: ledger for ledger in ledgers}
    for voucher_index, raw_voucher in enumerate(raw_vouchers):
        if not isinstance(raw_voucher, dict):
            raise TallyConnectorError("Tally data contains a malformed voucher record.")
        voucher_date = _as_date(raw_voucher.get("date"), "voucher date")
        voucher_type = str(raw_voucher.get("voucher_type") or "").strip()
        if not voucher_type:
            raise TallyConnectorError(f"Voucher on {voucher_date} is missing its type.")
        voucher_number = raw_voucher.get("voucher_number")
        voucher_number = None if voucher_number is None else str(voucher_number).strip() or None
        if canonical and not period_start <= voucher_date <= period_end:
            raise TallyConnectorError(
                f"Voucher on {voucher_date} is outside selected period {period_start} to {period_end}."
            )
        entries = raw_voucher.get("entries")
        if not isinstance(entries, list) or not entries:
            raise TallyConnectorError(f"Voucher on {voucher_date} contains no ledger entries.")

        source_id = str(
            raw_voucher.get("source_voucher_id")
            or raw_voucher.get("voucher_number")
            or _generated_voucher_id(raw_voucher, voucher_index)
        ).strip()
        if not source_id:
            raise TallyConnectorError(f"Voucher on {voucher_date} is missing a stable identifier.")
        if source_id in voucher_ids:
            raise TallyConnectorError(f"Tally data contains duplicate voucher identifier '{source_id}'.")
        voucher_ids.add(source_id)

        prepared_entries: list[dict[str, Any]] = []
        line_records: list[JournalLineRecord] = []
        signed_total = Decimal("0")
        for line_index, raw_entry in enumerate(entries, 1):
            if not isinstance(raw_entry, dict):
                raise TallyConnectorError(f"Voucher '{source_id}' contains a malformed ledger entry.")
            ledger_name = str(raw_entry.get("ledger_name") or "").strip()
            if ledger_name not in ledger_names:
                raise TallyConnectorError(
                    f"Voucher '{source_id}' references unknown ledger '{ledger_name}'."
                )
            entry_type = str(raw_entry.get("type") or "").strip().lower()
            if entry_type not in {"debit", "credit"}:
                raise TallyConnectorError(f"Voucher '{source_id}' has an invalid ledger entry side.")
            amount = abs(_as_decimal(raw_entry.get("amount"), f"amount in voucher '{source_id}'"))
            signed_amount = amount if entry_type == "debit" else -amount
            signed_total += signed_amount
            source_row_number = raw_entry.get("source_row_number", line_index)
            try:
                source_row_number = int(source_row_number)
            except (TypeError, ValueError):
                raise TallyConnectorError(f"Voucher '{source_id}' has an invalid source row number.") from None
            if source_row_number < 1:
                raise TallyConnectorError(f"Voucher '{source_id}' has an invalid source row number.")
            if any(line.source_row_number == source_row_number for line in line_records):
                raise TallyConnectorError(f"Voucher '{source_id}' repeats source row {source_row_number}.")
            prepared_entry = dict(raw_entry)
            prepared_entry.update({
                "ledger_name": ledger_name,
                "type": entry_type,
                "amount": amount,
                "source_row_number": source_row_number,
            })
            prepared_entries.append(prepared_entry)
            ledger = ledgers_by_name[ledger_name]
            ledger_code = ledger["external_id"] or ledger["name"]
            source_metadata = dict(raw_entry.get("source_metadata") or {})
            source_metadata.update({
                "tally_ledger_name": ledger_name,
                "tally_ledger_id": ledger.get("external_id"),
                "tally_entry_index": line_index,
                "tally_voucher_id": source_id,
                "tally_voucher_number": voucher_number,
                "tally_group_name": ledger["group_name"],
                "tally_group_hierarchy": list(ledger.get("group_hierarchy") or [ledger["group_name"]]),
                "tally_group_identifiers": list(ledger.get("group_hierarchy_records") or []),
            })
            if raw_entry.get("source_line_id"):
                source_metadata["source_line_id"] = str(raw_entry["source_line_id"])
            line_records.append(JournalLineRecord(
                source_row_number=source_row_number,
                ledger_account_code=ledger_code,
                amount=signed_amount,
                side=JournalLineSide.DEBIT if signed_amount >= 0 else JournalLineSide.CREDIT,
                dimensions=dict(raw_entry.get("dimensions") or {}),
                source_metadata=source_metadata,
            ))
        if abs(signed_total) > DOCUMENT_BALANCE_TOLERANCE:
            raise TallyConnectorError(
                f"Voucher '{source_id}' on {voucher_date} does not balance within ₹0.01: "
                f"signed balance is {signed_total}."
            )
        document_date = raw_voucher.get("document_date")
        document_date = _as_date(document_date, "document date") if document_date is not None else None
        prepared_voucher = dict(raw_voucher)
        prepared_voucher.update({
            "date": voucher_date,
            "document_date": document_date,
            "voucher_type": voucher_type,
            "source_voucher_id": source_id,
            "voucher_number": voucher_number,
            "entries": prepared_entries,
        })
        vouchers.append(prepared_voucher)
        if period_start <= voucher_date <= period_end:
            canonical_entries.append(JournalEntryRecord(
                source_document_id=source_id,
                posting_date=voucher_date,
                document_date=document_date,
                document_type=voucher_type,
                narration=raw_voucher.get("narration"),
                lines=tuple(line_records),
                source_family=SourceFamily.TALLY,
            ))
    return ledgers, vouchers, tuple(canonical_entries)


def _resolve_entity(
    parsed_data: Dict[str, Any],
    session: Session,
    *,
    entity_id: int | None,
    organization_id: int | None,
    materiality_threshold: Decimal,
) -> Entity:
    entity_name = str(parsed_data["entity"]["name"]).strip()
    if entity_id is not None:
        entity = session.execute(select(Entity).where(Entity.id == entity_id)).scalar_one_or_none()
        if entity is None:
            raise ValueError(f"Entity with ID {entity_id} not found.")
        return entity

    entity = session.execute(select(Entity).where(Entity.name == entity_name)).scalar_one_or_none()
    if entity is not None:
        entity.materiality_threshold = materiality_threshold
        return entity
    if organization_id is None:
        organization = session.execute(select(Organization)).scalars().first()
        if organization is None:
            organization = Organization(name="Default Organization")
            session.add(organization)
            session.flush()
        organization_id = organization.id
    entity = Entity(
        organization_id=organization_id,
        name=entity_name,
        materiality_threshold=materiality_threshold,
    )
    session.add(entity)
    session.flush()
    return entity


def _selected_period(
    parsed_data: Dict[str, Any],
    session: Session,
    *,
    clear_only_period: bool,
    target_period_start: Optional[Any],
    target_period_end: Optional[Any],
    import_batch_id: int | None,
) -> tuple[date, date, ImportBatch | None]:
    entity_data = parsed_data.get("entity")
    if not isinstance(entity_data, dict):
        raise TallyConnectorError("Tally data omitted the entity section.")
    fy_start = _as_date(entity_data.get("financial_year_start"), "financial year start")
    fy_end = _as_date(entity_data.get("financial_year_end"), "financial year end")
    if fy_start > fy_end:
        raise TallyConnectorError(f"Tally financial year start {fy_start} is after end {fy_end}.")
    target_start = _as_date(target_period_start, "selected period start") if target_period_start is not None else None
    target_end = _as_date(target_period_end, "selected period end") if target_period_end is not None else None
    if (target_start is None) != (target_end is None):
        raise TallyConnectorError("Selected period requires both start and end dates.")

    batch = None
    if import_batch_id is not None:
        batch = session.get(ImportBatch, import_batch_id)
        if batch is None:
            raise ValueError(f"Import batch {import_batch_id} not found.")
        if batch.kind not in (None, "journal"):
            raise ValueError(f"Import batch {import_batch_id} is not a journal batch.")
        if batch.source_family not in (None, SourceFamily.TALLY.value):
            raise ValueError(f"Import batch {import_batch_id} is not a Tally batch.")
        if batch.status not in ("STAGED", "ACTIVE"):
            raise ValueError(f"Tally normalization requires a staged or active batch; batch {import_batch_id} is {batch.status}.")
        if target_start is None and batch.coverage_start is not None and batch.coverage_end is not None:
            target_start, target_end = batch.coverage_start, batch.coverage_end

    if import_batch_id is not None and batch is not None:
        batch_start, batch_end = batch.coverage_start, batch.coverage_end
        if target_start is not None and (batch_start, batch_end) != (target_start, target_end):
            raise ValueError("Selected period does not match the import batch period.")
        if batch_start is not None and batch_end is not None:
            target_start, target_end = batch_start, batch_end
        if target_start is not None and (fy_start != target_start or fy_end != target_end):
            raise ValueError("Tally source period does not match the import batch period.")
    if clear_only_period and target_start is not None and (fy_start != target_start or fy_end != target_end):
        raise ValueError(
            f"Uploaded XML period ({fy_start} to {fy_end}) does not match "
            f"the currently selected period ({target_start} to {target_end}) for Re-upload."
        )
    return (target_start or fy_start), (target_end or fy_end), batch


def _account_map(
    session: Session,
    entity: Entity,
    ledgers: list[dict[str, Any]],
) -> dict[str, LedgerAccount]:
    existing = session.execute(select(LedgerAccount).where(LedgerAccount.entity_id == entity.id)).scalars().all()
    by_name = {account.name: account for account in existing}
    by_code = {account.external_code: account for account in existing if account.external_code}
    plans: dict[str, LedgerAccount | None] = {}
    for ledger in ledgers:
        name = ledger["name"]
        code = ledger["external_id"]
        by_name_account = by_name.get(name)
        by_code_account = by_code.get(code) if code else None
        if by_name_account is not None and by_code_account is not None and by_name_account.id != by_code_account.id:
            raise TallyConnectorError(f"Ledger '{name}' and external identifier '{code}' identify different accounts.")
        account = by_code_account or by_name_account
        if account is not None and account.name != name and by_name_account is not None:
            raise TallyConnectorError(f"Tally account rename would collide with existing ledger '{name}'.")
        plans[name] = account
    for ledger in ledgers:
        account = plans[ledger["name"]]
        if account is None:
            continue
        code = ledger["external_id"]
        if code and account.external_code not in (None, code):
            raise TallyConnectorError(f"Ledger '{ledger['name']}' has conflicting external identifiers.")
        if account.name != ledger["name"] and ledger["name"] in by_name:
            raise TallyConnectorError(f"Tally account rename would collide with existing ledger '{ledger['name']}'.")
    return plans  # type: ignore[return-value]


def _upsert_accounts(
    session: Session,
    entity: Entity,
    ledgers: list[dict[str, Any]],
    plans: dict[str, LedgerAccount],
) -> dict[str, LedgerAccount]:
    result: dict[str, LedgerAccount] = {}
    for ledger in ledgers:
        account = plans.get(ledger["name"])
        normal_balance = _normal_balance(ledger["group_name"])
        if account is None:
            account = LedgerAccount(
                entity_id=entity.id,
                external_code=ledger["external_id"],
                name=ledger["name"],
                group_name=ledger["group_name"],
                normal_balance=normal_balance,
            )
            session.add(account)
            session.flush()
        else:
            if account.name != ledger["name"]:
                account.name = ledger["name"]
            if ledger["external_id"]:
                account.external_code = ledger["external_id"]
            if account.group_name not in (None, ledger["group_name"]):
                raise TallyConnectorError(
                    f"Ledger '{ledger['name']}' has conflicting group hierarchy "
                    f"('{account.group_name}' versus '{ledger['group_name']}')."
                )
            account.group_name = ledger["group_name"]
            account.normal_balance = normal_balance
        result[ledger["name"]] = account
    return result


def _canonical_report(
    entries: tuple[JournalEntryRecord, ...],
    period_start: date,
    period_end: date,
    ledgers: list[dict[str, Any]],
) -> dict[str, Any]:
    report = build_reconciliation_report(entries, coverage_complete=True)
    report.update({
        "parser": "tally_xml",
        "source_family": SourceFamily.TALLY.value,
        "coverage_start": period_start.isoformat(),
        "coverage_end": period_end.isoformat(),
        "skip_reasons": [],
        "tally_group_hierarchy": {
            ledger["name"]: list(ledger.get("group_hierarchy_records") or [])
            for ledger in ledgers
        },
        "tally_voucher_identifiers": [
            {
                "source_voucher_id": entry.source_document_id,
                "voucher_number": next(
                    (
                        line.source_metadata.get("tally_voucher_number")
                        for line in entry.lines
                        if line.source_metadata.get("tally_voucher_number")
                    ),
                    None,
                ),
            }
            for entry in entries
        ],
    })
    return report


def _has_canonical_records(session: Session, batch_id: int) -> bool:
    return bool(
        session.scalar(
            select(func.count()).select_from(JournalEntry).where(JournalEntry.import_batch_id == batch_id)
        )
        or session.scalar(
            select(func.count()).select_from(BalanceCheckpoint).where(BalanceCheckpoint.import_batch_id == batch_id)
        )
    )


def _is_legacy_compatibility_batch(session: Session, batch: ImportBatch | None) -> bool:
    return bool(
        batch is not None
        and batch.status == "ACTIVE"
        and batch.source == "tally_xml"
        and (batch.validation_report or {}).get("parser") == "tally_xml"
        and not _has_canonical_records(session, batch.id)
    )


def normalize_tally_data(
    parsed_data: Dict[str, Any],
    session: Session,
    materiality_threshold: Decimal = Decimal("0.00"),
    entity_id: Optional[int] = None,
    organization_id: Optional[int] = None,
    clear_only_period: bool = False,
    target_period_start: Optional[Any] = None,
    target_period_end: Optional[Any] = None,
    import_batch_id: Optional[int] = None,
) -> Entity:
    """Validate Tally data, then write canonical batch or legacy direct rows."""
    period_start, period_end, batch = _selected_period(
        parsed_data,
        session,
        clear_only_period=clear_only_period,
        target_period_start=target_period_start,
        target_period_end=target_period_end,
        import_batch_id=import_batch_id,
    )
    # ponytail: the legacy XML upload auto-activates before normalization; keep its old rows until Task 8 stages it.
    allow_legacy_missing_closing = _is_legacy_compatibility_batch(session, batch)
    canonical = import_batch_id is not None
    ledgers, vouchers, canonical_entries = _prepare_records(
        parsed_data,
        period_start=period_start,
        period_end=period_end,
        canonical=canonical,
        allow_legacy_missing_closing=allow_legacy_missing_closing,
    )
    account_plans = None
    entity = _resolve_entity(
        parsed_data,
        session,
        entity_id=entity_id,
        organization_id=organization_id,
        materiality_threshold=materiality_threshold,
    )
    if batch is not None and batch.entity_id != entity.id:
        raise ValueError(f"Import batch {batch.id} does not belong to entity {entity.id}.")

    account_plans = _account_map(session, entity, ledgers)
    period = session.execute(select(FinancialPeriod).where(
        FinancialPeriod.entity_id == entity.id,
        FinancialPeriod.period_start == period_start,
        FinancialPeriod.period_end == period_end,
    )).scalars().first()
    if period is None:
        session.add(FinancialPeriod(
            entity_id=entity.id,
            period_start=period_start,
            period_end=period_end,
            source="tally_xml",
        ))
    if canonical:
        existing_entries = session.scalar(
            select(func.count()).select_from(JournalEntry).where(JournalEntry.import_batch_id == import_batch_id)
        )
        existing_lines = session.scalar(
            select(func.count()).select_from(JournalLine).join(JournalEntry).where(JournalEntry.import_batch_id == import_batch_id)
        )
        existing_checkpoints = session.scalar(
            select(func.count()).select_from(BalanceCheckpoint).where(BalanceCheckpoint.import_batch_id == import_batch_id)
        )
        expected_lines = sum(len(entry.lines) for entry in canonical_entries)
        if existing_entries or existing_lines or existing_checkpoints:
            if existing_entries == len(canonical_entries) and existing_lines == expected_lines:
                return entity
            raise TallyConnectorError(f"Import batch {import_batch_id} already contains partial canonical records.")
    with session.begin_nested():
        accounts = _upsert_accounts(session, entity, ledgers, account_plans or {})
        if canonical:
            assert batch is not None
            report = _canonical_report(canonical_entries, period_start, period_end, ledgers)
            for entry_record in canonical_entries:
                journal_entry = JournalEntry(
                    import_batch_id=batch.id,
                    entity_id=entity.id,
                    source_document_id=entry_record.source_document_id,
                    posting_date=entry_record.posting_date,
                    document_date=entry_record.document_date,
                    document_type=entry_record.document_type,
                    narration=entry_record.narration,
                )
                session.add(journal_entry)
                for line_record in entry_record.lines:
                    session.add(JournalLine(
                        journal_entry=journal_entry,
                        ledger_account=accounts[line_record.source_metadata["tally_ledger_name"]],
                        source_row_number=line_record.source_row_number,
                        amount=line_record.amount,
                        side=line_record.side.value,
                        dimensions=dict(line_record.dimensions),
                        source_metadata=dict(line_record.source_metadata),
                    ))

            movements = {ledger["name"]: Decimal("0") for ledger in ledgers}
            for voucher in vouchers:
                if period_start <= voucher["date"] <= period_end:
                    for entry in voucher["entries"]:
                        signed_amount = entry["amount"] if entry["type"] == "debit" else -entry["amount"]
                        movements[entry["ledger_name"]] += signed_amount
            for ledger in ledgers:
                account = accounts[ledger["name"]]
                closing = ledger["closing_balance"]
                if closing is None:
                    if canonical and not allow_legacy_missing_closing:
                        raise TallyConnectorError("Tally ledger data is missing a required closing balance.")
                    closing = ledger["opening_balance"] + movements[ledger["name"]]
                checkpoint_values = (
                    ((period_start, ledger["opening_balance"], "opening_closing"),)
                    if period_start == period_end
                    else ((period_start, ledger["opening_balance"], "opening"), (period_end, closing, "closing"))
                )
                for balance_date, balance, balance_type in checkpoint_values:
                    session.add(BalanceCheckpoint(
                        import_batch_id=batch.id,
                        entity_id=entity.id,
                        ledger_account_id=account.id,
                        balance_date=balance_date,
                        balance=balance,
                        source_metadata={"source": "tally", "balance_type": balance_type},
                    ))
            for ledger in ledgers:
                debits = sum(
                    (entry["amount"] for voucher in vouchers if period_start <= voucher["date"] <= period_end
                     for entry in voucher["entries"]
                     if entry["ledger_name"] == ledger["name"] and entry["type"] == "debit"),
                    Decimal("0"),
                )
                credits = sum(
                    (entry["amount"] for voucher in vouchers if period_start <= voucher["date"] <= period_end
                     for entry in voucher["entries"]
                     if entry["ledger_name"] == ledger["name"] and entry["type"] == "credit"),
                    Decimal("0"),
                )
                closing = ledger["closing_balance"]
                if closing is None:
                    if canonical and not allow_legacy_missing_closing:
                        raise TallyConnectorError("Tally ledger data is missing a required closing balance.")
                    closing = ledger["opening_balance"] + debits - credits
                session.add(TrialBalanceSnapshot(
                    import_batch_id=batch.id,
                    entity_id=entity.id,
                    ledger_account_id=accounts[ledger["name"]].id,
                    period_start=period_start,
                    period_end=period_end,
                    opening_balance=ledger["opening_balance"],
                    total_debits=debits,
                    total_credits=credits,
                    closing_balance=closing,
                ))
            for voucher in vouchers:
                if not period_start <= voucher["date"] <= period_end:
                    continue
                debits = [entry for entry in voucher["entries"] if entry["type"] == "debit"]
                credits = [entry for entry in voucher["entries"] if entry["type"] == "credit"]
                for pair in decompose_entries(debits, credits):
                    session.add(Transaction(
                        import_batch_id=batch.id,
                        entity_id=entity.id,
                        date=voucher["date"],
                        debit_account_id=accounts[pair["debit_ledger"]].id,
                        credit_account_id=accounts[pair["credit_ledger"]].id,
                        amount=pair["amount"],
                        narration=voucher.get("narration"),
                        voucher_type=voucher["voucher_type"],
                        source_voucher_id=_legacy_source_voucher_id(voucher),
                    ))
            batch.validation_report = {**(batch.validation_report or {}), **report}
            batch.source_metadata = {
                **(batch.source_metadata or {}),
                "parser": "tally_xml",
                "ledger_count": len(ledgers),
                "voucher_count": len(vouchers),
            }
        else:
            session.execute(delete(TrialBalanceSnapshot).where(
                TrialBalanceSnapshot.entity_id == entity.id,
                TrialBalanceSnapshot.period_start == period_start,
                TrialBalanceSnapshot.period_end == period_end,
            ))
            session.execute(delete(Transaction).where(
                Transaction.entity_id == entity.id,
                Transaction.date >= period_start,
                Transaction.date <= period_end,
            ))
            for ledger in ledgers:
                debits = sum(
                    (entry["amount"] for voucher in vouchers if period_start <= voucher["date"] <= period_end
                     for entry in voucher["entries"]
                     if entry["ledger_name"] == ledger["name"] and entry["type"] == "debit"),
                    Decimal("0"),
                )
                credits = sum(
                    (entry["amount"] for voucher in vouchers if period_start <= voucher["date"] <= period_end
                     for entry in voucher["entries"]
                     if entry["ledger_name"] == ledger["name"] and entry["type"] == "credit"),
                    Decimal("0"),
                )
                closing = ledger["closing_balance"]
                if closing is None:
                    closing = ledger["opening_balance"] + debits - credits
                session.add(TrialBalanceSnapshot(
                    import_batch_id=None,
                    entity_id=entity.id,
                    ledger_account_id=accounts[ledger["name"]].id,
                    period_start=period_start,
                    period_end=period_end,
                    opening_balance=ledger["opening_balance"],
                    total_debits=debits,
                    total_credits=credits,
                    closing_balance=closing,
                ))
            for voucher in vouchers:
                if not period_start <= voucher["date"] <= period_end:
                    continue
                debits = [entry for entry in voucher["entries"] if entry["type"] == "debit"]
                credits = [entry for entry in voucher["entries"] if entry["type"] == "credit"]
                for pair in decompose_entries(debits, credits):
                    session.add(Transaction(
                        import_batch_id=None,
                        entity_id=entity.id,
                        date=voucher["date"],
                        debit_account_id=accounts[pair["debit_ledger"]].id,
                        credit_account_id=accounts[pair["credit_ledger"]].id,
                        amount=pair["amount"],
                        narration=voucher.get("narration"),
                        voucher_type=voucher["voucher_type"],
                        source_voucher_id=_legacy_source_voucher_id(voucher),
                    ))
        session.flush()
    return entity
