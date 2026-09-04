# Architecture

## Release ingestion pipeline

```text
Tally HTTP or GL XLSX + signed prior-year TB
                    |
              source adapter
                    |
       canonical journal and balance records
                    |
       validation, reconciliation, readiness
                    |
        atomic active dataset and audit trail
                    |
                scrutiny rules
```

Tally and GL upload are the two user-facing ingestion surfaces. The signed
trial balance is an account-level baseline artifact inside the GL workflow,
not a third accounting source. Source adapters preserve raw artifacts and map
both sources into the same source-agnostic records before scrutiny rules run.

## GL upload contract

The first supported GL profile is the supplied transaction workbook. Required
headers are fixed and case-sensitive in the release contract:

- `Document Number`
- `G/L Account`
- `Posting Date`
- `Amount in local currency`

Document type/date, posting key, invoice/reference, clearing document, profit
centre, cost centre, text, supplier/vendor, WBS, purchasing document, and
customer fields are retained when present. The parser reads cached formula
values (`data_only=True`) and warns when a formula has no cached result; it
does not evaluate formulas. Missing required headers or values, invalid dates
or decimals, and rows outside declared coverage are hard failures.

The supplied workbook is expected to contain 59,168 data rows, 10,914
documents, and 445 G/L accounts. Every accepted document must balance to zero
within `₹0.01`; no row may disappear without a recorded skip or rejection
reason. Compact release fixtures live under
`backend/tests/fixtures/`: `golden_gl.xlsx` and `golden_tally.xml`.

## Signs, currency, and baseline

Canonical amounts use `Decimal` with debit-positive and credit-negative signs.
Positive signed GL local-currency amounts become debit lines; negative amounts
become credit lines. Tally values are normalized to the same convention. No
scrutiny rule re-interprets source signs after normalization.

An entity has one configured functional currency. `Amount in local currency`
means that configured currency because the GL source has no currency-code
column. P0 does not consolidate multiple currencies.

When establishing a financial year, GL ingestion also requires a structured,
machine-readable prior-year closing trial balance with one signed balance per
G/L account code at a stated balance date. That checkpoint becomes the
current-year opening baseline. A high-level balance-sheet or profit-and-loss
total cannot seed account-level openings. Duplicate account codes,
non-balanced signed totals, or incomplete account-level baseline coverage are
hard failures. A signed PDF may be retained as evidence but is not an
ingestible baseline without the structured account schedule.

## Period and dataset rules

- An entity has one active source family per financial year: Tally or GL
  upload.
- GL coverage is one full-year workbook or non-overlapping Q1-Q4 workbooks.
- A full-year workbook explicitly replaces the quarterly set; replaced batches
  remain `SUPERSEDED`.
- A corrected period is validated before its previous active batch is replaced.
- Gaps may remain staged, but a dataset is not `READY` until baseline and
  required coverage are complete.
- Active journal coverage may not overlap. The GL workflow validates rows
  against their declared coverage dates.

## Lifecycle and reconciliation

Every source follows preflight, stage, validate, reconcile, activate, and
report steps. Lifecycle statuses are `STAGED`, `ACTIVE`, `FAILED`, and
`SUPERSEDED`. A failed replacement leaves the previous active dataset
untouched. Re-uploading the same SHA-256 is an idempotent duplicate, not a
second accounting import.

Each batch report includes input, accepted, skipped, and rejected row counts;
debit, credit, and net totals; document and unbalanced-document counts;
account and unmapped-account counts; posting-date min/max; baseline coverage;
active batch IDs; and the dataset fingerprint. For each account and period:

```text
derived closing = signed opening checkpoint + cumulative signed journal movement
```

Readiness is `INVALID`, `PARTIAL`, `READY`, or `READY_WITH_WARNINGS`.
`INVALID` means parse or accounting validation failed. `PARTIAL` means valid
staged data is missing baseline or period coverage. Only `READY` and
`READY_WITH_WARNINGS` datasets may be passed to scrutiny rules.

## Tally contract

The Tally adapter uses the supported XML-over-HTTP response and fetches ledger
or account master data with group hierarchy, opening and closing balances,
voucher headers, every voucher ledger line, and stable Tally identifiers when
available. Connector errors become actionable typed ingestion errors. Request
bodies, raw XML, and credentials are never logged.

The compatibility response keeps this shape until it enters the shared
pipeline:

```python
{
    "entity": {
        "name": str,
        "financial_year_start": date,
        "financial_year_end": date,
    },
    "ledgers": [
        {
            "name": str,
            "group_name": str,
            "opening_balance": Decimal,
            "closing_balance": Decimal,
        },
    ],
    "vouchers": [
        {
            "date": date,
            "voucher_type": str,
            "source_voucher_id": str | None,
            "narration": str | None,
            "entries": [
                {
                    "ledger_name": str,
                    "type": "debit" | "credit",
                    "amount": Decimal,
                },
            ],
        },
    ],
}
```

The shared pipeline maps every entry to a canonical signed journal line and
retains the source document and line provenance. Voucher lines must balance
within `₹0.01`; no arbitrary debit/credit pairing may discard additional
lines.

## Canonical records and audit lineage

The canonical model contains one `JournalEntry` per source document, one
`JournalLine` per source row, and one `BalanceCheckpoint` per account at a
stated balance date. `JournalLine` retains source row number, signed amount,
derived side, posting key, quantity, currency, typed dimensions where useful,
and remaining source metadata. The source row number is the stable provenance
key within an immutable batch.

`ImportBatch` remains the provenance root. It retains raw source bytes,
content SHA-256, parser version, declared coverage, source family, validation
report, and lifecycle status. `ScrutinyRun` records the active dataset
fingerprint and ordered source-batch IDs; legacy single-batch lineage may
remain nullable during migration. Existing paired `Transaction` rows remain
for compatibility while consumers move to canonical journal lines.

## Rules boundary

Scrutiny rules consume canonical records and period dates. They do not know
Tally XML, GL headers, source signs, provider details, or file parsing rules.
