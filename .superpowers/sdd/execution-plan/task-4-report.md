# Task 4 report

## Status

DONE

## Scope

Implemented the fixed GL XLSX profile in `backend/app/ingestion/xlsx_normalizer.py`
and added focused coverage in `backend/tests/test_xlsx_ingestion.py`. The existing
explicit trial-balance mapping remains available through a clear dispatch path.

The fixed parser:

- requires the four exact GL headers in rows 1–15 and reads with
  `data_only=True`;
- returns canonical signed `JournalEntryRecord`/`JournalLineRecord` values;
- preserves source row numbers, known dimensions, unknown columns, and formula
  cache gaps;
- validates required values, dates, decimals, declared coverage, and document
  balance within `₹0.01`;
- persists entity-scoped external-code accounts and canonical journal rows when
  given a session and import batch;
- stores the shared reconciliation report on the batch, including counts,
  totals, warnings, skipped-row reasons, account codes, and readiness;
- never infers account classifications.

## Test-first evidence

### RED

Command:

```text
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_xlsx_ingestion.py
```

Output:

```text
ImportError: cannot import name 'normalize_gl_xlsx' from 'app.ingestion.xlsx_normalizer'
1 error during collection
```

### Focused GREEN

Command:

```text
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_xlsx_ingestion.py
```

Output:

```text
17 passed, 3 warnings in 1.14s
```

The focused tests cover golden GL row/document/account counts and zero balance,
signed debit/credit, exact header scanning, cached-formula behavior, source
dimensions and unknown-column preservation, repeated accounts, malformed
required values/dates/decimals, unbalanced documents, out-of-coverage rows,
skip reasons, warnings, and legacy compatibility.

## Required verification

Full backend suite:

```text
PYTHONPATH=. ../.venv/bin/pytest -q
```

```text
82 passed, 4 warnings in 5.95s
```

Diff whitespace check:

```text
git diff --check
```

```text
(no output; exit code 0)
```

Compilation:

```text
PYTHONPATH=. ../.venv/bin/python -m compileall -q app tests
```

```text
(no output; exit code 0)
```

Warnings are the existing FastAPI/Starlette deprecations and do not fail the
suite.

## Commit

Created after the green verification checks.

## Round 1 fix report — 2026-09-05

### Scope

Fixed the review findings in `backend/app/ingestion/xlsx_normalizer.py` with
focused regressions in `backend/tests/test_xlsx_ingestion.py`. The SDD ledger
was not modified. Legacy explicit trial-balance mapping and sign conventions
remain on the existing compatibility dispatch.

The fixes now:

- accept the supplied Q1 profile aliases, preserve nonnumeric optional
  quantities such as `OM`, leave canonical quantity unset, and aggregate the
  warning;
- skip only explicitly identified zero-valued summary/footer rows with a
  structured reason while still failing ordinary malformed rows;
- resolve accounts by entity-scoped external code only, creating a unique
  account name when a differently-coded legacy name collides;
- reject fixed-profile normalization for every non-`STAGED` batch before any
  writes and preserve active reports;
- retain structured staged-failure reconciliation counts and row reasons;
- inspect every non-required original formula cell without evaluating it,
  preserve cached unknown-column values, and aggregate blank/cache warnings;
- reject amount precision beyond `Numeric(20,2)` and numeric quantity precision
  beyond `Numeric(20,4)`.

### Test-first evidence

RED command:

```text
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_xlsx_ingestion.py -k 'supplied_q1 or nonnumeric_optional_quantity or structured_summary or entity_scoped_external_code or requires_staged_batch or structured_report_for_staged_failure or aggregates_uncached_formula or amount_precision or quantity_precision'
```

Result: exit code 1; 11 targeted regressions failed against the pre-fix
implementation (17 tests deselected).

### Verification

Fixed-profile and legacy compatibility tests:

```text
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_xlsx_ingestion.py
```

```text
28 passed, 3 warnings in 9.45s
```

Supplied workbook smoke/count/zero-balance check:

```text
PYTHONPATH=. ../.venv/bin/python -c 'from datetime import date; from decimal import Decimal; from app.ingestion.xlsx_normalizer import parse_gl_xlsx; p=parse_gl_xlsx(open("/Users/adinayak18/Downloads/GL Dump Q1.XLSX","rb").read(), date(2025,4,1), date(2025,6,30)); lines=[line for entry in p for line in entry.lines]; print({"rows":len(lines),"documents":len(p),"accounts":len({line.ledger_account_code for line in lines}),"signed_total":sum((line.amount for line in lines),Decimal("0")),"min_date":min(entry.posting_date for entry in p),"max_date":max(entry.posting_date for entry in p),"om_rows":[line.source_row_number for line in lines if line.source_row_number==28096 and line.quantity is None]})'
```

```text
{'rows': 59167, 'documents': 10914, 'accounts': 445, 'signed_total': Decimal('0.00'), 'min_date': datetime.date(2025, 4, 1), 'max_date': datetime.date(2025, 6, 30), 'om_rows': [28096]}
```

Parser reconciliation smoke check:

```text
PYTHONPATH=. ../.venv/bin/python -c 'from datetime import date; from app.ingestion.xlsx_normalizer import _parse_fixed_gl_xlsx; p=_parse_fixed_gl_xlsx(open("/Users/adinayak18/Downloads/GL Dump Q1.XLSX","rb").read(), date(2025,4,1), date(2025,6,30)); print({"input_rows":p.input_rows,"accepted_rows":sum(len(entry.lines) for entry in p.entries),"skipped_rows":p.skipped_rows,"skip_reasons":p.skip_reasons,"warning_count":len(p.warnings),"first_warnings":p.warnings[:3]})'
```

```text
{'input_rows': 59168, 'accepted_rows': 59167, 'skipped_rows': 1, 'skip_reasons': ({'row': 59169, 'reason': 'summary/footer row', 'column': 'L DESCRIPTION', 'marker': 'LIABILITY TOTAL'},), 'warning_count': 14, 'first_warnings': ("optional dimension is blank for optional column 'Assignment' in 1220 row(s).", "optional dimension is blank for optional column 'Clearing Document' in 22339 row(s).", "optional dimension is blank for optional column 'Cost Center' in 55708 row(s).")}
```

Full backend suite:

```text
PYTHONPATH=. ../.venv/bin/pytest -q
```

```text
93 passed, 4 warnings in 13.93s
```

Whitespace check:

```text
git diff --check
```

```text
(no output; exit code 0)
```

Compilation:

```text
PYTHONPATH=. ../.venv/bin/python -m compileall -q app tests
```

```text
(no output; exit code 0)
```

The warnings are the existing FastAPI/Starlette deprecations. The supplied
workbook was present and verified; its local-path regression is skipped when
that external fixture is unavailable in another checkout.

### Final post-interruption focused verification

Command:

```text
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_xlsx_ingestion.py
```

Result:

```text
28 passed, 3 warnings in 9.15s
```

The warnings remain the existing FastAPI/Starlette deprecations. No repeated
full-suite, smoke, compile, or other long-running checks were started after
the user requested stopping them.

## Round 2 fix report — 2026-09-05

### Scope

Fixed the production XLSX confirmation lifecycle in
`backend/app/routers/scrutiny.py` and tightened the footer rule in
`backend/app/ingestion/xlsx_normalizer.py`. Added API and parser regressions in
`backend/tests/test_xlsx_ingestion.py`. The SDD ledger and Task 5+ code were
not modified.

The XLSX confirmation route now creates a staged candidate, normalizes it,
activates it with replacement/supersession only after successful validation,
and returns the final active status, reconciliation report, and dataset
fingerprint. Candidate work is isolated in a savepoint so failed normalization
cannot remove an existing active dataset. Exact-hash active duplicates remain
no-ops and non-active duplicates remain rejected by the shared batch helper.
Explicit legacy trial-balance mappings continue through their compatibility
path and are activated only after their existing normalization completes.

Summary/footer recognition now requires a present, numerically zero amount;
summary labels with a blank amount fail required-value validation.

### Test-first evidence

RED command:

```text
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_xlsx_ingestion.py -k 'fixed_gl_confirm_activates_after_normalization or structured_summary'
```

Result: exit code 1; 2 regressions failed and 27 tests were deselected. The
API path failed because the route left the candidate active before fixed
normalization, and the blank-amount summary row was incorrectly skipped.

### Verification

Focused XLSX and API integration tests:

```text
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_xlsx_ingestion.py tests/test_api_integration.py
```

```text
39 passed, 3 warnings in 11.48s
```

Full backend suite:

```text
PYTHONPATH=. ../.venv/bin/pytest -q
```

```text
94 passed, 4 warnings in 14.92s
```

Supplied Q1 workbook smoke/count/zero-balance check:

```text
PYTHONPATH=. ../.venv/bin/python -c 'from datetime import date; from decimal import Decimal; from app.ingestion.xlsx_normalizer import parse_gl_xlsx; p=parse_gl_xlsx(open("/Users/adinayak18/Downloads/GL Dump Q1.XLSX","rb").read(), date(2025,4,1), date(2025,6,30)); lines=[line for entry in p for line in entry.lines]; print({"rows":len(lines),"documents":len(p),"accounts":len({line.ledger_account_code for line in lines}),"signed_total":sum((line.amount for line in lines),Decimal("0")),"min_date":min(entry.posting_date for entry in p),"max_date":max(entry.posting_date for entry in p),"om_rows":[line.source_row_number for line in lines if line.source_row_number==28096 and line.quantity is None]})'
```

```text
{'rows': 59167, 'documents': 10914, 'accounts': 445, 'signed_total': Decimal('0.00'), 'min_date': datetime.date(2025, 4, 1), 'max_date': datetime.date(2025, 6, 30), 'om_rows': [28096]}
```

Whitespace check:

```text
git diff --check
```

```text
(no output; exit code 0)
```

Compilation:

```text
PYTHONPATH=. ../.venv/bin/python -m compileall -q app tests
```

```text
(no output; exit code 0)
```

The three/four warnings are the existing FastAPI/Starlette deprecations. The
supplied workbook was available and passed the smoke check; the corresponding
local-path regression remains skipped when that external fixture is absent.
