# Phase 2 — Reliable Tally and GL Ingestion Implementation Plan

> **For agentic workers:** Use executing-plans to implement this plan task-by-task after user approval. Steps use checkbox syntax for tracking.

**Status:** Draft for approval; no implementation authorized by this document.

**Goal:** Verify and close the remaining reliability gaps in Tally and transaction-level XLSX ingestion without rebuilding the existing canonical pipeline.

**Architecture:** Keep the existing Tally HTTP/parser/normalizer path and fixed-profile XLSX parser. Both feed canonical journals, immutable import batches, reconciliation, and dataset readiness. Change shared persistence only when a regression proves a defect.

**Tech Stack:** Existing Python, FastAPI, SQLAlchemy, httpx, openpyxl, pytest, React, TypeScript, Vite.

**Spec:** `docs/superpowers/specs/2026-09-04-ledger-ingestion-p0-design.md`; Phase 2 of `docs/superpowers/plans/2026-09-04-ledger-scrutiny-roadmap.md`; current connector contract in `docs/tally-connector.md`.

## Global constraints

- Roadmap window: 21 September–2 October 2026; sequence below is proposed work order, not a fresh duration estimate.
- Preserve immutable import batches, source hashes, rule versions, exception fingerprints, review statuses, and auditor notes.
- Use existing dependencies. No state-management library, rule framework, or provider SDK without a failing requirement.
- Canonical amounts use `Decimal` and debit-positive/credit-negative signs.
- No CSV, legacy `.xls`, automatic column guessing, account-group inference, new tax rules, GST integration, or database redesign.
- Only `READY` and `READY_WITH_WARNINGS` datasets may be passed to scrutiny rules.
- Existing passing behavior needs verification, not a manufactured failing test or rewrite. Any newly discovered defect needs a failing regression before its fix.

## Decisions for approval

1. **Confirmed by user on 7 September: retain the current transaction-level GL format.** Required headers remain `Document Number`, `G/L Account`, `Posting Date`, and `Amount in local currency`. Use signed amounts and a structured account-level prior-year closing baseline. The older four-column balance upload and three sign conventions are incompatible with this implementation and will not be restored.
2. **Recommended: retain hard accounting failures.** Reject unbalanced documents beyond ₹0.01, invalid required amounts/dates, and invalid baselines. Accept metadata/classification warnings. This replaces the old roadmap's instruction to accept unbalanced balance uploads with a warning.
3. **User direction: remote system; exact topology not yet known.** Task 2.2 begins by identifying the backend host, Tally host, and available private connectivity, then proposing one concrete connection approach for approval. Prefer existing managed private connectivity where available; do not assume remote Tally is publicly reachable. Keep explicit local opt-in and exact hostname configuration. Do not silently enable loopback or broadly allow private addresses. If remote access requires a new bridge or agent, scope that separately before implementation.

Format decision 1 is confirmed. Policy 2 is included in this plan's approval request. Remote-connectivity investigation may proceed after plan approval; Task 2.2's deployment-dependent changes wait for a concrete topology decision. No answer or elapsed time counts as approval.

## Existing work to reuse

Source inspection found company/period selection, endpoint validation, disabled redirects, typed/sanitized connector failures, fixed GL parsing, canonical journal persistence, duplicate replay validation, structured reports, and atomic replacement orchestration. Existing tests cover many roadmap cases, including source parity and failed replacements. These are implementation observations, not a claim that tests were run during this planning session.

The Phase 1 execution record reports 232 backend tests passing on 6 September. Establish a fresh baseline before Phase 2 execution.

## Task 2.1 — Reconcile the contract and establish the baseline

**Deliverable:** One agreed ingestion contract and an evidence table identifying existing coverage versus actual gaps.

**Files:** This plan; `docs/superpowers/plans/2026-09-04-ledger-scrutiny-roadmap.md`; `docs/superpowers/specs/2026-09-04-ledger-ingestion-p0-design.md`. Documentation edits occur after approval.

**Interface:** No runtime changes. Contract remains canonical journals plus structured validation/readiness responses.

- [ ] Record the user's format, failure-policy, and deployment decisions above.
- [ ] Update only contradictory Phase 2 and global roadmap statements: fixed transaction GL, signed amounts, baseline requirement, hard document balancing, explicit endpoint configuration. Preserve GST deferral and Phase 1 history.
- [ ] Run the backend and frontend baselines below; record failures and skips explicitly.
- [ ] Map each acceptance case in Tasks 2.2–2.6 to an existing test or a missing regression. Do not duplicate tests that already prove it.

```bash
# From backend/
PYTHONPATH=. ../.venv/bin/pytest -q
# From frontend/, run separately
npm run lint
npm run build
```

**Acceptance:** Approved contract has no competing XLSX formats or balancing policies; fresh command results are recorded.

## Task 2.2 — Verify Tally connectivity and safe failures

**Depends on:** 2.1. Automated verification is independent of remote deployment; connection changes depend on the topology decision below.

**Files:** `backend/tests/test_tally_http.py`, `backend/tests/test_tally_ingestion.py`, `backend/tests/test_api_integration.py`; fix only demonstrated defects in `backend/app/ingestion/tally_http.py`, `backend/app/ingestion/tally_parser.py`, `backend/app/ingestion/tally_normalizer.py`, or `backend/app/routers/scrutiny.py`. Update `docs/tally-connector.md` and `.env.example` only for the agreed deployment configuration.

**Interface:** Preserve `fetch_trial_balance(*, endpoint, company_name, period_start, period_end, timeout_seconds=30) -> tuple[dict, bytes]` and `TallyConnectorError`.

- [ ] Run existing connector cases for timeout, connection/HTTP failure, malformed/rejected XML, absent status, absent closing balance, empty vouchers/ledgers, company/period selection, and credential-safe error handling.
- [ ] Produce a short remote-connectivity proposal identifying where backend and Tally run, who controls the network, whether a managed private route exists, and the exact supported endpoint/configuration. Ask only for environment facts that cannot be discovered. Obtain approval before implementing any new bridge, agent, or network access change; continue the independent automated verification while topology is unresolved.
- [ ] Exercise endpoint policy with the agreed topology: local access requires `TALLY_ALLOW_LOCAL_ENDPOINTS=1`; configured DNS hostnames use exact `TALLY_ALLOWED_HOSTS` entries. Confirm disallowed hosts and redirects cannot bypass it.
- [ ] For any missing case, add one regression using existing monkeypatch transport patterns; assert rejected endpoints never make an HTTP call and errors expose neither endpoint credentials nor source payloads.
- [ ] Implement only fixes needed by those failures; retain raw-response provenance in the batch path.
- [ ] Document configuration and a read-only pilot smoke procedure: selected company/period, reported ledger/document/line counts, and a deliberately unavailable endpoint with a safe error.

```bash
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_tally_http.py tests/test_tally_ingestion.py tests/test_api_integration.py -k tally
```

**Acceptance:** Automated failures are safe and actionable; supported responses preserve accounts, voucher lines, dates and source identifiers. Real Tally compatibility is marked verified only after the pilot smoke run, separately from mock-based tests.

## Task 2.3 — Verify fixed GL parsing and explain every row

**Depends on:** 2.1.

**Files:** `backend/tests/test_xlsx_ingestion.py`; defect-only changes to `backend/app/ingestion/xlsx_normalizer.py`; count clarification in the P0 design and this plan.

**Interface:** Keep `parse_gl_xlsx` and `normalize_gl_xlsx`; retain batch reports and source row numbers.

- [ ] Run existing cases for exact headers within rows 1–15, signed amounts, blank/summary rows, malformed/non-finite/overprecision amounts, invalid dates, out-of-period rows, formula cache behavior, repeated account codes, and unknown classifications.
- [ ] Confirm genuine journal rows cannot be silently classified as summaries; repeated account codes on separate journal lines remain valid.
- [ ] Verify accepted/skipped/rejected counts, skip reasons, warnings, source metadata, and source hash persistence. Required-data errors fail; unresolved classification remains a warning.
- [ ] Run the supplied Q1 workbook regression if its local artifact is available. Its `Sheet1` range is `A1:U59169`: 59,168 post-header input rows are accounted as 59,167 accepted journal lines from source rows 2–59168 plus one skipped source row, row 59169 (`L DESCRIPTION` = `LIABILITY TOTAL`, zero amount; reason `summary/footer row`), with zero rejected rows, 10,914 documents, and 445 accounts. Reconcile these measured counts and original row positions; do not change either expected count merely to make a test pass.
- [ ] Add a small synthetic regression for any uncovered discrepancy; fix the responsible parser branch only. If the real workbook is absent, record that acceptance gate as unverified.

```bash
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_xlsx_ingestion.py
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_xlsx_ingestion.py -k supplied_q1 -rs
```

**Acceptance:** Every input row has an explained outcome; malformed accounting data cannot activate; metadata warnings retain valid journals. Sample totals are supported by measured reconciliation.

## Task 2.4 — Prove replacement, duplicate and period isolation

**Depends on:** 2.2–2.3 for changed ingestion behavior.

**Files:** Existing `backend/tests/test_ingestion_pipeline.py`, `backend/tests/test_dataset_resolver.py`, `backend/tests/test_baseline_ingestion.py`, and `backend/tests/test_api_integration.py`. Candidate defect locations: `backend/app/ingestion/batches.py`, `backend/app/routers/scrutiny.py`, and the existing resolver/baseline code discovered during execution. No new lifecycle abstraction.

**Interface:** Existing staged activation, source-family exclusivity, source hashes, ordered source-batch IDs, fingerprint, and readiness states.

- [ ] Verify exact active replays add no journals; corrupted replay children are rejected without repopulation or mutation.
- [ ] Verify a valid corrected quarter replaces only its intended coverage, while earlier batch rows remain queryable.
- [ ] Verify malformed input and injected activation failures preserve prior active batch IDs, journals, reports, and dataset fingerprint.
- [ ] Verify non-overlapping quarters accumulate, invalid overlaps are rejected, and annual replacement supersedes the appropriate quarterly set.
- [ ] Verify entity/year/source-family isolation and opening-baseline completeness. Missing coverage or baseline must block scrutiny, not silently imply zero openings.
- [ ] Add only missing regressions; patch the shared failing boundary and rerun focused tests.

```bash
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_ingestion_pipeline.py tests/test_dataset_resolver.py tests/test_baseline_ingestion.py tests/test_api_integration.py
```

**Acceptance:** No failed replacement, duplicate upload, or other-period import changes the previously valid dataset; readiness remains deterministic.

## Task 2.5 — Verify Tally/GL parity through scrutiny

**Depends on:** 2.4.

**Files:** `backend/tests/test_api_integration.py`; production changes only if the new regression exposes a defect.

**Interface:** Existing import routes, baseline route, scrutiny route and persisted dataset lineage. Use separate entities because one source family is active per entity/year.

- [ ] Reuse `test_tally_and_gl_canonical_reports_have_matching_journal_totals`, which currently proves matching journal totals while GL remains `PARTIAL` without its baseline.
- [ ] Add an adjacent integration case supplying equivalent account openings and classification explicitly to both sources; do not infer GL classification from names.
- [ ] Assert equal per-account signed movements and derived closing balances, eligible readiness, and equal deterministic findings after excluding generated entity/run IDs and source-specific provenance.
- [ ] Assert each run retains its own actual ordered source-batch IDs and fingerprint. Source fingerprints need not be equal across different artifacts.
- [ ] Reuse the existing Phase 1 rerun/review regression to verify statuses and notes still survive.

```bash
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_api_integration.py -k 'parity or canonical_reports or compliance_versions_and_review or readiness'
```

**Acceptance:** Equivalent complete accounting inputs yield equivalent rule inputs/findings without introducing source-specific rule logic; both preserve provenance.

## Task 2.6 — Run the exit gate and record pilot evidence

**Depends on:** 2.2–2.5.

**Files:** This plan and `docs/tally-connector.md`; no planned frontend feature work. Any ingestion UI defect found during smoke testing gets its own bounded reproduction before an edit.

- [ ] Run the complete backend suite and frontend lint/build once after the final change.
- [ ] Smoke-test the existing browser workflow: Tally success/failure, GL upload with warnings, missing baseline/coverage, and failed replacement preserving the prior active dataset. Confirm displayed counts/readiness/errors match the API.
- [ ] Run the agreed live Tally smoke and supplied Q1 workbook check when available. Record unavailable external prerequisites as unverified gates, not passing tests.
- [ ] Review the phase diff for unrelated changes, data-loss risks, source leakage, and accidental GST/schema expansion.
- [ ] Record command results, skips, artifact counts, and any remaining pilot blockers. Commit independently reviewable fixes and documentation after their checks pass; preserve unrelated workspace changes.

**Exit gate:** Both ingestion paths preserve canonical data and provenance; invalid replacements are atomic; every source row is accounted for; scrutiny only runs on eligible datasets; live-environment claims have separate evidence.

## Scope and evidence review

Roadmap 2.1 maps to Tasks 2.2 and 2.4. Roadmap 2.2 maps to Tasks 2.3 and 2.4 with the explicitly proposed canonical-contract correction. The shared-source gate maps to 2.5; release verification to 2.6.

Graph verification used project `Users-adinayak18-Desktop-ledger-scrutiny`, generation `2026-09-06T18:25:28Z`, Tier 2. Connector and XLSX caller traces were fully returned at depth 1; relevant router snippets were inspected. Coverage checks reported no recorded gaps for inspected ingestion/router/test paths; this is best-effort evidence, not exhaustive proof. The Phase 1 plan had changed metadata and was read directly. Candidate files listed for execution are not claims that all their internals have already been reviewed.

No application code or tests were changed or run while preparing this approval draft. Implementation starts only after the user's approval and resolution of affected decisions.
