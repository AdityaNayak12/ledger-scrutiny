from datetime import date
from decimal import Decimal

from app.db.models import Entity, LedgerAccount, TrialBalanceSnapshot
from app.rules.engine import run_scrutiny


def test_static_rules_use_explicit_period_dates():
    period_start, period_end = date(2025, 4, 1), date(2026, 3, 31)
    entity = Entity(id=1, name="Test Company", materiality_threshold=Decimal("0.00"))
    accounts = [
        LedgerAccount(id=1, entity_id=1, name="Cash", group_name="Cash-in-hand", normal_balance="debit"),
        LedgerAccount(id=2, entity_id=1, name="Capital", group_name="Capital Account", normal_balance="credit"),
        LedgerAccount(id=3, entity_id=1, name="Suspense", group_name="Suspense Account", normal_balance="any"),
    ]
    snapshots = [
        TrialBalanceSnapshot(entity_id=1, ledger_account_id=1, period_start=period_start, period_end=period_end, opening_balance=Decimal("0"), total_debits=Decimal("0"), total_credits=Decimal("100"), closing_balance=Decimal("-100")),
        TrialBalanceSnapshot(entity_id=1, ledger_account_id=2, period_start=period_start, period_end=period_end, opening_balance=Decimal("0"), total_debits=Decimal("100"), total_credits=Decimal("0"), closing_balance=Decimal("100")),
        TrialBalanceSnapshot(entity_id=1, ledger_account_id=3, period_start=period_start, period_end=period_end, opening_balance=Decimal("0"), total_debits=Decimal("10"), total_credits=Decimal("0"), closing_balance=Decimal("10")),
    ]

    exceptions = run_scrutiny(entity, accounts, snapshots, period_start, period_end)

    assert {exception.rule_name for exception in exceptions} == {"normal_balance_check", "trial_balance_balances", "negative_cash_balance", "suspense_account_nonzero"}
    assert all(exception.period_start == period_start and exception.period_end == period_end for exception in exceptions)
