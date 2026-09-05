# Task 5 implementation report

## Round 1 fixes

Fix commit: `9233541bf6d02d58fcce9b57b4194096ca6554e4`

- Baseline headers are now exact, case-sensitive `Account Code`, `Balance Date`, `Signed Balance`, and `Currency` names. Alias headers are rejected, while the existing fixed-profile first-15-row header scan remains unchanged.
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
