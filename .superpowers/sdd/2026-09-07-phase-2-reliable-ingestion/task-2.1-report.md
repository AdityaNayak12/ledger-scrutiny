# Task 2.1 — Contract reconciliation and baseline evidence

**Status:** Complete. Documentation only; no runtime or test changes.

## Files changed

- `docs/superpowers/plans/2026-09-04-ledger-scrutiny-roadmap.md`
- `docs/superpowers/specs/2026-09-04-ledger-ingestion-p0-design.md`
- `.superpowers/sdd/2026-09-07-phase-2-reliable-ingestion/task-2.1-report.md`

The dedicated Phase 2 plan and the existing Phase 1/demo files were preserved. No GST scope or Phase 1 history was changed.

## Contract decisions

The named P0 design spec is authoritative.

1. **Format:** The only supported XLSX GL input is the transaction-level profile with these required headers: `Document Number`, `G/L Account`, `Posting Date`, and `Amount in local currency`. The legacy four-column balance upload (`ledger_name`, `group_name`, `opening_balance`, `closing_balance`) and its three sign conventions are not supported.
2. **Canonical amounts:** Amounts use `Decimal` with debit-positive/credit-negative signs. Tally is normalized to the same convention; scrutiny rules do not reinterpret source signs.
3. **Baseline/readiness:** A structured signed prior-year account-level closing trial-balance workbook is the machine-readable opening baseline. A signed PDF may be retained as evidence but cannot establish the baseline alone. Missing baseline or required coverage leaves the dataset `PARTIAL`; only `READY` and `READY_WITH_WARNINGS` datasets may reach scrutiny.
4. **Failure policy:** Reject invalid required data, out-of-period rows, documents whose signed lines do not balance within ₹0.01, duplicate baseline account codes, overlapping active journal coverage, non-balanced signed baselines, and failed activation. Metadata/classification issues and incomplete staged coverage may be warnings; they do not make invalid accounting data activatable.
5. **Deployment:** The Tally endpoint is explicit and must use `http` or `https` with a host. Local endpoints require `TALLY_ALLOW_LOCAL_ENDPOINTS=1`; deployed DNS hosts require exact `TALLY_ALLOWED_HOSTS` entries. Loopback is not silently enabled, private addresses are not broadly allowed, and redirects remain disabled. Remote Tally topology and any bridge/agent/private route are unresolved and are not implied by this task.

## Baseline command results

The fresh baseline was run in the controller before this documentation pass. It is recorded here rather than rerun because this task changes no executable files.

| Working directory | Command | Result |
|---|---|---|
| `backend/` | `PYTHONPATH=. ../.venv/bin/pytest -q` | **PASS** — 234 passed, 4 warnings in 21.11s. Warnings were existing FastAPI/Starlette deprecations only. |
| `frontend/` | `npm run lint` | **PASS** |
| `frontend/` | `npm run build` | **PASS** — Vite v8.1.4; 19 modules transformed. |

No Task 2.1 focused test command was run during the initial documentation pass, because no tests changed. The fix-round focused Q1 command and its run/skip result are recorded below. No browser smoke, live Tally pilot, or remote-connectivity command was run.

## Evidence mapping for Tasks 2.2–2.6

Named tests below were present in the full backend suite and are covered by the recorded `234 passed` result. This table does not claim live Tally compatibility or browser behavior from mock/API tests.

| Task | Acceptance case | Existing evidence | Status / remaining gap |
|---|---|---|---|
| 2.2 | Timeout, connection/HTTP failure, malformed/rejected XML, missing status, missing closing balance, and empty response failures are typed and actionable | `test_tally_connector_maps_timeout_without_leaking_exception`; `test_tally_connector_does_not_retain_http_exception_context`; `test_tally_connector_maps_http_status_failure_without_response_body`; `test_tally_connector_normalizes_connector_failures`; `test_tally_connector_rejects_response_without_status`; `test_tally_http_connector_fails_loudly_on_empty_response` | Covered by automated tests. |
| 2.2 | Company and period selection plus supported ledger/voucher response preservation | `test_tally_connector_request_selects_company_and_period`; `test_tally_http_connector_maps_ledger_balances`; `test_tally_parser_preserves_multiline_voucher_ids_rows_and_signed_values`; `test_tally_canonical_persists_group_and_voucher_provenance` | Covered by automated fixture tests. |
| 2.2 | Credential/source-safe errors and no request leakage | `test_tally_connector_does_not_expose_endpoint_credentials_or_raw_xml`; `test_tally_connector_does_not_expose_endpoint_path_in_error_or_context`; `test_tally_connector_does_not_expose_status_detail_secrets`; `test_tally_connector_does_not_expose_opaque_malformed_amount`; `test_tally_connector_failure_does_not_echo_endpoint_or_credentials`; `test_malformed_tally_parse_failure_returns_safe_structured_detail` | Covered by automated tests. |
| 2.2 | Invalid endpoint, local opt-in, exact deployed host allowlist, and redirect policy | `test_tally_connector_rejects_invalid_endpoint_without_posting`; `test_tally_connector_rejects_non_global_endpoint_without_posting`; `test_tally_connector_allows_localhost_only_with_explicit_opt_in`; `test_tally_connector_allows_exactly_configured_hostname`; `test_tally_connector_disables_redirects` | Covered by automated tests. Exact remote topology remains unresolved. |
| 2.2 | Real Tally compatibility and pilot smoke | None; the current evidence is mock/fixture-based | **Unverified.** Requires the remote backend/Tally topology and an approved connection approach. |
| 2.3 | Fixed required headers, signed rows, source-row provenance, and complete journal row/document persistence | `test_fixed_gl_parser_preserves_dimensions_and_source_row`; `test_xlsx_fixed_profile_preserves_signed_rows`; `test_fixed_gl_api_persists_complete_golden_file_rows_and_documents`; `test_xlsx_confirm_persists_fixed_canonical_fixture` | Covered for the existing fixtures. |
| 2.3 | Blank/summary row handling and explained accepted/skipped/rejected outcomes | `test_fixed_gl_parser_skips_only_structured_summary_rows`; `test_xlsx_skips_blank_lines`; `test_fixed_gl_normalizer_records_structured_report_for_staged_failure` | Covered by automated tests. |
| 2.3 | Required-data, malformed amount/date, precision, out-of-period, and document-balance failures | `test_fixed_gl_parser_rejects_malformed_required_values`; `test_fixed_gl_parser_rejects_amount_precision_that_numeric_20_2_cannot_retain`; `test_fixed_gl_parser_rejects_rows_outside_declared_coverage`; `test_fixed_gl_parser_rejects_unbalanced_documents`; `test_xlsx_fail_loud_validations_blank_canonical_value`; `test_xlsx_fail_loud_validations_unbalanced_document` | Covered by automated tests. |
| 2.3 | Metadata/classification warnings retain valid journals; repeated account codes remain valid | `test_fixed_gl_normalizer_reports_skips_formula_cache_warnings_and_unclassified_accounts`; `test_fixed_gl_parser_preserves_nonnumeric_optional_quantity_and_warns`; `test_fixed_gl_parser_allows_repeated_account_codes_within_a_document`; `test_fixed_gl_normalizer_resolves_accounts_by_entity_scoped_external_code_only` | Covered by automated tests. |
| 2.3 | Supplied Q1 artifact counts, footer explanation, account/document counts, and zero signed total | `test_supplied_q1_gl_workbook_is_accepted_with_footer_and_nonnumeric_quantity` | The fix-round focused run below confirms this test ran, rather than skipped: 59,167 accepted lines, 10,914 documents, 445 accounts, and ₹0.00 net. This is not live evidence. |
| 2.4 | Exact duplicate replay does not add journals; corrupted canonical children are rejected without repopulation or mutation | `test_staging_retains_raw_bytes_and_exact_hash_duplicate_is_idempotent`; `test_duplicate_tally_upload_is_idempotent_for_child_records`; `test_tally_duplicate_replay_rejects_corrupt_canonical_children`; `test_tally_duplicate_replay_with_no_children_rejects_without_repopulate`; `test_fixed_gl_duplicate_replay_validates_canonical_children`; `test_tally_duplicate_replay_rejects_foreign_account_child_without_mutating_active_data` | Covered by automated tests. |
| 2.4 | Corrected quarter replaces only its intended coverage and preserves earlier/other active batches | `test_activation_supersedes_only_replaced_active_coverage`; `test_corrected_quarter_replaces_same_coverage_in_resolution` | Covered by automated tests. |
| 2.4 | Malformed or failed activation leaves prior active data, reports, and fingerprint unchanged | `test_failed_replacement_leaves_old_active_batch_untouched`; `test_activation_flush_failure_restores_candidate_report`; `test_malformed_tally_replacement_retains_previous_active_dataset`; `test_fixed_gl_confirm_activates_after_normalization_and_rolls_back_invalid_replacement` | Covered by automated tests. |
| 2.4 | Non-overlapping quarter accumulation, annual replacement, overlap rejection, and source-family isolation | `test_q1_q2_accumulate_signed_movement_and_report_gaps`; `test_annual_coverage_excludes_quarterly_batches_for_same_family`; `test_annual_activation_explicitly_replaces_quarterly_coverage`; `test_active_overlap_and_source_family_conflict_are_rejected_without_replacing_dataset` | Covered by automated tests. |
| 2.4 | Entity/year/baseline isolation and readiness blocking for missing coverage or baseline | `test_same_hash_remains_importable_for_another_entity`; `test_cross_financial_year_journal_coverage_is_invalid_without_out_of_window_movement`; `test_normalizer_partial_journal_becomes_ready_after_complete_baseline`; `test_incomplete_period_coverage_remains_partial_after_complete_baseline`; `test_omitted_period_coverage_remains_partial_after_complete_baseline`; `test_scrutiny_reports_readiness_block_and_persists_ready_dataset_lineage` | Covered by automated tests. |
| 2.5 | Existing Tally/GL canonical reports have matching journal totals while GL is `PARTIAL` without a baseline | `test_tally_and_gl_canonical_reports_have_matching_journal_totals` | Covered by the existing automated integration test. |
| 2.5 | Complete equivalent Tally and GL inputs prove equal per-account movements, derived closings, and deterministic findings | No existing adjacent end-to-end integration test supplies equivalent account openings and explicit classifications to both sources | **Missing regression.** Must be added in Task 2.5; do not infer classification from names. |
| 2.5 | Each scrutiny run retains actual ordered source-batch IDs/fingerprint while source-specific provenance is excluded only from finding comparison | `test_scrutiny_reports_readiness_block_and_persists_ready_dataset_lineage`; `test_compliance_versions_and_review_survive_rerun` | Partial coverage: lineage and review preservation exist, but parity-specific ordered-lineage comparison remains **unverified**. |
| 2.6 | Final backend suite and frontend lint/build after the final Phase 2 change | The controller baseline above predates this documentation-only diff | **Not rerun by design.** No executable files changed. |
| 2.6 | Browser workflow for Tally success/failure, GL warnings, missing baseline/coverage, and failed replacement | No browser smoke result recorded; API evidence includes `test_scrutiny_reports_readiness_block_and_persists_ready_dataset_lineage` and `test_malformed_tally_replacement_retains_previous_active_dataset` | **Unverified browser evidence.** |
| 2.6 | Live Tally smoke and supplied Q1 workbook check | `test_supplied_q1_gl_workbook_is_accepted_with_footer_and_nonnumeric_quantity` is automated local fixture evidence; no live Tally test was run | Supplied Q1 automated gate is covered; live Tally pilot gate is **unverified**. |
| 2.6 | Diff review for unrelated changes, data-loss risk, source leakage, and accidental GST/schema expansion | This report and the scoped docs diff; existing source-leakage tests listed above | Self-reviewed; unrelated dirty Phase 1/demo changes were not staged or altered. |

## Self-review

- The roadmap no longer presents the legacy four-column balance upload, three sign conventions, or warning-only unbalanced import as supported behavior.
- The P0 design remains the canonical source of the journal, signed-baseline, hard-failure, readiness, provenance, and GST-deferral boundaries.
- Endpoint text is explicit about local opt-in, exact deployed host configuration, disabled redirects, and unresolved remote topology.
- No application code, tests, dependencies, schema, GST behavior, or Phase 1 history was changed.
- The working tree was treated as dirty and user-owned; only the three Task 2.1 documentation/report paths are intended for this commit.

## Concerns and follow-ups

- Remote Tally compatibility cannot be claimed until the backend host, Tally host, network owner, and approved private connectivity/topology are known and a pilot smoke is run.
- Task 2.5 still needs the complete source-parity regression for explicit openings/classifications, per-account closing balances, findings, and source-batch lineage.
- Browser smoke evidence is still outstanding for Task 2.6.
- The supplied Q1 result is automated local evidence, not evidence from a live customer environment.

## Fix round 1 — findings addressed

**Scope:** documentation and evidence correction only; no runtime or test source changes.

### 1. Active scope and timing

The roadmap now labels its original 4 September–4 November goal/window as historical, states that the active execution is Phase 2 only, and names 21 September–2 October 2026 as the binding Phase 2 window. Phase 1 is marked completed historical scope. Phase 3, Phase 4, the final integration gate, and follow-on work are explicitly marked deferred/historical. The GST provider plan remains for history only and is explicitly deferred until provider documentation, authentication, test access, and a separately approved plan exist.

Roadmap locations: `docs/superpowers/plans/2026-09-04-ledger-scrutiny-roadmap.md:5,13,15,19-25,40,59,77,117,121-123,199-203,286-290,334-336,348`.

### 2. Canonical task numbering

The dedicated Phase 2 plan is canonical for this execution. The roadmap now has one explicit task map at `docs/superpowers/plans/2026-09-04-ledger-scrutiny-roadmap.md:125-134`:

| Task | Canonical scope |
|---|---|
| 2.1 | Reconcile contract and establish baseline |
| 2.2 | Verify Tally connectivity and safe failures |
| 2.3 | Verify fixed GL parsing and explain every row |
| 2.4 | Prove replacement, duplicate, and period isolation |
| 2.5 | Prove Tally/GL parity through scrutiny |
| 2.6 | Run exit gate and record pilot evidence |

The former roadmap headings are now unnumbered workstreams, labelled with their canonical plan task: `Workstream: Harden the Tally HTTP connector (canonical Task 2.2)` at `...roadmap.md:136` and `Workstream: Refine the supported XLSX GL dump (canonical Task 2.3)` at `...roadmap.md:175`.

### 3. Baseline skip accounting and Q1 status

The captured full baseline summary was `234 passed, 4 warnings in 21.11s`; it contained no `skipped` count, so the recorded full-baseline skip count is **0**. The original report did not establish whether the conditional Q1 test ran, so it is no longer treated as covered solely by that summary.

The focused run below confirms that `test_supplied_q1_gl_workbook_is_accepted_with_footer_and_nonnumeric_quantity` **ran and passed**, not skipped: `1 passed, 31 deselected, 3 warnings`. Its assertions cover 59,167 accepted lines, 10,914 documents, 445 accounts, and ₹0.00 signed net. This remains local automated fixture evidence, not live Tally/customer evidence.

### 4. Evidence source locations

The original evidence matrix is supplemented by this exact source-location index. Locations are repository-relative and use current 1-based test-definition lines.

| Original evidence row | Test source locations |
|---|---|
| 2.2 typed connector failures | `backend/tests/test_tally_http.py:64,100,156,221,239,255`; `backend/tests/test_tally_ingestion.py:257,547,589` |
| 2.2 company/period and response preservation | `backend/tests/test_tally_http.py:24,78`; `backend/tests/test_tally_ingestion.py:364,817` |
| 2.2 safe errors/no leakage | `backend/tests/test_tally_http.py:114,133,185,204`; `backend/tests/test_api_integration.py:1161,1197` |
| 2.2 endpoint policy | `backend/tests/test_tally_http.py:171,297,314,332,350` |
| 2.2 live Tally pilot | No test source; live evidence remains unverified |
| 2.3 fixed profile/provenance/persistence | `backend/tests/test_xlsx_ingestion.py:154,619,740`; `backend/tests/test_api_integration.py:1375` |
| 2.3 row outcomes | `backend/tests/test_xlsx_ingestion.py:342,427,765` |
| 2.3 hard parser/API failures | `backend/tests/test_xlsx_ingestion.py:219,231,244,495,650,677` |
| 2.3 metadata warnings/repeated account codes | `backend/tests/test_xlsx_ingestion.py:254,283,321,376` |
| 2.3 supplied Q1 counts | `backend/tests/test_xlsx_ingestion.py:303` |
| 2.4 duplicate replay and child integrity | `backend/tests/test_ingestion_pipeline.py:67`; `backend/tests/test_api_integration.py:366,418,465,522,1065` |
| 2.4 corrected-period replacement | `backend/tests/test_ingestion_pipeline.py:183`; `backend/tests/test_dataset_resolver.py:197` |
| 2.4 failed replacement/activation rollback | `backend/tests/test_ingestion_pipeline.py:407,419`; `backend/tests/test_xlsx_ingestion.py:538`; `backend/tests/test_api_integration.py:1334` |
| 2.4 quarter/annual/overlap/source-family behavior | `backend/tests/test_ingestion_pipeline.py:197,384`; `backend/tests/test_dataset_resolver.py:167,216` |
| 2.4 isolation/baseline/readiness | `backend/tests/test_ingestion_pipeline.py:154,384`; `backend/tests/test_dataset_resolver.py:260,301,321,483`; `backend/tests/test_api_integration.py:1444` |
| 2.5 existing canonical totals | `backend/tests/test_api_integration.py:1216` |
| 2.5 complete source parity | No existing test source; regression remains missing |
| 2.5 lineage/review preservation | `backend/tests/test_api_integration.py:252,1444` |
| 2.6 final command gate | `.superpowers/sdd/2026-09-07-phase-2-reliable-ingestion/progress.md:10`; no post-fix full-suite run requested |
| 2.6 browser smoke | No browser-test source/evidence; API references at `backend/tests/test_api_integration.py:1334,1444` |
| 2.6 live Tally/Q1 gate | Q1 test at `backend/tests/test_xlsx_ingestion.py:303`; no live Tally test source |
| 2.6 scoped diff review | `docs/superpowers/plans/2026-09-04-ledger-scrutiny-roadmap.md:15,125-134,199-203,286-290,334-336`; this report |

### Focused test command and captured output

Working directory: `backend/`

Command:

```bash
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_xlsx_ingestion.py -k supplied_q1 -rs
```

Output:

```text
.                                                                        [100%]
=============================== warnings summary ===============================
../.venv/lib/python3.14/site-packages/fastapi/testclient.py:1
  /Users/adinayak18/Desktop/ledger-scrutiny/.venv/lib/python3.14/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

app/main.py:29
  /Users/adinayak18/Desktop/ledger-scrutiny/backend/app/main.py:29: DeprecationWarning:
          on_event is deprecated, use lifespan event handlers instead.

          Read more about it in the
          [FastAPI docs for Lifespan Events](https://fastapi.tiangolo.com/advanced/events/).

    @app.on_event("startup")

../.venv/lib/python3.14/site-packages/fastapi/applications.py:4681
  /Users/adinayak18/Desktop/ledger-scrutiny/.venv/lib/python3.14/site-packages/fastapi/applications.py:4681: DeprecationWarning:
          on_event is deprecated, use lifespan event handlers instead.

          Read more about it in the
          [FastAPI docs for Lifespan Events](https://fastapi.tiangolo.com/advanced/events/).

    return self.router.on_event(event_type)  # ty: ignore[deprecated]

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
1 passed, 31 deselected, 3 warnings in 7.86s
```

### Fix-round result

The three findings are addressed in the roadmap/report without changing runtime behavior. The remaining concerns are unchanged: live Tally topology/pilot evidence, the complete Task 2.5 source-parity regression, and browser smoke evidence remain unverified.

### Documentation validation commands and outputs

Command:

```bash
git diff --check
```

Output: exit `0`; no output.

Command:

```bash
rg -n '^\*\*Active execution status|^## Phase 3 .*deferred|^## Phase 4 .*deferred|^## Final Integration Gate .*deferred|^### Workstream:.*canonical Task 2\.' docs/superpowers/plans/2026-09-04-ledger-scrutiny-roadmap.md
if rg -q '^### Task 2\.[12]:' docs/superpowers/plans/2026-09-04-ledger-scrutiny-roadmap.md; then echo 'legacy Task 2.1/2.2 headings found'; exit 1; else echo 'no legacy Task 2.1/2.2 headings'; fi
```

Output:

```text
136:### Workstream: Harden the Tally HTTP connector (canonical Task 2.2)
175:### Workstream: Refine the supported XLSX GL dump (canonical Task 2.3)
199:## Phase 3 — GST Provider and Narrow Tax Surface (deferred / historical)
286:## Phase 4 — Pilot UX, Auditability, and Release (deferred / historical)
334:## Final Integration Gate — 2–4 November (deferred / historical)
no legacy Task 2.1/2.2 headings
```
