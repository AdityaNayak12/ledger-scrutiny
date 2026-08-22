from decimal import Decimal
from typing import Dict, Any, List, Optional
from datetime import date
from sqlalchemy.orm import Session
from sqlalchemy import select, delete

from app.db.models import FinancialPeriod, Organization, Entity, LedgerAccount, Transaction, TrialBalanceSnapshot
from app.rules.account_groups import get_normal_balance


def decompose_entries(debits: List[Dict[str, Any]], credits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Decomposes arbitrary debit and credit entries of a single voucher into simple 
    pairwise transactions (1 debit, 1 credit).
    Both lists are modified in-place during matching.
    """
    debit_list = [{"ledger_name": d["ledger_name"], "amount": d["amount"]} for d in debits if d["amount"] > 0]
    credit_list = [{"ledger_name": c["ledger_name"], "amount": c["amount"]} for c in credits if c["amount"] > 0]
    
    pairs = []
    d_idx, c_idx = 0, 0
    
    while d_idx < len(debit_list) and c_idx < len(credit_list):
        d = debit_list[d_idx]
        c = credit_list[c_idx]
        
        match_amount = min(d["amount"], c["amount"])
        if match_amount <= 0:
            break
            
        pairs.append({
            "debit_ledger": d["ledger_name"],
            "credit_ledger": c["ledger_name"],
            "amount": match_amount
        })
        
        d["amount"] -= match_amount
        c["amount"] -= match_amount
        
        if d["amount"] <= Decimal("0.0001"):
            d_idx += 1
        if c["amount"] <= Decimal("0.0001"):
            c_idx += 1
            
    return pairs


def normalize_tally_data(
    parsed_data: Dict[str, Any],
    session: Session,
    materiality_threshold: Decimal = Decimal("0.00"),
    entity_id: Optional[int] = None,
    organization_id: Optional[int] = None,
    clear_only_period: bool = False,
    target_period_start: Optional[Any] = None,
    target_period_end: Optional[Any] = None,
    import_batch_id: Optional[int] = None,
) -> Entity:
    """
    Normalizes parsed Tally XML data and writes it to the database.
    """
    ent_data = parsed_data["entity"]
    entity_name = ent_data["name"]
    fy_start = ent_data["financial_year_start"]
    fy_end = ent_data["financial_year_end"]
    
    if entity_id:
        entity = session.execute(select(Entity).where(Entity.id == entity_id)).scalar_one_or_none()
        if not entity:
            raise ValueError(f"Entity with ID {entity_id} not found.")
    else:
        entity = session.execute(
            select(Entity).where(Entity.name == entity_name)
        ).scalar_one_or_none()
        
        if not entity:
            if not organization_id:
                org = session.execute(select(Organization)).scalars().first()
                if not org:
                    org = Organization(name="Default Organization")
                    session.add(org)
                    session.flush()
                organization_id = org.id
            entity = Entity(
                organization_id=organization_id,
                name=entity_name,
                materiality_threshold=materiality_threshold
            )
            session.add(entity)
            session.flush()
        else:
            entity.materiality_threshold = materiality_threshold
            session.flush()
        
    t_start = date.fromisoformat(target_period_start) if isinstance(target_period_start, str) else target_period_start
    t_end = date.fromisoformat(target_period_end) if isinstance(target_period_end, str) else target_period_end

    p_start = t_start if clear_only_period and t_start else fy_start
    p_end = t_end if clear_only_period and t_end else fy_end
    
    if clear_only_period and (fy_start != p_start or fy_end != p_end):
        raise ValueError(
            f"Uploaded XML period ({fy_start} to {fy_end}) does not match "
            f"the currently selected period ({p_start} to {p_end}) for Re-upload."
        )

    fp = session.execute(select(FinancialPeriod).where(
        FinancialPeriod.entity_id == entity.id,
        FinancialPeriod.period_start == p_start,
        FinancialPeriod.period_end == p_end,
    ).order_by(FinancialPeriod.id.desc())).scalars().first()
    if fp is None:
        fp = FinancialPeriod(entity_id=entity.id, period_start=p_start, period_end=p_end, source="tally_xml")
        session.add(fp)

    # Legacy/direct callers retain replacement semantics. API uploads always
    # provide a batch and therefore preserve historical normalized records.
    if import_batch_id is None:
        session.execute(delete(TrialBalanceSnapshot).where(
            TrialBalanceSnapshot.entity_id == entity.id,
            TrialBalanceSnapshot.period_start == p_start,
            TrialBalanceSnapshot.period_end == p_end,
        ))
        session.execute(delete(Transaction).where(
            Transaction.entity_id == entity.id,
            Transaction.date >= p_start,
            Transaction.date <= p_end,
        ))

    ledger_map: Dict[str, LedgerAccount] = {}
    existing_ledgers = session.execute(
        select(LedgerAccount).where(LedgerAccount.entity_id == entity.id)
    ).scalars().all()
    
    for l in existing_ledgers:
        ledger_map[l.name] = l

    for ld in parsed_data["ledgers"]:
        lname = ld["name"]
        group_name = ld["group_name"]
        normal_bal = get_normal_balance(group_name)

        if lname not in ledger_map:
            l_account = LedgerAccount(
                entity_id=entity.id,
                name=lname,
                group_name=group_name,
                normal_balance=normal_bal
            )
            session.add(l_account)
            session.flush()
            ledger_map[lname] = l_account

    for ld in parsed_data["ledgers"]:
        lname = ld["name"]
        l_account = ledger_map[lname]

        op_bal = ld["opening_balance"]
        v_debits = Decimal("0.00")
        v_credits = Decimal("0.00")

        for v in parsed_data["vouchers"]:
            if not (p_start <= v["date"] <= p_end):
                continue
            for entry in v.get("entries", []):
                if entry["ledger_name"] == lname:
                    if entry["type"] == "debit":
                        v_debits += entry["amount"]
                    elif entry["type"] == "credit":
                        v_credits += entry["amount"]

        if ld.get("closing_balance") is not None:
            cl_bal = ld["closing_balance"]
        else:
            cl_bal = op_bal + v_debits - v_credits

        snapshot = TrialBalanceSnapshot(
            import_batch_id=import_batch_id,
            entity_id=entity.id,
            ledger_account_id=l_account.id,
            period_start=p_start,
            period_end=p_end,
            opening_balance=op_bal,
            total_debits=v_debits,
            total_credits=v_credits,
            closing_balance=cl_bal
        )
        session.add(snapshot)

    for v in parsed_data["vouchers"]:
        if not (p_start <= v["date"] <= p_end):
            continue
        debits = [e for e in v.get("entries", []) if e["type"] == "debit"]
        credits = [e for e in v.get("entries", []) if e["type"] == "credit"]
        pairs = decompose_entries(debits, credits)
        vch_num = v.get("source_voucher_id") or v.get("voucher_number")
        for p in pairs:
            deb_acc = ledger_map.get(p["debit_ledger"])
            cred_acc = ledger_map.get(p["credit_ledger"])

            if not deb_acc or not cred_acc:
                raise ValueError(
                    f"Voucher {vch_num} references unmapped ledger account: "
                    f"debit='{p['debit_ledger']}', credit='{p['credit_ledger']}'."
                )

            txn = Transaction(
                import_batch_id=import_batch_id,
                entity_id=entity.id,
                date=v["date"],
                debit_account_id=deb_acc.id,
                credit_account_id=cred_acc.id,
                amount=p["amount"],
                narration=v.get("narration"),
                voucher_type=v["voucher_type"],
                source_voucher_id=vch_num
            )
            session.add(txn)

    session.flush()
    return entity
