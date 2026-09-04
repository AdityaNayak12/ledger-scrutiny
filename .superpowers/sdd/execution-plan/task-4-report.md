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
