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


def get_normal_balance(group_name: str) -> str:
    """
    Get the expected normal balance side ('debit' or 'credit') for a given ledger group name.
    Supports exact Tally groups, case-insensitively.
    """
    clean_group = str(group_name).strip()
    if not clean_group:
        raise UnrecognizedAccountGroupError("Account group cannot be blank or empty.")

    if clean_group in TALLY_GROUPS_NORMAL_BALANCES:
        return TALLY_GROUPS_NORMAL_BALANCES[clean_group]

    for k, v in TALLY_GROUPS_NORMAL_BALANCES.items():
        if k.lower() == clean_group.lower():
            return v

    raise UnrecognizedAccountGroupError(
        f"Unrecognized ledger account group: '{group_name}'. "
        f"Allowed groups are: {', '.join(sorted(TALLY_GROUPS_NORMAL_BALANCES.keys()))}"
    )
