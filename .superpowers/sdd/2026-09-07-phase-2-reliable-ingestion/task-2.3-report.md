# Task 2.3 — Fixed GL parsing verification report

## Status

Fix round 1 is complete. The hard-failure report now gives every input source
row an explicit accepted/skipped/rejected accounting outcome while preserving
the `FAILED` batch and hard import failure semantics. The P0 design also
qualifies the Q1 document/account counts as distinct accepted transaction
counts, excluding the skipped footer.

One focused regression was added in `backend/tests/test_xlsx_ingestion.py`.
There were no schema or frontend changes and no synthetic production behavior
outside the reported failure case.

The supplied workbook was available locally and the conditional Q1 test ran;
this is local fixture evidence only, not live or customer evidence.

## Files changed

- `docs/superpowers/specs/2026-09-04-ledger-ingestion-p0-design.md` — clarified
  the worksheet range, accepted/skipped/rejected accounting, footer row,
  acceptance count, and accepted distinct document/account qualification.
- `backend/app/ingestion/xlsx_normalizer.py` — reports all non-skipped source
  rows as rejected when a hard parse failure aborts the batch, retaining the
  failing row's safe error and explicit reasons for the other rows.
- `backend/tests/test_xlsx_ingestion.py` — added the I1 regression and updated
  the unbalanced replacement assertion for complete row accounting.
- `.superpowers/sdd/2026-09-07-phase-2-reliable-ingestion/task-2.3-report.md` —
  this updated report.

The existing dirty Phase 1/demo files and unrelated untracked files were
preserved and were not staged or reverted.

## Required focused commands

All commands below ran from `backend/`.

### Before documentation edit

```bash
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_xlsx_ingestion.py
```

```text
32 passed, 3 warnings in 9.77s
```

```bash
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_xlsx_ingestion.py -k supplied_q1 -rs
```

```text
1 passed, 31 deselected, 3 warnings in 8.03s
```

### After documentation edit

```bash
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_xlsx_ingestion.py
```

```text
32 passed, 3 warnings in 9.91s
```

```bash
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_xlsx_ingestion.py -k supplied_q1 -rs
```

```text
1 passed, 31 deselected, 3 warnings in 8.10s
```

The focused warning set is unchanged: Starlette's `httpx` compatibility
deprecation and FastAPI `on_event` deprecations.

## Q1 count reconciliation

Artifact:

- Path: `/Users/adinayak18/Downloads/GL Dump Q1.XLSX`
- Availability: present locally
- Bytes: `6483523`
- SHA-256: `946ff760638f78854556f8a761506cbd5b4a853d6f6970f852d850743e3149d2`
- Worksheet: `Sheet1`
- Dimensions: `A1:U59169` (21 columns)
- Required headers are present exactly on row 1.

The source row accounting is:

| Outcome | Count | Original rows | Reason |
|---|---:|---|---|
| Input post-header rows | 59,168 | 2–59,169 | All worksheet rows after the header |
| Accepted journal lines | 59,167 | 2–59,168 | Valid fixed-profile transaction rows |
| Skipped | 1 | 59,169 | `L DESCRIPTION` = `LIABILITY TOTAL`; zero amount; `summary/footer row` |
| Rejected | 0 | — | No required-data or accounting failure |

The accepted source rows are contiguous (`2..59168`), do not overlap the skip
set, and their union with row 59169 equals all 59,168 input rows. The footer is
therefore the complete and measured explanation for the one-row difference
between the design's former “59,168 data rows” wording and the test's 59,167
accepted-line assertion. The counts are not changed to force a test result.

Q1 reconciliation results:

- Documents: `10,914`
- Distinct G/L accounts: `445`
- Signed net: `0.00`
- Accepted source row range: `2..59168`
- Source row `28096`: `Quantity = OM`; canonical `quantity = None`, with
  `source_metadata["Quantity"] = "OM"` and `dimensions["Quantity"] = "OM"`
- Skip reasons: exactly `[{'row': 59169, 'reason': 'summary/footer row', 'column': 'L DESCRIPTION', 'marker': 'LIABILITY TOTAL'}]`

The Q1 test passed with these assertions, and the parser returned only after
each accepted document passed the existing signed balance validation.

## Acceptance coverage matrix

| Acceptance case | Existing evidence | Result |
|---|---|---|
| Exact headers within rows 1–15 and `data_only=True` | `test_fixed_gl_parser_uses_data_only_and_scans_only_first_fifteen_rows` | Pass |
| Formula values are not evaluated | `test_fixed_gl_parser_never_evaluates_formula_values`; formula-cache warning tests | Pass |
| Signed debit-positive/credit-negative rows | `test_xlsx_fixed_profile_preserves_signed_rows`; canonical fixture side assertions | Pass |
| Blank rows and structured summary/footer rows | `test_xlsx_skips_blank_lines`; `test_fixed_gl_parser_skips_only_structured_summary_rows`; Q1 footer | Pass |
| Required malformed values and invalid dates | `test_fixed_gl_parser_rejects_malformed_required_values`; API blank-value case | Pass |
| Non-finite amounts | Existing `_parse_decimal` finite guard; bounded direct probe of `NaN`, `Infinity`, and `-Infinity` rejected with `must be a valid decimal` | Pass; no test/source change needed |
| Amount and quantity overprecision | `test_fixed_gl_parser_rejects_amount_precision_that_numeric_20_2_cannot_retain`; `test_fixed_gl_parser_rejects_numeric_quantity_precision_beyond_numeric_20_4` | Pass |
| Out-of-period rows and unbalanced documents | `test_fixed_gl_parser_rejects_rows_outside_declared_coverage`; `test_fixed_gl_parser_rejects_unbalanced_documents`; API unbalanced case | Pass; hard failures remain hard |
| Repeated account codes remain separate valid lines | `test_fixed_gl_parser_allows_repeated_account_codes_within_a_document` | Pass |
| Metadata/classification warnings retain valid journals | `test_fixed_gl_normalizer_reports_skips_formula_cache_warnings_and_unclassified_accounts`; `test_fixed_gl_parser_preserves_nonnumeric_optional_quantity_and_warns` | Pass; warnings non-fatal |
| Every normalizer row has an explained outcome | `test_fixed_gl_normalizer_records_structured_report_for_staged_failure`; skip/report assertions; Q1 union accounting | Pass |
| Hard failure before later worksheet rows still accounts for every row | `test_fixed_gl_failure_report_accounts_rows_after_hard_error`; failed replacement API assertions | Pass; failed batch remains hard `FAILED`, with all non-skipped rows rejected |
| Source row numbers and source metadata persist | `test_fixed_gl_parser_preserves_dimensions_and_source_row`; Q1 row 28096 probe | Pass |
| Source bytes/hash and duplicate provenance | Existing staging hash test; in-memory Q1 persistence probe below | Pass |
| Invalid input cannot activate; prior active data is preserved | `test_fixed_gl_confirm_activates_after_normalization_and_rolls_back_invalid_replacement`; API failure tests | Pass |
| Supplied Q1 counts and zero total | `test_supplied_q1_gl_workbook_is_accepted_with_footer_and_nonnumeric_quantity` | Pass; 1 ran, 0 skipped |

## Source, hash, and provenance review

The existing path remains fixed-profile transaction GL:

- `_parse_fixed_gl_xlsx` loads with `data_only=True`, finds exact required
  headers only in rows 1–15, preserves each accepted `source_row_number`, and
  records blank/summary skip reasons.
- Required values, dates, finite amounts, numeric capacity, declared coverage,
  and document balance are hard validation boundaries.
- When a hard parse error stops scanning, `_GLParseError` carries the header
  row and failure reporting enumerates the full source-row range. Every
  non-skipped row is represented in `reject_reasons`: the failing row keeps its
  safe validation error, prior rows record batch rejection, and later rows
  record that parsing stopped after the earlier hard failure.
- Optional dimension, uncached-formula, nonnumeric quantity, and unresolved
  account classification issues are warnings; valid journal lines remain
  persisted.
- `normalize_gl_xlsx` writes canonical `JournalEntry`/`JournalLine` records,
  copies source metadata and source row numbers, and stores the structured
  reconciliation report on the staged batch.
- `stage_import_batch` retains raw source bytes and computes the SHA-256 before
  duplicate lookup; `ImportBatch` keeps the hash, parser version, coverage,
  source family, metadata, and validation report.

An in-memory SQLite normalization probe of the supplied artifact reported:

```text
persisted_batch_id 1
persisted_status STAGED
persisted_source_family gl_upload
persisted_sha256_matches True
persisted_raw_bytes_length 6483523
persisted_source_metadata {'parser': 'xlsx_gl', 'required_headers': ['Document Number', 'G/L Account', 'Posting Date', 'Amount in local currency']}
persisted_report_counts {'input_rows': 59168, 'accepted_rows': 59167, 'skipped_rows': 1, 'rejected_rows': 0, 'document_count': 10914, 'account_count': 445}
persisted_report_skip_reasons [{'row': 59169, 'reason': 'summary/footer row', 'column': 'L DESCRIPTION', 'marker': 'LIABILITY TOTAL'}]
persisted_report_warning_count 459
persisted_journal_counts {'entries': 10914, 'lines': 59167, 'accounts': 445}
```

The 459 persisted warnings are the 14 aggregated optional-dimension/quantity
warnings plus 445 unresolved-account classification warnings. They do not
change the accepted journal count or activate malformed accounting data.

The parser warning list for Q1 was:

```text
optional dimension is blank for optional column 'Assignment' in 1220 row(s).
optional dimension is blank for optional column 'Clearing Document' in 22339 row(s).
optional dimension is blank for optional column 'Cost Center' in 55708 row(s).
optional dimension is blank for optional column 'Customer' in 58331 row(s).
optional dimension is blank for optional column 'Customer Name' in 58331 row(s).
optional dimension is blank for optional column 'Invoice No/Reference' in 17695 row(s).
optional dimension is blank for optional column 'Posting Key' in 2001 row(s).
optional dimension is blank for optional column 'Profit Center' in 2 row(s).
optional dimension is blank for optional column 'Purchasing Document' in 30816 row(s).
optional dimension is blank for optional column 'Supplier' in 30661 row(s).
optional dimension is blank for optional column 'Text/Bid/Cont/TndrNo' in 19953 row(s).
optional dimension is blank for optional column 'Vendor Name' in 30661 row(s).
optional dimension is blank for optional column 'WBS element' in 44403 row(s).
optional column 'Quantity' contains non-numeric values in 1 row(s); canonical quantity left unset.
```

## TDD — fix round 1

The required RED/GREEN cycle was run before the production fix.

### RED

Added `test_fixed_gl_failure_report_accounts_rows_after_hard_error` with a
valid row 2, a missing required `G/L Account` at row 3, and a valid later row
4. Before the production change:

```bash
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_xlsx_ingestion.py::test_fixed_gl_failure_report_accounts_rows_after_hard_error
```

```text
FAILED tests/test_xlsx_ingestion.py::test_fixed_gl_failure_report_accounts_rows_after_hard_error
E       assert 1 == 3
1 failed, 3 warnings in 0.11s
```

The failure confirmed the report had `input_rows=3`, `accepted_rows=0`,
`skipped_rows=0`, and only `rejected_rows=1`.

### GREEN

After carrying the detected header row through `_GLParseError` and constructing
one rejection reason per non-skipped source row:

```bash
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_xlsx_ingestion.py::test_fixed_gl_failure_report_accounts_rows_after_hard_error
```

```text
1 passed, 3 warnings in 0.08s
```

The regression now proves `input=3`, `accepted=0`, `skipped=0`, `rejected=3`,
and rejection rows `{2, 3, 4}` while the normalizer still raises the required
field error and marks the batch `FAILED`.

### Fix-round focused verification

```bash
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_xlsx_ingestion.py
```

```text
33 passed, 3 warnings in 10.15s
```

```bash
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_xlsx_ingestion.py -k supplied_q1 -rs
```

```text
1 passed, 32 deselected, 3 warnings in 8.08s
```

## Full relevant check

```bash
PYTHONPATH=. ../.venv/bin/pytest -q
```

```text
236 passed, 4 warnings in 22.14s
```

The full-suite warnings are the same existing Starlette/FastAPI deprecations,
plus one AnyIO/Starlette deprecated `HTTP_422_UNPROCESSABLE_ENTITY` warning.

Documentation validation:

```bash
git diff --check
```

```text
exit 0; no output
```

## Self-review

- The canonical transaction-level GL contract and signed amount convention are
  unchanged.
- A hard parse failure still aborts normalization and cannot activate a batch;
  the failure report now rejects every non-skipped source row explicitly.
- The new failure report carries the parser's header row so rejection reasons
  retain original worksheet row numbers even when the header is below row 1.
- The one skipped Q1 row is now named, located, and tied to its structured skip
  reason; accepted and rejected counts are not conflated.
- No summary heuristic was broadened, no genuine journal row was reclassified,
  and repeated account codes remain valid separate lines.
- Hard accounting failures remain hard; metadata and classification warnings
  remain non-fatal.
- No CSV, `.xls`, schema, GST, guessing, or unrelated Tally work was added.
- Only the Task 2.3 production/test/doc/report files are intended for the fix
  round commit; unrelated dirty files remain untouched.

## Concerns

- Q1 evidence is from the locally available artifact and local in-memory
  persistence, not a live/customer environment.
- The focused and full checks retain four existing deprecation-warning
  instances in the full suite; none are Task 2.3 failures.
- No browser smoke or live Tally evidence was attempted because those are
  outside this fixed-GL verification task and require separate environment
  prerequisites.
