import io
import difflib
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, List, Optional
import openpyxl

# Alias lists per field
ALIAS_MAP: Dict[str, List[str]] = {
    "ledger_name": ["ledger", "ledger name", "account", "account name", "particulars", "particular", "name"],
    "group_name": ["group", "parent", "group name", "account type", "category", "parent group", "grp"],
    "opening_balance": ["opening balance", "opening", "op balance", "ob", "op. bal", "opening bal", "op bal"],
    "closing_balance": ["closing balance", "closing", "cl balance", "cb", "cl. bal", "closing bal", "cl bal"],
    "opening_debit": ["opening debit", "op debit", "ob dr", "opening dr", "op dr"],
    "opening_credit": ["opening credit", "op credit", "ob cr", "opening cr", "op cr"],
    "closing_debit": ["closing debit", "cl debit", "cb dr", "closing dr", "cl dr", "debit", "dr"],
    "closing_credit": ["closing credit", "cl credit", "cb cr", "closing cr", "cl cr", "credit", "cr"],
}

MIN_CONFIDENCE_THRESHOLD = 0.60


def score_match(cell_value: str, field_name: str) -> float:
    """
    Computes match confidence score (0.0 to 1.0) between cell_value and field's alias list.
    """
    if not cell_value:
        return 0.0
    val_clean = cell_value.strip().lower()
    if not val_clean:
        return 0.0

    aliases = ALIAS_MAP.get(field_name, [])
    best_score = 0.0

    for alias in aliases:
        alias_clean = alias.strip().lower()
        if val_clean == alias_clean:
            return 1.0
        
        # Substring exact match
        if alias_clean in val_clean or val_clean in alias_clean:
            score = len(alias_clean) / max(len(val_clean), len(alias_clean))
            score = max(score, 0.85)
            if score > best_score:
                best_score = score

        # SequenceMatcher fuzzy similarity
        seq_score = difflib.SequenceMatcher(None, val_clean, alias_clean).ratio()
        if seq_score > best_score:
            best_score = seq_score

    return best_score if best_score >= MIN_CONFIDENCE_THRESHOLD else 0.0


def detect_headers_and_parse(file_bytes: bytes) -> Dict[str, Any]:
    """
    Scans the first 15 rows of the Excel sheet to detect header row and column mappings.
    Returns preview data including matched columns, confidence scores, missing fields, sample rows, and parse errors.
    Does NOT write to database.
    """
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

    scan_limit = min(15, len(rows_data))
    
    best_row_idx = -1
    best_match_count = 0
    best_row_score = 0.0
    best_column_mapping: Dict[str, Any] = {}

    for r_idx in range(scan_limit):
        row_vals = rows_data[r_idx]
        if not row_vals or all(v is None or str(v).strip() == "" for v in row_vals):
            continue

        matched_fields: Dict[str, Any] = {}

        for col_idx, cell_val in enumerate(row_vals):
            if cell_val is None:
                continue
            cell_str = str(cell_val).strip()
            if not cell_str:
                continue

            for field_name in ALIAS_MAP.keys():
                score = score_match(cell_str, field_name)
                if score > 0:
                    if field_name not in matched_fields or score > matched_fields[field_name]["confidence"]:
                        matched_fields[field_name] = {
                            "column": cell_str,
                            "col_idx": col_idx,
                            "confidence": round(score, 2)
                        }

        count = len(matched_fields)
        total_score = sum(m["confidence"] for m in matched_fields.values())

        if count > best_match_count or (count == best_match_count and total_score > best_row_score):
            best_match_count = count
            best_row_score = total_score
            best_row_idx = r_idx
            best_column_mapping = matched_fields

    if best_row_idx == -1 or best_match_count < 2:
        header_row_vals = rows_data[best_row_idx] if best_row_idx != -1 else []
        return {
            "header_row_number": best_row_idx + 1 if best_row_idx != -1 else None,
            "detected_headers": [str(v).strip() for v in header_row_vals if v is not None],
            "column_mapping": {},
            "missing_fields": ["ledger_name", "group_name", "opening_balance", "closing_balance"],
            "sample_rows": [],
            "parse_errors": [{"row_number": 0, "error": "Could not confidently identify header row in first 15 rows."}],
            "total_data_rows": 0
        }

    header_row_number = best_row_idx + 1
    
    # Identify missing core fields
    core_fields = ["ledger_name", "group_name", "opening_balance", "closing_balance"]
    missing_fields = []
    for cf in core_fields:
        if cf not in best_column_mapping:
            if cf == "opening_balance" and ("opening_debit" in best_column_mapping or "opening_credit" in best_column_mapping):
                continue
            if cf == "closing_balance" and ("closing_debit" in best_column_mapping or "closing_credit" in best_column_mapping):
                continue
            missing_fields.append(cf)

    # Read data rows below header row down to first fully blank row
    data_rows: List[Dict[str, Any]] = []
    parse_errors: List[Dict[str, Any]] = []
    
    row_offset = best_row_idx + 1
    for r_idx in range(row_offset, len(rows_data)):
        row_vals = rows_data[r_idx]
        actual_row_num = r_idx + 1

        if not row_vals or all(v is None or str(v).strip() == "" for v in row_vals):
            break

        row_dict: Dict[str, Any] = {}

        for field_name, mapping_info in best_column_mapping.items():
            c_idx = mapping_info["col_idx"]
            val = row_vals[c_idx] if c_idx < len(row_vals) else None
            row_dict[field_name] = str(val).strip() if val is not None else ""

        # Skip summary/subtotal rows
        ledger_val = row_dict.get("ledger_name", "").strip()
        group_val = row_dict.get("group_name", "").strip()
        ledger_upper = ledger_val.upper()
        group_upper = group_val.upper()
        if (
            not ledger_val
            or ledger_upper.endswith("TOTAL")
            or ledger_upper.endswith("SUBTOTAL")
            or ledger_upper == "GRAND TOTAL"
            or group_upper.endswith("TOTAL")
            or group_upper.endswith("SUBTOTAL")
            or group_upper == "GRAND TOTAL"
        ):
            continue

        data_rows.append({
            "row_number": actual_row_num,
            "raw_data": row_dict
        })

    header_row_vals = rows_data[best_row_idx] if best_row_idx != -1 else []
    detected_headers = [str(v).strip() for v in header_row_vals if v is not None and str(v).strip() != ""]

    return {
        "header_row_number": header_row_number,
        "detected_headers": detected_headers,
        "column_mapping": best_column_mapping,
        "missing_fields": missing_fields,
        "sample_rows": data_rows[:5],
        "parse_errors": parse_errors,
        "total_data_rows": len(data_rows)
    }
