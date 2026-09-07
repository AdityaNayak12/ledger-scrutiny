# Ledger Scrutiny Two-Month Roadmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Ledger Scrutiny pilot-ready from 4 September to 4 November 2026 with reliable Tally ingestion, one supported GL-dump format, a narrow deterministic compliance pack, and GST lookup that is safe in demo and provider modes.

**Architecture:** Reuse the current FastAPI, SQLAlchemy, append-only import-batch, and deterministic rule-engine paths. Add one compliance module beside the existing rules and one GST HTTP adapter beside the existing router. Keep the current database model unless a failing acceptance test proves a migration is required.

**Tech Stack:** Python, FastAPI, SQLAlchemy, Alembic, httpx, lxml, openpyxl, pytest, React, TypeScript, Vite, Docker Compose.

**Spec:** `docs/prd.md`, `docs/architecture.md`, `docs/tally-connector.md`, and the handwritten roadmap supplied on 4 September 2026.

> **6 September scope update:** GST implementation is deferred at the user's request. Phase 1 below and its detailed plan reflect the active scope. Earlier GST deadlines, Phase 3 provider examples, and GST-dependent release gates are historical proposals, not approved contracts or current blockers; replace them with a provider-specific plan before resuming GST work. Do not implement adapters or infer payloads from those examples.

## Global Constraints

- Delivery window: 4 September–4 November 2026.
- Release GL input: one fixed transaction-level XLSX profile with required headers `Document Number`, `G/L Account`, `Posting Date`, and `Amount in local currency`, plus a structured signed prior-year account-level closing baseline before a dataset is `READY`. Do not add CSV or legacy `.xls` in this window.
- Canonical amounts use `Decimal` and debit-positive/credit-negative signs. Reject a document whose signed lines do not balance within ₹0.01 or a non-balanced signed baseline; metadata/classification issues and incomplete staging may remain warnings only.
- Tally endpoints are explicit: require `http` or `https` with a host; local access requires `TALLY_ALLOW_LOCAL_ENDPOINTS=1`; deployed DNS hosts must be exact entries in `TALLY_ALLOWED_HOSTS`. Do not silently enable loopback or broadly allow private addresses.
- Compliance findings stay deterministic, explainable, versioned, and reviewable. LLMs do not make audit decisions.
- GST production lookup requires a selected provider, response contract, API key, and test credentials. Without them, ship tested adapter plus demo/sandbox mode.
- Income Tax and Custom Duty portal automation are follow-on work, not release acceptance criteria.
- Preserve immutable import batches, source hashes, rule versions, exception fingerprints, review statuses, and auditor notes.
- Use existing dependencies. No state-management library, rule framework, or provider SDK without a failing requirement.
- Every code task follows: failing test, focused test run, minimal implementation, focused plus full suite, small commit, fresh review.
- Never run two workers against the same write set. Execute dependent tasks serially; parallelize only disjoint docs, tests, or UI work.



## Scope Interpretation

The image is planning input, not an instruction document. It proposes Tally HTTP, GL-dump refinement, GST/Income Tax compliance, GST lookup API key, Income Tax hookups, Custom Duty checking, LLM abstraction, P0/P1 priorities, December completion, and January–March acquisition/trial-balance work. Crossed-out handwriting is unclear and excluded.

The repository already contains a Tally HTTP connector, fixed-profile transaction-level XLSX import, nine core rules, two manufacturing rules, exception review, and demo-only GST lookup. This plan hardens and extends those paths; it does not rebuild them.

## Definition of Done

By 4 November, a pilot user can import from Tally or the one supported transaction-level XLSX format with its structured account-level opening baseline, run a versioned core/manufacturing/compliance pack, inspect deterministic exceptions, use demo or configured GST lookup, review findings, and repeat the flow with audit history preserved.

## File Ownership Map

- Rules: `backend/app/rules/engine.py`, new `backend/app/rules/compliance.py`.
- Ingestion: `backend/app/ingestion/tally_http.py`, `backend/app/ingestion/tally_normalizer.py`, `backend/app/ingestion/xlsx_normalizer.py`, `backend/app/ingestion/batches.py`.
- API orchestration: `backend/app/routers/scrutiny.py`.
- External integration: new `backend/app/integrations/gst.py`.
- Tests: existing files under `backend/tests/`, plus `test_compliance_rules.py` and `test_gst_integration.py`.
- UI: `frontend/src/App.tsx`, `frontend/src/components/XlsxUploadModal.tsx`, only where changed API states require it.
- Docs/config: `.env.example`, `docs/architecture.md`, `docs/tally-connector.md`, and only contradictory parts of `docs/prd.md`.
- Do not modify `backend/app/db/models.py` or add an Alembic migration unless a test demonstrates missing provenance cannot fit existing fields.

---



## Phase 0 — Lock Inputs and Baseline

**Dates:** 4–7 September. **Gate:** decisions recorded before implementation.

### Task 0.1: Freeze the release contracts

**Files:** no source changes.

- [ ] Confirm the single transaction-level XLSX profile: `Document Number`, `G/L Account`, `Posting Date`, and `Amount in local currency`; use signed amounts with debit-positive/credit-negative signs and a structured signed prior-year account-level closing baseline. The legacy four-column balance upload and three sign conventions are not supported.
- [ ] Select the GST provider and map its response to the existing `GSTProfileResponse` fields.
- [ ] Confirm provider credentials by 7 September. If absent, set production connectivity to December follow-on.
- [ ] Approve the exact tax formulas before coding. Do not infer statutory compliance from account names.
- [ ] Run baseline: `cd backend && PYTHONPATH=. ../.venv/bin/pytest -q`; then `cd ../frontend && npm run lint && npm run build`.

---



## Phase 1 — P0 Rule and Compliance Foundation

**Dates:** 8–18 September. **Output:** explicit TDS-based compliance pack and versioned findings.

**Scope revision — 6 September:** The user has deferred all new GST/GSTIN checks and integration work until a provider contract is available. Phase 1 has no GST API, credential, response-shape, or identifier-format dependency. Existing GST behavior is outside this change. The detailed execution plan is `docs/superpowers/plans/2026-09-06-phase-1-compliance-foundation.md`; use its concrete code, test cases, and gates.

### Task 1.1: Add the compliance pack contract

**Files:**

- Create: `backend/app/rules/compliance.py`
- Modify: `backend/app/rules/engine.py`
- Create: `backend/tests/test_compliance_rules.py`
- Modify: `backend/tests/test_rules_engine.py`
- Modify: `backend/app/routers/scrutiny.py`
- Modify: `backend/tests/test_api_integration.py`

**Interface:** `run_compliance_checks(entity, accounts, snapshots, period_start, period_end) -> list[AuditException]`; retain `run_scrutiny` as the engine entry point.

- [ ] Characterize existing TDS behavior before moving it, including threshold equality, zero threshold, payable/debit TDS balances, stable messages, variance, and period fields.
- [ ] Move the existing TDS implementation into `compliance.py` unchanged and re-export it from the engine. The compliance dispatcher calls that implementation; do not duplicate the formula or create circular imports.
- [ ] Add tests proving compliance/manufacturing call the dispatcher exactly once, `None`/unknown engine packs retain core behavior, and stored GST data does not affect findings.
- [ ] Replace the existing shared TDS summand with pack-specific dispatch so TDS emits only once. Retain all other core and manufacturing checks.
- [ ] Set versions to `core-v1+compliance-v1` and `core-v1+compliance-v1+manufacturing-v1`; retain `core-v1` for the default/unknown engine pack.
- [ ] Permit `compliance_v1` in entity creation; continue rejecting unknown API packs and enforcing the manufacturing-sector constraint.
- [ ] Run focused tests and the full backend suite, obtain fresh review, and commit the independent engine/API changes separately.

### Task 1.2: Verify evidence and persisted findings

**Files:** `backend/tests/test_compliance_rules.py`, `backend/tests/test_rules_engine.py`, `backend/tests/test_api_integration.py`.

- [ ] Verify clean output at positive materiality, deterministic output, and required finding fields: `rule_name`, `severity`, `message`, period dates, and stable TDS `variance`.
- [ ] Preserve the current zero-threshold behavior explicitly; do not silently change the formula or message while reorganizing packs.
- [ ] Use a fixture with actual creditor evidence to verify stored run/finding versions and preservation of fingerprints, reviewed status, and notes across reruns.
- [ ] Verify prior runs remain queryable and manufacturing retains its existing findings.
- [ ] Keep messages limited to ledger evidence; do not infer statutory compliance or add new tax formulas, aliases, GST checks, provider stubs, or guessed payloads.
- [ ] Run focused/full backend tests and frontend baseline checks; record results and obtain a fresh review.

**Gate:** deterministic TDS output, exactly-once dispatch, correct stored versions, preserved reviews/provenance, and no dependency on GST data or services.

**Deferred:** GST rules/integration require a separate future plan after provider documentation, representative redacted responses, authentication, and test access are available. Later GST rule additions require a new compliance rule-set version and explicit history/review compatibility tests.

---

## Phase 2 — Reliable Tally and GL-Dump Ingestion

**Dates:** 21 September–2 October. **Output:** Tally and XLSX feed the same normalized model.

### Task 2.1: Harden the Tally HTTP connector

**Files:**

- Modify: `backend/app/ingestion/tally_http.py`
- Modify: `backend/app/routers/scrutiny.py`
- Modify: `backend/tests/test_tally_http.py`
- Modify: `backend/tests/test_tally_ingestion.py`
- Modify: `backend/tests/test_api_integration.py`

**Interface:** Keep `fetch_trial_balance(*, endpoint, company_name, period_start, period_end, timeout_seconds=30) -> tuple[dict, bytes]` unchanged.

- [ ] Add failing tests for timeout, connection failure, invalid endpoint, rejected XML, missing closing balance, empty ledgers, and successful company/period selection. Use `httpx.MockTransport` or existing monkeypatching.

```python
def test_tally_timeout_is_actionable(monkeypatch):
    def timeout(*args, **kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx, "post", timeout)
    monkeypatch.setenv("TALLY_ALLOW_LOCAL_ENDPOINTS", "1")
    with pytest.raises(TallyConnectorError, match="Could not reach TallyPrime"):
        fetch_trial_balance(
            endpoint="http://127.0.0.1:9000",
            company_name="Demo",
            period_start=date(2025, 4, 1),
            period_end=date(2026, 3, 31),
        )
```

- [ ] Run `cd backend && PYTHONPATH=. ../.venv/bin/pytest -q tests/test_tally_http.py tests/test_tally_ingestion.py`; verify failure.
- [ ] Preserve the current XML request and `{entity, ledgers, vouchers}` shape.
- [ ] Convert timeout, HTTP, XML, Tally-status, missing-balance, and empty-ledger failures to `TallyConnectorError` with actionable messages.
- [ ] Require `http` or `https` with a host; permit local endpoints only with `TALLY_ALLOW_LOCAL_ENDPOINTS=1`, and permit deployed DNS hostnames only through exact `TALLY_ALLOWED_HOSTS` entries. Do not silently enable loopback or broadly allow private addresses.
- [ ] Do not log request bodies, API keys, or full source XML. Keep source hashing and active-batch replacement in `batches.py`.
- [ ] Add an API test proving connector failures do not leak credentials/source XML. Run full backend tests and commit: `feat: harden Tally HTTP ingestion`.



### Task 2.2: Refine the supported XLSX GL dump

**Files:**

- Modify: `backend/app/ingestion/xlsx_normalizer.py`
- Modify: `backend/app/routers/scrutiny.py`
- Modify: `backend/app/ingestion/batches.py` only if current validation metadata cannot persist
- Modify: `backend/tests/test_xlsx_ingestion.py`
- Modify: `backend/tests/test_api_integration.py`

- [ ] Add or verify tests for the fixed transaction-level profile: exact headers within the first 15 rows, signed amounts, structured account-level baseline requirement, blank-line and summary-row handling, malformed decimals, unknown classifications, repeated account codes on separate journal lines, period isolation, source hash persistence, and hard rejection of unbalanced documents or baselines.

- [ ] Run `cd backend && PYTHONPATH=. ../.venv/bin/pytest -q tests/test_xlsx_ingestion.py tests/test_api_integration.py`; verify failure.
- [ ] Keep the fixed required headers, signed amounts, explicit baseline, and fail-loud validation.
- [ ] Persist counts for imported rows, skipped blank rows, skipped summary rows, and validation warnings.
- [ ] Preserve append-only behavior when `import_batch_id` is supplied; never delete another batch’s snapshots.
- [ ] Do not add CSV, `.xls`, automatic guessing, or a second GL schema. Run full backend tests and commit: `feat: make XLSX GL imports pilot-safe`.

**Gate:** both sources create the same canonical journal/balance records, and scrutiny needs no source-specific rule logic.

---



## Phase 3 — GST Provider and Narrow Tax Surface

**Dates:** 5–16 October. **Output:** provider-backed GST lookup when credentials exist; safe demo/sandbox otherwise.

### Task 3.1: Add the GST provider adapter

**Files:**

- Create: `backend/app/integrations/__init__.py`
- Create: `backend/app/integrations/gst.py`
- Modify: `backend/app/routers/scrutiny.py`
- Modify: `.env.example`
- Create: `backend/tests/test_gst_integration.py`
- Modify: `backend/tests/test_api_integration.py`

**Interface:**

```python
def lookup_gst_profile(
    gstin: str,
    *,
    base_url: str,
    api_key: str,
    timeout_seconds: float = 10.0,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, object]:
    raise NotImplementedError
```

Return exactly the fields consumed by `GSTProfileResponse`: GSTIN, legal/trade names, status, constitution, business activity, suggested sector/rule pack, source, and `simulated`. Production callers omit `transport`; tests pass `httpx.MockTransport`.

- [ ] Add failing `httpx.MockTransport` tests for success mapping, provider 4xx/5xx, timeout, malformed JSON, missing fields, and API-key header presence without key leakage.

```python
def test_gst_provider_maps_success_and_sends_key():
    seen = {}

    def handler(request):
        seen["api_key"] = request.headers["X-API-Key"]
        return httpx.Response(200, json={
            "gstin": "27ABCDE1234F1Z5",
            "legal_name": "Example Private Limited",
            "trade_name": "Example",
            "registration_status": "ACTIVE",
            "constitution": "Private Limited Company",
            "nature_of_business": ["Manufacturing"],
            "core_business_activity": "Manufacturer",
        })

    result = lookup_gst_profile(
        "27ABCDE1234F1Z5",
        base_url="https://gst.example.test/profile",
        api_key="test-secret",
        transport=httpx.MockTransport(handler),
    )
    assert result["registration_status"] == "ACTIVE"
    assert seen["api_key"] == "test-secret"
```

- [ ] Run `cd backend && PYTHONPATH=. ../.venv/bin/pytest -q tests/test_gst_integration.py`; verify failure.
- [ ] Implement one HTTP adapter with timeout, response validation, and one `GSTProviderError` for transport/contract failures. Keep it free of FastAPI and SQLAlchemy imports.
- [ ] Update `/gst-profile/lookup`: preserve `GST_LOOKUP_MODE=demo`; add `GST_LOOKUP_MODE=provider`, `GST_LOOKUP_URL`, `GST_LOOKUP_API_KEY`, and `GST_LOOKUP_TIMEOUT_SECONDS`; return 503 for missing configuration and 502 for provider failures; set `simulated=False` for live data.
- [ ] Add API tests for demo, provider success, missing configuration, provider failure, and invalid GSTIN. Run full backend tests and commit: `feat: add configurable GST profile provider`.



### Task 3.2: Bound Income Tax claims

**Files:**

- Modify: `backend/app/rules/compliance.py`
- Modify: `backend/tests/test_compliance_rules.py`
- Modify: `docs/architecture.md`

- [ ] Add a failing test that ledgers/snapshots cannot produce a claim about Income Tax return status, tax computation, or portal response.
- [ ] Update rule messages to name only the evidence checked, such as “TDS payable account was not found for material creditor balance.”
- [ ] Document that future Income Tax API work needs credentials, identifiers, period/rate contracts, retained evidence, and human review.
- [ ] Run the full backend suite and commit: `docs: bound tax compliance claims to available evidence`.

**Gate:** provider credentials decide whether live GST lookup ships; the rest of the pilot does not wait for them.

---



## Phase 4 — Pilot UX, Auditability, and Release

**Dates:** 19–30 October. **Output:** pilot user completes the flow without developer intervention.

### Task 4.1: Surface changed states in the UI

**Files:**

- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/XlsxUploadModal.tsx` only if upload response fields change
- Modify: `frontend/src/index.css` only for a required status style

- [ ] Write a manual acceptance checklist covering login, entity selection, Tally import, XLSX import, GST demo/provider lookup, scrutiny run, exception filtering, review status, auditor notes, and visible provider/import errors.
- [ ] Run `cd frontend && npm run lint && npm run build`; verify baseline.
- [ ] Add only the UI needed to show simulated versus provider-backed GST, import validation warnings, and changed error states. Do not refactor the dashboard or add a state library.
- [ ] Run lint/build again and perform one local browser smoke run. Commit: `feat: show pilot import and GST states`.



### Task 4.2: Verify repeatability and review preservation

**Files:**

- Modify: `backend/tests/test_api_integration.py`
- Modify: `backend/tests/test_models.py` only if an invariant is missing

- [ ] Add a failing end-to-end test that imports the same source twice, runs scrutiny twice, reviews one finding, and verifies the next run preserves status and notes through its fingerprint.
- [ ] Add a failing test that active-batch selection works while the superseded batch remains queryable.
- [ ] Run `cd backend && PYTHONPATH=. ../.venv/bin/pytest -q tests/test_api_integration.py tests/test_models.py`; implement only the missing invariant.
- [ ] Run the full backend suite and commit: `test: verify repeatable audit history`.



### Task 4.3: Publish deployment and pilot operations

**Files:** `.env.example`, `docs/tally-connector.md`, `docs/architecture.md`, and contradictory sections of `docs/prd.md`.

- [ ] Document `SECRET_KEY`, `CORS_ORIGINS`, `VITE_API_BASE_URL`, `GST_LOOKUP_MODE`, `GST_LOOKUP_URL`, `GST_LOOKUP_API_KEY`, `GST_LOOKUP_TIMEOUT_SECONDS`, and the Tally host allowlist.
- [ ] Document XLSX headers, sign conventions, period requirements, `.xls` exclusion, fictional demo GST data, live-provider data, and 503/502 meanings.
- [ ] Document the pilot checklist and rollback: select the prior active batch, rerun scrutiny, preserve review history.
- [ ] Run `docker compose config` and commit: `docs: publish pilot deployment and data contracts`.

---



## Final Integration Gate — 2–4 November

- [ ] Run `cd backend && PYTHONPATH=. ../.venv/bin/pytest -q`.
- [ ] Run `cd frontend && npm run lint && npm run build`.
- [ ] Complete one local end-to-end flow with demo GST mode and one Tally/XLSX fixture.
- [ ] Run one mocked provider-success test; never use a real API key in CI or fixtures.
- [ ] Confirm logs contain no secrets, raw XML, or full provider responses.
- [ ] Confirm rule-set version is stored on runs and findings; reviewed statuses and notes survive reruns; simulated GST is never shown as live.
- [ ] Have a fresh integration reviewer inspect task diffs, API contracts, migrations, and release notes.

**Release decision:** ship when the gate passes. If provider credentials are absent, ship tested adapter plus demo/sandbox and move live connectivity to December.

## Follow-on After the Two-Month Release

- December: expand approved GST/Income Tax rules, add production provider integrations, and define Custom Duty data contract.
- January–March: customer acquisition and Trial Balance Sheet creation.
- Reconsider LLM abstraction only for non-authoritative explanations after pilot evidence shows a real need.
- Do not promise full A–Z statutory coverage, portal filing, universal GL ingestion, or Custom Duty automation from this release.
