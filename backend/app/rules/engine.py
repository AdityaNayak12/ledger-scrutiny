from decimal import Decimal
from typing import List, Callable, Optional
from datetime import date
from app.db.models import Entity, LedgerAccount, TrialBalanceSnapshot, AuditException
from app.rules.account_groups import get_normal_balance

# Rule function signature type
RuleFunc = Callable[[Entity, List[LedgerAccount], List[TrialBalanceSnapshot]], List[AuditException]]
RULE_SET_VERSION = "1"


def register_rule(materiality_scope: str):
    """
    Decorator to register a rule function and explicitly set its materiality_scope.
    materiality_scope must be either 'exempt' or 'magnitude'.
    """
    if materiality_scope not in ("exempt", "magnitude"):
        raise ValueError(
            f"Invalid materiality_scope '{materiality_scope}'. "
            f"Must be 'exempt' or 'magnitude'."
        )
    def decorator(func: RuleFunc) -> RuleFunc:
        func.materiality_scope = materiality_scope
        return func
    return decorator


@register_rule(materiality_scope="exempt")
def check_normal_balance(
    entity: Entity, 
    accounts: List[LedgerAccount], 
    snapshots: List[TrialBalanceSnapshot]
) -> List[AuditException]:
    """
    Rule: Normal Balance Check
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
                period_start=entity.financial_year_start,
                period_end=entity.financial_year_end,
                rule_name="normal_balance_check",
                ledger_account_id=acc.id,
                severity="error",
                message=(
                    f"Account '{acc.name}' has normal balance 'debit' "
                    f"but has a credit closing balance of {variance}."
                )
            )
            exc.variance = variance
            exceptions.append(exc)
            
        elif normal_bal == "credit" and cl_bal > Decimal("0.00"):
            variance = cl_bal
            exc = AuditException(
                entity_id=entity.id,
                period_start=entity.financial_year_start,
                period_end=entity.financial_year_end,
                rule_name="normal_balance_check",
                ledger_account_id=acc.id,
                severity="error",
                message=(
                    f"Account '{acc.name}' has normal balance 'credit' "
                    f"but has a debit closing balance of {variance}."
                )
            )
            exc.variance = variance
            exceptions.append(exc)
            
    return exceptions


@register_rule(materiality_scope="magnitude")
def check_opening_balance_continuity(
    entity: Entity, 
    accounts: List[LedgerAccount], 
    snapshots: List[TrialBalanceSnapshot]
) -> List[AuditException]:
    """
    Rule: Opening Balance Continuity Check
    Checks if the opening balance of an account matches the prior period's closing balance.
    Applies ONLY to balance sheet groups.
    """
    exceptions = []
    
    BALANCE_SHEET_GROUPS = {
        "Capital Account", "Fixed Assets", "Investments", "Current Assets", 
        "Sundry Debtors", "Cash-in-hand", "Bank Accounts", "Stock-in-hand", 
        "Loans & Advances (Asset)", "Current Liabilities", "Sundry Creditors", 
        "Duties & Taxes", "Provisions", "Secured Loans", "Unsecured Loans", 
        "Loans (Liability)", "Reserves & Surplus"
    }
    
    acc_id_map = {acc.id: acc.name for acc in accounts}
    
    current_snaps = {}
    prior_snaps = {}
    
    for s in snapshots:
        acc_name = s.ledger_account.name if s.ledger_account else acc_id_map.get(s.ledger_account_id)
        if not acc_name:
            continue
            
        if s.period_start == entity.financial_year_start:
            current_snaps[acc_name] = s
        elif s.period_end <= entity.financial_year_start:
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
                period_start=entity.financial_year_start,
                period_end=entity.financial_year_end,
                rule_name="opening_balance_continuity",
                ledger_account_id=acc.id,
                severity="error",
                message=(
                    f"Account '{acc.name}' opening balance ({curr_opening}) does not match "
                    f"prior period closing balance ({prior_closing}). Variance: {variance}."
                )
            )
            exc.variance = variance
            exc.apply_materiality = True
            exceptions.append(exc)
            
    return exceptions


@register_rule(materiality_scope="exempt")
def trial_balance_balances(
    entity: Entity, 
    accounts: List[LedgerAccount], 
    snapshots: List[TrialBalanceSnapshot]
) -> List[AuditException]:
    """
    RULE 1: trial_balance_balances
    Sums all TrialBalanceSnapshot closing_balance values across every account.
    The sum across ALL accounts should be zero (total debits = total credits).
    """
    exceptions = []
    
    current_snapshots = [
        s for s in snapshots 
        if s.entity_id == entity.id and s.period_start == entity.financial_year_start
    ]
    
    if not current_snapshots:
        return exceptions
        
    net_sum = sum(s.closing_balance for s in current_snapshots)
    variance = abs(net_sum)
    
    if variance > Decimal("0.00"):
        exc = AuditException(
            entity_id=entity.id,
            period_start=entity.financial_year_start,
            period_end=entity.financial_year_end,
            rule_name="trial_balance_balances",
            ledger_account_id=None,
            severity="error",
            message=(
                f"Trial balance does not balance: net variance of {variance:.2f} "
                f"across {len(current_snapshots)} accounts."
            )
        )
        exc.variance = variance
        exc.apply_materiality = True
        exceptions.append(exc)
        
    return exceptions


@register_rule(materiality_scope="exempt")
def negative_cash_balance(
    entity: Entity, 
    accounts: List[LedgerAccount], 
    snapshots: List[TrialBalanceSnapshot]
) -> List[AuditException]:
    """
    RULE 2: negative_cash_balance
    Checks for credit (negative) closing balances on accounts in the 'Cash-in-hand' group.
    """
    exceptions = []
    
    current_snapshots = [
        s for s in snapshots 
        if s.entity_id == entity.id and s.period_start == entity.financial_year_start
    ]
    
    snap_map = {s.ledger_account_id: s for s in current_snapshots}
    
    for acc in accounts:
        if acc.group_name != "Cash-in-hand":
            continue
            
        if acc.id not in snap_map:
            continue
            
        snap = snap_map[acc.id]
        if snap.closing_balance < Decimal("0.00"):
            variance = abs(snap.closing_balance)
            exc = AuditException(
                entity_id=entity.id,
                period_start=entity.financial_year_start,
                period_end=entity.financial_year_end,
                rule_name="negative_cash_balance",
                ledger_account_id=acc.id,
                severity="error",
                message=(
                    f"Account '{acc.name}' has negative cash balance ({variance:.2f} credit). "
                    f"Cash balance cannot be negative."
                )
            )
            exc.variance = variance
            exc.apply_materiality = False
            exceptions.append(exc)
            
    return exceptions


@register_rule(materiality_scope="exempt")
def suspense_account_nonzero(
    entity: Entity, 
    accounts: List[LedgerAccount], 
    snapshots: List[TrialBalanceSnapshot]
) -> List[AuditException]:
    """
    RULE 3: suspense_account_nonzero
    Checks for non-zero closing balances on any account with 'Suspense' in name or group 'Suspense Account'.
    """
    exceptions = []
    
    current_snapshots = [
        s for s in snapshots 
        if s.entity_id == entity.id and s.period_start == entity.financial_year_start
    ]
    
    snap_map = {s.ledger_account_id: s for s in current_snapshots}
    
    for acc in accounts:
        is_suspense = (
            "suspense" in acc.name.lower() or 
            acc.group_name == "Suspense Account"
        )
        if not is_suspense:
            continue
            
        if acc.id not in snap_map:
            continue
            
        snap = snap_map[acc.id]
        if snap.closing_balance != Decimal("0.00"):
            variance = abs(snap.closing_balance)
            exc = AuditException(
                entity_id=entity.id,
                period_start=entity.financial_year_start,
                period_end=entity.financial_year_end,
                rule_name="suspense_account_nonzero",
                ledger_account_id=acc.id,
                severity="error",
                message=(
                    f"Suspense account '{acc.name}' has non-zero closing balance "
                    f"({snap.closing_balance:.2f})."
                )
            )
            exc.variance = variance
            exc.apply_materiality = True
            exceptions.append(exc)
            
    return exceptions


def filter_by_materiality(entity: Entity, exceptions: List[AuditException]) -> List[AuditException]:
    """
    Materiality Threshold Filter
    Suppresses exceptions where the variance is below the entity's materiality threshold,
    but only for rules that opted in (apply_materiality = True).
    """
    threshold = entity.materiality_threshold
    filtered = []
    
    for exc in exceptions:
        if exc.severity == "critical":
            filtered.append(exc)
            continue
            
        if not getattr(exc, "apply_materiality", False):
            filtered.append(exc)
            continue
            
        variance = getattr(exc, "variance", Decimal("0.00"))
        if variance >= threshold:
            filtered.append(exc)
            
    return filtered


# Static list of registered scrutiny rules
RULES: List[RuleFunc] = [
    check_normal_balance,
    check_opening_balance_continuity,
    trial_balance_balances,
    negative_cash_balance,
    suspense_account_nonzero,
]


def run_scrutiny(
    entity: Entity, 
    accounts: List[LedgerAccount], 
    snapshots: List[TrialBalanceSnapshot],
    rules: Optional[List[RuleFunc]] = None
) -> List[AuditException]:
    """
    Runs scrutiny rules in isolation, catching exceptions 
    per-rule, and applies the materiality filter at the end.
    """
    if rules is None:
        rules = RULES
        
    all_exceptions: List[AuditException] = []
    
    for rule in rules:
        scope = getattr(rule, "materiality_scope", None)
        if scope not in ("exempt", "magnitude"):
            raise ValueError(
                f"Rule '{rule.__name__}' missing explicit materiality_scope declaration. "
                f"Must declare 'exempt' or 'magnitude' via @register_rule."
            )

        try:
            exceptions = rule(entity, accounts, snapshots)
            for exc in exceptions:
                if scope == "magnitude":
                    exc.apply_materiality = True
                elif scope == "exempt":
                    exc.apply_materiality = False
            all_exceptions.extend(exceptions)
        except Exception as e:
            system_exc = AuditException(
                entity_id=entity.id,
                period_start=entity.financial_year_start,
                period_end=entity.financial_year_end,
                rule_name=rule.__name__,
                severity="critical",
                message=f"Rule '{rule.__name__}' failed with unexpected error: {str(e)}"
            )
            all_exceptions.append(system_exc)
            
    return filter_by_materiality(entity, all_exceptions)
