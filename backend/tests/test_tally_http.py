from datetime import date

import httpx
import pytest

from app.ingestion.tally_http import TallyConnectorError, build_ledger_request, fetch_trial_balance


RESPONSE = b"""<ENVELOPE><HEADER><STATUS>1</STATUS></HEADER><BODY><DATA><COLLECTION>
<LEDGER NAME="Cash"><PARENT>Cash-in-hand</PARENT><OPENINGBALANCE>100.00</OPENINGBALANCE><CLOSINGBALANCE>150.00</CLOSINGBALANCE></LEDGER>
<LEDGER NAME="Capital"><PARENT>Capital Account</PARENT><OPENINGBALANCE>100.00 Cr</OPENINGBALANCE><CLOSINGBALANCE>150.00 Cr</CLOSINGBALANCE></LEDGER>
</COLLECTION></DATA></BODY></ENVELOPE>"""


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
