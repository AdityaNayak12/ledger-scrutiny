# Task 2.4 — Replacement, duplicate, and period-isolation evidence

## Status

Complete after review fix round 2. Task 2.4 now has regression coverage for the missing replay-scope and corrupted-baseline cases, plus stronger assertions around atomic replacement, persisted lineage, retained rows, readiness, and unrelated IntegrityError propagation. The review findings I1, I2, M1, and M2 are addressed. The required focused command and the full backend suite pass.

## Scope and evidence basis

- Read the Task 2.4 brief and the full plan at `docs/superpowers/plans/2026-09-07-phase-2-reliable-ingestion.md`.
- Preserved the pre-existing dirty Phase 1/demo changes in the checkout.
- Used the indexed codebase graph at project `Users-adinayak18-Desktop-ledger-scrutiny`, generation `2026-09-06T18:25:28Z`, Tier 2 verification.
- Checked coverage for all operated-on ingestion, router, model, and test paths. No recorded coverage gaps were reported. `backend/app/ingestion/xlsx_normalizer.py` had changed metadata, so its behavior was read directly from source; graph coverage remains best-effort rather than proof of completeness.

## Files changed

Production changes were limited to existing ingestion boundaries:

- `backend/app/ingestion/batches.py`
  - Centralized exact-SHA duplicate scope validation now checks stored/requested financial period, normalized coverage (with period-date fallback), source family, and batch kind.
  - The same validator runs on both the initial hash lookup and the IntegrityError uniqueness-race reload path.
  - The IntegrityError reload is now reached only for the ImportBatch entity/content-hash uniqueness violation; unrelated FK/constraint failures re-raise before duplicate reload.
  - A same-entity hash reused for another period, family, or kind raises `BatchConflictError` without creating a batch or mutating the existing one.
- `backend/app/ingestion/baseline.py`
  - Active exact-SHA baseline replays now re-parse the source and compare the complete persisted checkpoint key/value/metadata set, including `BalanceCheckpoint.entity_id`, before returning the stored report.
  - Missing, altered, foreign, or otherwise mismatched checkpoint children are rejected without repopulation or active-batch mutation.

Regression/assertion coverage:

- `backend/tests/test_ingestion_pipeline.py`
  - Added explicit coverage mismatch rejection and a mismatched-coverage uniqueness-race regression.
  - Extended scope rejection to assert batch-kind isolation alongside period and source-family isolation.
  - Added a same-hash race regression with invalid `uploaded_by_user_id` and a SQLAlchemy-wrapped SQLite FK `IntegrityError`, asserting the error propagates and no success duplicate is created.
  - Added an injected post-supersession failure test proving the prior active status, report/fingerprint, and journal row survive commit while the candidate remains staged.
- `backend/tests/test_dataset_resolver.py`
  - Confirmed corrected-quarter selection preserves the original and later-quarter rows in storage and keeps ordered source lineage.
  - Added entity/year/source-family isolation coverage.
  - Added missing-baseline assertions proving openings remain unknown and readiness remains `PARTIAL`.
- `backend/tests/test_baseline_ingestion.py`
  - Added missing/altered active-checkpoint replay cases proving rejection without repopulation and report/status mutation.
  - Added a foreign-entity checkpoint regression and exact checkpoint snapshots proving the corrupted child remains unchanged.
- `backend/tests/test_api_integration.py`
  - Added exact snapshots across journal entries, journal lines, checkpoints, transactions, and trial-balance snapshots for altered Tally replay cases.
  - Added exact journal-child snapshots around direct GL replay validation while retaining the HTTP 409/status/fingerprint assertions.

No new schema, route, or unrelated production refactor was added. The one small private duplicate-scope validator is required to share the same checks across both staging paths. `backend/app/routers/scrutiny.py` and the resolver required no production change after the regressions were run.

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

5. Review fix round 1 RED/GREEN cycles:
   - Explicit coverage replay RED: `PYTHONPATH=. ../.venv/bin/pytest -q tests/test_ingestion_pipeline.py -k 'racing_coverage or explicit_coverage'` → 1 failed (the explicit-coverage regression).
   - Uniqueness-race coverage RED: `PYTHONPATH=. ../.venv/bin/pytest -q tests/test_ingestion_pipeline.py::test_duplicate_integrity_error_with_mismatched_coverage_is_rejected` → 1 failed.
   - Foreign baseline child RED: `PYTHONPATH=. ../.venv/bin/pytest -q tests/test_baseline_ingestion.py::test_active_baseline_replay_rejects_foreign_entity_checkpoint_without_mutation` → 1 failed.
   - Shared staging GREEN: `PYTHONPATH=. ../.venv/bin/pytest -q tests/test_ingestion_pipeline.py -k 'duplicate_integrity_error or exact_hash_replay_with_different_scope or explicit_coverage'` → 6 passed.
   - Baseline GREEN: `PYTHONPATH=. ../.venv/bin/pytest -q tests/test_baseline_ingestion.py -k 'active_exact_sha or active_baseline_replay'` → 5 passed.
   - Exact Tally/GL snapshot checks: `PYTHONPATH=. ../.venv/bin/pytest -q tests/test_api_integration.py -k 'tally_duplicate_replay_rejects_corrupt_canonical_children or fixed_gl_duplicate_replay_validates_canonical_children'` → 4 passed.
6. Review fix round 2 I1 RED/GREEN cycle:
   - RED with the recovery guard temporarily bypassed: `PYTHONPATH=. ../.venv/bin/pytest -q tests/test_ingestion_pipeline.py::test_unrelated_foreign_key_integrity_error_is_not_treated_as_duplicate` → 1 failed (`DID NOT RAISE`; the same-hash batch was incorrectly returned as a duplicate).
   - GREEN after restoring the guard immediately inside the `except IntegrityError` block: the same command → 1 passed.

## Verification results

- Brief command from `backend/`:

  `PYTHONPATH=. ../.venv/bin/pytest -q tests/test_ingestion_pipeline.py tests/test_dataset_resolver.py tests/test_baseline_ingestion.py tests/test_api_integration.py`

  **102 passed, 3 warnings, 8.59s.**

- Full backend suite from `backend/`:

  `PYTHONPATH=. ../.venv/bin/pytest -q`

  **248 passed, 4 warnings, 22.60s.**

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
- Duplicate scope checks cover period, explicit/fallback coverage, source family, batch kind, initial lookup, and uniqueness-race reload.
- Active baseline replay checks child ownership and leaves the foreign-owned child, active report, and active batch unchanged on rejection.

## Defects and decisions

1. **Defect:** shared staging accepted a same-entity content hash regardless of requested coverage/family/kind. **Decision:** reject the replay at the shared lifecycle boundary; preserve the original batch unchanged.
2. **Defect:** active baseline exact replays trusted persisted `BalanceCheckpoint` children and returned the prior report without validation. **Decision:** validate the complete child set and fail closed; never silently repopulate or downgrade the active batch.
3. Existing virtual resolver tests intentionally model historical active rows without running lifecycle transitions, so corrected-quarter evidence asserts resolver replacement metadata and physical row retention rather than forcing a `SUPERSEDED` status in that fixture.
4. **Review I1/M1:** keep one private validator at the shared staging boundary and invoke it before insertion and after a uniqueness collision reload. Compare the duplicate’s stored coverage with normalized requested coverage, not only financial-period dates.
5. **Review I2:** include `entity_id` in the baseline canonical-child tuple. This catches a checkpoint moved to another entity even when account/date/value/metadata still match.
6. **Review M2:** exact child snapshots are test-only assertions. The GL HTTP test retains endpoint semantics; the exact child snapshot is taken around the direct shared GL replay validator because the SQLite TestClient fixture’s error rollback makes a second-session post-error database snapshot unavailable for that route.

7. **Review I1 fix round 2:** the focused regression uses an existing same-hash batch, invalid `uploaded_by_user_id=999999`, and the observed SQLAlchemy/SQLite FK error payload. Local SQLite reports the composite UNIQUE violation first when both constraints are submitted together, so the test injects that unrelated FK `IntegrityError` at the target flush boundary while preserving the real race/replay setup. The production guard classifies the error before reload and re-raises this FK failure.

## Concerns / remaining gates

- No live Tally endpoint or browser smoke was run; Task 2.4 evidence is fixture/API/database based.
- The GL integration fixture has a pre-existing session/rollback interaction: after the GL duplicate-error HTTP path, a second `TestingSessionLocal` can observe an empty in-memory database. The endpoint still returns the expected 409 with active IDs/fingerprint; the exact child no-mutation guarantee is asserted directly against `validate_gl_xlsx_replay` in the same session. Fixing that fixture/router behavior would exceed the requested production ownership boundary.
- The graph reports no recorded gaps, but its coverage signal is explicitly best-effort.
- The report path is ignored by `.superpowers/sdd/.gitignore`; it must be force-added when staging the Task 2.4 commit.
- The initial sandbox-only staging attempt was blocked before staging with `fatal: Unable to create '/Users/adinayak18/Desktop/ledger-scrutiny/.git/index.lock': Operation not permitted`. An approved escalation then staged only the owned files and created the requested commit with message `test: prove ingestion replacement and replay isolation`.
- This fix round was not committed or staged, per controller instruction. Current Task 2.4 edits are limited to `backend/app/ingestion/batches.py`, `backend/tests/test_ingestion_pipeline.py`, and this report; unrelated dirty files remain untouched.
- Unrelated pre-existing changes remain untouched: the deleted Phase 1 report, demo/frontend edits, `.codebase-memory/`, and `backend/tests/test_demo_gl_contract.py`/`frontend/src/demo_gl_profile.json`.
