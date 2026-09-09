# Phase 2 Operational Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` to implement this plan task-by-task. Every worker and reviewer must use GPT-5.6 Luna with xhigh reasoning.

**Goal:** Convert the four remaining Phase 2 release gates from unverified to evidenced without changing the approved canonical ingestion contract.

**Architecture:** Add bounded resource handling at the upload/Tally trust boundaries. Validate the approved Tally network path at deployment level, then run one isolated live pilot and one real-browser workflow against the existing API/UI. Keep topology, credentials, and network policy outside application code unless an approved topology requires a narrowly scoped change.

**Tech Stack:** FastAPI, `UploadFile`, httpx, lxml, openpyxl, existing React/Vite UI, existing pytest integration tests, approved deployment ingress/egress controls.

**Spec:** `docs/superpowers/specs/2026-09-04-ledger-ingestion-p0-design.md`; current implementation contract: `docs/superpowers/plans/2026-09-07-phase-2-reliable-ingestion.md`.

## Global Constraints

- Keep the fixed GL headers: `Document Number`, `G/L Account`, `Posting Date`, and `Amount in local currency`.
- Keep signed amounts: positive debit, negative credit.
- Reject unbalanced documents beyond ₹0.01, invalid required amounts/dates, and invalid baselines; metadata/classification issues remain warnings.
- Do not add CSV, legacy `.xls`, automatic column guessing, multi-currency consolidation, account-group inference, GST/provider work, or new schema/dependencies.
- Do not assume public Tally reachability. Do not add a bridge, agent, or network access change until the actual topology and owner approve it.
- Never log credentials, request bodies, raw Tally XML, or full uploaded artifacts.
- All code changes use TDD, one focused regression per boundary, and one review before commit.

## Files and ownership

- Modify `backend/app/ingestion/tally_http.py`: bounded Tally response buffering only if application-level enforcement is needed after deployment limits.
- Modify `backend/app/routers/scrutiny.py`: bounded multipart reads for XML, GL XLSX, baseline XLSX, and signed PDF.
- Modify `backend/app/ingestion/xlsx_normalizer.py` and `backend/app/ingestion/baseline.py`: reject oversized XLSX ZIP expansion before openpyxl parses it.
- Create `backend/app/ingestion/limits.py` only if the same bounded-read/ZIP check cannot stay small and local; keep one implementation per boundary.
- Test in `backend/tests/test_tally_http.py`, `backend/tests/test_api_integration.py`, `backend/tests/test_xlsx_ingestion.py`, and `backend/tests/test_baseline_ingestion.py`.
- Update `docs/tally-connector.md` with the selected pilot topology and redacted evidence; update `docs/phase-2-closure-runbook.md` with operational and browser checklists.
- Do not modify frontend behavior unless browser smoke proves a displayed state or file-flow defect.

---

### Task 1: Lock the operational limits and topology facts

**Files:**
- Create: `docs/phase-2-closure-runbook.md`
- Modify: `docs/tally-connector.md`

**Output:** A signed-off limit table and a concrete network decision before code or deployment changes.

- [ ] Record these initial limits in the runbook: 32 MiB per uploaded XML/XLSX, 16 MiB per signed PDF, 32 MiB maximum Tally response, 128 MiB maximum uncompressed XLSX ZIP members, and 10× compressed-to-uncompressed expansion ratio. Change them only with an explicit pilot requirement and corresponding tests.
- [ ] Record the actual backend host/network, Tally host/listener port, network owner, selected company, pilot financial year, and whether a managed private route already exists. Missing facts keep the live gate unverified.
- [ ] Select one approved topology: existing managed private route first; otherwise stop and obtain separate approval for any bridge/agent/network change. Do not infer a topology from the repository.
- [ ] Set the deployment policy to the exact Tally hostname in `TALLY_ALLOWED_HOSTS`; leave `TALLY_ALLOW_LOCAL_ENDPOINTS` unset for the pilot unless the pilot is explicitly local.
- [ ] Commit the runbook/topology proposal separately from code. Review must confirm no secret, raw XML, or invented environment fact entered the repository.

---

### Task 2: Enforce upload and parser resource bounds

**Files:**
- Modify: `backend/app/routers/scrutiny.py`
- Modify: `backend/app/ingestion/xlsx_normalizer.py`
- Modify: `backend/app/ingestion/baseline.py`
- Modify: `backend/app/ingestion/tally_http.py` if response buffering is not fully bounded by the chosen ingress
- Test: `backend/tests/test_api_integration.py`
- Test: `backend/tests/test_xlsx_ingestion.py`
- Test: `backend/tests/test_baseline_ingestion.py`
- Test: `backend/tests/test_tally_http.py`

**Interfaces:** Preserve existing route response shapes and `fetch_trial_balance(...) -> tuple[dict, bytes]`. Oversized input returns a safe client error and creates no active/imported accounting children.

- [ ] Write failing tests for XML, GL XLSX, baseline XLSX, and signed PDF over 32/32/32/16 MiB; assert a 413-style response, no new active batch, and no raw artifact persisted when the limit is crossed before staging.
- [ ] Write a failing XLSX ZIP-bomb test whose compressed members exceed 128 MiB or the 10× ratio; assert the normalizer rejects it before openpyxl reads workbook rows.
- [ ] Write a failing Tally response test where `Content-Length` is over 32 MiB and another where streamed chunks cross 32 MiB; assert `TallyConnectorError` without retaining the response or leaking XML.
- [ ] Implement one bounded chunk reader for `UploadFile`; stop reading at `limit + 1`, raise a typed/safe limit error, and keep the existing failed-batch behavior for inputs that were already intentionally staged.
- [ ] Implement ZIP central-directory size/ratio validation before both XLSX normalizers. Keep XML entity resolution disabled and preserve existing parser errors.
- [ ] Implement streamed Tally response collection with the same 32 MiB ceiling if ingress cannot guarantee it. Keep `follow_redirects=False` and existing endpoint validation.
- [ ] Run the focused tests:

```bash
cd backend
PYTHONPATH=. ../.venv/bin/pytest -q \
  tests/test_api_integration.py -k 'size or limit or baseline' \
  tests/test_xlsx_ingestion.py -k 'zip or expansion' \
  tests/test_baseline_ingestion.py -k 'zip or expansion' \
  tests/test_tally_http.py -k 'size or response'
```

- [ ] Run the full backend suite and commit only the resource-bound changes after Luna xhigh review.

---

### Task 3: Verify endpoint policy and approved network path

**Files:**
- Modify: `docs/tally-connector.md`
- Test: `backend/tests/test_tally_http.py`
- Deployment change: approved ingress/egress configuration, outside this repository unless a checked-in deployment file exists

- [ ] Run unit tests for malformed URLs, disallowed hosts, loopback opt-in, exact allowlisted DNS host, redirects, and credential-safe errors.
- [ ] From the real backend network, resolve the exact approved Tally hostname and verify the backend can reach only the approved listener/port through the selected private route or egress proxy.
- [ ] Confirm redirects remain rejected and the resolved destination cannot bypass the hostname/egress policy.
- [ ] Verify `TALLY_ALLOWED_HOSTS` contains only the approved exact hostname and `TALLY_ALLOW_LOCAL_ENDPOINTS` is unset unless explicitly required by the local pilot.
- [ ] Record redacted command output and the network owner’s approval in `docs/tally-connector.md`; never record credentials, request bodies, or raw XML.
- [ ] If DNS pinning is required by the threat model, implement it in the approved egress proxy/firewall first. Do not add ad-hoc DNS/IP behavior to the connector without a topology decision.

---

### Task 4: Run the isolated live Tally pilot

**Files:**
- Modify: `docs/tally-connector.md`
- Create: redacted evidence bundle outside Git; do not commit credentials or raw XML

- [ ] Use a test entity and pilot financial year. Send the existing read-only request for the approved company and period.
- [ ] Verify the live response contains account masters, closing balances, voucher headers, every voucher ledger line, dates, and stable identifiers where supplied.
- [ ] Import through the existing Tally route. Confirm `READY`, active batch lineage, raw hash, parser version, accounted rows, and deterministic dataset fingerprint.
- [ ] Run scrutiny and record only counts, statuses, fingerprints, and redacted identifiers.
- [ ] Exercise one safe failure: timeout or rejected response. Confirm the active dataset remains unchanged and no secret/raw XML appears in logs.
- [ ] Repeat the same response hash once. Confirm exact-duplicate idempotence and no second accounting import.
- [ ] Record date, environment, selected company/period, result, hashes, counts, and operator approval. Mark the gate **verified** only when all checks pass.

---

### Task 5: Run real-browser acceptance

**Files:**
- Modify: `docs/phase-2-closure-runbook.md`
- Modify: `frontend/src/components/XlsxUploadModal.tsx` only if a browser defect is reproduced
- Test: existing browser workflow; no new frontend dependency unless the repository already uses one

- [ ] Start backend and frontend with the approved local/pilot configuration. Use the existing browser-testing workflow and real file selection.
- [ ] Check Tally success and Tally failure. UI must show the API’s readiness/status/error without exposing raw XML or credentials.
- [ ] Check GL upload with a warning, missing baseline/coverage, and a valid baseline. UI counts/readiness/errors must match the API response.
- [ ] Upload a corrected replacement that fails validation. Confirm the prior active dataset remains visible and the failed replacement is reported.
- [ ] Verify file-picker state, loading state, duplicate state, and retry behavior. Capture screenshots or redacted notes for each result.
- [ ] If a UI defect is found, add one failing component/integration regression, make the smallest fix, rerun lint/build, and obtain review. If no defect is found, change docs only.

---

### Task 6: Close the Phase 2 release gate

**Files:**
- Modify: `docs/phase-2-closure-runbook.md`
- Modify: `docs/tally-connector.md`
- Modify: `docs/superpowers/plans/2026-09-07-phase-2-reliable-ingestion.md`

- [ ] Run the final commands from a clean Phase 2 diff:

```bash
cd backend && PYTHONPATH=. ../.venv/bin/pytest -q
cd ../frontend && npm run lint && npm run build
cd .. && git diff --check
```

- [ ] Confirm the final review covers raw artifact retention, hash/idempotence, failed replacement atomicity, source-family isolation, tenant isolation, resource limits, and no unrelated changes.
- [ ] Mark each gate `verified`, `unverified`, or `blocked` with evidence location. Do not convert missing topology, live Tally, or browser evidence into a passing claim.
- [ ] Commit documentation and any approved code separately, then request a final Luna xhigh review.
- [ ] Only after all required gates are verified, declare Phase 2 operationally closed. Otherwise report the exact remaining gate and owner.

## Self-review checklist

- [ ] No placeholder values remain: the limit table above is the default to test and deploy unless explicitly changed and recorded.
- [ ] No code task changes the canonical GL headers, signs, lifecycle statuses, source-family policy, schema, or rule logic.
- [ ] Every trust boundary has a negative test for oversize or unsafe input.
- [ ] Live claims have redacted evidence; mock tests are not labelled as live proof.
- [ ] Browser, topology, and resource-limit gates have separate evidence from backend unit tests.
