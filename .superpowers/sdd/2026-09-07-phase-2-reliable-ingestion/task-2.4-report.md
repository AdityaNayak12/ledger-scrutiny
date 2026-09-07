# Task 2.4 — Replacement, duplicate, and period-isolation evidence

## Status

Complete. Task 2.4 now has regression coverage for the missing replay-scope and corrupted-baseline cases, plus stronger assertions around atomic replacement, persisted lineage, retained rows, and readiness. The required focused command and the full backend suite pass.

## Scope and evidence basis

- Read the Task 2.4 brief and the full plan at `docs/superpowers/plans/2026-09-07-phase-2-reliable-ingestion.md`.
- Preserved the pre-existing dirty Phase 1/demo changes in the checkout.
- Used the indexed codebase graph at project `Users-adinayak18-Desktop-ledger-scrutiny`, generation `2026-09-06T18:25:28Z`, Tier 2 verification.
- Checked coverage for all operated-on ingestion, router, model, and test paths. No recorded coverage gaps were reported. `backend/app/ingestion/xlsx_normalizer.py` had changed metadata, so its behavior was read directly from source; graph coverage remains best-effort rather than proof of completeness.

## Files changed

Production changes were limited to existing ingestion boundaries:

- `backend/app/ingestion/batches.py`
  - An exact-SHA replay is now idempotent only when its normalized coverage, source family, and batch kind match the existing batch.
  - A same-entity hash reused for another period, family, or kind raises `BatchConflictError` without creating a batch or mutating the existing one.
- `backend/app/ingestion/baseline.py`
  - Active exact-SHA baseline replays now re-parse the source and compare the complete persisted checkpoint key/value/metadata set before returning the stored report.
  - Missing, altered, foreign, or otherwise mismatched checkpoint children are rejected without repopulation or active-batch mutation.

Regression/assertion coverage:

- `backend/tests/test_ingestion_pipeline.py`
  - Added mismatched-period and mismatched-source-family replay rejection.
  - Added an injected post-supersession failure test proving the prior active status, report/fingerprint, and journal row survive commit while the candidate remains staged.
- `backend/tests/test_dataset_resolver.py`
  - Confirmed corrected-quarter selection preserves the original and later-quarter rows in storage and keeps ordered source lineage.
  - Added entity/year/source-family isolation coverage.
  - Added missing-baseline assertions proving openings remain unknown and readiness remains `PARTIAL`.
- `backend/tests/test_baseline_ingestion.py`
  - Added missing/altered active-checkpoint replay cases proving rejection without repopulation and report/status mutation.
- `backend/tests/test_api_integration.py`
  - Strengthened exact replay and malformed replacement cases to compare persisted active reports and exact journal-entry counts.

No new abstraction, schema, route, or unrelated production refactor was added. `backend/app/routers/scrutiny.py` and the resolver required no production change after the regressions were run.

## TDD evidence

### RED/GREEN cycles

1. `PYTHONPATH=. ../.venv/bin/pytest -q tests/test_ingestion_pipeline.py -k exact_hash_replay_with_different_scope`
   - RED: 2 failed; both mismatched replays were incorrectly returned instead of rejected.
   - After the minimal `batches.py` fix: GREEN, 2 passed.
2. `PYTHONPATH=. ../.venv/bin/pytest -q tests/test_ingestion_pipeline.py -k 'activation_failure_after_supersession or exact_hash_replay_with_different_scope'`
   - GREEN: 3 passed.
3. `PYTHONPATH=. ../.venv/bin/pytest -q tests/test_baseline_ingestion.py -k active_baseline_replay_rejects_corrupt_children`
   - RED: 2 failed; the active baseline replay returned its stored report despite missing/altered children.
   - After the minimal replay-integrity check in `baseline.py`: GREEN with the existing valid-replay case, 3 passed in the combined narrow run.
4. Resolver/API narrow checks after assertion strengthening were GREEN:
   - Resolver accumulation/correction/annual cases: 3 passed.
   - Entity/year/family isolation: 1 passed.
   - Missing baseline/openings: 1 passed.
   - Replay/malformed API cases: 6 passed.

## Verification results

- Brief command from `backend/`:

  `PYTHONPATH=. ../.venv/bin/pytest -q tests/test_ingestion_pipeline.py tests/test_dataset_resolver.py tests/test_baseline_ingestion.py tests/test_api_integration.py`

  **97 passed, 3 warnings, 7.92s.**

- Full backend suite from `backend/`:

  `PYTHONPATH=. ../.venv/bin/pytest -q`

  **243 passed, 4 warnings, 21.69s.**

- `git diff --check`: passed with no whitespace errors.

Warnings are pre-existing FastAPI/Starlette deprecations (`httpx` TestClient, `on_event`, and one deprecated 422 constant); no new warning was introduced by Task 2.4.

## Acceptance checklist

- Exact active replays add no canonical children: covered by existing Tally counts, existing GL replay validation, and the new baseline integrity cases.
- Corrupted replay children are rejected without repopulation: existing Tally/GL missing/altered/foreign/no-child cases plus new baseline missing/altered cases; persisted active reports/statuses remain unchanged.
- Corrected quarter replaces only its selected coverage: resolver selects correction plus Q2, and both original and Q2 journal rows remain queryable.
- Malformed replacement preserves prior dataset: existing API Tally replacement test now compares the complete persisted active report and exact old/new journal counts.
- Injected activation failure is atomic after supersession has started: new lifecycle regression verifies old active status, report/fingerprint, journal row, and active-ID count after commit.
- Non-overlapping accumulation, overlap rejection, and annual replacement: existing lifecycle/resolver tests run in the focused command.
- Entity/year/source-family isolation: new resolver regression plus existing same-year source-family conflict regression.
- Missing coverage/baseline blocks readiness and does not imply zero openings: existing API scrutiny-block test, existing partial-coverage resolver tests, and new unknown-opening assertion.

## Defects and decisions

1. **Defect:** shared staging accepted a same-entity content hash regardless of requested coverage/family/kind. **Decision:** reject the replay at the shared lifecycle boundary; preserve the original batch unchanged.
2. **Defect:** active baseline exact replays trusted persisted `BalanceCheckpoint` children and returned the prior report without validation. **Decision:** validate the complete child set and fail closed; never silently repopulate or downgrade the active batch.
3. Existing virtual resolver tests intentionally model historical active rows without running lifecycle transitions, so corrected-quarter evidence asserts resolver replacement metadata and physical row retention rather than forcing a `SUPERSEDED` status in that fixture.

## Concerns / remaining gates

- No live Tally endpoint or browser smoke was run; Task 2.4 evidence is fixture/API/database based.
- The graph reports no recorded gaps, but its coverage signal is explicitly best-effort.
- The report path is ignored by `.superpowers/sdd/.gitignore`; it must be force-added when staging the Task 2.4 commit.
- The initial sandbox-only staging attempt was blocked before staging with `fatal: Unable to create '/Users/adinayak18/Desktop/ledger-scrutiny/.git/index.lock': Operation not permitted`. An approved escalation then staged only the owned files and created the requested commit with message `test: prove ingestion replacement and replay isolation`.
- Unrelated pre-existing changes remain untouched: the deleted Phase 1 report, demo/frontend edits, `.codebase-memory/`, and `backend/tests/test_demo_gl_contract.py`/`frontend/src/demo_gl_profile.json`.
