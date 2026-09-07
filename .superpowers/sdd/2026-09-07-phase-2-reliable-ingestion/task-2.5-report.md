# Task 2.5 — Tally/GL scrutiny parity evidence

## Status

Complete. The adjacent integration regression proves that equivalent complete Tally and fixed-profile GL inputs produce equal canonical account movements, openings, derived closings, and deterministic scrutiny findings while retaining independent source lineage. One real shared router defect was fixed: scrutiny failed when a GL entity had both an active journal batch and an active baseline batch for the same financial period.

## Scope and evidence basis

- Read the Task 2.5 brief and the full plan at `docs/superpowers/plans/2026-09-07-phase-2-reliable-ingestion.md` before implementation.
- Preserved all unrelated pre-existing Phase 1/demo work in the checkout.
- Used the indexed codebase graph for project `Users-adinayak18-Desktop-ledger-scrutiny`, generation `2026-09-06T18:25:28Z`, Tier 2 verification. Coverage checks reported no recorded issue for the test, router, ingestion, dataset, and rule paths used here; modified files were metadata-changed, so their current source was read directly.
- Reused the existing `test_tally_and_gl_canonical_reports_have_matching_journal_totals` parity coverage and the existing `test_compliance_versions_and_review_survive_rerun` Phase 1 rerun/review coverage through the required focused command.

## Files changed

- `backend/tests/test_api_integration.py`
  - Added `test_tally_and_gl_complete_parity_has_equal_scrutiny_inputs_and_findings` adjacent to the existing parity test.
  - Uses separate entities for Tally and GL so the one-source-family-per-entity/year rule is respected.
  - Supplies the same account codes, explicit Tally `PARENT` classifications, explicit GL account classifications keyed by external code, equivalent signed opening balances, and equivalent signed journal movements.
  - Registers the GL structured opening baseline through the existing shared `stage_import_batch`/`normalize_balance_checkpoint_xlsx` workflow; no new baseline route or test abstraction was added.
  - Compares account-level signed movements, opening balances, derived closing balances, eligible readiness, and deterministic findings while excluding generated finding IDs/timestamps and source-specific provenance.
  - Verifies each persisted `ScrutinyRun` retains its own actual ordered `source_batch_ids` and dataset fingerprint, and verifies each batch retains its own source family and raw bytes. The Tally and GL dataset fingerprints are intentionally not required to match.
- `backend/app/routers/scrutiny.py`
  - Added the minimal active-journal filter to the `ScrutinyRun.import_batch_id` lookup, accepting both current `journal` and legacy `NULL` kinds while excluding `balance_checkpoint` batches.
- `.superpowers/sdd/2026-09-07-phase-2-reliable-ingestion/task-2.5-report.md`
  - This evidence report.

## TDD evidence

1. New regression first ran RED on a formatting-only expectation: Tally returned SQLite-decimal strings with two fractional places while GL returned equivalent strings without trailing zeroes. No production defect was inferred; the assertion was corrected to compare canonical `Decimal` values.
2. The corrected regression then ran RED with `sqlalchemy.exc.MultipleResultsFound`. `trigger_scrutiny_run` selected both the active GL journal and active GL balance-checkpoint batch for the same financial period through `scalar_one_or_none()`.
3. The minimal router filter was added, and the new regression ran GREEN.
4. The fixture was adjusted to use a nonzero materiality threshold so the existing default TDS warning did not obscure the intended deterministic creditor finding. No rule behavior was changed.

## Verification results

- New regression:

  `PYTHONPATH=. ../.venv/bin/pytest -q tests/test_api_integration.py::test_tally_and_gl_complete_parity_has_equal_scrutiny_inputs_and_findings`

  **1 passed, 4 warnings.**

- Exact command from the brief:

  `PYTHONPATH=. ../.venv/bin/pytest -q tests/test_api_integration.py -k 'parity or canonical_reports or compliance_versions_and_review or readiness'`

  **4 passed, 32 deselected, 4 warnings.**

- Full backend suite after the production fix:

  `PYTHONPATH=. ../.venv/bin/pytest -q`

  **249 passed, 5 warnings.**

- `../.venv/bin/python -m py_compile tests/test_api_integration.py`: passed.
- `git diff --check`: run before commit; no whitespace errors.

Warnings are the existing FastAPI/Starlette deprecations plus the pre-existing GL fallback warning at `backend/app/routers/scrutiny.py:1300` when the rule engine receives derived in-memory snapshots. The warning does not change the parity result and was not expanded into an unrelated production refactor.

## Acceptance checklist

- Equivalent complete Tally/GL accounting inputs: covered with separate entities and the same two external account codes.
- Explicit classification: Tally group hierarchy is supplied by XML `PARENT`; GL classification is supplied explicitly before import by external-code mapping. No GL classification is inferred from account names.
- Equal per-account signed movements: covered for `1000` (`+100`) and `2000` (`-100`) using `Decimal` comparison.
- Equal account openings and derived closings: covered for openings `+50/-50` and closings `+150/-150`.
- Eligible readiness: both resolved datasets are asserted as `READY` or `READY_WITH_WARNINGS`, and both scrutiny routes return HTTP 200.
- Equal deterministic findings: both entities produce the same creditor-balance finding after excluding generated IDs/timestamps; the finding is also asserted explicitly.
- Independent ordered lineage: each stored run is checked against its own resolver-selected ordered source-batch IDs and dataset fingerprint; raw source bytes and source families are checked independently.
- Phase 1 status/note preservation: existing rerun/review regression is selected and passes in the exact focused command.
- No source-specific scrutiny logic: no rule or dataset production code was changed; only the shared journal-batch selection defect was fixed.

## Defect and decision

**Defect:** A complete GL dataset has an active journal batch and an active balance-checkpoint batch sharing one `FinancialPeriod`. The scrutiny route queried active batches by period alone and required exactly one result, so it raised `MultipleResultsFound` before running rules.

**Decision:** Select only active journal batches for `ScrutinyRun.import_batch_id`, while keeping both journal and baseline IDs in the resolver’s ordered `source_batch_ids`. This preserves the run’s primary journal association and the complete provenance list with the smallest shared fix.

## Remaining note

The current repository has no separate public baseline HTTP endpoint; the test uses the existing shared baseline ingestion function that the GL workflow already relies on. Adding or redesigning that route is outside Task 2.5 ownership.
