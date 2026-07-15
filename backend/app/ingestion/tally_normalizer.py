from decimal import Decimal
from typing import Dict, Any, List
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.db.models import Entity, LedgerAccount, Transaction, TrialBalanceSnapshot
from app.rules.account_groups import get_normal_balance


def decompose_entries(debits: List[Dict[str, Any]], credits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Decomposes arbitrary debit and credit entries of a single voucher into simple 
    pairwise transactions (1 debit, 1 credit).
    Both lists are modified in-place during matching.
    """
    # Create working copies of amounts to not mutate the original parsed data structure
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
        
        if d["amount"] <= Decimal("0.00"):
            d_idx += 1
        if c["amount"] <= Decimal("0.00"):
            c_idx += 1
            
    return pairs


def normalize_tally_data(
    parsed_data: Dict[str, Any], 
    session: Session, 
    materiality_threshold: Decimal = Decimal("0.00")
) -> Entity:
    """
    Normalizes parsed Tally XML data and writes it to the database.
    
    Args:
        parsed_data: Output dictionary from tally_parser.parse_tally_xml.
        session: SQLAlchemy DB session.
        materiality_threshold: Materiality threshold for the entity.
        
    Returns:
        The created/retrieved Entity model object.
    """
    # 1. Handle Entity
    ent_data = parsed_data["entity"]
    entity_name = ent_data["name"]
    fy_start = ent_data["financial_year_start"]
    fy_end = ent_data["financial_year_end"]
    
    # Check if entity already exists in DB
    entity = session.execute(
        select(Entity).where(
            Entity.name == entity_name,
            Entity.financial_year_start == fy_start,
            Entity.financial_year_end == fy_end
        )
    ).scalar_one_or_none()
    
    if not entity:
        entity = Entity(
            name=entity_name,
            financial_year_start=fy_start,
            financial_year_end=fy_end,
            materiality_threshold=materiality_threshold
        )
        session.add(entity)
        session.flush() # Populate entity.id
    else:
        # Update materiality threshold if provided
        entity.materiality_threshold = materiality_threshold
        session.flush()
        
    # 2. Handle Ledger Accounts
    # Keep a cache of {ledger_name: ledger_id} for mapping transactions
    ledger_cache: Dict[str, int] = {}
    
    # Pre-load existing accounts for this entity to prevent duplicate inserts
    existing_accounts = session.execute(
        select(LedgerAccount).where(LedgerAccount.entity_id == entity.id)
    ).scalars().all()
    for acc in existing_accounts:
        ledger_cache[acc.name] = acc.id
        
    for ld in parsed_data["ledgers"]:
        lname = ld["name"]
        gname = ld["group_name"]
        
        # Get normal balance or raise UnrecognizedAccountGroupError
        normal_bal = get_normal_balance(gname)
        
        if lname not in ledger_cache:
            new_acc = LedgerAccount(
                entity_id=entity.id,
                name=lname,
                group_name=gname,
                normal_balance=normal_bal
            )
            session.add(new_acc)
            session.flush()
            ledger_cache[lname] = new_acc.id
            
    # Helper to retrieve ledger ID, raising error if not found in cache (should not happen if all are listed)
    def get_ledger_id(name: str) -> int:
        if name not in ledger_cache:
            raise ValueError(f"Voucher references untracked ledger account: '{name}'")
        return ledger_cache[name]

    # 3. Handle Transactions
    # Keep track of transaction aggregates for TrialBalanceSnapshots
    # {ledger_id: {"debits": Decimal, "credits": Decimal}}
    tb_aggregates: Dict[int, Dict[str, Decimal]] = {
        lid: {"debits": Decimal("0.00"), "credits": Decimal("0.00")} for lid in ledger_cache.values()
    }
    
    for vch in parsed_data["vouchers"]:
        vdate = vch["date"]
        vtype = vch["voucher_type"]
        vnum = vch["source_voucher_id"]
        vnarr = vch["narration"]
        
        # Separate entries into debits and credits
        debits = [e for e in vch["entries"] if e["type"] == "debit"]
        credits = [e for e in vch["entries"] if e["type"] == "credit"]
        
        # Decompose split vouchers into pairwise transactions
        pairs = decompose_entries(debits, credits)
        
        for pair in pairs:
            debit_id = get_ledger_id(pair["debit_ledger"])
            credit_id = get_ledger_id(pair["credit_ledger"])
            amt = pair["amount"]
            
            # Record simple transaction
            txn = Transaction(
                entity_id=entity.id,
                date=vdate,
                debit_account_id=debit_id,
                credit_account_id=credit_id,
                amount=amt,
                narration=vnarr,
                voucher_type=vtype,
                source_voucher_id=vnum
            )
            session.add(txn)
            
            # Aggregate for snapshot calculation
            tb_aggregates[debit_id]["debits"] += amt
            tb_aggregates[credit_id]["credits"] += amt
            
    session.flush()

    # 4. Generate Trial Balance Snapshots
    for ld in parsed_data["ledgers"]:
        lname = ld["name"]
        lid = ledger_cache[lname]
        op_bal = ld["opening_balance"] # Positive for Debit, negative for Credit
        
        debits_sum = tb_aggregates[lid]["debits"]
        credits_sum = tb_aggregates[lid]["credits"]
        
        # closing_balance = opening_balance + total_debits - total_credits
        cl_bal = op_bal + debits_sum - credits_sum
        
        snapshot = TrialBalanceSnapshot(
            entity_id=entity.id,
            ledger_account_id=lid,
            period_start=fy_start,
            period_end=fy_end,
            opening_balance=op_bal,
            total_debits=debits_sum,
            total_credits=credits_sum,
            closing_balance=cl_bal
        )
        session.add(snapshot)
        
    session.flush()
    return entity
