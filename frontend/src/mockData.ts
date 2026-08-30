
export interface Entity {
  id: number;
  name: string;
  financial_year_start: string;
  financial_year_end: string;
  materiality_threshold: number;
  gstin?: string | null;
  sector?: string | null;
  rule_pack?: string | null;
  has_uploaded: boolean;
  scrutinized: boolean;
}

export interface Exception {
  id: number;
  rule_name: string;
  severity: string;
  message: string;
  ledger_account_name: string | null;
  status: "PENDING" | "REVIEWED" | "CLEARED" | "FLAGGED_FOR_FOLLOWUP" | string;
  auditor_notes: string | null;
  created_at: string;
}

export interface DemoPeriod {
  period_start: string;
  period_end: string;
  source: string;
}

export const DEMO_DATA_VERSION = "pitch-demo-v2";

export const INITIAL_MOCK_ENTITIES: Entity[] = [
  {
    id: 1,
    name: "Meridian Components Private Limited — Demo",
    financial_year_start: "2025-04-01",
    financial_year_end: "2026-03-31",
    materiality_threshold: 100000,
    gstin: "27DEMOX0000D1Z0",
    sector: "manufacturing",
    rule_pack: "manufacturing_v1",
    has_uploaded: true,
    scrutinized: true,
  },
];

export const MOCK_PERIODS: Record<number, DemoPeriod[]> = {
  1: [
    { period_start: "2025-04-01", period_end: "2026-03-31", source: "tally_xml" },
    { period_start: "2024-04-01", period_end: "2025-03-31", source: "tally_xml" },
  ],
};

const MOCK_EXCEPTIONS: Record<string, Exception[]> = {
  "1:2025-04-01": [
    {
      id: 101,
      rule_name: "opening_balance_continuity",
      severity: "error",
      message: "Plant & Machinery opens at ₹48,25,000, while the audited FY 2024-25 closing balance was ₹44,00,000. The unexplained continuity difference is ₹4,25,000.",
      ledger_account_name: "Plant & Machinery",
      status: "PENDING",
      auditor_notes: null,
      created_at: "2026-04-02T09:15:00.000Z",
    },
    {
      id: 102,
      rule_name: "manufacturing_low_inventory_movement",
      severity: "warning",
      message: "Material inventory moved by only ₹30,000 against COGS of ₹1,34,70,000. This is within the Manufacturing v1 firm-policy tolerance of 1% of COGS (₹1,34,700). Review inventory valuation and cut-off.",
      ledger_account_name: null,
      status: "PENDING",
      auditor_notes: null,
      created_at: "2026-04-02T09:15:01.000Z",
    },
    {
      id: 103,
      rule_name: "suspense_account_nonzero",
      severity: "error",
      message: "Suspense Account retains a material ₹2,75,000 closing balance. Unallocated entries should be identified and resolved before finalisation.",
      ledger_account_name: "Suspense Account",
      status: "REVIEWED",
      auditor_notes: "Management schedule requested; three journal entries are awaiting supporting invoices.",
      created_at: "2026-04-02T09:15:02.000Z",
    },
    {
      id: 104,
      rule_name: "normal_balance_check",
      severity: "error",
      message: "HDFC Current Account has a ₹6,80,000 credit closing balance although it is classified as a bank asset. Confirm whether this is an overdraft and reclassify if required.",
      ledger_account_name: "HDFC Current Account",
      status: "CLEARED",
      auditor_notes: "Sanction letter inspected. Balance relates to a secured overdraft and is included in borrowings for finalisation.",
      created_at: "2026-04-02T09:15:03.000Z",
    },
    {
      id: 105,
      rule_name: "manufacturing_gross_margin_shift",
      severity: "warning",
      message: "Gross margin moved from 27.5% in the prior period to 10.2% in the current period (17.3 percentage points). This exceeds the Manufacturing v1 firm-policy threshold of 10 percentage points.",
      ledger_account_name: null,
      status: "PENDING",
      auditor_notes: null,
      created_at: "2026-04-02T09:15:04.000Z",
    },
  ],
  "1:2024-04-01": [
    {
      id: 201,
      rule_name: "suspense_account_nonzero",
      severity: "error",
      message: "Suspense Account had a ₹1,40,000 closing balance at FY 2024-25 year end and required management allocation.",
      ledger_account_name: "Suspense Account",
      status: "CLEARED",
      auditor_notes: "Adjusted through journal voucher JV-948 after invoice verification.",
      created_at: "2025-04-03T11:30:00.000Z",
    },
  ],
};

export function getMockExceptions(entityId: number, periodStart?: string): Exception[] {
  const findings = MOCK_EXCEPTIONS[`${entityId}:${periodStart || ""}`] || [];
  return findings.map((finding) => ({ ...finding }));
}
