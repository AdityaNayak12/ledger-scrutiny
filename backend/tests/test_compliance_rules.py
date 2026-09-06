from datetime import date
from decimal import Decimal

import pytest

from app.db.models import Entity, LedgerAccount, TrialBalanceSnapshot
from app.rules.compliance import run_compliance_checks

START, END = date(2025, 4, 1), date(2026, 3, 31)


@pytest.mark.parametrize("creditors, tds, threshold, expected", [
    ("99", "0", "100", 0), ("100", "0", "100", 1),
    ("101", "0", "100", 1), ("100", "-1", "100", 0),
    ("100", "1", "100", 1), ("0", "0", "100", 0),
    ("0", "0", "0", 1),
])
def test_compliance_preserves_tds_evidence(creditors, tds, threshold, expected):
    entity = Entity(id=1, name="Example",
                    materiality_threshold=Decimal(threshold))
    accounts = [
        LedgerAccount(id=1, entity_id=1, name="Supplier",
                      group_name="Sundry Creditors", normal_balance="credit"),
        LedgerAccount(id=2, entity_id=1, name="TDS payable",
                      group_name="Duties & Taxes", normal_balance="credit"),
    ]
    snapshots = [TrialBalanceSnapshot(
        entity_id=1, ledger_account_id=account.id, period_start=START,
        period_end=END, opening_balance=Decimal("0"), closing_balance=closing,
        total_debits=Decimal("0"), total_credits=Decimal("0"),
    ) for account, closing in zip(accounts, [-Decimal(creditors), Decimal(tds)])]
    findings = run_compliance_checks(entity, accounts, snapshots, START, END)
    assert len(findings) == expected
    if expected:
        finding = findings[0]
        assert finding.rule_name == "tds_liability_check"
        assert finding.severity == "warning"
        assert finding.variance == Decimal(creditors)
        assert (finding.period_start, finding.period_end) == (START, END)
        assert finding.message == (
            f"Total Sundry Creditors balance is {Decimal(creditors):.2f} "
            "(exceeds materiality), but no TDS liability account with a payable "
            "balance was found. Verify if TDS is applicable and has been deducted."
        )
