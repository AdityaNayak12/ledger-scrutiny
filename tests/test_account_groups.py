import pytest
from rules.account_groups import get_normal_balance, UnrecognizedAccountGroupError


def test_get_normal_balance_valid():
    assert get_normal_balance("Capital Account") == "credit"
    assert get_normal_balance("Fixed Assets") == "debit"
    assert get_normal_balance("Sundry Debtors") == "debit"
    assert get_normal_balance("Sundry Creditors") == "credit"
    assert get_normal_balance("Sales Accounts") == "credit"
    assert get_normal_balance("Purchase Accounts") == "debit"
    assert get_normal_balance("Direct Expenses") == "debit"
    assert get_normal_balance("Indirect Expenses") == "debit"
    assert get_normal_balance("Direct Income") == "credit"
    assert get_normal_balance("Indirect Income") == "credit"
    assert get_normal_balance("Current Assets") == "debit"
    assert get_normal_balance("Current Liabilities") == "credit"
    assert get_normal_balance("Bank Accounts") == "debit"
    assert get_normal_balance("Cash-in-hand") == "debit"
    assert get_normal_balance("Loans (Liability)") == "credit"
    assert get_normal_balance("Duties & Taxes") == "credit"
    assert get_normal_balance("Provisions") == "credit"
    assert get_normal_balance("Investments") == "debit"
    assert get_normal_balance("Stock-in-hand") == "debit"


def test_get_normal_balance_invalid():
    with pytest.raises(UnrecognizedAccountGroupError) as exc_info:
        get_normal_balance("Suspense Account")
    assert "Unrecognized ledger account group: 'Suspense Account'" in str(exc_info.value)
