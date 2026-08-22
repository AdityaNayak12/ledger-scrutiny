"""Supported TallyPrime HTTP/XML connector.

This module intentionally talks to a running TallyPrime instance; it never
attempts to decode the private ``*.1800`` company-data files.
"""
from datetime import date
from decimal import Decimal
from xml.sax.saxutils import escape

import httpx
from lxml import etree

from app.ingestion.tally_parser import parse_tally_amount


class TallyConnectorError(ValueError):
    pass


def _tally_date(value: date) -> str:
    return value.strftime("%-d-%b-%Y")


def build_ledger_request(company_name: str, period_start: date, period_end: date) -> bytes:
    """Build a read-only TDL collection request for ledger balances."""
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
        <NATIVEMETHOD>Parent</NATIVEMETHOD>
        <NATIVEMETHOD>OpeningBalance</NATIVEMETHOD>
        <NATIVEMETHOD>ClosingBalance</NATIVEMETHOD>
      </COLLECTION>
    </TDLMESSAGE></TDL>
  </DESC></BODY>
</ENVELOPE>""".encode("utf-8")


def fetch_trial_balance(
    *, endpoint: str,
    company_name: str,
    period_start: date,
    period_end: date,
    timeout_seconds: float = 30,
) -> tuple[dict, bytes]:
    """Fetch ledger balances from TallyPrime and map them to our core schema."""
    request_body = build_ledger_request(company_name, period_start, period_end)
    try:
        response = httpx.post(
            endpoint,
            content=request_body,
            headers={"Content-Type": "text/xml; charset=utf-8"},
            timeout=timeout_seconds,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise TallyConnectorError(f"Could not reach TallyPrime at {endpoint}: {exc}") from exc

    try:
        root = etree.fromstring(response.content)
    except etree.XMLSyntaxError as exc:
        raise TallyConnectorError("TallyPrime returned invalid XML.") from exc

    status = root.findtext(".//HEADER/STATUS")
    if status and status.strip() != "1":
        detail = " ".join(text.strip() for text in root.xpath(".//LINEERROR/text()") if text.strip())
        raise TallyConnectorError(detail or "TallyPrime rejected the export request.")

    ledgers = []
    for ledger in root.findall(".//LEDGER"):
        name = (ledger.get("NAME") or ledger.findtext("NAME") or "").strip()
        group = (ledger.findtext("PARENT") or "").strip()
        if not name or not group:
            continue
        opening = parse_tally_amount(ledger.findtext("OPENINGBALANCE") or "0")
        closing_text = ledger.findtext("CLOSINGBALANCE")
        if closing_text is None:
            raise TallyConnectorError(f"Tally response omitted closing balance for ledger '{name}'.")
        ledgers.append({
            "name": name,
            "group_name": group,
            "opening_balance": opening,
            "closing_balance": parse_tally_amount(closing_text),
        })

    if not ledgers:
        raise TallyConnectorError(
            "TallyPrime returned no ledgers. Check that the company is loaded, the exact company name is selected, "
            "and the HTTP server is enabled."
        )

    return {
        "entity": {
            "name": company_name,
            "financial_year_start": period_start,
            "financial_year_end": period_end,
        },
        "ledgers": ledgers,
        "vouchers": [],
    }, response.content
