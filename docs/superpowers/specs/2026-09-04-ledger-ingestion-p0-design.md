# Ledger Ingestion P0 Design

**Date:** 2026-09-04  
**Status:** Draft for user review after P0 approval  
**Scope:** Canonical ingestion and reconciliation before scrutiny-rule work

## Goal

Make the two supported ingestion paths trustworthy before any compliance or scrutiny rule depends on them:

1. Tally connector: account master, opening/closing balances, and voucher lines.
2. GL upload workflow: one full-year transaction dump or non-overlapping quarterly transaction dumps, with a structured signed prior-year account-level closing trial-balance artifact as the opening baseline.

Both paths must produce the same canonical journal representation, preserve the original source, and activate data only after deterministic validation and reconciliation.

The signed prior-year document is sufficient only if it contains one signed balance per G/L account code. A high-level balance-sheet or profit-and-loss total cannot seed account-level openings and is outside this design.

## Why the current plan needs this change

The supplied `GL Dump Q1.XLSX` is a transaction-level SAP-like export, not a trial-balance snapshot. It has 21 columns, including document number, G/L account, posting date, posting key and signed local-currency amount. Its `Sheet1` range is `A1:U59169`: row 1 is the header, and rows 2–59169 are 59,168 post-header source rows. Rows 2–59168 are 59,167 accepted journal lines; row 59169 is the explicit zero-valued `LIABILITY TOTAL` summary/footer row and is skipped. No rows are rejected. The accepted transaction rows contain 10,914 distinct document IDs and 445 distinct G/L account codes; the accepted documents balance to zero. :codex-file-citation{path="/Users/adinayak18/Downloads/GL Dump Q1.XLSX" purpose="source" artifact_kind="workbook" sheet="Sheet1" range="A1:U59169"}

The current XLSX normalizer is shaped around `ledger_name`, `group_name`, `opening_balance` and `closing_balance`, so it cannot faithfully ingest this file. The current Tally HTTP connector returns `vouchers: []`, and the existing paired debit/credit `Transaction` model cannot preserve arbitrary multi-line journal documents. These are P0 ingestion gaps, not rule gaps.

## Architecture

```text
Tally HTTP ───────────────┐
                          ├─ source adapter ─→ canonical journal/balance records
GL XLSX + signed TB ──────┘                              │
                                                         ↓
                                            validation and reconciliation
                                                         │
                                                         ↓
                                             atomic active dataset + audit trail
                                                         │
                                                         ↓
                                                 scrutiny rules (later phase)
```

There are still only two user-facing ingestion surfaces. The signed trial balance is a balance-baseline artifact inside the GL workflow, not a third accounting source.

### Source and period rules

- An entity has one active source family per financial year: Tally or GL upload.
- A GL dataset may contain Q1–Q4 files or one full-year file.
- Active journal coverage may not overlap. A full-year file explicitly replaces the quarterly set; the old batches remain `SUPERSEDED`.
- A corrected file for an existing period is validated before the previous active batch is replaced.
- Gaps are allowed for staging but the dataset is not `READY` until the opening baseline and the required period coverage are complete.
- The entity has one configured functional currency. The workbook’s “local currency” is interpreted as that currency because the source has no currency-code column.

### Sign convention

Canonical amounts use `Decimal` and debit-positive/credit-negative signs. For GL data, positive signed local-currency amounts become debit lines and negative amounts become credit lines. Tally values are normalized to the same convention. No rule may re-interpret source signs after normalization.

## Canonical data model

The existing append-only `ImportBatch` remains the provenance root. It is extended with:

- `kind`: `journal` or `balance_checkpoint`;
- retained raw source bytes for reproducible parsing;
- declared coverage dates and source family metadata;
- the existing SHA-256, parser version, validation report and lifecycle status.

Add the following models:

### `JournalEntry`

One source document, linked to an `ImportBatch`, with entity, source document identifier, posting date, optional document date/type and header narration.

### `JournalLine`

One source row, linked to a `JournalEntry` and `LedgerAccount`, with:

- source row number;
- signed amount and derived debit/credit side;
- posting key, quantity and currency;
- typed core dimensions where useful;
- JSON source metadata for the remaining export columns.

The source row number is the stable provenance key within an immutable batch. We do not invent a cross-system document-line key when the GL export does not provide one.

### `BalanceCheckpoint`

One signed balance per account at a stated balance date, linked to its source batch. The prior-year closing checkpoint becomes the current-year opening baseline. A checkpoint may also represent a Tally opening/closing balance response.

### `LedgerAccount` changes

- Add `external_code` and make it the preferred entity-level identity.
- Retain the human-readable name/description.
- Allow group and normal-balance classification to be unknown for GL accounts.
- Do not infer groups from account descriptions.

The existing paired `Transaction` table remains for legacy compatibility during migration. New ingestion writes canonical journal lines; later rule work can migrate consumers without forcing arbitrary pairings.

### Audit-run lineage

Because one scrutiny period can use several quarterly batches, extend `ScrutinyRun` with the active dataset fingerprint and ordered source-batch IDs. The existing single `import_batch_id` may remain nullable for legacy runs.

## Import lifecycle

Every source follows the same pipeline:

1. **Preflight:** inspect file/request metadata without changing active data.
2. **Stage:** create an immutable batch and normalized rows in a transaction.
3. **Validate:** enforce schema, dates, amounts, account identity, document balancing, period overlap and baseline rules.
4. **Reconcile:** calculate row/document/account totals and the resulting dataset fingerprint.
5. **Activate:** mark the new batch active and supersede replaced coverage in the same transaction.
6. **Report:** return counts, warnings, reconciliation totals, readiness and source provenance.

Statuses are `STAGED`, `ACTIVE`, `FAILED` and `SUPERSEDED`. A failed replacement leaves the old active dataset untouched. Re-uploading the same SHA-256 is an idempotent duplicate, not another accounting import.

## GL upload contract

The first supported profile is the supplied workbook shape. Required headers are:

- `Document Number`
- `G/L Account`
- `Posting Date`
- `Amount in local currency`

This transaction-level profile is the only supported XLSX GL format. The legacy four-column balance upload (`ledger_name`, `group_name`, `opening_balance`, `closing_balance`) and its three sign conventions are not part of this contract.

The parser retains document type/date, posting key, invoice/reference, clearing document, profit centre, cost centre, text, supplier/vendor, WBS, purchasing document and customer fields when present. It reads cached formula values (`data_only=True`) and warns when a formula has no cached result; it never evaluates Excel formulas.

The GL workflow accepts a transaction workbook and, when establishing a financial year, a structured signed prior-year account-level closing trial-balance workbook. A signed PDF may be retained as evidence, but a PDF alone is not an ingestible baseline in P0; it must be accompanied by a machine-readable account schedule. The API may receive both artifacts in one confirmation request or register the baseline first; both are part of the same GL workflow and share the same entity/source contract.

Hard failures:

- missing required headers or required values;
- invalid date/decimal values;
- rows outside declared coverage;
- a document whose signed lines do not sum to zero within ₹0.01;
- duplicate account codes in a balance checkpoint;
- overlapping active journal coverage;
- a non-balanced signed trial balance;
- failed database activation.

Warnings:

- blank optional dimensions;
- missing cached descriptions;
- unclassified accounts;
- incomplete period coverage while staging.

## Tally contract

The Tally adapter must fetch and normalize:

- ledger/account master and group hierarchy;
- opening and closing balances;
- voucher headers;
- every voucher ledger line;
- stable Tally identifiers when available.

Connector errors must become actionable, typed ingestion errors. Request bodies, raw XML and credentials are not logged. The adapter remains independent of FastAPI and SQLAlchemy; persistence occurs in the shared pipeline.

Tally endpoint configuration is explicit:

- The caller supplies the endpoint; it must use `http` or `https` and include a host.
- Local endpoints require `TALLY_ALLOW_LOCAL_ENDPOINTS=1`.
- A deployed DNS hostname must be an exact entry in `TALLY_ALLOWED_HOSTS`.
- Do not silently enable loopback or broadly allow private addresses; redirects remain disabled.
- Remote Tally topology is unresolved until the pilot environment and an approved connection approach are known. Do not assume public reachability or add a bridge, agent, or network access change under this contract.

## Reconciliation and readiness

Each batch report includes:

- input, accepted, skipped and rejected row counts;
- debit, credit and net totals;
- document count and unbalanced-document count;
- account count and unmapped-account count;
- posting-date min/max;
- opening baseline coverage;
- active batch IDs and dataset fingerprint.

For each account and period:

```text
derived closing = signed opening checkpoint + cumulative signed journal movement
```

Readiness states:

- `INVALID`: parse or accounting validation failed;
- `PARTIAL`: valid staged data but missing baseline or period coverage;
- `READY`: valid baseline and required coverage are present;
- `READY_WITH_WARNINGS`: ready for ingestion, with non-fatal metadata/classification warnings.

Only `READY` and `READY_WITH_WARNINGS` datasets may be passed to scrutiny rules.

## API and UI boundaries

Keep the existing Tally and XLSX routes as compatibility entry points, but route both through the shared pipeline. The GL confirmation response must expose the reconciliation report, lifecycle status, readiness and the active dataset fingerprint. The UI only needs to surface those states and the baseline-file requirement; rule screens are not part of P0.

## P0 implementation tasks

### 0.1 Freeze contracts and fixtures

Document the GL header profile, sign convention, period rules, account-level baseline format, currency requirement and Tally response contract. Add a compact golden GL fixture and a minimal equivalent Tally fixture. Record the supplied workbook’s expected counts and zero-balance invariants.

### 0.2 Add canonical models and migration

Modify `backend/app/db/models.py` and add an Alembic migration for `JournalEntry`, `JournalLine`, `BalanceCheckpoint`, account external codes/classification nullability, raw batch bytes, batch kind and scrutiny-run dataset lineage. Preserve existing rows and legacy transaction behavior.

### 0.3 Implement the shared batch pipeline

Extend `backend/app/ingestion/batches.py` with staged activation, exact-hash idempotency, active-period overlap checks, atomic replacement, raw-source retention and dataset fingerprinting. Add `backend/app/ingestion/schema.py` for canonical records and `backend/app/ingestion/reconciliation.py` for shared validation/reporting.

### 0.4 Replace trial-balance XLSX normalization

Refactor `backend/app/ingestion/xlsx_normalizer.py` to parse the fixed GL profile, preserve source rows/dimensions, create journal entries/lines, enforce period/document checks and emit warnings without guessing classifications.

### 0.5 Add signed opening-balance ingestion

Add the balance-checkpoint parser to the GL workflow. Accept the structured XLSX schedule, validate account-level uniqueness, signed-total balance, account-code matching and opening-baseline completeness, and retain any signed PDF as supporting evidence. Keep the source artifact and reconciliation evidence.

### 0.6 Expand Tally ingestion

Update `backend/app/ingestion/tally_http.py`, `tally_parser.py` and `tally_normalizer.py` to fetch vouchers and account data, normalize multi-line vouchers and balances, and pass them through the same pipeline. Preserve actionable connector failures and credential-safe logging.

### 0.7 Add active-dataset derivation

Implement the resolver that selects the non-overlapping active batches for an entity and financial year, computes cumulative account movements, derives closing balances, identifies gaps and produces the dataset fingerprint consumed by later scrutiny runs.

### 0.8 Wire API, UI and verification

Update `backend/app/routers/scrutiny.py`, the XLSX upload component and integration tests to expose staged/failed/partial/ready states. Prove Q1+Q2 accumulation, corrected-quarter replacement, annual-versus-quarter exclusivity, malformed replacement rollback, signed-baseline continuity, Tally/GL canonical parity and full-file row preservation.

## Acceptance gates

P0 is complete only when:

- the supplied Q1 workbook imports with 59,168 input rows accounted as 59,167 accepted journal lines, one explained summary/footer skip, zero rejected rows, 10,914 documents, 445 accounts and the expected date range;
- every accepted document balances and no row disappears without a recorded reason;
- Q1, Q2 and Q3 can be accumulated without overlap or duplication;
- a full-year file is an explicit alternative to the quarterly set;
- a corrected file cannot corrupt the prior active dataset;
- the signed prior-year account-level closing balances become current-year openings;
- Tally supplies voucher lines as well as balances;
- both sources populate the same canonical journal model;
- raw artifacts, hashes, parser versions, validation reports and dataset lineage are retained;
- no scrutiny rule contains source-specific ingestion logic.

## Out of scope

P0 does not add CSV or legacy `.xls`, automatic column guessing, multi-currency consolidation, automatic account-group inference, GST provider work, Income Tax/Custom Duty portal automation, LLM decision-making or new compliance formulas. Those can depend on this certified ingestion layer later.

## Files to touch during implementation

- `backend/app/db/models.py`
- `backend/app/ingestion/batches.py`
- `backend/app/ingestion/xlsx_normalizer.py`
- `backend/app/ingestion/tally_http.py`
- `backend/app/ingestion/tally_parser.py`
- `backend/app/ingestion/tally_normalizer.py`
- `backend/app/routers/scrutiny.py`
- `backend/app/ingestion/schema.py` (new)
- `backend/app/ingestion/reconciliation.py` (new)
- `backend/alembic/versions/20260904_canonical_ledger_ingestion.py` (new)
- focused ingestion/model/API tests under `backend/tests/`
- `frontend/src/components/XlsxUploadModal.tsx` and `frontend/src/App.tsx` only for changed ingestion states
- `docs/architecture.md`, `docs/tally-connector.md` and the roadmap where the old trial-balance contract contradicts this design
