
export interface Entity {
  id: number;
  name: string;
  financial_year_start: string;
  financial_year_end: string;
  materiality_threshold: number;
  has_uploaded: boolean;
  scrutinized: boolean;
}

export interface Exception {
  id: number;
  rule_name: string;
  severity: "critical" | "warning" | "info";
  message: string;
  ledger_account_name: string | null;
  created_at: string;
}

export const INITIAL_MOCK_ENTITIES: Entity[] = [
  {
    id: 1,
    name: "Acme Audited Corp",
    financial_year_start: "2025-04-01",
    financial_year_end: "2026-03-31",
    materiality_threshold: 15000,
    has_uploaded: true,
    scrutinized: true,
  },
  {
    id: 2,
    name: "Rahul Enterprises",
    financial_year_start: "2025-04-01",
    financial_year_end: "2026-03-31",
    materiality_threshold: 5000,
    has_uploaded: false,
    scrutinized: false,
  },
  {
    id: 3,
    name: "Verma Traders",
    financial_year_start: "2024-04-01",
    financial_year_end: "2025-03-31",
    materiality_threshold: 20000,
    has_uploaded: true,
    scrutinized: false,
  }
];

export const MOCK_EXCEPTIONS: Record<number, Exception[]> = {
  1: [
    {
      id: 101,
      rule_name: "normal_balance_check",
      severity: "critical",
      message: "Account 'HDFC Bank' has normal balance 'debit' but has a credit closing balance of 45,000.00 (Unapproved Overdraft).",
      ledger_account_name: "HDFC Bank",
      created_at: new Date().toISOString(),
    },
    {
      id: 102,
      rule_name: "opening_balance_continuity",
      severity: "warning",
      message: "Account 'Furniture and Fixtures' opening balance (1,50,000.00) does not match prior period closing balance (1,80,000.00). Continuity variance: 30,000.00.",
      ledger_account_name: "Furniture and Fixtures",
      created_at: new Date().toISOString(),
    },
    {
      id: 103,
      rule_name: "normal_balance_check",
      severity: "warning",
      message: "Account 'Office Rent' has normal balance 'debit' but has a credit closing balance of 18,000.00 (Possible wrong accounting entry or prepayment code).",
      ledger_account_name: "Office Rent",
      created_at: new Date().toISOString(),
    },
    {
      id: 104,
      rule_name: "normal_balance_check",
      severity: "info",
      message: "Account 'Share Capital' has normal balance 'credit' but has a debit closing balance of 2,000.00 (Pending allotment call money).",
      ledger_account_name: "Share Capital",
      created_at: new Date().toISOString(),
    }
  ],
  2: [],
  3: [
    {
      id: 301,
      rule_name: "normal_balance_check",
      severity: "critical",
      message: "Account 'Verma Traders' has normal balance 'credit' but has a debit closing balance of 25,000.00 (Debit variance).",
      ledger_account_name: "Verma Traders",
      created_at: new Date().toISOString(),
    }
  ]
};
