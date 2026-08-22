from datetime import date
from decimal import Decimal
from typing import List

from app.db.models import AuditException, Entity, LedgerAccount, TrialBalanceSnapshot

RULE_SET_VERSION = "1"


def check_normal_balance(entity: Entity, accounts: List[LedgerAccount], snapshots: List[TrialBalanceSnapshot], period_start: date, period_end: date) -> List[AuditException]:
    snapshots_by_account = {s.ledger_account_id: s for s in snapshots if s.entity_id == entity.id and s.period_start == period_start}
    exceptions = []
    for account in accounts:
        snapshot = snapshots_by_account.get(account.id)
        if not snapshot or account.normal_balance.lower() == "any":
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


def filter_by_materiality(entity: Entity, exceptions: List[AuditException]) -> List[AuditException]:
    return [exception for exception in exceptions if exception.severity == "critical" or not getattr(exception, "apply_materiality", False) or getattr(exception, "variance", Decimal("0.00")) >= entity.materiality_threshold]


def run_scrutiny(entity: Entity, accounts: List[LedgerAccount], snapshots: List[TrialBalanceSnapshot], period_start: date, period_end: date) -> List[AuditException]:
    exceptions = (
        check_normal_balance(entity, accounts, snapshots, period_start, period_end)
        + check_opening_balance_continuity(entity, accounts, snapshots, period_start, period_end)
        + trial_balance_balances(entity, accounts, snapshots, period_start, period_end)
        + negative_cash_balance(entity, accounts, snapshots, period_start, period_end)
        + suspense_account_nonzero(entity, accounts, snapshots, period_start, period_end)
    )
    return filter_by_materiality(entity, exceptions)
