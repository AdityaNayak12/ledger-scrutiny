# Architecture

## Pipeline
Tally XML export -> ingestion parser -> normalizer -> Postgres
  -> rules engine -> FastAPI -> (future) exception review UI

Each arrow is a hard boundary. The normalizer is the only thing allowed to
know about Tally's XML shape. Everything after it only ever sees the
internal schema below.

## Audit lifecycle

The system treats every upload and scrutiny execution as durable audit
evidence. A re-upload never deletes the prior import: it creates a new
`import_batch`, marks the prior active batch for the period as `SUPERSEDED`,
and uses the new batch for subsequent scrutiny runs.

```
Entity -> FinancialPeriod -> ImportBatch -> ScrutinyRun -> Finding -> ReviewAction
```

- `ImportBatch` records the source, content SHA-256, uploader, parser version,
  validation report, and lifecycle status.
- `ScrutinyRun` records the exact active import batch, rule-set version,
  timestamps, status, and summary.
- Findings retain a stable fingerprint so review state can survive a rerun
  when only amounts in the explanation change.
- `ReviewAction` is append-only. The finding's current status/notes are a
  convenience projection of the latest decision, not the only audit record.

Database changes are managed by Alembic. Apply production migrations with
`PYTHONPATH=. alembic upgrade head` from `backend/`; do not rely on
`create_all` as a migration mechanism.

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

import_batches
  id, entity_id, financial_period_id, source, original_filename,
  content_sha256, parser_version, status, validation_report, created_at

scrutiny_runs
  id, entity_id, financial_period_id, import_batch_id, rule_set_version,
  status, summary, started_at, completed_at

review_actions
  id, exception_id, user_id, status, auditor_notes, created_at

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
