from datetime import date
from decimal import Decimal

import pytest

from app.db.models import Entity, LedgerAccount, TrialBalanceSnapshot
from app.rules import engine
from app.rules.engine import rule_set_version, run_scrutiny


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

    assert {exception.rule_name for exception in exceptions} == {"normal_balance_check", "trial_balance_balances", "negative_cash_balance", "suspense_account_nonzero", "tds_liability_check"}
    assert all(exception.period_start == period_start and exception.period_end == period_end for exception in exceptions)


@pytest.mark.parametrize("pack, version", [
    (None, "core-v1"),
    ("unknown", "core-v1"),
    ("compliance_v1", "core-v1+compliance-v1"),
    ("manufacturing_v1", "core-v1+compliance-v1+manufacturing-v1"),
])
def test_pack_contract(pack, version):
    start, end = date(2025, 4, 1), date(2026, 3, 31)
    entity = Entity(id=1, name="Example",
                    materiality_threshold=Decimal("100"))
    account = LedgerAccount(id=1, entity_id=1, name="Supplier",
                            group_name="Sundry Creditors", normal_balance="credit")
    snapshot = TrialBalanceSnapshot(
        entity_id=1, ledger_account_id=1, period_start=start, period_end=end,
        opening_balance=Decimal("-100"), closing_balance=Decimal("-100"),
        total_debits=Decimal("0"), total_credits=Decimal("0"),
    )
    findings = run_scrutiny(entity, [account], [snapshot], start, end, pack)
    names = [finding.rule_name for finding in findings]
    assert names.count("tds_liability_check") == 1
    assert set(names) == {"tds_liability_check", "trial_balance_balances"}
    assert rule_set_version(pack) == version
    repeated = run_scrutiny(entity, [account], [snapshot], start, end, pack)
    signature = lambda f: (f.rule_name, f.severity, f.message, f.period_start,
                           f.period_end, getattr(f, "variance", None))
    assert list(map(signature, findings)) == list(map(signature, repeated))


@pytest.mark.parametrize("pack", [None, "unknown", "compliance_v1", "manufacturing_v1"])
@pytest.mark.parametrize("gstin", [None, "", "arbitrary-unvalidated-value"])
def test_clean_pack_does_not_depend_on_gst(pack, gstin):
    start, end = date(2025, 4, 1), date(2026, 3, 31)
    entity = Entity(id=1, name="Example", gstin=gstin,
                    materiality_threshold=Decimal("100"))
    assert run_scrutiny(entity, [], [], start, end, pack) == []
    assert entity.gstin == gstin


@pytest.mark.parametrize("pack, expected_calls", [
    (None, 0), ("unknown", 0), ("compliance_v1", 1), ("manufacturing_v1", 1),
])
def test_compliance_dispatch_is_explicit(monkeypatch, pack, expected_calls):
    calls = []
    def record(*args):
        calls.append(args)
        return []
    monkeypatch.setattr(engine, "run_compliance_checks", record)
    entity = Entity(id=1, name="Example", rule_pack="compliance_v1",
                    materiality_threshold=Decimal("100"))
    start, end = date(2025, 4, 1), date(2026, 3, 31)
    engine.run_scrutiny(entity, [], [], start, end, pack)
    assert len(calls) == expected_calls
    if calls:
        assert calls[0] == (entity, [], [], start, end)
