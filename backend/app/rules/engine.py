from decimal import Decimal
from typing import List, Callable, Optional
from datetime import date
from app.db.models import Entity, LedgerAccount, TrialBalanceSnapshot, AuditException
from app.rules.account_groups import get_normal_balance

# Rule function signature type
RuleFunc = Callable[[Entity, List[LedgerAccount], List[TrialBalanceSnapshot]], List[AuditException]]

# Global registry of rule functions
_RULE_REGISTRY: List[RuleFunc] = []


def register_rule(rule_fn: RuleFunc) -> RuleFunc:
    """Decorator to register a rule function in the engine."""
    if rule_fn not in _RULE_REGISTRY:
        _RULE_REGISTRY.append(rule_fn)
    return rule_fn



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
    Applies ONLY to balance sheet groups.
    """
    exceptions = []
    
    # Balance sheet groups list from specifications
    BALANCE_SHEET_GROUPS = {
        "Capital Account", "Fixed Assets", "Investments", "Current Assets", 
        "Sundry Debtors", "Cash-in-hand", "Bank Accounts", "Stock-in-hand", 
        "Loans & Advances (Asset)", "Current Liabilities", "Sundry Creditors", 
        "Duties & Taxes", "Provisions", "Secured Loans", "Unsecured Loans", 
        "Loans (Liability)", "Reserves & Surplus"
    }
    
    # Map accounts by ID to support local unit test lookups without database relations
    acc_id_map = {acc.id: acc.name for acc in accounts}
    
    # Map snapshots by account name and period role
    current_snaps = {}  # {account_name: TrialBalanceSnapshot}
    prior_snaps = {}    # {account_name: TrialBalanceSnapshot}
    
    for s in snapshots:
        acc_name = s.ledger_account.name if s.ledger_account else acc_id_map.get(s.ledger_account_id)
        if not acc_name:
            continue
            
        if s.period_start == entity.financial_year_start:
            current_snaps[acc_name] = s
        elif s.period_end <= entity.financial_year_start:
            # Keep the latest prior snapshot if there are multiple
            if acc_name not in prior_snaps or s.period_end > prior_snaps[acc_name].period_end:
                prior_snaps[acc_name] = s
            
    for acc in accounts:
        if acc.group_name not in BALANCE_SHEET_GROUPS:
            continue
            
        if acc.name not in current_snaps or acc.name not in prior_snaps:
            continue
            
        curr_snap = current_snaps[acc.name]
        prior_snap = prior_snaps[acc.name]
        
        curr_opening = curr_snap.opening_balance
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
            exc.apply_materiality = True
            exceptions.append(exc)
            
    return exceptions


def filter_by_materiality(entity: Entity, exceptions: List[AuditException]) -> List[AuditException]:
    """
    Rule 3: Materiality Threshold Filter
    Suppresses exceptions where the variance is below the entity's materiality threshold,
    but only for rules that opted in (apply_materiality = True).
    """
    threshold = entity.materiality_threshold
    filtered = []
    
    for exc in exceptions:
        # Critical/system exceptions should never be filtered out
        if exc.severity == "critical":
            filtered.append(exc)
            continue
            
        # If the exception did not opt-in for materiality, it bypasses the filter entirely
        if not getattr(exc, "apply_materiality", False):
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
            if rule.__name__ == "check_normal_balance":
                print(f"[DEBUG] check_normal_balance generated exceptions count: {len(exceptions)}")
                for e in exceptions:
                    print(f"  - Account ID {e.ledger_account_id}: {e.message}")
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
