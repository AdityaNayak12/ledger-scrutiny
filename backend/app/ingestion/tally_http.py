"""Supported TallyPrime HTTP/XML connector."""

from datetime import date
from urllib.parse import urlsplit, urlunsplit
from xml.sax.saxutils import escape

import httpx

from app.ingestion.tally_parser import TallyConnectorError, parse_tally_xml


def _tally_date(value: date) -> str:
    return value.strftime("%-d-%b-%Y")


def _safe_endpoint(endpoint: str) -> str:
    """Keep credentials, query strings, and fragments out of error messages."""
    try:
        parsed = urlsplit(str(endpoint))
        if not parsed.scheme or not parsed.hostname:
            return "<configured endpoint>"
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = host
        if parsed.port is not None:
            netloc = f"{netloc}:{parsed.port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    except (TypeError, ValueError):
        return "<configured endpoint>"


def _validate_endpoint(endpoint: str) -> None:
    try:
        parsed = urlsplit(str(endpoint).strip())
    except (TypeError, ValueError):
        raise TallyConnectorError("Tally endpoint must be a valid http or https URL.") from None
    if parsed.scheme.lower() not in {"http", "https"}:
        raise TallyConnectorError("Tally endpoint must use http or https.")
    if not parsed.hostname:
        raise TallyConnectorError("Tally endpoint must include a host.")
    try:
        parsed.port
    except ValueError:
        raise TallyConnectorError("Tally endpoint must include a valid port.") from None


def build_ledger_request(company_name: str, period_start: date, period_end: date) -> bytes:
    """Build one read-only TDL request for account master data and vouchers."""
    company = escape(company_name)
    return f"""<ENVELOPE>
  <HEADER><VERSION>1</VERSION><TALLYREQUEST>EXPORT</TALLYREQUEST><TYPE>COLLECTION</TYPE><ID>LedgerScrutinyCollection</ID></HEADER>
  <BODY><DESC>
    <STATICVARIABLES>
      <SVCURRENTCOMPANY>{company}</SVCURRENTCOMPANY>
      <SVFROMDATE TYPE="Date">{_tally_date(period_start)}</SVFROMDATE>
      <SVTODATE TYPE="Date">{_tally_date(period_end)}</SVTODATE>
      <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
    </STATICVARIABLES>
    <TDL><TDLMESSAGE>
      <COLLECTION NAME="LedgerScrutinyCollection" ISINITIALIZE="Yes">
        <TYPE>Ledger</TYPE>
        <NATIVEMETHOD>Name</NATIVEMETHOD>
        <NATIVEMETHOD>GUID</NATIVEMETHOD>
        <NATIVEMETHOD>MasterID</NATIVEMETHOD>
        <NATIVEMETHOD>Parent</NATIVEMETHOD>
        <NATIVEMETHOD>OpeningBalance</NATIVEMETHOD>
        <NATIVEMETHOD>ClosingBalance</NATIVEMETHOD>
      </COLLECTION>
      <COLLECTION NAME="LedgerScrutinyGroups" ISINITIALIZE="Yes">
        <TYPE>Group</TYPE>
        <NATIVEMETHOD>Name</NATIVEMETHOD>
        <NATIVEMETHOD>GUID</NATIVEMETHOD>
        <NATIVEMETHOD>MasterID</NATIVEMETHOD>
        <NATIVEMETHOD>Parent</NATIVEMETHOD>
      </COLLECTION>
      <COLLECTION NAME="LedgerScrutinyVouchers" ISINITIALIZE="Yes">
        <TYPE>Voucher</TYPE>
        <NATIVEMETHOD>GUID</NATIVEMETHOD>
        <NATIVEMETHOD>MasterID</NATIVEMETHOD>
        <NATIVEMETHOD>VoucherNumber</NATIVEMETHOD>
        <NATIVEMETHOD>Date</NATIVEMETHOD>
        <NATIVEMETHOD>VoucherTypeName</NATIVEMETHOD>
        <NATIVEMETHOD>Narration</NATIVEMETHOD>
        <NATIVEMETHOD>AllLedgerEntries</NATIVEMETHOD>
      </COLLECTION>
    </TDLMESSAGE></TDL>
  </DESC></BODY>
</ENVELOPE>""".encode("utf-8")


def fetch_trial_balance(
    *,
    endpoint: str,
    company_name: str,
    period_start: date,
    period_end: date,
    timeout_seconds: float = 30,
) -> tuple[dict, bytes]:
    """Fetch and validate the complete Tally account/voucher export."""
    _validate_endpoint(endpoint)
    request_body = build_ledger_request(company_name, period_start, period_end)
    safe_endpoint = _safe_endpoint(endpoint)
    try:
        response = httpx.post(
            endpoint,
            content=request_body,
            headers={"Content-Type": "text/xml; charset=utf-8"},
            timeout=timeout_seconds,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else "unknown"
        raise TallyConnectorError(
            f"TallyPrime HTTP request to {safe_endpoint} failed with status {status_code}."
        ) from exc
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        raise TallyConnectorError(f"Could not reach TallyPrime at {safe_endpoint}.") from exc

    raw_xml = response.content
    parsed_data = parse_tally_xml(
        raw_xml,
        fallback_entity_name=company_name,
        fallback_period_start=period_start,
        fallback_period_end=period_end,
        require_status=True,
    )
    if parsed_data["entity"]["name"] != company_name:
        raise TallyConnectorError(
            f"Tally response company '{parsed_data['entity']['name']}' does not match the requested company."
        )
    if (
        parsed_data["entity"]["financial_year_start"] != period_start
        or parsed_data["entity"]["financial_year_end"] != period_end
    ):
        raise TallyConnectorError(
            "Tally response period does not match the requested period "
            f"{period_start} to {period_end}."
        )
    for ledger in parsed_data["ledgers"]:
        if ledger.get("closing_balance") is None:
            raise TallyConnectorError(
                f"Tally response omitted closing balance for ledger '{ledger['name']}'."
            )
    return parsed_data, raw_xml
