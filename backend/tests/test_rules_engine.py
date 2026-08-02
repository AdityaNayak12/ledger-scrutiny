import pytest
from decimal import Decimal
from datetime import date

from app.db.models import Entity, LedgerAccount, TrialBalanceSnapshot, AuditException
from app.rules.engine import (
    register_rule,
    run_scrutiny,
    check_normal_balance,
    check_opening_balance_continuity,
    trial_balance_balances,
    negative_cash_balance,
    suspense_account_nonzero,
    filter_by_materiality
)


@pytest.fixture
def sample_entity():
    entity = Entity(
        id=1,
        name="Test Company",
        materiality_threshold=Decimal("1000.00")
    )
    entity.financial_year_start = date(2025, 4, 1)
    entity.financial_year_end = date(2026, 3, 31)
    return entity


def test_normal_balance_check_clean(sample_entity):
    # Debit account with positive (debit) closing balance
    acc_debit = LedgerAccount(id=1, entity_id=1, name="Cash", group_name="Cash-in-hand", normal_balance="debit")
    snap_debit = TrialBalanceSnapshot(
        entity_id=1,
        ledger_account_id=1,
        period_start=date(2025, 4, 1),
        period_end=date(2026, 3, 31),
        opening_balance=Decimal("0.00"),
        total_debits=Decimal("500.00"),
        total_credits=Decimal("0.00"),
        closing_balance=Decimal("500.00") # Debit
    )
    
    # Credit account with negative (credit) closing balance
    acc_credit = LedgerAccount(id=2, entity_id=1, name="Capital", group_name="Capital Account", normal_balance="credit")
    snap_credit = TrialBalanceSnapshot(
        entity_id=1,
        ledger_account_id=2,
        period_start=date(2025, 4, 1),
        period_end=date(2026, 3, 31),
        opening_balance=Decimal("0.00"),
        total_debits=Decimal("0.00"),
        total_credits=Decimal("500.00"),
        closing_balance=Decimal("-500.00") # Credit
    )
    
    exceptions = check_normal_balance(sample_entity, [acc_debit, acc_credit], [snap_debit, snap_credit])
    assert len(exceptions) == 0


def test_normal_balance_check_violations(sample_entity):
    # Debit account with negative (credit) closing balance
    acc_debit = LedgerAccount(id=1, entity_id=1, name="Cash", group_name="Cash-in-hand", normal_balance="debit")
    snap_debit = TrialBalanceSnapshot(
        entity_id=1,
        ledger_account_id=1,
        period_start=date(2025, 4, 1),
        period_end=date(2026, 3, 31),
        opening_balance=Decimal("0.00"),
        total_debits=Decimal("0.00"),
        total_credits=Decimal("5000.00"),
        closing_balance=Decimal("-5000.00") # Credit (Violation)
    )
    
    # Credit account with positive (debit) closing balance
    acc_credit = LedgerAccount(id=2, entity_id=1, name="Capital", group_name="Capital Account", normal_balance="credit")
    snap_credit = TrialBalanceSnapshot(
        entity_id=1,
        ledger_account_id=2,
        period_start=date(2025, 4, 1),
        period_end=date(2026, 3, 31),
        opening_balance=Decimal("0.00"),
        total_debits=Decimal("3000.00"),
        total_credits=Decimal("0.00"),
        closing_balance=Decimal("3000.00") # Debit (Violation)
    )
    
    exceptions = check_normal_balance(sample_entity, [acc_debit, acc_credit], [snap_debit, snap_credit])
    assert len(exceptions) == 2
    
    exc_map = {exc.ledger_account_id: exc for exc in exceptions}
    assert exc_map[1].rule_name == "normal_balance_check"
    assert exc_map[1].variance == Decimal("5000.00")
    assert "credit closing balance of 5000" in exc_map[1].message
    
    assert exc_map[2].rule_name == "normal_balance_check"
    assert exc_map[2].variance == Decimal("3000.00")
    assert "debit closing balance of 3000" in exc_map[2].message


def test_opening_balance_continuity_clean(sample_entity):
    acc = LedgerAccount(id=1, entity_id=1, name="Cash", group_name="Cash-in-hand", normal_balance="debit")
    
    prior_snap = TrialBalanceSnapshot(
        entity_id=1,
        ledger_account_id=1,
        period_start=date(2024, 4, 1),
        period_end=date(2025, 3, 31),
        opening_balance=Decimal("0.00"),
        total_debits=Decimal("12000.00"),
        total_credits=Decimal("2000.00"),
        closing_balance=Decimal("10000.00") # Prior closing
    )
    
    current_snap = TrialBalanceSnapshot(
        entity_id=1,
        ledger_account_id=1,
        period_start=date(2025, 4, 1), # FY start
        period_end=date(2026, 3, 31),
        opening_balance=Decimal("10000.00"), # Matching current opening
        total_debits=Decimal("0.00"),
        total_credits=Decimal("0.00"),
        closing_balance=Decimal("10000.00")
    )
    
    exceptions = check_opening_balance_continuity(sample_entity, [acc], [prior_snap, current_snap])
    assert len(exceptions) == 0


def test_opening_balance_continuity_broken(sample_entity):
    acc = LedgerAccount(id=1, entity_id=1, name="Cash", group_name="Cash-in-hand", normal_balance="debit")
    
    prior_snap = TrialBalanceSnapshot(
        entity_id=1,
        ledger_account_id=1,
        period_start=date(2024, 4, 1),
        period_end=date(2025, 3, 31),
        opening_balance=Decimal("0.00"),
        total_debits=Decimal("12000.00"),
        total_credits=Decimal("2000.00"),
        closing_balance=Decimal("10000.00") # Prior closing is 10000
    )
    
    current_snap = TrialBalanceSnapshot(
        entity_id=1,
        ledger_account_id=1,
        period_start=date(2025, 4, 1),
        period_end=date(2026, 3, 31),
        opening_balance=Decimal("8500.00"), # Current opening is 8500 (Mismatch!)
        total_debits=Decimal("0.00"),
        total_credits=Decimal("0.00"),
        closing_balance=Decimal("8500.00")
    )
    
    exceptions = check_opening_balance_continuity(sample_entity, [acc], [prior_snap, current_snap])
    assert len(exceptions) == 1
    assert exceptions[0].rule_name == "opening_balance_continuity"
    assert exceptions[0].variance == Decimal("1500.00")
    assert "opening balance (8500" in exceptions[0].message
    assert "prior period closing balance (10000" in exceptions[0].message


def test_materiality_threshold_filter(sample_entity):
    # Entity threshold is 1000.00
    exc1 = AuditException(id=1, rule_name="r1", severity="error", message="m1")
    exc1.variance = Decimal("500.00") # Suppress (500 < 1000)
    exc1.apply_materiality = True
    
    exc2 = AuditException(id=2, rule_name="r2", severity="error", message="m2")
    exc2.variance = Decimal("1500.00") # Keep (1500 >= 1000)
    exc2.apply_materiality = True
    
    exc3 = AuditException(id=3, rule_name="r3", severity="error", message="m3")
    exc3.variance = Decimal("1000.00") # Keep (1000 >= 1000)
    exc3.apply_materiality = True
    
    exc_critical = AuditException(id=4, rule_name="system", severity="critical", message="critical error")
    
    filtered = filter_by_materiality(sample_entity, [exc1, exc2, exc3, exc_critical])
    assert len(filtered) == 3
    assert filtered[0].id == 2
    assert filtered[1].id == 3
    assert filtered[2].id == 4


def test_rules_engine_isolation(sample_entity):
    @register_rule(materiality_scope="exempt")
    def failing_rule(ent, accs, snaps):
        raise RuntimeError("Something went wrong")
        
    @register_rule(materiality_scope="magnitude")
    def successful_rule(ent, accs, snaps):
        exc = AuditException(rule_name="successful_rule", severity="error", message="Successful rule warning")
        exc.variance = Decimal("2000.00")
        return [exc]
        
    exceptions = run_scrutiny(sample_entity, [], [], rules=[failing_rule, successful_rule])
    
    assert len(exceptions) == 2
    rules_run = [e.rule_name for e in exceptions]
    assert "failing_rule" in rules_run
    assert "successful_rule" in rules_run
    
    failing_exc = next(e for e in exceptions if e.rule_name == "failing_rule")
    assert failing_exc.severity == "critical"
    assert "failing_rule' failed with unexpected error: Something went wrong" in failing_exc.message


def test_rule_missing_materiality_scope_fails_loud(sample_entity):
    def unregistered_rule(ent, accs, snaps):
        return []
        
    with pytest.raises(ValueError) as exc_info:
        run_scrutiny(sample_entity, [], [], rules=[unregistered_rule])
        
    assert "missing explicit materiality_scope declaration" in str(exc_info.value)


def test_trial_balance_balances_clean(sample_entity):
    acc1 = LedgerAccount(id=1, entity_id=1, name="Capital", group_name="Capital Account", normal_balance="credit")
    snap1 = TrialBalanceSnapshot(
        entity_id=1,
        ledger_account_id=1,
        period_start=date(2025, 4, 1),
        period_end=date(2026, 3, 31),
        opening_balance=Decimal("0.00"),
        total_debits=Decimal("0.00"),
        total_credits=Decimal("50000.00"),
        closing_balance=Decimal("-50000.00")
    )
    
    acc2 = LedgerAccount(id=2, entity_id=1, name="Cash", group_name="Cash-in-hand", normal_balance="debit")
    snap2 = TrialBalanceSnapshot(
        entity_id=1,
        ledger_account_id=2,
        period_start=date(2025, 4, 1),
        period_end=date(2026, 3, 31),
        opening_balance=Decimal("0.00"),
        total_debits=Decimal("50000.00"),
        total_credits=Decimal("0.00"),
        closing_balance=Decimal("50000.00")
    )
    
    exceptions = trial_balance_balances(sample_entity, [acc1, acc2], [snap1, snap2])
    assert len(exceptions) == 0


def test_trial_balance_balances_imbalance(sample_entity):
    acc1 = LedgerAccount(id=1, entity_id=1, name="Capital", group_name="Capital Account", normal_balance="credit")
    snap1 = TrialBalanceSnapshot(
        entity_id=1,
        ledger_account_id=1,
        period_start=date(2025, 4, 1),
        period_end=date(2026, 3, 31),
        opening_balance=Decimal("0.00"),
        total_debits=Decimal("0.00"),
        total_credits=Decimal("50000.00"),
        closing_balance=Decimal("-50000.00")
    )
    
    acc2 = LedgerAccount(id=2, entity_id=1, name="Cash", group_name="Cash-in-hand", normal_balance="debit")
    snap2 = TrialBalanceSnapshot(
        entity_id=1,
        ledger_account_id=2,
        period_start=date(2025, 4, 1),
        period_end=date(2026, 3, 31),
        opening_balance=Decimal("0.00"),
        total_debits=Decimal("75000.00"),
        total_credits=Decimal("0.00"),
        closing_balance=Decimal("75000.00")
    )
    
    exceptions = run_scrutiny(sample_entity, [acc1, acc2], [snap1, snap2], rules=[trial_balance_balances])
    assert len(exceptions) == 1
    exc = exceptions[0]
    assert exc.rule_name == "trial_balance_balances"
    assert exc.ledger_account_id is None
    assert exc.variance == Decimal("25000.00")
    assert "Trial balance does not balance: net variance of 25000.00 across 2 accounts" in exc.message


def test_negative_cash_balance(sample_entity):
    acc = LedgerAccount(id=1, entity_id=1, name="Petty Cash Variance", group_name="Cash-in-hand", normal_balance="debit")
    snap = TrialBalanceSnapshot(
        entity_id=1,
        ledger_account_id=1,
        period_start=date(2025, 4, 1),
        period_end=date(2026, 3, 31),
        opening_balance=Decimal("2000.00"),
        total_debits=Decimal("0.00"),
        total_credits=Decimal("2150.00"),
        closing_balance=Decimal("-150.00")
    )
    
    exceptions = run_scrutiny(sample_entity, [acc], [snap], rules=[negative_cash_balance])
    assert len(exceptions) == 1
    exc = exceptions[0]
    assert exc.rule_name == "negative_cash_balance"
    assert exc.ledger_account_id == 1
    assert exc.variance == Decimal("150.00")
    assert "has negative cash balance (150.00 credit)" in exc.message
    assert "Cash balance cannot be negative" in exc.message


def test_suspense_account_nonzero_flagged(sample_entity):
    acc = LedgerAccount(id=1, entity_id=1, name="Unreconciled Suspense", group_name="Suspense Account", normal_balance="any")
    snap = TrialBalanceSnapshot(
        entity_id=1,
        ledger_account_id=1,
        period_start=date(2025, 4, 1),
        period_end=date(2026, 3, 31),
        opening_balance=Decimal("0.00"),
        total_debits=Decimal("25000.00"),
        total_credits=Decimal("0.00"),
        closing_balance=Decimal("25000.00")
    )
    
    exceptions = run_scrutiny(sample_entity, [acc], [snap], rules=[suspense_account_nonzero])
    assert len(exceptions) == 1
    exc = exceptions[0]
    assert exc.rule_name == "suspense_account_nonzero"
    assert exc.ledger_account_id == 1
    assert exc.variance == Decimal("25000.00")
    assert "Suspense account 'Unreconciled Suspense' has non-zero closing balance (25000.00)" in exc.message


def test_suspense_account_nonzero_clean(sample_entity):
    acc = LedgerAccount(id=1, entity_id=1, name="Suspense Account", group_name="Suspense Account", normal_balance="any")
    snap = TrialBalanceSnapshot(
        entity_id=1,
        ledger_account_id=1,
        period_start=date(2025, 4, 1),
        period_end=date(2026, 3, 31),
        opening_balance=Decimal("0.00"),
        total_debits=Decimal("10000.00"),
        total_credits=Decimal("10000.00"),
        closing_balance=Decimal("0.00")
    )
    
    exceptions = run_scrutiny(sample_entity, [acc], [snap], rules=[suspense_account_nonzero])
    assert len(exceptions) == 0
