from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import openpyxl

from app.ingestion.tally_parser import parse_tally_xml


FIXTURE_DIR = Path(__file__).parent / "fixtures"
GL_HEADERS = (
    "Document Number",
    "G/L Account",
    "Posting Date",
    "Amount in local currency",
)


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    return value


def test_golden_gl_fixture_counts_and_zero_balance_invariants():
    workbook = openpyxl.load_workbook(FIXTURE_DIR / "golden_gl.xlsx", data_only=True)
    rows = list(workbook.active.values)
    headers = rows[0]
    data_rows = rows[1:]

    assert tuple(headers[:4]) == GL_HEADERS
    assert len(data_rows) == 6
    assert len({row[0] for row in data_rows}) == 3
    assert len({row[1] for row in data_rows}) == 5
    assert min(_as_date(row[2]) for row in data_rows) == date(2025, 4, 1)
    assert max(_as_date(row[2]) for row in data_rows) == date(2025, 6, 30)

    amounts_by_document = {}
    for document, _account, _posting_date, amount, *_ in data_rows:
        amounts_by_document.setdefault(document, Decimal("0.00"))
        amounts_by_document[document] += Decimal(str(amount))

    assert all(total == Decimal("0.00") for total in amounts_by_document.values())
    assert sum(amounts_by_document.values(), Decimal("0.00")) == Decimal("0.00")


def test_golden_tally_fixture_counts_and_zero_balance_invariants():
    xml = (FIXTURE_DIR / "golden_tally.xml").read_bytes()
    parsed = parse_tally_xml(xml)

    assert parsed["entity"]["name"] == "Ledger Fixture Co"
    assert parsed["entity"]["financial_year_start"] == date(2025, 4, 1)
    assert parsed["entity"]["financial_year_end"] == date(2026, 3, 31)
    assert len(parsed["ledgers"]) == 5
    assert len(parsed["vouchers"]) == 3
    assert sum(len(voucher["entries"]) for voucher in parsed["vouchers"]) == 6
    assert {ledger["name"] for ledger in parsed["ledgers"]} == {
        "110000",
        "120000",
        "210000",
        "400000",
        "500000",
    }

    assert sum(
        (ledger["opening_balance"] for ledger in parsed["ledgers"]),
        Decimal("0.00"),
    ) == Decimal("0.00")
    assert sum(
        (ledger["closing_balance"] for ledger in parsed["ledgers"]),
        Decimal("0.00"),
    ) == Decimal("0.00")

    for voucher in parsed["vouchers"]:
        signed_total = sum(
            entry["amount"] if entry["type"] == "debit" else -entry["amount"]
            for entry in voucher["entries"]
        )
        assert signed_total == Decimal("0.00")
