# Task 5 implementation report

## Round 1 fixes

Fix commit: `9233541bf6d02d58fcce9b57b4194096ca6554e4`

- Baseline headers are exact, case-sensitive `Account Code`, `Balance Date`, `Signed Balance`, and `Currency` names. Alias headers are rejected, and the fixed profile requires those headers on worksheet row 1.
- Baseline dataset fingerprints now include sorted account-code/signed-balance pairs, balance date, and currency, preventing balanced schedules with different account balances from colliding.
- Route follow-up remains unchanged: FastAPI baseline upload wiring is outside this worker's owned implementation files.

## Verification

Focused baseline tests:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. ../.venv/bin/pytest -q -p no:cacheprovider tests/test_baseline_ingestion.py
15 passed, 3 warnings in 0.18s
```

Full backend, compileall, and diff check:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. ../.venv/bin/pytest -q -p no:cacheprovider
109 passed, 4 warnings in 14.57s
../.venv/bin/python -m compileall -q app
git diff --check
full_compile_diff=passed
```

The warnings are existing FastAPI/Starlette deprecations; no test or compile failures occurred.

## Round 2 fixes

Fix commit: `021a56a484fda7c422cc1b629550b0e098183d0d`

- Header parsing no longer searches rows 1–15. Headers outside the documented row-1 position, including row 2 and row 16, are rejected.
- The public normalizer hashes caller bytes before parsing and requires an exact match with the staged `ImportBatch.content_sha256`. A mismatch fails the staged batch with a JSON-safe report while retaining staged raw bytes and optional signed-PDF evidence; no checkpoints are written.
- Exact-SHA ACTIVE replays return the existing JSON-safe report as a no-op, including after the batch is reloaded without the staging duplicate marker.

## Round 2 verification

Focused baseline tests:

```text
venv/bin/python -m pytest tests/test_baseline_ingestion.py -q
18 passed, 3 warnings in 0.22s
```

Full backend tests:

```text
venv/bin/python -m pytest -q
5 failed, 107 passed, 4 warnings in 13.22s
```

The five failures are the existing migration tests. Their subprocess fails before migration execution because this environment's venv has no `alembic.__main__`:

```text
/Users/adinayak18/Desktop/ledger-scrutiny/backend/venv/bin/python: No module named alembic.__main__; 'alembic' is a package and cannot be directly executed
```

Compile and whitespace checks:

```text
venv/bin/python -m compileall -q app tests
exit 0 (no output)
git diff --check
exit 0 (no output)
```

The route/API wiring remains the documented follow-up outside this worker's two implementation files. No unresolved Task 5 baseline concern remains beyond the pre-existing migration-test environment blocker above.

## Round 3/4 fix

- Baseline normalization now validates `ImportBatch.kind == "balance_checkpoint"` before the ACTIVE exact-SHA replay fast path, so an active journal batch cannot be reported as an idempotent baseline replay.
- Added a regression test proving an active journal batch with matching bytes is rejected and persists no balance checkpoints; the active balance-checkpoint replay test remains idempotent.

## Round 3/4 verification

Graph generation `2026-09-05T09:58:18Z`: `backend/app/ingestion/baseline.py` and `backend/tests/test_baseline_ingestion.py` both reported `no_recorded_issue` / `metadata_match` (best-effort coverage signal).

Focused baseline tests:

```text
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_baseline_ingestion.py
19 passed, 3 warnings in 0.22s
```

Full backend tests:

```text
PYTHONPATH=. ../.venv/bin/pytest -q
113 passed, 4 warnings in 14.44s
```

Compile and whitespace checks:

```text
../.venv/bin/python -m compileall -q app tests
exit 0 (no output)
git diff --check
exit 0 (no output)
```

The warnings are existing FastAPI/Starlette deprecations; no test or compile failures occurred.

## Round 5 final fixes

- Baseline normalization now derives completeness from the entity's full external-code master, so `expected_account_codes` cannot narrow the required set.
- `balance_checkpoint` baseline normalization now requires `source_family == "gl_upload"` before active replay, evidence handling, or workbook parsing; rejected Tally batches retain their staged lifecycle and artifacts.
- Baseline row-1 header validation now rejects nonblank headers outside the four exact required names while allowing blank trailing cells.
- Added regressions for caller-supplied account subsets, Tally checkpoint rejection, extra headers, and blank trailing header cells.

## Round 5 verification

Focused baseline tests:

```text
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_baseline_ingestion.py
23 passed, 3 warnings in 0.26s
```

Full backend tests:

```text
PYTHONPATH=. ../.venv/bin/pytest -q
117 passed, 4 warnings in 14.64s
```

Compile and whitespace checks:

```text
../.venv/bin/python -m compileall -q app tests
exit 0 (no output)
git diff --check
exit 0 (no output)
```

The warnings remain existing FastAPI/Starlette deprecations; no test or compile failures occurred.
