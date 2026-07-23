from datetime import date, datetime
from decimal import Decimal
from typing import Dict, Any, List
from lxml import etree


def parse_tally_date(date_str: str) -> date:
    """Parses Tally date string format (YYYYMMDD or YYYY-MM-DD) into date object."""
    if not date_str:
        raise ValueError("Empty date string")
    date_str = date_str.strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unable to parse Tally date: '{date_str}'")


def parse_tally_amount(amount_str: str) -> Decimal:
    """
    Parses Tally amount string into Decimal.
    Handles Dr/Cr suffixes and sign conventions.
    In Tally ledger balances:
    - Positive values or 'Dr' suffix indicate Debit (positive).
    - Negative values or 'Cr' suffix indicate Credit (negative).
    We normalize this to: Positive for Debit, Negative for Credit.
    """
    if not amount_str:
        return Decimal("0.00")
    
    clean_str = amount_str.strip()
    is_debit = False
    has_suffix = False
    
    if clean_str.upper().endswith("DR"):
        is_debit = True
        has_suffix = True
        clean_str = clean_str[:-2].strip()
    elif clean_str.upper().endswith("CR"):
        is_debit = False
        has_suffix = True
        clean_str = clean_str[:-2].strip()
    
    try:
        val = Decimal(clean_str)
        if not has_suffix:
            # No suffix: positive is Debit, negative is Credit
            return val
        else:
            # Suffix present: force sign based on suffix
            abs_val = abs(val)
            return abs_val if is_debit else -abs_val
    except Exception as e:
        raise ValueError(f"Invalid Tally amount format '{amount_str}': {e}")


def parse_tally_xml(xml_content: bytes) -> Dict[str, Any]:
    """
    Parses Tally XML export and extracts structured entity, ledger, and transaction data.
    
    Returns a dictionary of the shape:
    {
        "entity": { 
            "name": str,
            "financial_year_start": date,
            "financial_year_end": date
        },
        "ledgers": [
            {"name": str, "group_name": str, "opening_balance": Decimal},
            ...
        ],
        "vouchers": [
            {
                "date": date,
                "voucher_type": str,
                "source_voucher_id": str,
                "narration": str,
                "entries": [
                    {"ledger_name": str, "type": str, "amount": Decimal}, # type is 'debit' or 'credit'
                    ...
                ]
            },
            ...
        ]
    }
    """
    parser = etree.XMLParser(recover=True, remove_blank_text=True)
    root = etree.fromstring(xml_content, parser=parser)
    
    # 1. Parse Company Details
    company_node = root.find(".//COMPANY")
    entity_name = None
    fy_start = None
    fy_end = None
    
    if company_node is not None:
        rename_node = company_node.find("RENAME")
        if rename_node is not None and rename_node.text:
            entity_name = rename_node.text.strip()
            
        books_from_node = company_node.find("BOOKSFROM")
        if books_from_node is not None and books_from_node.text:
            fy_start = parse_tally_date(books_from_node.text)
            
        books_to_node = company_node.find("BOOKSTO")
        if books_to_node is not None and books_to_node.text:
            fy_end = parse_tally_date(books_to_node.text)
    else:
        # Fallback to parsing from STATICVARIABLES in Trial Balance exports
        static_vars = root.find(".//STATICVARIABLES")
        if static_vars is not None:
            comp_name_node = static_vars.find("SVCOMPANYNAME")
            if comp_name_node is not None and comp_name_node.text:
                entity_name = comp_name_node.text.strip()
                
            from_date_node = static_vars.find("SVFROMDATE")
            if from_date_node is not None and from_date_node.text:
                try:
                    fy_start = parse_tally_date(from_date_node.text)
                except Exception:
                    pass
                    
            to_date_node = static_vars.find("SVTODATE")
            if to_date_node is not None and to_date_node.text:
                try:
                    fy_end = parse_tally_date(to_date_node.text)
                except Exception:
                    pass

    if not entity_name:
        raise ValueError("Could not resolve company name from XML export.")
    if not fy_start or not fy_end:
        raise ValueError("Could not resolve financial year start or end dates from XML export.")

    # Detect if Trial Balance export
    report_node = root.find(".//REPORTNAME")
    is_trial_balance = report_node is not None and report_node.text and "Trial Balance" in report_node.text

    # 2. Parse Ledgers
    ledgers = []
    # Tally ledgers are usually defined inside <LEDGER> tags under <TALLYMESSAGE>
    ledger_nodes = root.findall(".//LEDGER")
    for ledger in ledger_nodes:
        name = ledger.get("NAME")
        if not name:
            continue
            
        parent_node = ledger.find("PARENT")
        group_name = parent_node.text.strip() if parent_node is not None and parent_node.text else "Suspense Account"
        
        op_bal_node = ledger.find("OPENINGBALANCE")
        op_bal_str = op_bal_node.text if op_bal_node is not None else "0.00"
        opening_balance = parse_tally_amount(op_bal_str)
        
        cl_bal_node = ledger.find("CLOSINGBALANCE")
        closing_balance = None
        if cl_bal_node is not None and cl_bal_node.text:
            closing_balance = parse_tally_amount(cl_bal_node.text)
        elif is_trial_balance:
            raise ValueError(f"Ledger account '{name.strip()}' is missing CLOSINGBALANCE node or has empty value in Trial Balance export.")
        
        ledgers.append({
            "name": name.strip(),
            "group_name": group_name,
            "opening_balance": opening_balance,
            "closing_balance": closing_balance
        })
        
    # 3. Parse Vouchers
    vouchers = []
    voucher_nodes = root.findall(".//VOUCHER")
    for vch in voucher_nodes:
        vch_type = vch.get("VCHTYPE") or "Journal"
        
        date_node = vch.find("DATE")
        if date_node is not None and date_node.text:
            vch_date = parse_tally_date(date_node.text)
        else:
            vch_date = fy_start
            
        vch_num_node = vch.find("VOUCHERNUMBER")
        vch_num = vch_num_node.text.strip() if vch_num_node is not None and vch_num_node.text else None
        
        narration_node = vch.find("NARRATION")
        narration = narration_node.text.strip() if narration_node is not None and narration_node.text else None
        
        entries = []
        # Ledger entries inside voucher - support both standard and transactional tags
        entry_nodes = vch.findall("ALLLEDGERENTRIES.LIST") + vch.findall("LEDGERENTRIES.LIST")
        for entry in entry_nodes:
            ledger_name_node = entry.find("LEDGERNAME")
            if ledger_name_node is not None and ledger_name_node.text:
                ledger_name = ledger_name_node.text.strip()
            else:
                continue
                
            deemed_positive = entry.find("ISDEEMEDPOSITIVE")
            is_debit = deemed_positive is not None and deemed_positive.text.strip().lower() == "yes"
            
            amount_node = entry.find("AMOUNT")
            amount_str = amount_node.text if amount_node is not None else "0.00"
            # Voucher entry amounts are absolute values in our schema, but Tally stores them as signed decimals
            try:
                raw_amt = Decimal(amount_str.strip())
                entry_amount = abs(raw_amt)
            except Exception:
                entry_amount = Decimal("0.00")
                
            entries.append({
                "ledger_name": ledger_name,
                "type": "debit" if is_debit else "credit",
                "amount": entry_amount
            })
            
        if entries:
            # Validate that the voucher is balanced (debits == credits)
            debit_sum = sum(e["amount"] for e in entries if e["type"] == "debit")
            credit_sum = sum(e["amount"] for e in entries if e["type"] == "credit")
            if abs(debit_sum - credit_sum) > Decimal("0.01"):
                raise ValueError(
                    f"Voucher number '{vch_num or 'N/A'}' of type '{vch_type}' on date '{vch_date}' "
                    f"is unbalanced. Sum of debits (₹{debit_sum:.2f}) does not equal sum of credits (₹{credit_sum:.2f})."
                )
            vouchers.append({
                "date": vch_date,
                "voucher_type": vch_type,
                "source_voucher_id": vch_num,
                "narration": narration,
                "entries": entries
            })
            
    return {
        "entity": {
            "name": entity_name,
            "financial_year_start": fy_start,
            "financial_year_end": fy_end
        },
        "ledgers": ledgers,
        "vouchers": vouchers
    }
