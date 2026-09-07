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

PUBLIC_ENDPOINT = "http://8.8.8.8:9000"


def test_tally_http_connector_maps_ledger_balances(monkeypatch):
    def fake_post(*args, **kwargs):
        return httpx.Response(200, content=RESPONSE, request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("app.ingestion.tally_http.httpx.post", fake_post)
    parsed, raw = fetch_trial_balance(
        endpoint=PUBLIC_ENDPOINT,
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


def test_tally_http_connector_rejects_account_export_without_vouchers(monkeypatch):
    content = b"""<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA>
    <COMPANY><RENAME>Example Company</RENAME><BOOKSFROM>20250401</BOOKSFROM><BOOKSTO>20260331</BOOKSTO></COMPANY>
    <LEDGER NAME="Cash"><PARENT>Cash-in-hand</PARENT><OPENINGBALANCE>0</OPENINGBALANCE><CLOSINGBALANCE>0</CLOSINGBALANCE></LEDGER>
    </DATA></BODY></ENVELOPE>"""

    def fake_post(*args, **kwargs):
        return httpx.Response(200, content=content, request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("app.ingestion.tally_http.httpx.post", fake_post)
    with pytest.raises(TallyConnectorError, match="no vouchers"):
        fetch_trial_balance(
            endpoint=PUBLIC_ENDPOINT,
            company_name="Example Company",
            period_start=date(2025, 4, 1),
            period_end=date(2026, 3, 31),
        )


def test_tally_http_connector_fails_loudly_on_empty_response(monkeypatch):
    def fake_post(*args, **kwargs):
        return httpx.Response(200, content=b"<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER></ENVELOPE>", request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("app.ingestion.tally_http.httpx.post", fake_post)
    with pytest.raises(TallyConnectorError, match="returned no ledgers"):
        fetch_trial_balance(
            endpoint=PUBLIC_ENDPOINT,
            company_name="Example Company",
            period_start=date(2025, 4, 1),
            period_end=date(2026, 3, 31),
        )


def test_tally_connector_request_selects_company_and_period():
    request = build_ledger_request("A & B", date(2025, 4, 1), date(2026, 3, 31)).decode()
    assert "<SVCURRENTCOMPANY>A &amp; B</SVCURRENTCOMPANY>" in request
    assert "1-Apr-2025" in request
    assert "31-Mar-2026" in request
    assert "<ID>LedgerScrutinyCollection</ID>" in request
    assert "<COLLECTION>LedgerScrutinyLedgers, LedgerScrutinyGroups, LedgerScrutinyVouchers</COLLECTION>" in request
    assert "<FETCH>Name, GUID, MasterID, Parent, OpeningBalance, ClosingBalance</FETCH>" in request
    assert "<FETCH>Name, GUID, MasterID, Parent</FETCH>" in request
    assert "<FETCH>GUID, VCHKEY, MasterID, VoucherNumber, Date, VoucherTypeName, Narration, AllLedgerEntries.*</FETCH>" in request
    assert "<NATIVEMETHOD>AllLedgerEntries</NATIVEMETHOD>" not in request


@pytest.mark.parametrize(
    ("content", "match"),
    [
        (b"<not-xml", "invalid XML"),
        (b"<ENVELOPE><HEADER><STATUS>0</STATUS><LINEERROR>opaque-secret-123</LINEERROR></HEADER></ENVELOPE>", "rejected the export request"),
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
            endpoint=PUBLIC_ENDPOINT,
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
            endpoint="http://user:super-secret@8.8.8.8:9000",
            company_name="Example Company",
            period_start=date(2025, 4, 1),
            period_end=date(2026, 3, 31),
        )
    message = str(exc_info.value)
    assert "super-secret" not in message
    assert raw_secret not in message


def test_tally_connector_does_not_expose_endpoint_path_in_error_or_context(monkeypatch):
    endpoint = "http://user:secret@8.8.8.8:9000/export/signed-token?access=secret#fragment"

    def fake_post(*args, **kwargs):
        raise httpx.ConnectError("upstream failure", request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("app.ingestion.tally_http.httpx.post", fake_post)
    with pytest.raises(TallyConnectorError) as exc_info:
        fetch_trial_balance(
            endpoint=endpoint,
            company_name="Example Company",
            period_start=date(2025, 4, 1),
            period_end=date(2026, 3, 31),
        )

    error = exc_info.value
    message = "Could not reach TallyPrime at http://8.8.8.8:9000."
    assert str(error) == message
    assert repr(error) == f"TallyConnectorError({message!r})"
    assert error.__cause__ is None
    assert error.__context__ is None


def test_tally_connector_maps_http_status_failure_without_response_body(monkeypatch):
    def fake_post(*args, **kwargs):
        return httpx.Response(503, content=b"private upstream detail", request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("app.ingestion.tally_http.httpx.post", fake_post)
    with pytest.raises(TallyConnectorError, match="status 503") as exc_info:
        fetch_trial_balance(
            endpoint=PUBLIC_ENDPOINT,
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
            endpoint=PUBLIC_ENDPOINT,
            company_name="Example Company",
            period_start=date(2025, 4, 1),
            period_end=date(2026, 3, 31),
        )
    assert "secret-value" not in str(exc_info.value)


def test_tally_connector_does_not_expose_opaque_malformed_amount(monkeypatch):
    content = RESPONSE.replace(b"-50.00", b"opaque-secret-456", 1)

    def fake_post(*args, **kwargs):
        return httpx.Response(200, content=content, request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("app.ingestion.tally_http.httpx.post", fake_post)
    with pytest.raises(TallyConnectorError) as exc_info:
        fetch_trial_balance(
            endpoint=PUBLIC_ENDPOINT,
            company_name="Example Company",
            period_start=date(2025, 4, 1),
            period_end=date(2026, 3, 31),
        )
    assert "opaque-secret-456" not in str(exc_info.value)


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
            endpoint=PUBLIC_ENDPOINT,
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
            endpoint=PUBLIC_ENDPOINT,
            company_name="Example Company",
            period_start=date(2025, 4, 1),
            period_end=date(2026, 3, 31),
        )
    assert "secret timeout detail" not in str(exc_info.value)


@pytest.mark.parametrize("exception_kind", ["status", "http", "type", "value"])
def test_tally_connector_does_not_retain_http_exception_context(monkeypatch, exception_kind):
    def fake_post(*args, **kwargs):
        request = httpx.Request("POST", args[0])
        if exception_kind == "status":
            response = httpx.Response(503, request=request)
            raise httpx.HTTPStatusError("authorization=secret", request=request, response=response)
        if exception_kind == "http":
            raise httpx.ConnectError("authorization=secret", request=request)
        if exception_kind == "type":
            raise TypeError("authorization=secret")
        raise ValueError("authorization=secret")

    monkeypatch.setattr("app.ingestion.tally_http.httpx.post", fake_post)
    with pytest.raises(TallyConnectorError) as exc_info:
        fetch_trial_balance(
            endpoint="http://user:secret@8.8.8.8:9000",
            company_name="Example Company",
            period_start=date(2025, 4, 1),
            period_end=date(2026, 3, 31),
        )

    error = exc_info.value
    assert error.__cause__ is None
    assert error.__context__ is None
    assert "secret" not in str(error)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:9000",
        "http://127.0.0.1:9000",
        "http://10.0.0.1:9000",
        "http://169.254.169.254/latest/meta-data",
        "http://0.0.0.0:9000",
        "http://192.0.2.1:9000",
        "http://[::1]:9000",
        "http://[fe80::1]:9000",
        "http://[fd00::1]:9000",
        "http://metadata.google.internal:80",
    ],
)
def test_tally_connector_rejects_non_global_endpoint_without_posting(monkeypatch, endpoint):
    monkeypatch.delenv("TALLY_ALLOW_LOCAL_ENDPOINTS", raising=False)
    monkeypatch.delenv("TALLY_ALLOWED_HOSTS", raising=False)

    def unexpected_post(*args, **kwargs):
        raise AssertionError("non-global endpoints must be rejected before HTTP")

    monkeypatch.setattr("app.ingestion.tally_http.httpx.post", unexpected_post)
    with pytest.raises(TallyConnectorError, match="endpoint"):
        fetch_trial_balance(
            endpoint=endpoint,
            company_name="Example Company",
            period_start=date(2025, 4, 1),
            period_end=date(2026, 3, 31),
        )


def test_tally_connector_allows_localhost_only_with_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("TALLY_ALLOW_LOCAL_ENDPOINTS", "1")

    def fake_post(*args, **kwargs):
        return httpx.Response(200, content=RESPONSE, request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("app.ingestion.tally_http.httpx.post", fake_post)
    parsed, raw = fetch_trial_balance(
        endpoint="http://localhost:9000",
        company_name="Example Company",
        period_start=date(2025, 4, 1),
        period_end=date(2026, 3, 31),
    )

    assert parsed["entity"]["name"] == "Example Company"
    assert raw == RESPONSE


def test_tally_connector_allows_exactly_configured_hostname(monkeypatch):
    monkeypatch.setenv("TALLY_ALLOWED_HOSTS", "tally.example.test")

    def fake_post(*args, **kwargs):
        return httpx.Response(200, content=RESPONSE, request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("app.ingestion.tally_http.httpx.post", fake_post)
    parsed, raw = fetch_trial_balance(
        endpoint="http://tally.example.test:9000",
        company_name="Example Company",
        period_start=date(2025, 4, 1),
        period_end=date(2026, 3, 31),
    )

    assert parsed["entity"]["name"] == "Example Company"
    assert raw == RESPONSE


def test_tally_connector_rejects_lookalike_hostname_with_configured_allowlist(monkeypatch):
    monkeypatch.setenv("TALLY_ALLOWED_HOSTS", "tally.example.test")

    def unexpected_post(*args, **kwargs):
        raise AssertionError("lookalike endpoints must be rejected before HTTP")

    monkeypatch.setattr("app.ingestion.tally_http.httpx.post", unexpected_post)
    with pytest.raises(TallyConnectorError, match="host must be explicitly allowed"):
        fetch_trial_balance(
            endpoint="http://tally.example.test.attacker.test:9000",
            company_name="Example Company",
            period_start=date(2025, 4, 1),
            period_end=date(2026, 3, 31),
        )


def test_tally_connector_disables_redirects(monkeypatch):
    captured = {}

    def fake_post(*args, **kwargs):
        captured.update(kwargs)
        return httpx.Response(200, content=RESPONSE, request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("app.ingestion.tally_http.httpx.post", fake_post)
    fetch_trial_balance(
        endpoint=PUBLIC_ENDPOINT,
        company_name="Example Company",
        period_start=date(2025, 4, 1),
        period_end=date(2026, 3, 31),
    )

    assert captured["follow_redirects"] is False
