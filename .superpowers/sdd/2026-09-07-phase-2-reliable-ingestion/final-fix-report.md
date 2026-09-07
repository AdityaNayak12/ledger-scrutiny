# Phase 2 final-fix report

**Date:** 2026-09-08
**Scope:** Resolve final-review findings 1–4 and final-fix-review Important finding I1 at the shared GL ingestion boundaries.
**Execution:** Delegated implementation and review; all subagents used GPT-5.6 Luna with xhigh reasoning.

## Result

Findings 1–4 and Important finding I1 are resolved with the existing batch, normalizer, response, auth, and PDF-evidence patterns. No schema, migration, dependency, upload-size, DNS, or unrelated frontend-demo changes were added.

## Commit status

The initial fixes are recorded in `5732ad1`; this follow-up records the shared checkpoint-lifecycle fixes. Pre-existing demo/frontend changes remain unstaged.

## Fixes

### Finding 1 — public GL baseline intake

- Added the protected multipart route `POST /entities/{entity_id}/upload-xlsx/baseline` in `backend/app/routers/scrutiny.py`.
- The route accepts `target_period_start`, `target_period_end`, optional `expected_balance_date`, optional `expected_currency`, an opening-baseline XLSX, and optional `signed_pdf` evidence.
- It stages a `balance_checkpoint` with `create_import_batch`, normalizes/retains evidence with `normalize_balance_checkpoint_xlsx`, and activates through `activate_import_batch`.
- Responses use `IngestionResponse` and include readiness, baseline coverage, gaps, active/source batch IDs, dataset fingerprint, and source lineage.
- Invalid baseline replacements retain the prior active journal/dataset; failed batches retain their source workbook and validation report.
- Existing `XlsxUploadModal` now offers optional opening-baseline XLSX and signed PDF inputs. Non-mock submission sends the journal to the existing confirm route, then the baseline to the public baseline route. Mock submission remains on the existing synthetic result/timing path.
- The complete Tally/GL parity integration now uploads the journal and baseline through public routes, checks `READY`, runs scrutiny successfully, verifies duplicate idempotence, and verifies signed-PDF evidence retention.

### Finding 2 — baseline hard-failure accounting

- Baseline failure reports now set `rejected_rows = input_rows - skipped_rows` when a hard failure occurs, with `accepted_rows = 0`.
- Every non-skipped source row receives a `{row, reason}` entry, including rows after the first hard row error.
- Header failures now establish the post-header row count before header validation when the workbook is readable.
- Added regressions for a row error, global signed-total failure, and missing/unknown account failure; each asserts `input_rows = accepted_rows + skipped_rows + rejected_rows` and row-level reasons.

### Finding 3 — GL provenance binding

- Before parsing or writing children, `normalize_gl_xlsx` now verifies the staged batch is owned by the requested entity, `STAGED`, `journal`, `gl_upload`, and SHA-256-equal to the supplied bytes.
- A staged kind/family/hash mismatch fails that batch with an invalid zero-row report and writes no journal children. Existing terminal-status and entity-ownership fail-closed behavior remains unchanged.
- Added direct regressions for mismatched bytes and wrong batch kind/source family.

### Finding 4 — activation source-family isolation

- `activate_import_batch` now applies the financial-year source-family conflict check across active journal and checkpoint batches.
- A GL checkpoint cannot activate beside an active Tally journal in the same financial year.
- A GL checkpoint can activate beside a GL journal; journal overlap/replacement rules remain journal-only.
- Added both same-family allow and mixed-family reject regressions.

### Important finding I1 — failed public baseline uploads retain signed-PDF evidence

- The public baseline route now reapplies the uploaded signed PDF with the existing `_retain_pdf_evidence` helper after savepoint rollback in both normal and unexpected failure branches.
- Failed batches still receive the pre-rollback validation report and retain the original workbook; the active journal/dataset is not changed.
- The public invalid-replacement regression now uploads signed PDF evidence and asserts exact filename, content type, SHA-256, base64 bytes, workbook bytes, failure report, and active-journal preservation.

## Verification evidence

### RED

The new regressions failed before implementation:

```text
8 failed, 3 warnings in 0.61s
```

The failures covered the three baseline accounting cases, GL wrong kind/family, GL mismatched bytes, mixed-family checkpoint activation, and the missing public baseline route.

### Focused GREEN

```bash
cd backend
PYTHONPATH=. ../.venv/bin/pytest -q \
  tests/test_baseline_ingestion.py::test_baseline_failure_report_accounts_every_source_row \
  tests/test_xlsx_ingestion.py::test_fixed_gl_normalizer_rejects_wrong_staged_batch_contract_without_children \
  tests/test_xlsx_ingestion.py::test_fixed_gl_normalizer_rejects_mismatched_staged_bytes_without_children \
  tests/test_ingestion_pipeline.py::test_checkpoint_activation_rejects_mixed_source_family_for_same_financial_year \
  tests/test_ingestion_pipeline.py::test_checkpoint_activation_allows_same_gl_source_family_as_journal \
  tests/test_api_integration.py::test_tally_and_gl_complete_parity_has_equal_scrutiny_inputs_and_findings
```

Result: `9 passed`.

The added public invalid-replacement regression also passed:

```text
1 passed, 3 warnings in 0.38s
```

### I1 RED/GREEN

The new signed-PDF assertions first reproduced the review finding:

```text
1 failed, 3 warnings in 0.38s
KeyError: 'supporting_evidence'
```

After the route fix, the same public-route regression passed:

```text
1 passed, 3 warnings in 0.27s
```

The complete relevant ingestion/API suite also passed after the fix:

```text
129 passed, 4 warnings in 18.16s
```

### Full backend

```bash
cd backend
PYTHONPATH=. ../.venv/bin/pytest -q
```

Result: `257 passed, 5 warnings in 22.56s`.

The focused rerun after the activation-report regression fix passed `6 passed`; the full suite then passed with the result above.

### Frontend

```bash
cd frontend
npm run lint
npm run build
```

Result: lint passed; Vite production build passed (`19 modules transformed`, `446ms`).

## Remaining deployment gates

Final-review findings 5–6 remain explicit deployment gates and were not implemented in this round, per scope:

5. **Upload/response size limits:** deployment must enforce documented maximum multipart upload and Tally response bytes before buffering/persistence, plus archive/XML expansion protections. The application still has no such limit.

6. **DNS resolution/pinning:** if the threat model includes DNS compromise or hostile network control, the approved deployment must validate/pin resolved addresses or place Tally behind an egress filter/proxy. The current exact-host allowlist contract remains unchanged.

Live Tally topology and browser file-driven smoke gates remain unverified, as in the final review.

## Round-2b — final-rereview-2 follow-up

### Implementation

- Updated the shared `activate_import_batch` boundary to explicitly flush staged checkpoint children before deriving candidate balance dates. The flush is limited to `balance_checkpoint` activation, so existing journal activation behavior and transaction semantics are unchanged.
- Added a direct lifecycle regression that stages old and replacement GL checkpoint children without an intervening flush and verifies the old batch is superseded and the candidate becomes active with its children persisted.
- Added a public baseline failure-injection regression. It raises after activation has flushed candidate children and transitioned candidate/old statuses, then verifies savepoint rollback preserves the old checkpoint's `ACTIVE` status, dataset fingerprint, and exact children while the candidate is retained as `FAILED` with an invalid report and no persisted children.
- Retained the existing invalid-source preservation regression unchanged.

### Verification evidence

Focused RED before the shared-boundary fix:

```text
tests/test_ingestion_pipeline.py::test_checkpoint_activation_replaces_pending_candidate_children
FAILED — old.status was ACTIVE instead of SUPERSEDED
```

Focused GREEN after the fix:

```text
tests/test_ingestion_pipeline.py::test_checkpoint_activation_replaces_pending_candidate_children — 1 passed
tests/test_api_integration.py::test_public_baseline_activation_failure_preserves_old_checkpoint_and_dataset — 1 passed
```

Relevant backend suite:

```bash
cd backend
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_ingestion_pipeline.py tests/test_api_integration.py
```

Result: `71 passed, 4 warnings in 9.12s`.

`git diff --check` also passed. The follow-up implementation is recorded separately from `5732ad1`.

## Workspace hygiene

Pre-existing user-owned changes were preserved, including `docs/pitch-demo.md`, `frontend/src/App.tsx`, the existing demo portions of `frontend/src/components/XlsxUploadModal.tsx`, `frontend/src/mockData.ts`, the deleted Phase 1 report, `.codebase-memory/`, `backend/tests/test_demo_gl_contract.py`, and `frontend/src/demo_gl_profile.json`. No unrelated files were staged.
