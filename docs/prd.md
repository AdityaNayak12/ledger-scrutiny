# Audit scrutiny engine — project spec

## What this is
An ingestion + trial balance scrutiny engine for CA audit workflows. It replaces
the manual grunt work of checking a client's ledger for basic errors before the
real audit starts: wrong balance types, opening/closing continuity breaks, and
below-materiality noise.

This is stage one only. Vouching, GST/TDS reconciliation, and compliance
rule packs (Companies Act, Income Tax Act, GST Act, etc.) come later and are
out of scope for this build.

## Non-negotiable design principles
1. No LLM/ML calls anywhere in this pipeline. Every check is deterministic
   code. This is required, not a preference: audit-facing logic must produce
   the same output for the same input every time, and needs to be defensible
   to a reviewer without "the model decided this."
2. Source-agnostic core. Tally is the first ingestion adapter, but the
   internal schema (see ARCHITECTURE.md) must not leak Tally-specific
   concepts. Adding SAP or Zoho later should mean writing a new adapter,
   not touching the rules engine or database schema.
3. Rules are independent functions, not a monolith. Each rule takes
   normalized ledger data and returns a list of exceptions. Adding rule #6
   must never risk breaking rules #1-5. This is what lets the rule set grow
   to the size CORAA's has (164 rules) without becoming unmaintainable.
4. Every flagged exception carries a human-readable reason. The output of
   this system is a list a CA reviews and clears, not a verdict.

## MVP scope (this build)
- Parse a Tally XML ledger/day book export into the internal schema.
- Store normalized data in Postgres (entities, ledger_accounts,
  transactions, trial_balance_snapshots).
- Run scrutiny rules:
  - Normal balance check (account group vs debit/credit side).
  - Opening balance = prior year closing balance, per account.
  - Materiality-threshold filtering (configurable per entity).
- Expose via FastAPI: upload export, trigger scrutiny run, list exceptions.
- No frontend build in this pass — API returns JSON, testable via
  curl/Postman/pytest first.

## Explicitly deferred (not in this build)
- Vouching / document OCR
- GST/TDS/portal reconciliation
- Compliance rule packs beyond basic trial balance scrutiny
- SAP / Zoho adapters
- Auth, multi-tenant access control
- Frontend UI

## Tech stack
- Python 3.11+, FastAPI, SQLAlchemy 2.x, Postgres, pytest
- lxml for XML parsing
- No background job queue yet (single-run synchronous processing is fine
  at this data volume)
