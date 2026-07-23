import pytest
from decimal import Decimal
from datetime import date

from app.db.models import Entity, LedgerAccount, TrialBalanceSnapshot, AuditException
from app.rules.engine import (
    run_scrutiny,
    check_normal_balance,
    check_opening_balance_continuity,
    filter_by_materiality,
    register_rule,
    clear_registered_rules
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
    # No variance attribute, but severity is critical, so must keep
    
    filtered = filter_by_materiality(sample_entity, [exc1, exc2, exc3, exc_critical])
    assert len(filtered) == 3
    assert filtered[0].id == 2
    assert filtered[1].id == 3
    assert filtered[2].id == 4


def test_rules_engine_isolation(sample_entity):
    # Register a failing rule and a successful rule
    clear_registered_rules()
    
    @register_rule
    def failing_rule(ent, accs, snaps):
        raise RuntimeError("Something went wrong")
        
    @register_rule
    def successful_rule(ent, accs, snaps):
        exc = AuditException(rule_name="successful_rule", severity="error", message="Successful rule warning")
        exc.variance = Decimal("2000.00")
        return [exc]
        
    # Run the engine
    exceptions = run_scrutiny(sample_entity, [], [])
    
    assert len(exceptions) == 2
    
    rules_run = [e.rule_name for e in exceptions]
    assert "failing_rule" in rules_run
    assert "successful_rule" in rules_run
    
    # Verify that the failing rule created a critical exception
    failing_exc = next(e for e in exceptions if e.rule_name == "failing_rule")
    assert failing_exc.severity == "critical"
    assert "failing_rule' failed with unexpected error: Something went wrong" in failing_exc.message
    
    # Restore standard rules for other tests by clearing and re-importing
    clear_registered_rules()
    register_rule(check_normal_balance)
    register_rule(check_opening_balance_continuity)
