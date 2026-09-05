"""Parse the supported TallyPrime XML export into validated plain data."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import re
from typing import Any, Dict

from lxml import etree


class TallyConnectorError(ValueError):
    """Actionable error raised for invalid or unusable Tally connector data."""


_ENTRY_TAGS = {"ALLLEDGERENTRIES.LIST", "LEDGERENTRIES.LIST"}
_IDENTIFIER_NAMES = ("GUID", "VCHKEY", "MASTERID", "ID", "EXTERNALID", "ALTERID")
_SENSITIVE_DETAIL = re.compile(
    r"(?i)\b(password|passwd|token|secret|api[_-]?key|authorization|bearer)\s*[:=]\s*[^\s,;]+"
)


def _local_name(value: Any) -> str:
    return str(value).rsplit("}", 1)[-1].upper()


def _children(node: etree._Element, *names: str) -> list[etree._Element]:
    wanted = {_local_name(name) for name in names}
    return [child for child in node if _local_name(child.tag) in wanted]


def _descendants(node: etree._Element, *names: str) -> list[etree._Element]:
    wanted = {_local_name(name) for name in names}
    return [child for child in node.iter() if _local_name(child.tag) in wanted]


def _text(node: etree._Element | None, *names: str) -> str | None:
    if node is None:
        return None
    for child in _children(node, *names):
        if child.text and child.text.strip():
            return child.text.strip()
    return None


def _attribute(node: etree._Element, *names: str) -> str | None:
    if node is None:
        return None
    wanted = {_local_name(name) for name in names}
    for key, value in node.attrib.items():
        if _local_name(key) in wanted and str(value).strip():
            return str(value).strip()
    return None


def _stable_identifier(node: etree._Element) -> str | None:
    for name in _IDENTIFIER_NAMES:
        value = _attribute(node, name) or _text(node, name)
        if value:
            return value
    return None


def _safe_status_detail(root: etree._Element) -> str:
    detail = " ".join(
        text.strip() for node in _descendants(root, "LINEERROR") for text in [node.text or ""] if text.strip()
    )
    if not detail:
        return ""
    detail = _SENSITIVE_DETAIL.sub(lambda match: f"{match.group(1)}=[redacted]", detail)
    detail = re.sub(r"<[^>]*>", "", detail).strip()
    return detail[:240]


def parse_tally_date(date_str: str) -> date:
    """Parse common Tally date representations into a :class:`date`."""
    if date_str is None or not str(date_str).strip():
        raise TallyConnectorError("Tally response contains an empty date.")
    value = str(date_str).strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%d-%b-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise TallyConnectorError(f"Unable to parse Tally date '{value}'.")


def parse_tally_amount(amount_str: str) -> Decimal:
    """Parse a Tally amount without converting malformed data to zero."""
    if amount_str is None or not str(amount_str).strip():
        raise TallyConnectorError("Invalid Tally amount: value is empty.")

    original = str(amount_str).strip()
    value = original
    suffix: str | None = None
    upper = value.upper()
    if upper.endswith("DR"):
        suffix = "DR"
        value = value[:-2].strip()
    elif upper.endswith("CR"):
        suffix = "CR"
        value = value[:-2].strip()

    try:
        amount = Decimal(value.replace(",", "").strip())
    except (InvalidOperation, TypeError, ValueError):
        raise TallyConnectorError(f"Invalid Tally amount '{original}'.") from None
    if not amount.is_finite():
        raise TallyConnectorError(f"Invalid Tally amount '{original}'.")
    if suffix == "DR":
        return abs(amount)
    if suffix == "CR":
        return -abs(amount)
    return amount


def _parse_date_text(value: str | None, label: str) -> date | None:
    if value is None:
        return None
    try:
        return parse_tally_date(value)
    except TallyConnectorError as error:
        raise TallyConnectorError(f"Invalid Tally {label}: {error}") from error


def _entity_period(
    root: etree._Element,
    *,
    fallback_entity_name: str | None,
    fallback_period_start: date | None,
    fallback_period_end: date | None,
) -> tuple[str, date, date]:
    company = next(iter(_descendants(root, "COMPANY")), None)
    static = next(iter(_descendants(root, "STATICVARIABLES")), None)

    entity_name = (
        _attribute(company, "NAME")
        or _text(company, "RENAME", "NAME", "COMPANYNAME")
        or _text(static, "SVCOMPANYNAME", "SVCURRENTCOMPANY")
        or fallback_entity_name
    )
    start_text = _text(company, "BOOKSFROM", "FROMDATE") or _text(
        static, "SVFROMDATE", "BOOKSFROM", "FROMDATE"
    )
    end_text = _text(company, "BOOKSTO", "TODATE") or _text(
        static, "SVTODATE", "BOOKSTO", "TODATE"
    )
    start = _parse_date_text(start_text, "financial year start") or fallback_period_start
    end = _parse_date_text(end_text, "financial year end") or fallback_period_end

    if not entity_name or not str(entity_name).strip():
        raise TallyConnectorError("Tally response omitted the company/entity name.")
    if start is None or end is None:
        raise TallyConnectorError("Tally response omitted the financial period start or end date.")
    if start > end:
        raise TallyConnectorError(f"Tally response has an invalid period: {start} is after {end}.")
    return str(entity_name).strip(), start, end


def _group_data(root: etree._Element) -> tuple[list[dict[str, Any]], dict[str, str | None]]:
    groups: list[dict[str, Any]] = []
    parents: dict[str, str | None] = {}
    seen: dict[str, str | None] = {}
    for group in _descendants(root, "GROUP"):
        name = _attribute(group, "NAME") or _text(group, "NAME")
        if not name:
            raise TallyConnectorError("Tally response contains a group without a name.")
        parent = _text(group, "PARENT", "PARENTGROUP")
        previous = seen.get(name)
        if name in seen and previous != parent:
            raise TallyConnectorError(f"Tally response contains duplicate group '{name}' with conflicting parents.")
        if name in seen:
            continue
        seen[name] = parent
        parents[name] = parent
        item: dict[str, Any] = {"name": name, "parent_name": parent}
        external_id = _stable_identifier(group)
        if external_id:
            item["external_id"] = external_id
        groups.append(item)
    return groups, parents


def _group_path(group_name: str, parents: dict[str, str | None]) -> list[str]:
    path: list[str] = []
    current: str | None = group_name
    seen: set[str] = set()
    while current and current not in seen:
        path.append(current)
        seen.add(current)
        current = parents.get(current)
    if current in seen:
        raise TallyConnectorError(f"Tally group hierarchy contains a cycle at '{current}'.")
    return path


def _parse_ledgers(root: etree._Element, report_name: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    groups, parents = _group_data(root)
    ledger_nodes = _descendants(root, "LEDGER")
    if not ledger_nodes:
        raise TallyConnectorError("Tally response returned no ledgers/account data.")

    ledgers: list[dict[str, Any]] = []
    names: set[str] = set()
    external_ids: dict[str, str] = {}
    require_closing = "TRIAL BALANCE" in report_name.upper()
    for ledger in ledger_nodes:
        name = _attribute(ledger, "NAME") or _text(ledger, "NAME")
        group_name = _text(ledger, "PARENT", "PARENTGROUP", "GROUP")
        if not name:
            raise TallyConnectorError("Tally response contains a ledger without a name.")
        if not group_name:
            raise TallyConnectorError(f"Ledger '{name}' is missing its group hierarchy.")
        if name in names:
            raise TallyConnectorError(f"Tally response contains duplicate ledger name '{name}'.")
        names.add(name)

        opening_text = _text(ledger, "OPENINGBALANCE")
        if opening_text is None:
            raise TallyConnectorError(f"Ledger '{name}' is missing opening balance.")
        opening = parse_tally_amount(opening_text)
        closing_text = _text(ledger, "CLOSINGBALANCE")
        if require_closing and closing_text is None:
            raise TallyConnectorError(f"Ledger account '{name}' is missing CLOSINGBALANCE.")
        closing = parse_tally_amount(closing_text) if closing_text is not None else None

        external_id = _stable_identifier(ledger)
        if external_id:
            previous_name = external_ids.get(external_id)
            if previous_name is not None and previous_name != name:
                raise TallyConnectorError(
                    f"Tally response reuses external ledger identifier '{external_id}' for '{previous_name}' and '{name}'."
                )
            external_ids[external_id] = name
        item: dict[str, Any] = {
            "name": name,
            "group_name": group_name,
            "group_hierarchy": _group_path(group_name, parents),
            "opening_balance": opening,
            "closing_balance": closing,
        }
        if external_id:
            item["external_id"] = external_id
        ledgers.append(item)
    return ledgers, groups


def _voucher_id(
    voucher: etree._Element,
    index: int,
    date_value: date,
    voucher_type: str,
    entries: list[dict[str, Any]],
) -> str:
    external_id = _stable_identifier(voucher)
    if external_id:
        return external_id
    number = _text(voucher, "VOUCHERNUMBER", "VOUCHERNO", "NUMBER")
    if number:
        return number
    fingerprint = repr((date_value.isoformat(), voucher_type, entries, index)).encode("utf-8")
    return f"generated:{sha256(fingerprint).hexdigest()}"


def _parse_vouchers(root: etree._Element) -> list[dict[str, Any]]:
    vouchers: list[dict[str, Any]] = []
    voucher_ids: set[str] = set()
    for voucher in _descendants(root, "VOUCHER"):
        voucher_type = _attribute(voucher, "VCHTYPE", "TYPE") or _text(
            voucher, "VOUCHERTYPENAME", "VCHTYPE", "TYPE"
        )
        date_text = _attribute(voucher, "DATE") or _text(voucher, "DATE", "POSTINGDATE")
        if not voucher_type:
            raise TallyConnectorError("Tally response contains a voucher without a voucher type.")
        if not date_text:
            raise TallyConnectorError(f"Voucher of type '{voucher_type}' is missing its date.")
        voucher_date = parse_tally_date(date_text)
        narration = _text(voucher, "NARRATION", "DESCRIPTION")
        entry_nodes = [node for node in voucher.iter() if _local_name(node.tag) in _ENTRY_TAGS]
        if not entry_nodes:
            raise TallyConnectorError(
                f"Voucher of type '{voucher_type}' on {voucher_date} contains no ledger entries."
            )

        entries: list[dict[str, Any]] = []
        for row_number, entry in enumerate(entry_nodes, 1):
            ledger_name = _text(entry, "LEDGERNAME", "LEDGER")
            if not ledger_name:
                raise TallyConnectorError(f"Voucher on {voucher_date} contains a ledger entry without a ledger name.")
            amount_text = _text(entry, "AMOUNT")
            if amount_text is None:
                raise TallyConnectorError(f"Voucher on {voucher_date} ledger '{ledger_name}' is missing its amount.")
            raw_amount = parse_tally_amount(amount_text)
            deemed = _text(entry, "ISDEEMEDPOSITIVE")
            if deemed is None:
                entry_type = "debit" if raw_amount >= 0 else "credit"
            elif deemed.strip().lower() in {"yes", "y", "true", "1"}:
                entry_type = "debit"
            elif deemed.strip().lower() in {"no", "n", "false", "0"}:
                entry_type = "credit"
            else:
                raise TallyConnectorError(
                    f"Voucher on {voucher_date} ledger '{ledger_name}' has invalid ISDEEMEDPOSITIVE value."
                )
            line: dict[str, Any] = {
                "ledger_name": ledger_name,
                "type": entry_type,
                "amount": abs(raw_amount),
                "source_row_number": row_number,
            }
            line_id = _stable_identifier(entry)
            if line_id:
                line["source_line_id"] = line_id
            entries.append(line)

        source_id = _voucher_id(voucher, len(vouchers), voucher_date, voucher_type, entries)
        if source_id in voucher_ids:
            raise TallyConnectorError(f"Tally response contains duplicate voucher identifier '{source_id}'.")
        voucher_ids.add(source_id)
        signed_total = sum(
            (line["amount"] if line["type"] == "debit" else -line["amount"] for line in entries),
            Decimal("0"),
        )
        if abs(signed_total) > Decimal("0.01"):
            debit_total = sum((line["amount"] for line in entries if line["type"] == "debit"), Decimal("0"))
            credit_total = sum((line["amount"] for line in entries if line["type"] == "credit"), Decimal("0"))
            raise TallyConnectorError(
                f"Voucher '{source_id}' on {voucher_date} is unbalanced and does not balance within ₹0.01: "
                f"debits={debit_total}, credits={credit_total}."
            )
        document_date = _parse_date_text(_text(voucher, "DOCUMENTDATE", "REFERENCEDATE"), "document date")
        vouchers.append({
            "date": voucher_date,
            "document_date": document_date,
            "voucher_type": voucher_type.strip(),
            "source_voucher_id": source_id,
            "narration": narration,
            "entries": entries,
        })
    return vouchers


def parse_tally_xml(
    xml_content: bytes,
    *,
    fallback_entity_name: str | None = None,
    fallback_period_start: date | None = None,
    fallback_period_end: date | None = None,
    require_status: bool = False,
) -> Dict[str, Any]:
    """Parse and validate entity, account, balance, and voucher data."""
    if not xml_content:
        raise TallyConnectorError("Tally response was empty.")
    try:
        root = etree.fromstring(
            xml_content,
            parser=etree.XMLParser(recover=False, remove_blank_text=True, resolve_entities=False),
        )
    except (etree.XMLSyntaxError, TypeError, ValueError) as error:
        raise TallyConnectorError("TallyPrime returned invalid XML.") from error

    status_nodes = _descendants(root, "STATUS")
    status = status_nodes[0].text.strip() if status_nodes and status_nodes[0].text else None
    if require_status and status is None:
        raise TallyConnectorError("Tally response omitted status.")
    if status and status != "1":
        detail = _safe_status_detail(root)
        raise TallyConnectorError(detail or "TallyPrime rejected the export request.")

    report_nodes = _descendants(root, "REPORTNAME")
    report_name = report_nodes[0].text.strip() if report_nodes and report_nodes[0].text else ""
    entity_name, period_start, period_end = _entity_period(
        root,
        fallback_entity_name=fallback_entity_name,
        fallback_period_start=fallback_period_start,
        fallback_period_end=fallback_period_end,
    )
    ledgers, groups = _parse_ledgers(root, report_name)
    vouchers = _parse_vouchers(root)
    return {
        "entity": {
            "name": entity_name,
            "financial_year_start": period_start,
            "financial_year_end": period_end,
        },
        "groups": groups,
        "ledgers": ledgers,
        "vouchers": vouchers,
    }
