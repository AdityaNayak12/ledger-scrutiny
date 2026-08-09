class UnrecognizedAccountGroupError(ValueError):
    """Raised when an unrecognized account group name is encountered."""
    pass


# Mapping of Tally's default chart of accounts group names to their expected normal balance side
TALLY_GROUPS_NORMAL_BALANCES = {
    "Capital Account": "credit",
    "Fixed Assets": "debit",
    "Sundry Debtors": "debit",
    "Sundry Creditors": "credit",
    "Sales Accounts": "credit",
    "Purchase Accounts": "debit",
    "Direct Expenses": "debit",
    "Indirect Expenses": "debit",
    "Direct Income": "credit",
    "Indirect Income": "credit",
    "Current Assets": "debit",
    "Current Liabilities": "credit",
    "Bank Accounts": "debit",
    "Cash-in-hand": "debit",
    "Loans (Liability)": "credit",
    "Duties & Taxes": "credit",
    "Provisions": "credit",
    "Investments": "debit",
    "Stock-in-hand": "debit",
    "Suspense Account": "any",
}


GROUP_KEYWORD_MAPPINGS = [
    ("CAPITAL", "Capital Account"),
    ("EQUITY", "Capital Account"),
    ("RESERVE", "Capital Account"),
    ("EARNINGS", "Capital Account"),
    ("SHARE", "Capital Account"),
    ("FIXED ASSET", "Fixed Assets"),
    ("ASSET", "Current Assets"),
    ("DEBTOR", "Sundry Debtors"),
    ("RECEIVABLE", "Sundry Debtors"),
    ("CREDITOR", "Sundry Creditors"),
    ("PAYABLE", "Sundry Creditors"),
    ("BANK", "Bank Accounts"),
    ("CASH", "Cash-in-hand"),
    ("SALES", "Sales Accounts"),
    ("REVENUE", "Sales Accounts"),
    ("INCOME", "Indirect Income"),
    ("TURNOVER", "Sales Accounts"),
    ("PURCHASE", "Purchase Accounts"),
    ("EXPENSE", "Indirect Expenses"),
    ("RENT", "Indirect Expenses"),
    ("SALARY", "Indirect Expenses"),
    ("LOAN", "Loans (Liability)"),
    ("BORROWING", "Loans (Liability)"),
    ("LIABILITY", "Current Liabilities"),
    ("PROVISION", "Provisions"),
    ("DUTY", "Duties & Taxes"),
    ("TAX", "Duties & Taxes"),
    ("INVESTMENT", "Investments"),
    ("STOCK", "Stock-in-hand"),
]


def get_normal_balance(group_name: str) -> str:
    """
    Get the expected normal balance side ('debit' or 'credit') for a given ledger group name.
    Supports exact Tally groups, case-insensitive matches, keyword inference, and numerical account codes.
    """
    clean_group = str(group_name).strip()
    if not clean_group:
        raise UnrecognizedAccountGroupError("Account group cannot be blank or empty.")

    if clean_group in TALLY_GROUPS_NORMAL_BALANCES:
        return TALLY_GROUPS_NORMAL_BALANCES[clean_group]

    for k, v in TALLY_GROUPS_NORMAL_BALANCES.items():
        if k.lower() == clean_group.lower():
            return v

    group_upper = clean_group.upper()
    for kw, target_group in GROUP_KEYWORD_MAPPINGS:
        if kw in group_upper:
            return TALLY_GROUPS_NORMAL_BALANCES[target_group]

    # Handle numerical account codes (e.g., '1010010101') gracefully
    if clean_group.replace(" ", "").replace("-", "").replace("_", "").isdigit():
        return "any"

    raise UnrecognizedAccountGroupError(
        f"Unrecognized ledger account group: '{group_name}'. "
        f"Allowed groups are: {', '.join(sorted(TALLY_GROUPS_NORMAL_BALANCES.keys()))}"
    )
