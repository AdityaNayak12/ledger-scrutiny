from datetime import date
from decimal import Decimal

from app.db.models import Entity, LedgerAccount, TrialBalanceSnapshot
from app.rules.manufacturing import manufacturing_gross_margin_shift, manufacturing_low_inventory_movement

CURRENT_START = date(2025, 4, 1)
CURRENT_END = date(2026, 3, 31)
PRIOR_START = date(2024, 4, 1)
PRIOR_END = date(2025, 3, 31)


def _snapshot(account, start, end, opening, closing):
    return TrialBalanceSnapshot(
        entity_id=1,
        ledger_account_id=account.id,
        ledger_account=account,
        period_start=start,
        period_end=end,
        opening_balance=Decimal(opening),
        closing_balance=Decimal(closing),
        total_debits=Decimal("0"),
        total_credits=Decimal("0"),
    )


def _accounts():
    return [
        LedgerAccount(id=1, entity_id=1, name="Sales", group_name="Sales Accounts", normal_balance="credit"),
        LedgerAccount(id=2, entity_id=1, name="Purchases", group_name="Purchase Accounts", normal_balance="debit"),
        LedgerAccount(id=3, entity_id=1, name="Raw Materials", group_name="Stock-in-hand", normal_balance="debit"),
        LedgerAccount(id=4, entity_id=1, name="WIP", group_name="Stock-in-hand", normal_balance="debit"),
    ]


def test_inventory_rule_handles_near_zero_exact_and_offsetting_movements():
    entity = Entity(id=1, name="Manufacturer", materiality_threshold=Decimal("100000"))
    sales, purchases, raw_materials, wip = _accounts()
    activity = [
        _snapshot(sales, CURRENT_START, CURRENT_END, "0", "-15000000"),
        _snapshot(purchases, CURRENT_START, CURRENT_END, "0", "13500000"),
    ]
    near_zero = activity + [
        _snapshot(raw_materials, CURRENT_START, CURRENT_END, "1600000", "1640000"),
        _snapshot(wip, CURRENT_START, CURRENT_END, "500000", "480000"),
    ]
    findings = manufacturing_low_inventory_movement(entity, _accounts(), near_zero, CURRENT_START, CURRENT_END)
    assert len(findings) == 1
    assert findings[0].severity == "warning"

    exact = activity + [
        _snapshot(raw_materials, CURRENT_START, CURRENT_END, "1600000", "1600000"),
        _snapshot(wip, CURRENT_START, CURRENT_END, "500000", "500000"),
    ]
    assert manufacturing_low_inventory_movement(entity, _accounts(), exact, CURRENT_START, CURRENT_END)[0].severity == "error"

    offsetting = activity + [
        _snapshot(raw_materials, CURRENT_START, CURRENT_END, "1600000", "1900000"),
        _snapshot(wip, CURRENT_START, CURRENT_END, "500000", "200000"),
    ]
    assert manufacturing_low_inventory_movement(entity, _accounts(), offsetting, CURRENT_START, CURRENT_END) == []


def test_margin_rule_skips_immaterial_sales():
    entity = Entity(id=1, name="Manufacturer", materiality_threshold=Decimal("100000"))
    sales, purchases, raw_materials, _ = _accounts()
    snapshots = [
        _snapshot(sales, PRIOR_START, PRIOR_END, "0", "-50000"),
        _snapshot(purchases, PRIOR_START, PRIOR_END, "0", "30000"),
        _snapshot(raw_materials, PRIOR_START, PRIOR_END, "10000", "10000"),
        _snapshot(sales, CURRENT_START, CURRENT_END, "0", "-50000"),
        _snapshot(purchases, CURRENT_START, CURRENT_END, "0", "45000"),
        _snapshot(raw_materials, CURRENT_START, CURRENT_END, "10000", "10000"),
    ]
    assert manufacturing_gross_margin_shift(entity, _accounts(), snapshots, CURRENT_START, CURRENT_END) == []
