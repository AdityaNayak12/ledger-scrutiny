# Task 1 Report: Compose packs without duplicating TDS

## Implementation

- Added `backend/app/rules/compliance.py` as the explicit compliance pack boundary.
- Moved the existing `tds_liability_check` implementation unchanged into the compliance module.
- Added typed `run_compliance_checks(...) -> list[AuditException]`, currently dispatching to the single TDS implementation.
- Updated `backend/app/rules/engine.py` to import the compliance dispatcher and re-export `tds_liability_check`.
- Updated `rule_set_version` to return the exact deterministic contracts:
  - `None` and unknown packs: `core-v1`
  - `compliance_v1`: `core-v1+compliance-v1`
  - `manufacturing_v1`: `core-v1+compliance-v1+manufacturing-v1`
- Updated `run_scrutiny` so the explicit `rule_pack` argument controls whether TDS runs through compliance; omission does not read `entity.rule_pack`.
- Kept the existing core checks, both manufacturing rules, final materiality filtering, and the legacy engine import path intact.

## Files

- Created: `backend/app/rules/compliance.py`
- Created: `backend/tests/test_compliance_rules.py`
- Modified: `backend/app/rules/engine.py`
- Modified: `backend/tests/test_rules_engine.py`
- No router/API, model, or manufacturing-rule files were modified.
- Existing unrelated untracked `docs/` and `.codebase-memory/` content was preserved.

## TDD RED/GREEN evidence

RED, before implementation:

```text
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_compliance_rules.py tests/test_rules_engine.py
1 collection error: ModuleNotFoundError: No module named 'app.rules.compliance'
3 warnings
```

GREEN, after the minimum implementation:

```text
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_compliance_rules.py tests/test_rules_engine.py
28 passed, 3 warnings in 0.12s
```

The added tests cover the exact pack/version table, deterministic repeated output, TDS evidence characterization, GST-independence for every supported pack, explicit dispatcher routing, and the compliance boundary.

## Tests and outputs

Focused Task 1 plus manufacturing tests:

```text
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_compliance_rules.py tests/test_rules_engine.py tests/test_manufacturing_rules.py
30 passed, 3 warnings in 0.12s
```

Full backend suite:

```text
PYTHONPATH=. ../.venv/bin/pytest -q
226 passed, 4 warnings in 19.79s
```

`git diff --check` passed. The warnings are pre-existing dependency/framework deprecations: Starlette/httpx, FastAPI `on_event`, and Starlette's deprecated HTTP 422 constant.

## Self-review

- TDS has one implementation in `app.rules.compliance`; `engine.py` only imports it for the compatibility re-export and dispatch.
- `compliance.py` does not import `engine.py`, so the dependency direction is one-way.
- Unknown and omitted packs retain the core fallback and run TDS exactly once.
- `compliance_v1` and `manufacturing_v1` route TDS through compliance exactly once.
- Manufacturing rule calls and formulas were left untouched.
- The explicit `rule_pack` argument remains authoritative even when `entity.rule_pack` is set.
- No router/API files were changed.
- No unrelated untracked files were staged or modified.

## Concerns

No new functional concerns. The backend suite still reports four existing deprecation warnings; resolving those is outside Task 1 scope.
