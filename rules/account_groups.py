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
}


def get_normal_balance(group_name: str) -> str:
    """
    Get the expected normal balance side ('debit' or 'credit') for a given ledger group name.
    
    Args:
        group_name: The name of the ledger account group.
        
    Returns:
        The normal balance side: 'debit' or 'credit'.
        
    Raises:
        UnrecognizedAccountGroupError: If the group_name is not in the recognized mapping.
    """
    if group_name not in TALLY_GROUPS_NORMAL_BALANCES:
        raise UnrecognizedAccountGroupError(
            f"Unrecognized ledger account group: '{group_name}'. "
            f"Allowed groups are: {', '.join(sorted(TALLY_GROUPS_NORMAL_BALANCES.keys()))}"
        )
    return TALLY_GROUPS_NORMAL_BALANCES[group_name]
