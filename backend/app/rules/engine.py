from datetime import date
from decimal import Decimal
from typing import List

from app.db.models import AuditException, Entity, LedgerAccount, TrialBalanceSnapshot
from app.rules.manufacturing import manufacturing_gross_margin_shift, manufacturing_low_inventory_movement

CORE_RULE_SET_VERSION = "core-v1"


def rule_set_version(rule_pack: str | None) -> str:
    return f"{CORE_RULE_SET_VERSION}+manufacturing-v1" if rule_pack == "manufacturing_v1" else CORE_RULE_SET_VERSION


def check_normal_balance(entity: Entity, accounts: List[LedgerAccount], snapshots: List[TrialBalanceSnapshot], period_start: date, period_end: date) -> List[AuditException]:
    snapshots_by_account = {s.ledger_account_id: s for s in snapshots if s.entity_id == entity.id and s.period_start == period_start}
    exceptions = []
    for account in accounts:
        snapshot = snapshots_by_account.get(account.id)
        if not snapshot or account.normal_balance.lower() == "any":
            continue
            
        if account.group_name == "Sundry Creditors":
            continue
        if account.group_name == "Sundry Debtors":
            continue
        if account.group_name == "Bank Accounts" and "current" in account.name.lower():
            continue
            
        if (account.normal_balance.lower() == "debit" and snapshot.closing_balance < 0) or (account.normal_balance.lower() == "credit" and snapshot.closing_balance > 0):
            variance = abs(snapshot.closing_balance)
            side = "credit" if snapshot.closing_balance < 0 else "debit"
            exception = AuditException(entity_id=entity.id, period_start=period_start, period_end=period_end, rule_name="normal_balance_check", ledger_account_id=account.id, severity="error", message=f"Account '{account.name}' has normal balance '{account.normal_balance.lower()}' but has a {side} closing balance of {variance}.")
            exception.variance = variance
            exceptions.append(exception)
    return exceptions


def check_opening_balance_continuity(entity: Entity, accounts: List[LedgerAccount], snapshots: List[TrialBalanceSnapshot], period_start: date, period_end: date) -> List[AuditException]:
    balance_sheet_groups = {"Capital Account", "Fixed Assets", "Investments", "Current Assets", "Sundry Debtors", "Cash-in-hand", "Bank Accounts", "Stock-in-hand", "Loans & Advances (Asset)", "Current Liabilities", "Sundry Creditors", "Duties & Taxes", "Provisions", "Secured Loans", "Unsecured Loans", "Loans (Liability)", "Reserves & Surplus"}
    account_names = {account.id: account.name for account in accounts}
    current, prior = {}, {}
    for snapshot in snapshots:
        name = snapshot.ledger_account.name if snapshot.ledger_account else account_names.get(snapshot.ledger_account_id)
        if not name:
            continue
        if snapshot.period_start == period_start:
            current[name] = snapshot
        elif snapshot.period_end <= period_start and (name not in prior or snapshot.period_end > prior[name].period_end):
            prior[name] = snapshot
    exceptions = []
    for account in accounts:
        if account.group_name not in balance_sheet_groups or account.name not in current or account.name not in prior:
            continue
        opening, closing = current[account.name].opening_balance, prior[account.name].closing_balance
        if opening != closing:
            variance = abs(opening - closing)
            exception = AuditException(entity_id=entity.id, period_start=period_start, period_end=period_end, rule_name="opening_balance_continuity", ledger_account_id=account.id, severity="error", message=f"Account '{account.name}' opening balance ({opening}) does not match prior period closing balance ({closing}). Variance: {variance}.")
            exception.variance = variance
            exception.apply_materiality = True
            exceptions.append(exception)
    return exceptions


def trial_balance_balances(entity: Entity, accounts: List[LedgerAccount], snapshots: List[TrialBalanceSnapshot], period_start: date, period_end: date) -> List[AuditException]:
    current = [s for s in snapshots if s.entity_id == entity.id and s.period_start == period_start]
    variance = abs(sum((s.closing_balance for s in current), Decimal("0.00")))
    if not current or not variance:
        return []
    exception = AuditException(entity_id=entity.id, period_start=period_start, period_end=period_end, rule_name="trial_balance_balances", severity="error", message=f"Trial balance does not balance: net variance of {variance:.2f} across {len(current)} accounts.")
    exception.variance = variance
    exception.apply_materiality = True
    return [exception]


def negative_cash_balance(entity: Entity, accounts: List[LedgerAccount], snapshots: List[TrialBalanceSnapshot], period_start: date, period_end: date) -> List[AuditException]:
    snapshots_by_account = {s.ledger_account_id: s for s in snapshots if s.entity_id == entity.id and s.period_start == period_start}
    exceptions = []
    for account in accounts:
        snapshot = snapshots_by_account.get(account.id)
        if account.group_name == "Cash-in-hand" and snapshot and snapshot.closing_balance < 0:
            variance = abs(snapshot.closing_balance)
            exception = AuditException(entity_id=entity.id, period_start=period_start, period_end=period_end, rule_name="negative_cash_balance", ledger_account_id=account.id, severity="error", message=f"Account '{account.name}' has negative cash balance ({variance:.2f} credit). Cash balance cannot be negative.")
            exception.variance = variance
            exceptions.append(exception)
    return exceptions


def suspense_account_nonzero(entity: Entity, accounts: List[LedgerAccount], snapshots: List[TrialBalanceSnapshot], period_start: date, period_end: date) -> List[AuditException]:
    snapshots_by_account = {s.ledger_account_id: s for s in snapshots if s.entity_id == entity.id and s.period_start == period_start}
    exceptions = []
    for account in accounts:
        snapshot = snapshots_by_account.get(account.id)
        if snapshot and ("suspense" in account.name.lower() or account.group_name == "Suspense Account") and snapshot.closing_balance:
            variance = abs(snapshot.closing_balance)
            exception = AuditException(entity_id=entity.id, period_start=period_start, period_end=period_end, rule_name="suspense_account_nonzero", ledger_account_id=account.id, severity="error", message=f"Suspense account '{account.name}' has non-zero closing balance ({snapshot.closing_balance:.2f}).")
            exception.variance = variance
            exception.apply_materiality = True
            exceptions.append(exception)
    return exceptions



def current_account_credit_balance(entity: Entity, accounts: List[LedgerAccount], snapshots: List[TrialBalanceSnapshot], period_start: date, period_end: date) -> List[AuditException]:
    snapshots_by_account = {s.ledger_account_id: s for s in snapshots if s.entity_id == entity.id and s.period_start == period_start}
    exceptions = []
    for account in accounts:
        snapshot = snapshots_by_account.get(account.id)
        if snapshot and account.group_name == "Bank Accounts" and "current" in account.name.lower() and snapshot.closing_balance < 0:
            variance = abs(snapshot.closing_balance)
            exception = AuditException(entity_id=entity.id, period_start=period_start, period_end=period_end, rule_name="current_account_credit_balance", ledger_account_id=account.id, severity="error", message=f"Current bank account '{account.name}' has a credit (negative) closing balance of {variance:.2f}. Balance cannot be negative unless it is an overdraft account.")
            exception.variance = variance
            exceptions.append(exception)
    return exceptions

def creditor_debit_balance(entity: Entity, accounts: List[LedgerAccount], snapshots: List[TrialBalanceSnapshot], period_start: date, period_end: date) -> List[AuditException]:
    snapshots_by_account = {s.ledger_account_id: s for s in snapshots if s.entity_id == entity.id and s.period_start == period_start}
    exceptions = []
    for account in accounts:
        snapshot = snapshots_by_account.get(account.id)
        if snapshot and account.group_name == "Sundry Creditors" and snapshot.closing_balance > 0:
            variance = abs(snapshot.closing_balance)
            exception = AuditException(entity_id=entity.id, period_start=period_start, period_end=period_end, rule_name="creditor_debit_balance", ledger_account_id=account.id, severity="error", message=f"Creditor account '{account.name}' has a debit closing balance of {variance:.2f}. This indicates an advance given to the supplier or an excess payment made.")
            exception.variance = variance
            exceptions.append(exception)
    return exceptions

def debtor_credit_balance(entity: Entity, accounts: List[LedgerAccount], snapshots: List[TrialBalanceSnapshot], period_start: date, period_end: date) -> List[AuditException]:
    snapshots_by_account = {s.ledger_account_id: s for s in snapshots if s.entity_id == entity.id and s.period_start == period_start}
    exceptions = []
    for account in accounts:
        snapshot = snapshots_by_account.get(account.id)
        if snapshot and account.group_name == "Sundry Debtors" and snapshot.closing_balance < 0:
            variance = abs(snapshot.closing_balance)
            exception = AuditException(entity_id=entity.id, period_start=period_start, period_end=period_end, rule_name="debtor_credit_balance", ledger_account_id=account.id, severity="error", message=f"Debtor account '{account.name}' has a credit closing balance of {variance:.2f}. This indicates an advance received from the customer.")
            exception.variance = variance
            exceptions.append(exception)
    return exceptions

def tds_liability_check(entity: Entity, accounts: List[LedgerAccount], snapshots: List[TrialBalanceSnapshot], period_start: date, period_end: date) -> List[AuditException]:
    snapshots_by_account = {s.ledger_account_id: s for s in snapshots if s.entity_id == entity.id and s.period_start == period_start}
    
    total_creditor_balance = Decimal("0.00")
    has_tds_payable = False
    
    for account in accounts:
        snapshot = snapshots_by_account.get(account.id)
        if not snapshot:
            continue
            
        if account.group_name == "Sundry Creditors" and snapshot.closing_balance < 0:
            total_creditor_balance += abs(snapshot.closing_balance)
            
        if "tds" in account.name.lower() and account.group_name in {"Duties & Taxes", "Current Liabilities"} and snapshot.closing_balance < 0:
            has_tds_payable = True

    if total_creditor_balance >= entity.materiality_threshold and not has_tds_payable:
        exception = AuditException(
            entity_id=entity.id, 
            period_start=period_start, 
            period_end=period_end, 
            rule_name="tds_liability_check", 
            severity="warning", 
            message=f"Total Sundry Creditors balance is {total_creditor_balance:.2f} (exceeds materiality), but no TDS liability account with a payable balance was found. Verify if TDS is applicable and has been deducted."
        )
        exception.variance = total_creditor_balance
        return [exception]
        
    return []


def filter_by_materiality(entity: Entity, exceptions: List[AuditException]) -> List[AuditException]:
    return [exception for exception in exceptions if exception.severity == "critical" or not getattr(exception, "apply_materiality", False) or getattr(exception, "variance", Decimal("0.00")) >= entity.materiality_threshold]


def run_scrutiny(entity: Entity, accounts: List[LedgerAccount], snapshots: List[TrialBalanceSnapshot], period_start: date, period_end: date, rule_pack: str | None = None) -> List[AuditException]:
    exceptions = (
        check_normal_balance(entity, accounts, snapshots, period_start, period_end)
        + current_account_credit_balance(entity, accounts, snapshots, period_start, period_end)
        + creditor_debit_balance(entity, accounts, snapshots, period_start, period_end)
        + debtor_credit_balance(entity, accounts, snapshots, period_start, period_end)
        + tds_liability_check(entity, accounts, snapshots, period_start, period_end)
        + check_opening_balance_continuity(entity, accounts, snapshots, period_start, period_end)
        + trial_balance_balances(entity, accounts, snapshots, period_start, period_end)
        + negative_cash_balance(entity, accounts, snapshots, period_start, period_end)
        + suspense_account_nonzero(entity, accounts, snapshots, period_start, period_end)
    )
    if rule_pack == "manufacturing_v1":
        exceptions += manufacturing_low_inventory_movement(entity, accounts, snapshots, period_start, period_end)
        exceptions += manufacturing_gross_margin_shift(entity, accounts, snapshots, period_start, period_end)
    return filter_by_materiality(entity, exceptions)
