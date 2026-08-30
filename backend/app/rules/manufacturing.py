from datetime import date
from decimal import Decimal
from typing import List, Optional

from app.db.models import AuditException, Entity, LedgerAccount, TrialBalanceSnapshot

INVENTORY_TOLERANCE_RATE = Decimal("0.01")
GROSS_MARGIN_SHIFT_THRESHOLD = Decimal("10.00")


def _money(value: Decimal) -> str:
    return f"₹{value:,.2f}"


def _metrics(
    accounts: List[LedgerAccount],
    snapshots: List[TrialBalanceSnapshot],
    period_start: date,
) -> Optional[dict]:
    accounts_by_id = {account.id: account for account in accounts}
    rows = [snapshot for snapshot in snapshots if snapshot.period_start == period_start]
    if not rows:
        return None

    def account_for(snapshot: TrialBalanceSnapshot) -> Optional[LedgerAccount]:
        return snapshot.ledger_account or accounts_by_id.get(snapshot.ledger_account_id)

    sales = -sum(
        (snapshot.closing_balance for snapshot in rows if account_for(snapshot) and account_for(snapshot).group_name == "Sales Accounts"),
        Decimal("0.00"),
    )
    direct_costs = sum(
        (snapshot.closing_balance for snapshot in rows if account_for(snapshot) and account_for(snapshot).group_name in {"Purchase Accounts", "Direct Expenses"}),
        Decimal("0.00"),
    )
    inventory = [
        (account_for(snapshot), snapshot)
        for snapshot in rows
        if account_for(snapshot) and account_for(snapshot).group_name == "Stock-in-hand"
    ]
    opening_inventory = sum((snapshot.opening_balance for _, snapshot in inventory), Decimal("0.00"))
    closing_inventory = sum((snapshot.closing_balance for _, snapshot in inventory), Decimal("0.00"))
    cogs = direct_costs + opening_inventory - closing_inventory
    return {
        "sales": sales,
        "direct_costs": direct_costs,
        "inventory": inventory,
        "opening_inventory": opening_inventory,
        "closing_inventory": closing_inventory,
        "cogs": cogs,
    }


def manufacturing_low_inventory_movement(
    entity: Entity,
    accounts: List[LedgerAccount],
    snapshots: List[TrialBalanceSnapshot],
    period_start: date,
    period_end: date,
) -> List[AuditException]:
    metrics = _metrics(accounts, snapshots, period_start)
    materiality = Decimal(entity.materiality_threshold)
    if not metrics or metrics["sales"] <= 0 or metrics["sales"] < materiality or metrics["cogs"] <= 0 or metrics["cogs"] < materiality:
        return []

    material_components = [
        (account, snapshot)
        for account, snapshot in metrics["inventory"]
        if max(abs(snapshot.opening_balance), abs(snapshot.closing_balance)) >= materiality
    ]
    if not material_components:
        return []

    tolerance = metrics["cogs"] * INVENTORY_TOLERANCE_RATE
    aggregate_movement = abs(metrics["closing_inventory"] - metrics["opening_inventory"])
    component_movements = [abs(snapshot.closing_balance - snapshot.opening_balance) for _, snapshot in material_components]
    if aggregate_movement > tolerance or any(movement > tolerance for movement in component_movements):
        return []

    exact_copy_forward = all(movement == 0 for movement in component_movements)
    component_text = ", ".join(
        f"{account.name}: {_money(abs(snapshot.closing_balance - snapshot.opening_balance))}"
        for account, snapshot in material_components
    )
    exception = AuditException(
        entity_id=entity.id,
        period_start=period_start,
        period_end=period_end,
        rule_name="manufacturing_low_inventory_movement",
        severity="error" if exact_copy_forward else "warning",
        message=(
            f"Material inventory moved by only {_money(aggregate_movement)} against COGS of {_money(metrics['cogs'])}. "
            f"This is within the Manufacturing v1 firm-policy tolerance of 1% of COGS ({_money(tolerance)}). "
            f"Component movements — {component_text}. Review inventory valuation and cut-off."
        ),
    )
    exception.variance = aggregate_movement
    return [exception]


def manufacturing_gross_margin_shift(
    entity: Entity,
    accounts: List[LedgerAccount],
    snapshots: List[TrialBalanceSnapshot],
    period_start: date,
    period_end: date,
) -> List[AuditException]:
    current = _metrics(accounts, snapshots, period_start)
    prior_periods = {
        snapshot.period_start: snapshot.period_end
        for snapshot in snapshots
        if snapshot.period_end < period_start
    }
    if not current or not prior_periods:
        return []
    prior_start = max(prior_periods, key=lambda start: prior_periods[start])
    prior = _metrics(accounts, snapshots, prior_start)
    materiality = Decimal(entity.materiality_threshold)
    if (
        not prior
        or current["sales"] <= 0
        or prior["sales"] <= 0
        or current["sales"] < materiality
        or prior["sales"] < materiality
        or current["cogs"] <= 0
        or prior["cogs"] <= 0
    ):
        return []

    current_margin = ((current["sales"] - current["cogs"]) / current["sales"]) * Decimal("100")
    prior_margin = ((prior["sales"] - prior["cogs"]) / prior["sales"]) * Decimal("100")
    movement = abs(current_margin - prior_margin)
    if movement < GROSS_MARGIN_SHIFT_THRESHOLD:
        return []

    exception = AuditException(
        entity_id=entity.id,
        period_start=period_start,
        period_end=period_end,
        rule_name="manufacturing_gross_margin_shift",
        severity="warning",
        message=(
            f"Gross margin moved from {prior_margin:.1f}% in the prior period to {current_margin:.1f}% in the current period "
            f"({movement:.1f} percentage points). This exceeds the Manufacturing v1 firm-policy threshold of "
            f"{GROSS_MARGIN_SHIFT_THRESHOLD:.0f} percentage points. Review pricing, production costs, inventory valuation, and cut-off."
        ),
    )
    exception.variance = movement
    return [exception]
