from datetime import date

import httpx
import pytest

from app.ingestion.tally_http import TallyConnectorError, build_ledger_request, fetch_trial_balance


RESPONSE = b"""<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA>
<COMPANY><RENAME>Example Company</RENAME><BOOKSFROM>20250401</BOOKSFROM><BOOKSTO>20260331</BOOKSTO></COMPANY>
<COLLECTION>
<LEDGER NAME="Cash" GUID="LEDGER-CASH"><PARENT>Cash-in-hand</PARENT><OPENINGBALANCE>100.00</OPENINGBALANCE><CLOSINGBALANCE>150.00</CLOSINGBALANCE></LEDGER>
<LEDGER NAME="Capital" GUID="LEDGER-CAPITAL"><PARENT>Capital Account</PARENT><OPENINGBALANCE>100.00 Cr</OPENINGBALANCE><CLOSINGBALANCE>150.00 Cr</CLOSINGBALANCE></LEDGER>
</COLLECTION>
<VOUCHER VCHTYPE="Receipt" GUID="VOUCHER-1"><DATE>20250401</DATE><VOUCHERNUMBER>RCPT-1</VOUCHERNUMBER>
<ALLLEDGERENTRIES.LIST><LEDGERNAME>Cash</LEDGERNAME><ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>-50.00</AMOUNT></ALLLEDGERENTRIES.LIST>
<ALLLEDGERENTRIES.LIST><LEDGERNAME>Capital</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>50.00</AMOUNT></ALLLEDGERENTRIES.LIST>
</VOUCHER>
</DATA></BODY></ENVELOPE>"""


def test_tally_http_connector_maps_ledger_balances(monkeypatch):
    def fake_post(*args, **kwargs):
        return httpx.Response(200, content=RESPONSE, request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("app.ingestion.tally_http.httpx.post", fake_post)
    parsed, raw = fetch_trial_balance(
        endpoint="http://localhost:9000",
        company_name="Example Company",
        period_start=date(2025, 4, 1),
        period_end=date(2026, 3, 31),
    )

    assert raw == RESPONSE
    assert parsed["entity"]["name"] == "Example Company"
    assert parsed["ledgers"][0]["closing_balance"] == 150
    assert parsed["ledgers"][1]["closing_balance"] == -150
    assert parsed["ledgers"][0]["external_id"] == "LEDGER-CASH"
    assert parsed["vouchers"][0]["source_voucher_id"] == "VOUCHER-1"
    assert len(parsed["vouchers"][0]["entries"]) == 2


def test_tally_http_connector_accepts_account_export_without_vouchers(monkeypatch):
    content = b"""<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA>
    <COMPANY><RENAME>Example Company</RENAME><BOOKSFROM>20250401</BOOKSFROM><BOOKSTO>20260331</BOOKSTO></COMPANY>
    <LEDGER NAME="Cash"><PARENT>Cash-in-hand</PARENT><OPENINGBALANCE>0</OPENINGBALANCE><CLOSINGBALANCE>0</CLOSINGBALANCE></LEDGER>
    </DATA></BODY></ENVELOPE>"""

    def fake_post(*args, **kwargs):
        return httpx.Response(200, content=content, request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("app.ingestion.tally_http.httpx.post", fake_post)
    parsed, raw_xml = fetch_trial_balance(
        endpoint="http://localhost:9000",
        company_name="Example Company",
        period_start=date(2025, 4, 1),
        period_end=date(2026, 3, 31),
    )
    assert parsed["vouchers"] == []
    assert raw_xml == content


def test_tally_http_connector_fails_loudly_on_empty_response(monkeypatch):
    def fake_post(*args, **kwargs):
        return httpx.Response(200, content=b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER></ENVELOPE>", request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("app.ingestion.tally_http.httpx.post", fake_post)
    with pytest.raises(TallyConnectorError, match="returned no ledgers"):
        fetch_trial_balance(
            endpoint="http://localhost:9000",
            company_name="Example Company",
            period_start=date(2025, 4, 1),
            period_end=date(2026, 3, 31),
        )


def test_tally_connector_request_selects_company_and_period():
    request = build_ledger_request("A & B", date(2025, 4, 1), date(2026, 3, 31)).decode()
    assert "<SVCURRENTCOMPANY>A &amp; B</SVCURRENTCOMPANY>" in request
    assert "1-Apr-2025" in request
    assert "31-Mar-2026" in request
    assert "<TYPE>Ledger</TYPE>" in request
    assert "<TYPE>Group</TYPE>" in request
    assert "<TYPE>Voucher</TYPE>" in request


@pytest.mark.parametrize(
    ("content", "match"),
    [
        (b"<not-xml", "invalid XML"),
        (b"<ENVELOPE><HEADER><STATUS>0</STATUS><LINEERROR>Company not found</LINEERROR></HEADER></ENVELOPE>", "Company not found"),
        (b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><LEDGER NAME=\"Cash\"><PARENT>Cash-in-hand</PARENT><OPENINGBALANCE>1</OPENINGBALANCE></LEDGER><VOUCHER VCHTYPE=\"Receipt\"><DATE>20250401</DATE><VOUCHERNUMBER>1</VOUCHERNUMBER><ALLLEDGERENTRIES.LIST><LEDGERNAME>Cash</LEDGERNAME><ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>-1</AMOUNT></ALLLEDGERENTRIES.LIST><ALLLEDGERENTRIES.LIST><LEDGERNAME>Cash</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE><AMOUNT>1</AMOUNT></ALLLEDGERENTRIES.LIST></VOUCHER></ENVELOPE>", "closing balance"),
        (b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER></ENVELOPE>", "no ledgers"),
    ],
)
def test_tally_connector_normalizes_connector_failures(monkeypatch, content, match):
    def fake_post(*args, **kwargs):
        return httpx.Response(200, content=content, request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("app.ingestion.tally_http.httpx.post", fake_post)
    with pytest.raises(TallyConnectorError, match=match):
        fetch_trial_balance(
            endpoint="http://localhost:9000",
            company_name="Example Company",
            period_start=date(2025, 4, 1),
            period_end=date(2026, 3, 31),
        )


def test_tally_connector_does_not_expose_endpoint_credentials_or_raw_xml(monkeypatch):
    raw_secret = "password=super-secret"

    def fake_post(*args, **kwargs):
        raise httpx.ConnectError(raw_secret, request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("app.ingestion.tally_http.httpx.post", fake_post)
    with pytest.raises(TallyConnectorError) as exc_info:
        fetch_trial_balance(
            endpoint="http://user:super-secret@example.test:9000",
            company_name="Example Company",
            period_start=date(2025, 4, 1),
            period_end=date(2026, 3, 31),
        )
    message = str(exc_info.value)
    assert "super-secret" not in message
    assert raw_secret not in message


def test_tally_connector_maps_http_status_failure_without_response_body(monkeypatch):
    def fake_post(*args, **kwargs):
        return httpx.Response(503, content=b"private upstream detail", request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("app.ingestion.tally_http.httpx.post", fake_post)
    with pytest.raises(TallyConnectorError, match="status 503") as exc_info:
        fetch_trial_balance(
            endpoint="http://localhost:9000",
            company_name="Example Company",
            period_start=date(2025, 4, 1),
            period_end=date(2026, 3, 31),
        )
    assert "private upstream detail" not in str(exc_info.value)


def test_tally_connector_rejects_invalid_endpoint_without_posting(monkeypatch):
    def unexpected_post(*args, **kwargs):
        raise AssertionError("invalid endpoints must be rejected before HTTP")

    monkeypatch.setattr("app.ingestion.tally_http.httpx.post", unexpected_post)
    with pytest.raises(TallyConnectorError, match="endpoint must use http or https"):
        fetch_trial_balance(
            endpoint="ftp://user:secret@example.test:9000/export?token=private",
            company_name="Example Company",
            period_start=date(2025, 4, 1),
            period_end=date(2026, 3, 31),
        )


def test_tally_connector_does_not_expose_status_detail_secrets(monkeypatch):
    def fake_post(*args, **kwargs):
        return httpx.Response(
            200,
            content=b"<ENVELOPE><HEADER><STATUS>0</STATUS><LINEERROR>token=secret-value</LINEERROR></HEADER></ENVELOPE>",
            request=httpx.Request("POST", args[0]),
        )

    monkeypatch.setattr("app.ingestion.tally_http.httpx.post", fake_post)
    with pytest.raises(TallyConnectorError) as exc_info:
        fetch_trial_balance(
            endpoint="http://localhost:9000",
            company_name="Example Company",
            period_start=date(2025, 4, 1),
            period_end=date(2026, 3, 31),
        )
    assert "secret-value" not in str(exc_info.value)


def test_tally_connector_rejects_response_without_status(monkeypatch):
    def fake_post(*args, **kwargs):
        return httpx.Response(
            200,
            content=RESPONSE.replace(b"<STATUS>1</STATUS>", b""),
            request=httpx.Request("POST", args[0]),
        )

    monkeypatch.setattr("app.ingestion.tally_http.httpx.post", fake_post)
    with pytest.raises(TallyConnectorError, match="omitted status"):
        fetch_trial_balance(
            endpoint="http://localhost:9000",
            company_name="Example Company",
            period_start=date(2025, 4, 1),
            period_end=date(2026, 3, 31),
        )


def test_tally_connector_maps_timeout_without_leaking_exception(monkeypatch):
    def timeout(*args, **kwargs):
        raise httpx.TimeoutException("secret timeout detail")

    monkeypatch.setattr("app.ingestion.tally_http.httpx.post", timeout)
    with pytest.raises(TallyConnectorError, match="Could not reach TallyPrime") as exc_info:
        fetch_trial_balance(
            endpoint="http://localhost:9000",
            company_name="Example Company",
            period_start=date(2025, 4, 1),
            period_end=date(2026, 3, 31),
        )
    assert "secret timeout detail" not in str(exc_info.value)
