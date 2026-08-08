import io
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select, delete
import openpyxl

from app.db.models import Entity, LedgerAccount, TrialBalanceSnapshot
from app.rules.account_groups import get_normal_balance, UnrecognizedAccountGroupError


def normalize_xlsx_confirm(
    file_bytes: bytes,
    column_mapping: Dict[str, str],
    sign_convention: str,
    target_period_start: str,
    target_period_end: str,
    entity_id: int,
    session: Session,
    clear_only_period: bool = True
) -> Entity:
    """
    Normalizes XLSX trial balance data using an explicit, user-approved column_mapping and sign_convention.
    Enforces fail-loud data validations for blank ledger names, unrecognized account groups, and non-numeric balances.
    Writes TrialBalanceSnapshot and LedgerAccount objects to database.
    """
    if sign_convention not in ("negative_is_credit", "positive_is_credit", "separate_dr_cr_columns"):
        raise ValueError(
            f"Invalid sign_convention '{sign_convention}'. "
            "Must be one of 'negative_is_credit', 'positive_is_credit', or 'separate_dr_cr_columns'."
        )

    entity = session.execute(select(Entity).where(Entity.id == entity_id)).scalar_one_or_none()
    if not entity:
        raise ValueError(f"Entity with ID {entity_id} not found.")

    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as e:
        raise ValueError(f"Failed to parse Excel file: {str(e)}")

    ws = wb.active
    if not ws:
        raise ValueError("Workbook has no active sheet.")

    rows_data = list(ws.iter_rows(values_only=True))
    if not rows_data:
        raise ValueError("Excel sheet is empty.")

    # Find the header row matching user's explicit column_mapping
    header_row_idx = -1
    col_index_map: Dict[str, int] = {}

    scan_limit = min(15, len(rows_data))
    for r_idx in range(scan_limit):
        row_vals = rows_data[r_idx]
        if not row_vals:
            continue

        clean_row = [str(v).strip() if v is not None else "" for v in row_vals]

        # Check how many mapped columns match this row
        temp_map: Dict[str, int] = {}
        for field_name, expected_header in column_mapping.items():
            exp_clean = str(expected_header).strip().lower()
            for c_idx, cell_str in enumerate(clean_row):
                if cell_str.lower() == exp_clean:
                    temp_map[field_name] = c_idx
                    break

        if len(temp_map) > len(col_index_map):
            col_index_map = temp_map
            header_row_idx = r_idx

    if header_row_idx == -1 or not col_index_map:
        raise ValueError(
            "Could not locate the specified header row matching the provided column_mapping in the first 15 rows."
        )

    # Validate essential fields exist in col_index_map
    if "ledger_name" not in col_index_map:
        raise ValueError("Provided column_mapping must contain a mapping for 'ledger_name'.")
    if "group_name" not in col_index_map:
        raise ValueError("Provided column_mapping must contain a mapping for 'group_name'.")

    p_start = date.fromisoformat(target_period_start) if isinstance(target_period_start, str) else target_period_start
    p_end = date.fromisoformat(target_period_end) if isinstance(target_period_end, str) else target_period_end

    # Clear existing snapshots for entity in target period
    session.execute(
        delete(TrialBalanceSnapshot).where(
            TrialBalanceSnapshot.entity_id == entity.id,
            TrialBalanceSnapshot.period_start == p_start,
            TrialBalanceSnapshot.period_end == p_end
        )
    )

    ledger_map: Dict[str, LedgerAccount] = {}
    existing_ledgers = session.execute(
        select(LedgerAccount).where(LedgerAccount.entity_id == entity.id)
    ).scalars().all()
    for l in existing_ledgers:
        ledger_map[l.name] = l

    # Parse and validate data rows
    row_offset = header_row_idx + 1
    for r_idx in range(row_offset, len(rows_data)):
        row_vals = rows_data[r_idx]
        actual_row_num = r_idx + 1

        if not row_vals or all(v is None or str(v).strip() == "" for v in row_vals):
            break

        # 1. Validate Ledger Name
        l_idx = col_index_map["ledger_name"]
        raw_ledger = row_vals[l_idx] if l_idx < len(row_vals) else None
        ledger_name = str(raw_ledger).strip() if raw_ledger is not None else ""
        if not ledger_name:
            raise ValueError(f"Row {actual_row_num}: Ledger account name cannot be blank or empty.")

        # 2. Validate Group Name & Normal Balance
        g_idx = col_index_map["group_name"]
        raw_group = row_vals[g_idx] if g_idx < len(row_vals) else None
        group_name = str(raw_group).strip() if raw_group is not None else ""
        if not group_name:
            raise ValueError(f"Row {actual_row_num}: Account group cannot be blank or empty for ledger '{ledger_name}'.")

        try:
            normal_bal = get_normal_balance(group_name)
        except UnrecognizedAccountGroupError as err:
            raise ValueError(f"Row {actual_row_num}: {str(err)}")

        # Helper to parse Decimal balance from row
        def parse_decimal_field(field_key: str) -> Decimal:
            if field_key not in col_index_map:
                return Decimal("0.00")
            col_idx = col_index_map[field_key]
            val = row_vals[col_idx] if col_idx < len(row_vals) else None
            if val is None or str(val).strip() == "":
                return Decimal("0.00")
            
            clean_str = str(val).replace(",", "").strip()
            try:
                return Decimal(clean_str)
            except (InvalidOperation, TypeError):
                raise ValueError(
                    f"Row {actual_row_num}: Non-numeric balance value '{val}' for field '{field_key}' in ledger '{ledger_name}'."
                )

        # 3. Calculate Opening & Closing Balances based on sign_convention
        if sign_convention == "separate_dr_cr_columns":
            op_dr = parse_decimal_field("opening_debit")
            op_cr = parse_decimal_field("opening_credit")
            op_bal = op_dr - op_cr

            cl_dr = parse_decimal_field("closing_debit")
            cl_cr = parse_decimal_field("closing_credit")
            cl_bal = cl_dr - cl_cr
        elif sign_convention == "positive_is_credit":
            raw_op = parse_decimal_field("opening_balance")
            raw_cl = parse_decimal_field("closing_balance")
            op_bal = -raw_op
            cl_bal = -raw_cl
        else:  # negative_is_credit
            op_bal = parse_decimal_field("opening_balance")
            cl_bal = parse_decimal_field("closing_balance")

        # Create or update LedgerAccount
        if ledger_name not in ledger_map:
            l_account = LedgerAccount(
                entity_id=entity.id,
                name=ledger_name,
                group_name=group_name,
                normal_balance=normal_bal
            )
            session.add(l_account)
            session.flush()
            ledger_map[ledger_name] = l_account
        else:
            l_account = ledger_map[ledger_name]

        # Insert TrialBalanceSnapshot
        snapshot = TrialBalanceSnapshot(
            entity_id=entity.id,
            ledger_account_id=l_account.id,
            period_start=p_start,
            period_end=p_end,
            opening_balance=op_bal,
            total_debits=Decimal("0.00"),
            total_credits=Decimal("0.00"),
            closing_balance=cl_bal
        )
        session.add(snapshot)

    session.flush()
    return entity
