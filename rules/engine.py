from decimal import Decimal
from typing import List, Callable, Optional
from datetime import date
from db.models import Entity, LedgerAccount, TrialBalanceSnapshot, AuditException
from rules.account_groups import get_normal_balance

# Rule function signature type
RuleFunc = Callable[[Entity, List[LedgerAccount], List[TrialBalanceSnapshot]], List[AuditException]]

# Global registry of rule functions
_RULE_REGISTRY: List[RuleFunc] = []


def register_rule(rule_fn: RuleFunc) -> RuleFunc:
    """Decorator to register a rule function in the engine."""
    if rule_fn not in _RULE_REGISTRY:
        _RULE_REGISTRY.append(rule_fn)
    return rule_fn


def get_registered_rules() -> List[RuleFunc]:
    """Returns a list of all registered rule functions."""
    return list(_RULE_REGISTRY)


def clear_registered_rules() -> None:
    """Clears the rule registry (mainly for testing)."""
    _RULE_REGISTRY.clear()


@register_rule
def check_normal_balance(
    entity: Entity, 
    accounts: List[LedgerAccount], 
    snapshots: List[TrialBalanceSnapshot]
) -> List[AuditException]:
    """
    Rule 1: Normal Balance Check
    Checks if the closing balance of an account group matches its expected normal balance side.
    """
    exceptions = []
    
    # Filter snapshots for the current entity's financial year
    current_snapshots = [
        s for s in snapshots 
        if s.entity_id == entity.id and s.period_start == entity.financial_year_start
    ]
    
    # Create a map of ledger_account_id -> snapshot for quick lookup
    snap_map = {s.ledger_account_id: s for s in current_snapshots}
    
    for acc in accounts:
        if acc.id not in snap_map:
            continue
            
        snap = snap_map[acc.id]
        normal_bal = acc.normal_balance.lower()
        cl_bal = snap.closing_balance
        
        # We store: Debit as positive, Credit as negative
        if normal_bal == "debit" and cl_bal < Decimal("0.00"):
            variance = abs(cl_bal)
            exc = AuditException(
                entity_id=entity.id,
                rule_name="normal_balance_check",
                ledger_account_id=acc.id,
                severity="error",
                message=(
                    f"Account '{acc.name}' has normal balance 'debit' "
                    f"but has a credit closing balance of {variance}."
                )
            )
            # Attach transient Python attribute for materiality filtering
            exc.variance = variance
            exceptions.append(exc)
            
        elif normal_bal == "credit" and cl_bal > Decimal("0.00"):
            variance = cl_bal
            exc = AuditException(
                entity_id=entity.id,
                rule_name="normal_balance_check",
                ledger_account_id=acc.id,
                severity="error",
                message=(
                    f"Account '{acc.name}' has normal balance 'credit' "
                    f"but has a debit closing balance of {variance}."
                )
            )
            # Attach transient Python attribute for materiality filtering
            exc.variance = variance
            exceptions.append(exc)
            
    return exceptions


@register_rule
def check_opening_balance_continuity(
    entity: Entity, 
    accounts: List[LedgerAccount], 
    snapshots: List[TrialBalanceSnapshot]
) -> List[AuditException]:
    """
    Rule 2: Opening Balance Continuity Check
    Checks if the opening balance of an account matches the prior period's closing balance.
    """
    exceptions = []
    
    # Group snapshots by ledger account
    acc_snapshots: dict[int, List[TrialBalanceSnapshot]] = {}
    for s in snapshots:
        if s.entity_id == entity.id:
            acc_snapshots.setdefault(s.ledger_account_id, []).append(s)
            
    for acc in accounts:
        snaps = acc_snapshots.get(acc.id, [])
        if not snaps:
            continue
            
        # Find the snapshot for the current period
        current_snap = next(
            (s for s in snaps if s.period_start == entity.financial_year_start), 
            None
        )
        if current_snap is None:
            continue
            
        # Find the prior period snapshot (the one ending just before current_snap starts)
        prior_snap = None
        for s in snaps:
            if s.period_end <= current_snap.period_start:
                if prior_snap is None or s.period_end > prior_snap.period_end:
                    prior_snap = s
                    
        if prior_snap is not None:
            curr_opening = current_snap.opening_balance
            prior_closing = prior_snap.closing_balance
            
            if curr_opening != prior_closing:
                variance = abs(curr_opening - prior_closing)
                exc = AuditException(
                    entity_id=entity.id,
                    rule_name="opening_balance_continuity",
                    ledger_account_id=acc.id,
                    severity="error",
                    message=(
                        f"Account '{acc.name}' opening balance ({curr_opening}) does not match "
                        f"prior period closing balance ({prior_closing}). Variance: {variance}."
                    )
                )
                # Attach transient Python attribute for materiality filtering
                exc.variance = variance
                exceptions.append(exc)
                
    return exceptions


def filter_by_materiality(entity: Entity, exceptions: List[AuditException]) -> List[AuditException]:
    """
    Rule 3: Materiality Threshold Filter
    Suppresses exceptions where the variance is below the entity's materiality threshold.
    """
    threshold = entity.materiality_threshold
    filtered = []
    
    for exc in exceptions:
        # Critical or system exceptions should never be filtered out
        if exc.severity == "critical":
            filtered.append(exc)
            continue
            
        variance = getattr(exc, "variance", Decimal("0.00"))
        if variance >= threshold:
            filtered.append(exc)
            
    return filtered


def run_scrutiny(
    entity: Entity, 
    accounts: List[LedgerAccount], 
    snapshots: List[TrialBalanceSnapshot]
) -> List[AuditException]:
    """
    Runs all registered scrutiny rules in isolation, catching exceptions 
    per-rule, and applies the materiality filter at the end.
    """
    all_exceptions: List[AuditException] = []
    
    for rule in _RULE_REGISTRY:
        try:
            exceptions = rule(entity, accounts, snapshots)
            all_exceptions.extend(exceptions)
        except Exception as e:
            # Catch exceptions per-rule so one broken rule can't take down the whole run
            system_exc = AuditException(
                entity_id=entity.id,
                rule_name=rule.__name__,
                severity="critical",
                message=f"Rule '{rule.__name__}' failed with unexpected error: {str(e)}"
            )
            all_exceptions.append(system_exc)
            
    # Apply materiality threshold filter (Rule 3)
    return filter_by_materiality(entity, all_exceptions)
