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

No Task 2.1 focused test command was run, because no tests changed. No browser smoke, live Tally pilot, or remote-connectivity command was run.

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
| 2.3 | Supplied Q1 artifact counts, footer explanation, account/document counts, and zero signed total | `test_supplied_q1_gl_workbook_is_accepted_with_footer_and_nonnumeric_quantity` | Covered by the local automated fixture test in the full-suite result: 59,167 accepted lines, 10,914 documents, 445 accounts, and ₹0.00 net. This is not live evidence. |
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
