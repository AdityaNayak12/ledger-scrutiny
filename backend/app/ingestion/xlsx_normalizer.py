"""Fixed GL XLSX ingestion plus the legacy trial-balance compatibility path."""

import io
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Mapping, Optional

import openpyxl
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import (
    Entity,
    FinancialPeriod,
    ImportBatch,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    TrialBalanceSnapshot,
)
from app.ingestion.batches import fail_import_batch
from app.ingestion.reconciliation import build_reconciliation_report
from app.ingestion.schema import (
    DOCUMENT_BALANCE_TOLERANCE,
    GL_REQUIRED_HEADERS,
    JournalEntryRecord,
    JournalLineRecord,
    JournalLineSide,
    SourceFamily,
)
from app.rules.account_groups import UnrecognizedAccountGroupError, get_normal_balance


_OPTIONAL_ALIASES = {
    "document_type": ("Document Type",),
    "document_date": ("Document Date",),
    "posting_key": ("Posting Key",),
    "reference": (
        "Reference", "Invoice Reference", "Invoice/Reference", "Invoice / Reference", "Invoice",
        "Invoice No/Reference",
    ),
    "clearing_document": ("Clearing Document",),
    "profit_center": ("Profit Centre", "Profit Center"),
    "cost_center": ("Cost Centre", "Cost Center"),
    "text": ("Text", "Description", "Text/Bid/Cont/TndrNo", "L DESCRIPTION"),
    "supplier": ("Supplier", "Vendor", "Supplier/Vendor", "Vendor Name"),
    "wbs": ("WBS", "WBS Element", "WBS element"),
    "purchasing_document": ("Purchasing Document",),
    "customer": ("Customer", "Customer Name"),
    "quantity": ("Quantity",),
    "currency": ("Currency", "Currency Code"),
}
_FIXED_MAPPING_KEYS = {
    "document_number",
    "gl_account",
    "posting_date",
    "amount",
    "amount_in_local_currency",
}


class _ParsedGL:
    __slots__ = ("entries", "input_rows", "skipped_rows", "skip_reasons", "warnings", "coverage_start", "coverage_end")

    def __init__(
        self,
        entries: tuple[JournalEntryRecord, ...],
        input_rows: int,
        skipped_rows: int,
        skip_reasons: tuple[dict[str, Any], ...],
        warnings: tuple[str, ...],
        coverage_start: date,
        coverage_end: date,
    ) -> None:
        self.entries = entries
        self.input_rows = input_rows
        self.skipped_rows = skipped_rows
        self.skip_reasons = skip_reasons
        self.warnings = warnings
        self.coverage_start = coverage_start
        self.coverage_end = coverage_end


class _GLParseError(ValueError):
    """Validation error carrying the reconciliation progress available before failure."""

    def __init__(
        self,
        message: str,
        *,
        input_rows: int = 0,
        skipped_rows: int = 0,
        skip_reasons: tuple[dict[str, Any], ...] = (),
    ) -> None:
        super().__init__(message)
        self.input_rows = input_rows
        self.skipped_rows = skipped_rows
        self.skip_reasons = skip_reasons


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _text_value(value: Any) -> Optional[str]:
    if _is_blank(value):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _parse_date(value: Any, row_number: int, header: str, *, required: bool) -> Optional[date]:
    if _is_blank(value):
        if required:
            raise ValueError(f"Row {row_number}: {header} is a required value.")
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        candidate = value.strip()
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            try:
                return datetime.fromisoformat(candidate).date()
            except ValueError:
                pass
    raise ValueError(f"Row {row_number}: {header} must be a valid date; got {value!r}.")


def _parse_decimal(value: Any, row_number: int, header: str, *, required: bool) -> Optional[Decimal]:
    if _is_blank(value):
        if required:
            raise ValueError(f"Row {row_number}: {header} is a required value.")
        return None
    if isinstance(value, bool):
        raise ValueError(f"Row {row_number}: {header} must be a valid decimal; got {value!r}.")
    try:
        result = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"Row {row_number}: {header} must be a valid decimal; got {value!r}.") from None
    if not result.is_finite():
        raise ValueError(f"Row {row_number}: {header} must be a valid decimal; got {value!r}.")
    return result


def _validate_numeric_capacity(
    value: Decimal,
    row_number: int,
    header: str,
    *,
    scale: int,
    precision: int = 20,
) -> Decimal:
    decimal_tuple = value.as_tuple()
    decimal_scale = max(0, -decimal_tuple.exponent)
    if decimal_scale > scale:
        raise ValueError(
            f"Row {row_number}: {header} supports at most {scale} decimal places "
            f"(Numeric({precision},{scale})); got {value}."
        )
    integer_digits = max(0, value.adjusted() + 1) if value else 0
    if integer_digits > precision - scale:
        raise ValueError(
            f"Row {row_number}: {header} exceeds Numeric({precision},{scale}) capacity; got {value}."
        )
    return value


def _parse_storage_decimal(
    value: Any,
    row_number: int,
    header: str,
    *,
    required: bool,
    scale: int,
) -> Optional[Decimal]:
    result = _parse_decimal(value, row_number, header, required=required)
    if result is None:
        return None
    return _validate_numeric_capacity(result, row_number, header, scale=scale)


def _effective_coverage(
    target_period_start: Any,
    target_period_end: Any,
    *,
    declared_coverage: tuple[Any, Any] | None = None,
    declared_coverage_start: Any = None,
    declared_coverage_end: Any = None,
    coverage_start: Any = None,
    coverage_end: Any = None,
) -> tuple[date, date]:
    if declared_coverage is not None:
        declared_coverage_start, declared_coverage_end = declared_coverage
    start_value = declared_coverage_start if declared_coverage_start is not None else coverage_start
    end_value = declared_coverage_end if declared_coverage_end is not None else coverage_end
    start_value = target_period_start if start_value is None else start_value
    end_value = target_period_end if end_value is None else end_value
    start = _parse_date(start_value, 0, "Declared coverage start", required=True)
    end = _parse_date(end_value, 0, "Declared coverage end", required=True)
    assert start is not None and end is not None
    if start > end:
        raise ValueError(f"Declared coverage start {start} is after end {end}.")
    return start, end


def _header_name(value: Any, column_number: int) -> str:
    return str(value).strip() if not _is_blank(value) else f"__column_{column_number}"


def _fixed_header_row(worksheet: Any) -> tuple[int, list[str], dict[str, int]]:
    max_scan_row = min(15, worksheet.max_row)
    for row_number in range(1, max_scan_row + 1):
        values = [cell.value for cell in worksheet[row_number]]
        headers = [_header_name(value, index + 1) for index, value in enumerate(values)]
        positions: dict[str, int] = {}
        for index, value in enumerate(values):
            if value in GL_REQUIRED_HEADERS:
                if value in positions:
                    raise ValueError(f"Required header {value!r} appears more than once.")
                positions[value] = index
        if all(header in positions for header in GL_REQUIRED_HEADERS):
            return row_number, headers, positions
    raise ValueError(
        "Could not locate the exact required GL headers in the first 15 rows. "
        f"Required headers: {', '.join(GL_REQUIRED_HEADERS)}."
    )


def _optional_columns(headers: list[str]) -> dict[str, tuple[str, int]]:
    columns: dict[str, tuple[str, int]] = {}
    for field_name, aliases in _OPTIONAL_ALIASES.items():
        for alias in aliases:
            if alias in headers:
                columns[field_name] = (alias, headers.index(alias))
                break
    return columns


def _formula_cells(file_bytes: bytes, worksheet_title: str, header_row: int, required_columns: dict[str, int]) -> set[tuple[int, int]]:
    """Return original formula cells outside the required fields without evaluating them."""
    try:
        formula_workbook = openpyxl.load_workbook(
            io.BytesIO(file_bytes), data_only=False, read_only=True
        )
    except Exception as error:
        raise ValueError(f"Failed to inspect original Excel formula cells: {error}") from error
    try:
        formula_sheet = formula_workbook[worksheet_title]
        required_indexes = set(required_columns.values())
        formulas: set[tuple[int, int]] = set()
        for row_number, row in enumerate(formula_sheet.iter_rows(min_row=header_row + 1), header_row + 1):
            for column_index, cell in enumerate(row):
                if column_index in required_indexes:
                    continue
                value = cell.value
                if cell.data_type == "f" or (isinstance(value, str) and value.startswith("=")):
                    formulas.add((row_number, column_index))
        return formulas
    finally:
        formula_workbook.close()


def _summary_skip_reason(
    values: list[Any],
    headers: list[str],
    required_columns: dict[str, int],
    row_number: int,
) -> dict[str, Any] | None:
    """Recognize explicit zero-valued footer labels, not arbitrary malformed rows."""
    required_indexes = set(required_columns.values())
    marker: tuple[str, str] | None = None
    for index, value in enumerate(values):
        if index in required_indexes or _is_blank(value):
            continue
        text = _text_value(value)
        normalized = text.upper() if text else ""
        if normalized == "TOTAL" or normalized.endswith("TOTAL") or normalized.endswith("SUBTOTAL"):
            marker = (headers[index], text)
            break
    if marker is None:
        return None

    identity_headers = ("Document Number", "G/L Account", "Posting Date")
    if any(not _is_blank(values[required_columns[header]]) for header in identity_headers):
        return None
    amount_value = values[required_columns["Amount in local currency"]]
    if not _is_blank(amount_value):
        try:
            amount = Decimal(str(amount_value).replace(",", "").strip())
        except (InvalidOperation, TypeError, ValueError):
            return None
        if not amount.is_finite() or amount != 0:
            return None
    return {
        "row": row_number,
        "reason": "summary/footer row",
        "column": marker[0],
        "marker": marker[1],
    }


def _warning_messages(
    warning_counts: Counter[tuple[str, str]],
    invalid_quantity_counts: Counter[str],
) -> tuple[str, ...]:
    messages = [
        f"{category} for optional column '{header}' in {count} row(s)."
        for (category, header), count in sorted(warning_counts.items())
    ]
    messages.extend(
        f"optional column '{header}' contains non-numeric values in {count} row(s); "
        "canonical quantity left unset."
        for header, count in sorted(invalid_quantity_counts.items())
    )
    return tuple(messages)


def _failure_reconciliation_report(file_bytes: bytes, error: ValueError) -> dict[str, Any]:
    """Build a JSON-safe failure report even when parsing stops at the first bad row."""
    input_rows = getattr(error, "input_rows", 0)
    skipped_rows = getattr(error, "skipped_rows", 0)
    skip_reasons = list(getattr(error, "skip_reasons", ()))
    if not input_rows:
        try:
            workbook = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
            try:
                worksheet = workbook.active
                if worksheet is not None and worksheet.max_row:
                    header_row, _headers, _required_columns = _fixed_header_row(worksheet)
                    input_rows = max(0, worksheet.max_row - header_row)
            finally:
                workbook.close()
        except Exception:
            pass

    row_match = re.search(r"Row (\d+):", str(error))
    reject_reason: dict[str, Any] = {"reason": str(error)}
    if row_match:
        reject_reason["row"] = int(row_match.group(1))
    report = build_reconciliation_report(
        (), input_rows=input_rows, rejected=1, errors=[str(error)], coverage_complete=False,
    )
    report.update({
        "parser": "xlsx_gl",
        "source_family": SourceFamily.GL_UPLOAD.value,
        "accepted": 0,
        "accepted_rows": 0,
        "skipped": skipped_rows,
        "skipped_rows": skipped_rows,
        "skip_reasons": skip_reasons,
        "reject_reasons": [reject_reason],
    })
    return report


def _parse_fixed_gl_xlsx(
    file_bytes: bytes,
    target_period_start: Any,
    target_period_end: Any,
    *,
    declared_coverage: tuple[Any, Any] | None = None,
    declared_coverage_start: Any = None,
    declared_coverage_end: Any = None,
    coverage_start: Any = None,
    coverage_end: Any = None,
) -> _ParsedGL:
    period_start, period_end = _effective_coverage(
        target_period_start,
        target_period_end,
        declared_coverage=declared_coverage,
        declared_coverage_start=declared_coverage_start,
        declared_coverage_end=declared_coverage_end,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
    )
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as error:
        raise ValueError(f"Failed to parse Excel file: {error}") from error

    input_rows = 0
    skip_reasons: list[dict[str, Any]] = []
    try:
        worksheet = workbook.active
        if worksheet is None or worksheet.max_row == 0:
            raise ValueError("Workbook has no active sheet or the active sheet is empty.")
        header_row, headers, required_columns = _fixed_header_row(worksheet)
        optional_columns = _optional_columns(headers)
        formula_cells = _formula_cells(file_bytes, worksheet.title, header_row, required_columns)
        entries: dict[str, dict[str, Any]] = {}
        warning_counts: Counter[tuple[str, str]] = Counter()
        invalid_quantity_counts: Counter[str] = Counter()
        invalid_quantity_headers: set[str] = set()
        input_rows = max(0, worksheet.max_row - header_row)

        for row_number, row in enumerate(
            worksheet.iter_rows(min_row=header_row + 1, max_row=worksheet.max_row, values_only=True),
            header_row + 1,
        ):
            values = list(row)
            if all(_is_blank(value) for value in values):
                skip_reasons.append({"row": row_number, "reason": "blank source row"})
                continue

            summary_reason = _summary_skip_reason(values, headers, required_columns, row_number)
            if summary_reason is not None:
                skip_reasons.append(summary_reason)
                continue

            def required_value(header: str) -> Any:
                value = values[required_columns[header]] if required_columns[header] < len(values) else None
                if _is_blank(value):
                    raise ValueError(f"Row {row_number}: {header} is a required value.")
                return value

            source_document_id = _text_value(required_value("Document Number"))
            account_code = _text_value(required_value("G/L Account"))
            posting_date = _parse_date(
                required_value("Posting Date"), row_number, "Posting Date", required=True
            )
            amount = _parse_decimal(
                required_value("Amount in local currency"),
                row_number,
                "Amount in local currency",
                required=True,
            )
            assert source_document_id is not None
            assert account_code is not None and posting_date is not None and amount is not None
            amount = _validate_numeric_capacity(
                amount, row_number, "Amount in local currency", scale=2
            )
            if not period_start <= posting_date <= period_end:
                raise ValueError(
                    f"Row {row_number}: posting date {posting_date} is outside declared coverage "
                    f"{period_start} to {period_end}."
                )

            side = JournalLineSide.DEBIT if amount >= 0 else JournalLineSide.CREDIT

            def optional_value(field_name: str) -> Any:
                if field_name not in optional_columns:
                    return None
                _header, column_index = optional_columns[field_name]
                return values[column_index] if column_index < len(values) else None

            def optional_text(field_name: str) -> Optional[str]:
                return _text_value(optional_value(field_name))

            document_date = _parse_date(
                optional_value("document_date"), row_number, "Document Date", required=False
            )
            quantity = None
            quantity_header = optional_columns.get("quantity", (None, 0))[0]
            raw_quantity = optional_value("quantity")
            if not _is_blank(raw_quantity):
                try:
                    quantity = _parse_storage_decimal(
                        raw_quantity, row_number, "Quantity", required=False, scale=4
                    )
                except ValueError as error:
                    if "must be a valid decimal" not in str(error):
                        raise
                    assert quantity_header is not None
                    invalid_quantity_headers.add(quantity_header)
                    invalid_quantity_counts[quantity_header] += 1
            document_type = optional_text("document_type")
            text = optional_text("text")

            for column_index, header in enumerate(headers):
                if header in GL_REQUIRED_HEADERS:
                    continue
                value = values[column_index] if column_index < len(values) else None
                if _is_blank(value):
                    category = (
                        "missing cached formula value"
                        if (row_number, column_index) in formula_cells
                        else "optional dimension is blank"
                    )
                    warning_counts[(category, header)] += 1

            source_metadata: dict[str, Any] = {}
            key_counts: defaultdict[str, int] = defaultdict(int)
            source_keys: list[str] = []
            for index, header in enumerate(headers):
                key_counts[header] += 1
                source_key = header if key_counts[header] == 1 else f"{header}#{key_counts[header]}"
                source_keys.append(source_key)
                source_metadata[source_key] = _json_value(values[index] if index < len(values) else None)

            known_optional_headers = {header for header, _index in optional_columns.values()}
            dimensions = {
                source_keys[index]: source_metadata[source_keys[index]]
                for index, header in enumerate(headers)
                if header not in GL_REQUIRED_HEADERS
                and (header not in known_optional_headers or header in invalid_quantity_headers)
            }
            line = JournalLineRecord(
                source_row_number=row_number,
                ledger_account_code=account_code,
                amount=amount,
                side=side,
                posting_key=optional_text("posting_key"),
                quantity=quantity,
                currency=optional_text("currency"),
                reference=optional_text("reference"),
                clearing_document=optional_text("clearing_document"),
                profit_center=optional_text("profit_center"),
                cost_center=optional_text("cost_center"),
                text=text,
                supplier=optional_text("supplier"),
                wbs=optional_text("wbs"),
                purchasing_document=optional_text("purchasing_document"),
                customer=optional_text("customer"),
                dimensions=dimensions,
                source_metadata=source_metadata,
            )
            document = entries.setdefault(
                source_document_id,
                {
                    "posting_date": posting_date,
                    "document_date": document_date,
                    "document_type": document_type,
                    "narration": text,
                    "lines": [],
                },
            )
            if document["posting_date"] != posting_date:
                raise ValueError(
                    f"Document {source_document_id} has inconsistent Posting Date values "
                    f"({document['posting_date']} and {posting_date})."
                )
            if document["document_date"] is None and document_date is not None:
                document["document_date"] = document_date
            if document["document_type"] is None and document_type is not None:
                document["document_type"] = document_type
            if document["narration"] is None and text is not None:
                document["narration"] = text
            document["lines"].append(line)

        canonical_entries = tuple(
            JournalEntryRecord(
                source_document_id=document_id,
                posting_date=document["posting_date"],
                document_date=document["document_date"],
                document_type=document["document_type"],
                narration=document["narration"],
                lines=tuple(document["lines"]),
                source_family=SourceFamily.GL_UPLOAD,
            )
            for document_id, document in entries.items()
        )
        for entry in canonical_entries:
            signed_total = sum((line.amount for line in entry.lines), Decimal("0"))
            if abs(signed_total) > DOCUMENT_BALANCE_TOLERANCE:
                raise ValueError(
                    f"Document {entry.source_document_id} does not balance within "
                    f"₹{DOCUMENT_BALANCE_TOLERANCE}: signed balance is {signed_total}."
                )
        return _ParsedGL(
            entries=canonical_entries,
            input_rows=input_rows,
            skipped_rows=len(skip_reasons),
            skip_reasons=tuple(skip_reasons),
            warnings=_warning_messages(warning_counts, invalid_quantity_counts),
            coverage_start=period_start,
            coverage_end=period_end,
        )
    except ValueError as error:
        if isinstance(error, _GLParseError):
            raise
        raise _GLParseError(
            str(error),
            input_rows=input_rows,
            skipped_rows=len(skip_reasons),
            skip_reasons=tuple(skip_reasons),
        ) from error
    finally:
        workbook.close()


def parse_gl_xlsx(
    file_bytes: bytes,
    target_period_start: Any,
    target_period_end: Any,
    *,
    declared_coverage: tuple[Any, Any] | None = None,
    declared_coverage_start: Any = None,
    declared_coverage_end: Any = None,
    coverage_start: Any = None,
    coverage_end: Any = None,
) -> tuple[JournalEntryRecord, ...]:
    """Parse the fixed GL profile into source-preserving canonical records."""
    return _parse_fixed_gl_xlsx(
        file_bytes,
        target_period_start,
        target_period_end,
        declared_coverage=declared_coverage,
        declared_coverage_start=declared_coverage_start,
        declared_coverage_end=declared_coverage_end,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
    ).entries


def _entity(session: Session, entity_id: int) -> Entity:
    entity = session.execute(select(Entity).where(Entity.id == entity_id)).scalar_one_or_none()
    if entity is None:
        raise ValueError(f"Entity with ID {entity_id} not found.")
    return entity


def normalize_gl_xlsx(
    file_bytes: bytes,
    target_period_start: Any,
    target_period_end: Any,
    entity_id: int,
    session: Session,
    *,
    import_batch_id: Optional[int],
    declared_coverage: tuple[Any, Any] | None = None,
    declared_coverage_start: Any = None,
    declared_coverage_end: Any = None,
    coverage_start: Any = None,
    coverage_end: Any = None,
) -> dict[str, Any]:
    """Parse and persist fixed-profile GL records without depending on FastAPI."""
    entity = _entity(session, entity_id)
    if import_batch_id is None:
        raise ValueError("Fixed GL normalization requires an import_batch_id.")
    batch = session.get(ImportBatch, import_batch_id)
    if batch is None:
        raise ValueError(f"Import batch {import_batch_id} not found.")
    if batch.entity_id != entity_id:
        raise ValueError(f"Import batch {import_batch_id} does not belong to entity {entity_id}.")
    if batch.status != "STAGED":
        raise ValueError(
            f"Fixed GL normalization requires a STAGED ImportBatch; batch {import_batch_id} is {batch.status}."
        )

    if (
        declared_coverage is None
        and declared_coverage_start is None
        and declared_coverage_end is None
        and coverage_start is None
        and coverage_end is None
        and batch.coverage_start is not None
        and batch.coverage_end is not None
    ):
        declared_coverage = (batch.coverage_start, batch.coverage_end)

    try:
        parsed = _parse_fixed_gl_xlsx(
            file_bytes,
            target_period_start,
            target_period_end,
            declared_coverage=declared_coverage,
            declared_coverage_start=declared_coverage_start,
            declared_coverage_end=declared_coverage_end,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
        )
    except ValueError as error:
        fail_import_batch(
            session,
            batch,
            errors=[str(error)],
            validation_report=_failure_reconciliation_report(file_bytes, error),
        )
        raise

    accounts = session.execute(select(LedgerAccount).where(LedgerAccount.entity_id == entity_id)).scalars().all()
    accounts_by_code = {account.external_code: account for account in accounts if account.external_code}
    account_names = {account.name for account in accounts}
    unclassified_codes: set[str] = set()
    account_by_code: dict[str, LedgerAccount] = {}
    for entry in parsed.entries:
        for line in entry.lines:
            code = line.ledger_account_code
            account = account_by_code.get(code) or accounts_by_code.get(code)
            if account is None:
                account_name = code
                if account_name in account_names:
                    account_name = f"G/L {code}"
                    suffix = 2
                    while account_name in account_names:
                        account_name = f"G/L {code} ({suffix})"
                        suffix += 1
                account = LedgerAccount(entity_id=entity.id, external_code=code, name=account_name)
                session.add(account)
                session.flush()
                account_names.add(account_name)
            account_by_code[code] = account
            if account.group_name is None or account.normal_balance is None:
                unclassified_codes.add(code)

    warnings = list(parsed.warnings)
    warnings.extend(
        f"Account {code}: classification could not be resolved; group and normal balance remain unset."
        for code in sorted(unclassified_codes)
    )
    report = build_reconciliation_report(
        parsed.entries,
        input_rows=parsed.input_rows,
        skipped=parsed.skipped_rows,
        warnings=warnings,
        unmapped_account_codes=unclassified_codes,
        coverage_complete=True,
    )
    report.update({
        "parser": "xlsx_gl",
        "source_family": SourceFamily.GL_UPLOAD.value,
        "coverage_start": parsed.coverage_start.isoformat(),
        "coverage_end": parsed.coverage_end.isoformat(),
        "skip_reasons": list(parsed.skip_reasons),
    })

    for entry_record in parsed.entries:
        entry = JournalEntry(
            import_batch_id=batch.id,
            entity_id=entity.id,
            source_document_id=entry_record.source_document_id,
            posting_date=entry_record.posting_date,
            document_date=entry_record.document_date,
            document_type=entry_record.document_type,
            narration=entry_record.narration,
        )
        session.add(entry)
        for line_record in entry_record.lines:
            account = account_by_code[line_record.ledger_account_code]
            session.add(JournalLine(
                journal_entry=entry,
                ledger_account=account,
                source_row_number=line_record.source_row_number,
                amount=line_record.amount,
                side=line_record.side.value,
                posting_key=line_record.posting_key,
                quantity=line_record.quantity,
                currency=line_record.currency,
                reference=line_record.reference,
                clearing_document=line_record.clearing_document,
                profit_center=line_record.profit_center,
                cost_center=line_record.cost_center,
                text=line_record.text,
                supplier=line_record.supplier,
                wbs=line_record.wbs,
                purchasing_document=line_record.purchasing_document,
                customer=line_record.customer,
                dimensions=dict(line_record.dimensions),
                source_metadata=dict(line_record.source_metadata),
            ))

    batch.source_metadata = {
        **(batch.source_metadata or {}),
        "parser": "xlsx_gl",
        "required_headers": list(GL_REQUIRED_HEADERS),
    }
    batch.validation_report = report
    session.flush()
    return report


def _uses_fixed_gl_profile(column_mapping: Optional[Dict[str, str]]) -> bool:
    if not column_mapping:
        return True
    keys = {str(key).strip().lower() for key in column_mapping}
    values = {str(value).strip() for value in column_mapping.values()}
    return bool(keys & _FIXED_MAPPING_KEYS) or set(GL_REQUIRED_HEADERS).issubset(values)


def normalize_xlsx_confirm(
    file_bytes: bytes,
    column_mapping: Optional[Dict[str, str]],
    sign_convention: str,
    target_period_start: str,
    target_period_end: str,
    entity_id: int,
    session: Session,
    clear_only_period: bool = True,
    import_batch_id: Optional[int] = None,
) -> Entity:
    """Dispatch fixed GL uploads while preserving the explicit legacy mapping path."""
    entity = _entity(session, entity_id)
    if _uses_fixed_gl_profile(column_mapping):
        normalize_gl_xlsx(
            file_bytes,
            target_period_start,
            target_period_end,
            entity_id,
            session,
            import_batch_id=import_batch_id,
        )
        return entity
    return _normalize_legacy_trial_balance(
        file_bytes=file_bytes,
        column_mapping=column_mapping,
        sign_convention=sign_convention,
        target_period_start=target_period_start,
        target_period_end=target_period_end,
        entity_id=entity_id,
        session=session,
        clear_only_period=clear_only_period,
        import_batch_id=import_batch_id,
    )


def _normalize_legacy_trial_balance(
    file_bytes: bytes,
    column_mapping: Dict[str, str],
    sign_convention: str,
    target_period_start: str,
    target_period_end: str,
    entity_id: int,
    session: Session,
    clear_only_period: bool = True,
    import_batch_id: Optional[int] = None,
) -> Entity:
    """Legacy explicit trial-balance behavior retained for existing callers."""
    if sign_convention not in ("negative_is_credit", "positive_is_credit", "separate_dr_cr_columns"):
        raise ValueError(
            f"Invalid sign_convention '{sign_convention}'. "
            "Must be one of 'negative_is_credit', 'positive_is_credit', or 'separate_dr_cr_columns'."
        )

    entity = _entity(session, entity_id)

    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as e:
        raise ValueError(f"Failed to parse Excel file: {str(e)}")

    ws = wb.active
    if not ws:
        raise ValueError("Workbook has no active sheet.")

    rows_data = list(ws.iter_rows(values_only=True))
    if not rows_data:
        raise ValueError("Excel sheet is empty.")

    header_row_idx = -1
    col_index_map: Dict[str, int] = {}

    scan_limit = min(15, len(rows_data))
    for r_idx in range(scan_limit):
        row_vals = rows_data[r_idx]
        if not row_vals:
            continue

        clean_row = [str(v).strip() if v is not None else "" for v in row_vals]

        temp_map: Dict[str, int] = {}
        for field_name, expected_header in column_mapping.items():
            exp_clean = str(expected_header).strip().lower()
            for c_idx, cell_str in enumerate(clean_row):
                if cell_str.lower() == exp_clean:
                    temp_map[field_name] = c_idx
                    break

        if len(temp_map) > len(col_index_map):
            col_index_map = temp_map
            header_row_idx = r_idx

    if header_row_idx == -1 or not col_index_map:
        raise ValueError(
            "Could not locate the specified header row matching the provided column_mapping in the first 15 rows."
        )

    if "ledger_name" not in col_index_map:
        raise ValueError("Provided column_mapping must contain a mapping for 'ledger_name'.")
    if "group_name" not in col_index_map:
        raise ValueError("Provided column_mapping must contain a mapping for 'group_name'.")

    p_start = date.fromisoformat(target_period_start) if isinstance(target_period_start, str) else target_period_start
    p_end = date.fromisoformat(target_period_end) if isinstance(target_period_end, str) else target_period_end

    fp = session.execute(select(FinancialPeriod).where(
        FinancialPeriod.entity_id == entity.id,
        FinancialPeriod.period_start == p_start,
        FinancialPeriod.period_end == p_end,
    ).order_by(FinancialPeriod.id.desc())).scalars().first()
    if fp is None:
        session.add(FinancialPeriod(
            entity_id=entity.id,
            period_start=p_start,
            period_end=p_end,
            source="xlsx_trial_balance",
        ))

    if import_batch_id is None:
        session.execute(delete(TrialBalanceSnapshot).where(
            TrialBalanceSnapshot.entity_id == entity.id,
            TrialBalanceSnapshot.period_start == p_start,
            TrialBalanceSnapshot.period_end == p_end,
        ))

    ledger_map: Dict[str, LedgerAccount] = {}
    existing_ledgers = session.execute(
        select(LedgerAccount).where(LedgerAccount.entity_id == entity.id)
    ).scalars().all()
    for l in existing_ledgers:
        ledger_map[l.name] = l

    row_offset = header_row_idx + 1
    for r_idx in range(row_offset, len(rows_data)):
        row_vals = rows_data[r_idx]
        actual_row_num = r_idx + 1

        if not row_vals or all(v is None or str(v).strip() == "" for v in row_vals):
            continue

        l_idx = col_index_map["ledger_name"]
        raw_ledger = row_vals[l_idx] if l_idx < len(row_vals) else None
        ledger_name = str(raw_ledger).strip() if raw_ledger is not None else ""

        g_idx = col_index_map.get("group_name", l_idx)
        raw_group = row_vals[g_idx] if g_idx < len(row_vals) else None
        group_name = str(raw_group).strip() if raw_group is not None else ledger_name

        name_upper = ledger_name.upper()
        group_upper = group_name.upper()
        if (
            name_upper.endswith("TOTAL")
            or name_upper.endswith("SUBTOTAL")
            or name_upper == "GRAND TOTAL"
            or group_upper.endswith("TOTAL")
            or group_upper.endswith("SUBTOTAL")
            or group_upper == "GRAND TOTAL"
        ):
            continue

        if not ledger_name:
            if not raw_group or str(raw_group).strip() == "":
                continue
            raise ValueError(f"Row {actual_row_num}: Ledger account name cannot be blank or empty.")

        try:
            normal_bal = get_normal_balance(group_name)
        except UnrecognizedAccountGroupError as err:
            raise ValueError(f"Row {actual_row_num}: {str(err)}")

        def parse_decimal_field(field_key: str) -> Decimal:
            if field_key not in col_index_map:
                return Decimal("0.00")
            col_idx = col_index_map[field_key]
            val = row_vals[col_idx] if col_idx < len(row_vals) else None
            if val is None or str(val).strip() == "":
                return Decimal("0.00")

            clean_str = str(val).replace(",", "").strip()
            try:
                return Decimal(clean_str)
            except (InvalidOperation, TypeError):
                raise ValueError(
                    f"Row {actual_row_num}: Non-numeric balance value '{val}' for field '{field_key}' in ledger '{ledger_name}'."
                )

        if sign_convention == "separate_dr_cr_columns":
            op_dr = parse_decimal_field("opening_debit")
            op_cr = parse_decimal_field("opening_credit")
            op_bal = op_dr - op_cr

            cl_dr = parse_decimal_field("closing_debit")
            cl_cr = parse_decimal_field("closing_credit")
            cl_bal = cl_dr - cl_cr
        elif sign_convention == "positive_is_credit":
            raw_op = parse_decimal_field("opening_balance")
            raw_cl = parse_decimal_field("closing_balance")
            op_bal = -raw_op
            cl_bal = -raw_cl
        else:
            op_bal = parse_decimal_field("opening_balance")
            cl_bal = parse_decimal_field("closing_balance")

        if ledger_name not in ledger_map:
            l_account = LedgerAccount(
                entity_id=entity.id,
                name=ledger_name,
                group_name=group_name,
                normal_balance=normal_bal,
            )
            session.add(l_account)
            session.flush()
            ledger_map[ledger_name] = l_account
        else:
            l_account = ledger_map[ledger_name]

        session.add(TrialBalanceSnapshot(
            import_batch_id=import_batch_id,
            entity_id=entity.id,
            ledger_account_id=l_account.id,
            period_start=p_start,
            period_end=p_end,
            opening_balance=op_bal,
            total_debits=Decimal("0.00"),
            total_credits=Decimal("0.00"),
            closing_balance=cl_bal,
        ))

    session.flush()
    return entity
