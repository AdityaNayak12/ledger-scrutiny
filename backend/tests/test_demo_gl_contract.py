import json
from pathlib import Path


PROFILE_PATH = Path(__file__).parents[2] / "frontend" / "src" / "demo_gl_profile.json"


def test_demo_gl_profile_matches_supplied_workbook_contract():
    profile = json.loads(PROFILE_PATH.read_text())

    assert profile["headers"] == [
        "Assignment",
        "Invoice No/Reference",
        "Document Number",
        "G/L Account",
        "L DESCRIPTION",
        "Document Type",
        "Posting Date",
        "Document Date",
        "Posting Key",
        "Quantity",
        "Amount in local currency",
        "Clearing Document",
        "Profit Center",
        "Cost Center",
        "Text/Bid/Cont/TndrNo",
        "Supplier",
        "Vendor Name",
        "WBS element",
        "Purchasing Document",
        "Customer Name",
        "Customer",
    ]
    assert profile["source_rows"] == 59168
    assert profile["accepted_rows"] == 59167
    assert profile["documents"] == 10914
    assert profile["accounts"] == 445
    assert profile["signed_total"] == "0.00"
    assert profile["skipped_rows"] == 1
    assert profile["warnings"] == [
        {"column": "Quantity", "source_row": 28096, "source_value": "OM", "normalized_value": None}
    ]
    assert all(len(row) == len(profile["headers"]) for row in profile["sample_rows"])
    assert profile["sample_rows"][0][8] == "40"
    assert profile["sample_rows"][0][10] > 0
    assert profile["sample_rows"][1][8] == "50"
    assert profile["sample_rows"][1][10] < 0
    assert profile["sample_rows"][2][9] == "OM"
    assert profile["footer_row"][4] == "LIABILITY TOTAL"
