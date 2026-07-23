# Architecture

## Pipeline
Tally XML export -> ingestion parser -> normalizer -> Postgres
  -> rules engine -> FastAPI -> (future) exception review UI

Each arrow is a hard boundary. The normalizer is the only thing allowed to
know about Tally's XML shape. Everything after it only ever sees the
internal schema below.

## Internal schema (source-agnostic)

entities
  id, name, financial_year_start, financial_year_end, materiality_threshold

ledger_accounts
  id, entity_id, name, group_name, normal_balance ('debit'|'credit')
  group_name examples: 'Capital Account', 'Fixed Assets', 'Sundry Debtors',
  'Sundry Creditors', 'Sales', 'Purchases', 'Direct Expenses', etc.
  normal_balance is derived from group_name via a lookup table
  (see rules/account_groups.py) — this is what rule #1 checks against.

transactions
  id, entity_id, date, debit_account_id, credit_account_id, amount,
  narration, voucher_type, source_voucher_id

trial_balance_snapshots
  id, entity_id, ledger_account_id, period_start, period_end,
  opening_balance, total_debits, total_credits, closing_balance

exceptions
  id, entity_id, rule_name, ledger_account_id (nullable), severity,
  message, created_at

## Why this schema shape
- ledger_accounts.normal_balance is precomputed at normalization time
  (not derived at query time) so the rules engine never needs to know
  Tally's group naming conventions.
- trial_balance_snapshots is a separate table from transactions rather
  than a computed view, because scrutiny needs to compare *periods*
  (this year's opening vs last year's closing), and materializing
  snapshots makes that a simple join instead of an aggregation over
  every transaction each time.
- exceptions is its own table, not just an API response, because a CA
  needs to be able to mark one reviewed/cleared without re-running the
  whole scrutiny pass. Persisting exceptions is what makes the "human
  reviews an exception queue" workflow possible later.

## Rules engine contract
Every rule is a function with this signature:

    def rule_fn(entity: Entity, accounts: list[LedgerAccount],
                snapshots: list[TrialBalanceSnapshot]) -> list[Exception]

The engine (rules/engine.py) collects all registered rule functions and
runs each independently, catching exceptions per-rule so one broken rule
can't take down the whole scrutiny run. This is the plugin architecture
referenced in PROJECT_SPEC.md.

## Adding a new ingestion source later (SAP, Zoho)
1. Write `ingestion/<source>_parser.py` that reads the source's native
   export format.
2. Write `ingestion/<source>_normalizer.py` that maps it into the same
   entities/ledger_accounts/transactions/trial_balance_snapshots shape
   Tally's normalizer produces.
3. Nothing in rules/ or routers/ changes.