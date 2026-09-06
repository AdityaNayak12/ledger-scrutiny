from datetime import date
from decimal import Decimal
from typing import List

from app.db.models import AuditException, Entity, LedgerAccount, TrialBalanceSnapshot


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


def run_compliance_checks(
    entity: Entity,
    accounts: list[LedgerAccount],
    snapshots: list[TrialBalanceSnapshot],
    period_start: date,
    period_end: date,
) -> list[AuditException]:
    return tds_liability_check(entity, accounts, snapshots, period_start, period_end)
