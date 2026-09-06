# Phase 1 Compliance Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver an explicit compliance pack, available through the API, with deterministic TDS findings and persisted rule-set versions by 18 September 2026.

**Architecture:** Add `compliance.py` alongside the existing rules and retain `run_scrutiny` as the engine entry point. Move the existing TDS implementation into that module unchanged and re-export it from the engine; each pack calls it exactly once. Reuse the router's existing run versioning, fingerprints, and review preservation.

**Tech Stack:** Existing Python, FastAPI, SQLAlchemy, Decimal, pytest.

**Spec:** `docs/superpowers/plans/2026-09-04-ledger-scrutiny-roadmap.md`, Phase 1, Tasks 1.1–1.2. Read that roadmap alongside this plan. The user’s 6 September scope revision defers all new GST/GSTIN work; this revision supersedes earlier GST requirements for Phase 1.

## Global Constraints

- Delivery window: 4 September–4 November 2026.
- Compliance findings stay deterministic, explainable, versioned, and reviewable. LLMs do not make audit decisions.
- Preserve immutable import batches, source hashes, rule versions, exception fingerprints, review statuses, and auditor notes.
- Use existing dependencies. No state-management library, rule framework, or provider SDK without a failing requirement.
- Every code task follows: failing test, focused test run, minimal implementation, focused plus full suite, small commit, fresh review.
- Never run two workers against the same write set. Execute dependent tasks serially; parallelize only disjoint docs, tests, or UI work.
- Do not modify `backend/app/db/models.py` or add an Alembic migration unless a test demonstrates missing provenance cannot fit existing fields.
- Phase 1 dates: 8–18 September. All new GST/GSTIN checks, provider adapters, response mappings, credentials, and integration tests are deferred. No assumed GST format or provider contract belongs in this phase. Existing GST functionality is outside this change.
- No new tax formulas, tax rates, return reconciliation, portal integration, account-group aliases, or frontend work. Those need separate evidence/requirements.

## Code findings that affect implementation

Verified against the current checkout on 6 September using Tier 2 graph discovery, call tracing, and direct source reads. Graph generation: `2026-09-06T13:47:05Z`; coverage metadata matched for the seven inspected roadmap/source/test files, with no recorded issues. This is a best-effort coverage signal, not proof of exhaustive indexing.

1. `engine.py:140` already implements TDS, and `run_scrutiny` calls it for every pack. Adding the compliance dispatcher without removing that shared invocation would duplicate warnings.
2. `create_entity` in `routers/scrutiny.py:603` accepts only `None` and `manufacturing_v1`. A compliance engine without API acceptance would be inaccessible through normal entity creation. Task 2 is a necessary addition to the roadmap's file list.
3. `trigger_scrutiny_run` already stores `ScrutinyRun.rule_set_version`, copies it to every finding's `rule_version`, and carries reviews forward through fingerprints. Test this path before changing it.
4. Fingerprints include normalized message text. Preserve the existing TDS message exactly in this phase so existing reviewed findings retain identity.
5. The current TDS predicate is `creditor_total >= materiality_threshold`. At a zero threshold, even zero creditors produces a warning. Preserve and explicitly characterize this existing behavior; use a positive threshold for clean-entity tests. Correcting that predicate would be a separate behavior/version decision, not an accidental pack refactor.
6. The manufacturing pitch test's name says five findings, but its current assertion is eight. The existing eight findings must remain unchanged.

## Task order and schedule

| Task | Dates | Depends on | Reviewable output |
| --- | --- | --- | --- |
| 1. Compliance routing and versions | 8–11 Sep | Baseline and existing TDS behavior characterized | Exactly-once TDS, core fallback, composite versions |
| 2. API pack acceptance | 14 Sep | Task 1 | Entities can select compliance |
| 3. Persisted findings and phase gate | 15–18 Sep | Tasks 1–2 | Version/review regression checks and acceptance evidence |

Dates are implementation/review windows, not measured effort estimates. Execute serially because the tests and engine overlap. No GST API, credentials, or schema is required. This phase adds an explicit versioned pack boundary, not a new tax determination: its initial findings intentionally match existing core TDS behavior.

Before edits, run `PYTHONPATH=. ../.venv/bin/pytest -q` from `backend` and record the baseline. Separate pre-existing failures from regressions; do not claim the phase gate passes with unresolved relevant failures.

## File map

| File | Responsibility/change |
| --- | --- |
| Create `backend/app/rules/compliance.py` | Relocated unchanged TDS check and compliance dispatcher |
| Modify `backend/app/rules/engine.py` | Import/re-export TDS, select pack, return composite version |
| Create `backend/tests/test_compliance_rules.py` | TDS boundary/evidence tests and GST independence |
| Modify `backend/tests/test_rules_engine.py` | Pack composition, fallback, deterministic output, materiality |
| Modify `backend/app/routers/scrutiny.py` | Add compliance to entity-creation allowlist |
| Modify `backend/tests/test_api_integration.py` | Pack acceptance, persisted version and review regression |
| Update this plan during execution | Checkboxes, command results, phase gate evidence |

Run existing `backend/tests/test_manufacturing_rules.py` unchanged. Read models to understand existing provenance; no schema change is planned.

## Task 1: Compose packs without duplicating TDS

**Files:** Create `backend/app/rules/compliance.py` and `backend/tests/test_compliance_rules.py`; modify `backend/app/rules/engine.py` and `backend/tests/test_rules_engine.py`.

**Interfaces:** Keep `run_scrutiny(..., rule_pack: str | None = None)` and `rule_set_version(rule_pack: str | None) -> str`. Add `run_compliance_checks(entity, accounts, snapshots, period_start, period_end) -> list[AuditException]`, using the typed parameters shown below. Keep `from app.rules.engine import tds_liability_check` working through a re-export.

| Engine argument | Effective checks | Version |
| --- | --- | --- |
| `None`, unknown string | Existing core including TDS once | `core-v1` |
| `compliance_v1` | Existing core with TDS routed through compliance once | `core-v1+compliance-v1` |
| `manufacturing_v1` | Existing core and both manufacturing rules; TDS routed through compliance once | `core-v1+compliance-v1+manufacturing-v1` |

The explicit argument remains authoritative; do not silently change omission to read `entity.rule_pack`.

- [x] Add a parameterized engine contract test with imports for `pytest` and `rule_set_version`:

```python
@pytest.mark.parametrize("pack, version", [
    (None, "core-v1"),
    ("unknown", "core-v1"),
    ("compliance_v1", "core-v1+compliance-v1"),
    ("manufacturing_v1", "core-v1+compliance-v1+manufacturing-v1"),
])
def test_pack_contract(pack, version):
    start, end = date(2025, 4, 1), date(2026, 3, 31)
    entity = Entity(id=1, name="Example",
                    materiality_threshold=Decimal("100"))
    account = LedgerAccount(id=1, entity_id=1, name="Supplier",
                            group_name="Sundry Creditors", normal_balance="credit")
    snapshot = TrialBalanceSnapshot(
        entity_id=1, ledger_account_id=1, period_start=start, period_end=end,
        opening_balance=Decimal("-100"), closing_balance=Decimal("-100"),
        total_debits=Decimal("0"), total_credits=Decimal("0"),
    )
    findings = run_scrutiny(entity, [account], [snapshot], start, end, pack)
    names = [finding.rule_name for finding in findings]
    assert names.count("tds_liability_check") == 1
    assert set(names) == {"tds_liability_check", "trial_balance_balances"}
    assert rule_set_version(pack) == version
    repeated = run_scrutiny(entity, [account], [snapshot], start, end, pack)
    signature = lambda f: (f.rule_name, f.severity, f.message, f.period_start,
                           f.period_end, getattr(f, "variance", None))
    assert list(map(signature, findings)) == list(map(signature, repeated))
```

- [x] Create `test_compliance_rules.py` with the imports, dates, and TDS characterization test below:

```python
from datetime import date
from decimal import Decimal

import pytest

from app.db.models import Entity, LedgerAccount, TrialBalanceSnapshot
from app.rules.compliance import run_compliance_checks

START, END = date(2025, 4, 1), date(2026, 3, 31)


@pytest.mark.parametrize("creditors, tds, threshold, expected", [
    ("99", "0", "100", 0), ("100", "0", "100", 1),
    ("101", "0", "100", 1), ("100", "-1", "100", 0),
    ("100", "1", "100", 1), ("0", "0", "100", 0),
    ("0", "0", "0", 1),
])
def test_compliance_preserves_tds_evidence(creditors, tds, threshold, expected):
    entity = Entity(id=1, name="Example",
                    materiality_threshold=Decimal(threshold))
    accounts = [
        LedgerAccount(id=1, entity_id=1, name="Supplier",
                      group_name="Sundry Creditors", normal_balance="credit"),
        LedgerAccount(id=2, entity_id=1, name="TDS payable",
                      group_name="Duties & Taxes", normal_balance="credit"),
    ]
    snapshots = [TrialBalanceSnapshot(
        entity_id=1, ledger_account_id=account.id, period_start=START,
        period_end=END, opening_balance=Decimal("0"), closing_balance=closing,
        total_debits=Decimal("0"), total_credits=Decimal("0"),
    ) for account, closing in zip(accounts, [-Decimal(creditors), Decimal(tds)])]
    findings = run_compliance_checks(entity, accounts, snapshots, START, END)
    assert len(findings) == expected
    if expected:
        finding = findings[0]
        assert finding.rule_name == "tds_liability_check"
        assert finding.severity == "warning"
        assert finding.variance == Decimal(creditors)
        assert (finding.period_start, finding.period_end) == (START, END)
        assert finding.message == (
            f"Total Sundry Creditors balance is {Decimal(creditors):.2f} "
            "(exceeds materiality), but no TDS liability account with a payable "
            "balance was found. Verify if TDS is applicable and has been deducted."
        )
```

- [x] Run `PYTHONPATH=. ../.venv/bin/pytest -q tests/test_compliance_rules.py tests/test_rules_engine.py` from `backend`; expect missing dispatcher and failed pack/version assertions.
- [x] Move the entire existing `tds_liability_check` definition from the engine into `compliance.py` unchanged, adding imports for `date`, `Decimal`, `List`, and the four model types `AuditException`, `Entity`, `LedgerAccount`, `TrialBalanceSnapshot`. Import it and `run_compliance_checks` into the engine. This creates one implementation with one-way module imports; do not have compliance import the engine.
- [x] Add this dispatcher:

```python
def run_compliance_checks(
    entity: Entity,
    accounts: list[LedgerAccount],
    snapshots: list[TrialBalanceSnapshot],
    period_start: date,
    period_end: date,
) -> list[AuditException]:
    return tds_liability_check(entity, accounts, snapshots, period_start, period_end)
```

- [x] Replace `rule_set_version` with:

```python
def rule_set_version(rule_pack: str | None) -> str:
    if rule_pack == "manufacturing_v1":
        return f"{CORE_RULE_SET_VERSION}+compliance-v1+manufacturing-v1"
    if rule_pack == "compliance_v1":
        return f"{CORE_RULE_SET_VERSION}+compliance-v1"
    return CORE_RULE_SET_VERSION
```

- [x] Replace only the existing TDS summand in `run_scrutiny` with this conditional expression. Retain the other core checks, both manufacturing calls, and the final materiality filter:

```python
+ (
    run_compliance_checks(entity, accounts, snapshots, period_start, period_end)
    if rule_pack in {"compliance_v1", "manufacturing_v1"}
    else tds_liability_check(entity, accounts, snapshots, period_start, period_end)
)
```

- [x] Add this regression proving clean output and independence from GST data for every supported pack. No format is interpreted or validated:

```python
@pytest.mark.parametrize("pack", [None, "unknown", "compliance_v1", "manufacturing_v1"])
@pytest.mark.parametrize("gstin", [None, "", "arbitrary-unvalidated-value"])
def test_clean_pack_does_not_depend_on_gst(pack, gstin):
    start, end = date(2025, 4, 1), date(2026, 3, 31)
    entity = Entity(id=1, name="Example", gstin=gstin,
                    materiality_threshold=Decimal("100"))
    assert run_scrutiny(entity, [], [], start, end, pack) == []
    assert entity.gstin == gstin
```

- [x] Add a routing test, since output equality alone cannot prove the dispatcher is actually used. Add `from app.rules import engine` to `test_rules_engine.py`:

```python
@pytest.mark.parametrize("pack, expected_calls", [
    (None, 0), ("unknown", 0), ("compliance_v1", 1), ("manufacturing_v1", 1),
])
def test_compliance_dispatch_is_explicit(monkeypatch, pack, expected_calls):
    calls = []
    def record(*args):
        calls.append(args)
        return []
    monkeypatch.setattr(engine, "run_compliance_checks", record)
    entity = Entity(id=1, name="Example", rule_pack="compliance_v1",
                    materiality_threshold=Decimal("100"))
    start, end = date(2025, 4, 1), date(2026, 3, 31)
    engine.run_scrutiny(entity, [], [], start, end, pack)
    assert len(calls) == expected_calls
    if calls:
        assert calls[0] == (entity, [], [], start, end)
```

- [x] Run focused tests including `tests/test_manufacturing_rules.py`, then the full backend suite. The current static-rules test must remain unchanged and pass. Record results, obtain fresh review, and commit `feat: add versioned compliance rule pack`.

**Acceptance:** Core fallback is preserved, TDS is reused once, compliance is included in manufacturing, warnings are deterministic, versions match the table, and existing manufacturing formulas are untouched.

## Task 2: Make the pack selectable through the API

**Files:** Modify `backend/app/routers/scrutiny.py` and `backend/tests/test_api_integration.py`.

**Interfaces:** Existing `POST /entities` request/response fields. `rule_pack="compliance_v1"` is accepted without a manufacturing-sector requirement. Unknown API pack values still return 400; engine fallback remains a separate compatibility behavior.

- [x] Add this test using the existing `client` and `get_auth_headers`:

```python
@pytest.mark.parametrize("pack, sector, expected", [
    ("compliance_v1", None, 201),
    (None, None, 201),
    ("manufacturing_v1", "manufacturing", 201),
    ("manufacturing_v1", None, 400),
    ("unknown", None, 400),
])
def test_entity_rule_pack_contract(pack, sector, expected):
    response = client.post("/entities", headers=get_auth_headers(), json={
        "name": "Pack contract", "materiality_threshold": "100.00",
        "rule_pack": pack, "sector": sector,
    })
    assert response.status_code == expected, response.text
    if expected == 201:
        assert response.json()["rule_pack"] == pack
```

- [x] Run `PYTHONPATH=. ../.venv/bin/pytest -q tests/test_api_integration.py -k entity_rule_pack_contract` from `backend`; expect compliance creation to fail with 400.
- [x] Change the existing allowlist condition to:

```python
if entity_in.rule_pack not in (None, "compliance_v1", "manufacturing_v1"):
```

- [x] Run the focused test and full backend suite; record results, obtain fresh review, and commit `feat: allow compliance pack on entities`.

**Acceptance:** Normal entity creation exposes the pack without weakening existing authentication or manufacturing validation. No new endpoint, schema, or UI is needed for this backend foundation.

## Task 3: Verify stored versions, review preservation, and manufacturing compatibility

**Files:** Modify `backend/tests/test_api_integration.py`; update this plan with gate results. Modify router provenance code only if this test exposes a defect.

**Interfaces:** Existing upload, scrutiny-run, exception-list, and review routes; `ScrutinyRun.rule_set_version`, `AuditException.rule_version`, `fingerprint`, `status`, and `auditor_notes`.

- [x] Add `AuditException` to the test's model imports. Add this regression using the existing authenticated client, `_canonical_tally_xml`, `Path`, and `TestingSessionLocal`. Reuse the manufacturing fixture because it contains material creditors without a qualifying TDS payable balance; the generic sample export does not:

```python
def test_compliance_versions_and_review_survive_rerun():
    headers = get_auth_headers()
    created = client.post("/entities", headers=headers, json={
        "name": "Compliance history", "materiality_threshold": "100.00",
        "rule_pack": "compliance_v1",
    })
    assert created.status_code == 201, created.text
    entity_id = created.json()["id"]
    fixture = (Path(__file__).parents[2] / "sample_data" /
               "pitch_manufacturing_demo" / "meridian_fy2025_26.xml")
    contents = _canonical_tally_xml(fixture.read_bytes(), add_voucher_if_missing=True)
    upload = client.post(f"/entities/{entity_id}/upload", headers=headers,
                         files={"file": ("ready.xml",
                                contents,
                                "text/xml")})
    assert upload.status_code == 200, upload.text
    params = {"period_start": "2025-04-01", "period_end": "2026-03-31"}
    run_ids = []
    fingerprint = None
    for attempt in range(2):
        response = client.post(f"/entities/{entity_id}/scrutiny-run",
                               params=params, headers=headers)
        assert response.status_code == 200, response.text
        run_id = response.json()["scrutiny_run_id"]
        run_ids.append(run_id)
        with TestingSessionLocal() as session:
            run = session.get(ScrutinyRun, run_id)
            assert run.rule_set_version == "core-v1+compliance-v1"
            assert run.dataset_fingerprint == upload.json()["dataset_fingerprint"]
            assert run.source_batch_ids == upload.json()["source_batch_ids"]
            rows = session.scalars(select(AuditException).where(
                AuditException.scrutiny_run_id == run_id)).all()
            assert rows
            assert all(row.rule_version == run.rule_set_version for row in rows)
            tds_rows = [row for row in rows if row.rule_name == "tds_liability_check"]
            assert len(tds_rows) == 1
            finding = tds_rows[0]
            assert finding.fingerprint
            finding_id = finding.id
            if attempt == 0:
                fingerprint = finding.fingerprint
            else:
                assert finding.fingerprint == fingerprint
                assert finding.status == "CLEARED"
                assert finding.auditor_notes == "TDS evidence reviewed."
        if attempt == 0:
            reviewed = client.patch(f"/entities/{entity_id}/exceptions/{finding_id}", headers=headers,
                                    json={"status": "CLEARED",
                                          "auditor_notes": "TDS evidence reviewed."})
            assert reviewed.status_code == 200, reviewed.text
    assert run_ids[0] != run_ids[1]
    with TestingSessionLocal() as session:
        assert session.get(ScrutinyRun, run_ids[0]) is not None
        previous = session.scalars(select(AuditException).where(
            AuditException.scrutiny_run_id == run_ids[0],
            AuditException.rule_name == "tds_liability_check")).one()
        assert previous.status == "CLEARED"
        assert previous.auditor_notes == "TDS evidence reviewed."
```

- [x] Run `PYTHONPATH=. ../.venv/bin/pytest -q tests/test_api_integration.py -k compliance_versions_and_review`. This is a regression check over existing persistence; passing immediately is expected. Do not manufacture a production change merely to create a red test.
- [x] In the existing manufacturing pitch test, after asserting a successful run, add:

```python
with TestingSessionLocal() as session:
    stored_run = session.get(ScrutinyRun, run.json()["scrutiny_run_id"])
    assert stored_run.rule_set_version == "core-v1+compliance-v1+manufacturing-v1"
    stored_findings = session.scalars(select(AuditException).where(
        AuditException.scrutiny_run_id == stored_run.id)).all()
    assert all(finding.rule_version == stored_run.rule_set_version
               for finding in stored_findings)
```

- [x] Run `PYTHONPATH=. ../.venv/bin/pytest -q tests/test_api_integration.py -k 'compliance_versions_and_review or manufacturing_pitch or exception_review'`. Preserve the manufacturing test's existing eight findings; no new GST-related finding may appear.
- [x] Run the full backend suite: `PYTHONPATH=. ../.venv/bin/pytest -q` from `backend`.
- [x] Run roadmap baseline checks from `frontend`: `npm run lint` and `npm run build`. These are release-baseline checks; this phase has no frontend edits.
- [x] Record command outcomes and any pre-existing failures below. Obtain fresh review of the phase diff, then commit `test: verify compliance finding provenance and reviews`.

**Acceptance:** Compliance and manufacturing versions are stored on runs and findings; a reviewed TDS warning retains its fingerprint, status, and notes across reruns; the earlier run remains queryable. Existing lineage and manufacturing findings remain intact.

## Phase 1 exit gate

- [x] Compliance executes without GST data, credentials, or provider calls; no new GST-related warning is emitted.
- [x] TDS is called once for each pack; existing threshold, evidence, message, and variance behavior is preserved.
- [x] Clean entities at positive materiality return no compliance findings regardless of the stored GST value.
- [x] `None`/unknown engine pack behavior remains core; unknown API pack remains rejected.
- [x] Composite versions exactly match the contract table and are persisted on every finding.
- [x] Manufacturing retains both existing checks and its eight-findings demo regression.
- [x] Reviews and prior-run provenance survive reruns.
- [x] Messages name only available evidence and do not assert statutory compliance.
- [x] Focused and full backend tests pass; frontend baseline outcomes are recorded.
- [x] No dependency, schema, account alias, portal, or frontend expansion entered the diff.

**Execution record (6 September 2026, Task 3):** Parent-provided phase baselines were 199 passed before Phase 1, 226 passed after Task 1, and 231 passed after Task 2; warnings remained the pre-existing deprecation warnings. The Task 2 focused command/result was `PYTHONPATH=. ../.venv/bin/pytest -q tests/test_api_integration.py -k entity_rule_pack_contract` from `backend`: exit 0, `5 passed, 29 deselected, 3 warnings`. The pre-edit focused selector returned exit 5 with `34 deselected, 3 warnings` because the new regression did not yet exist; this was test discovery, not a product failure. The recorded plan change below is the Task 3 incremental diff. After the Task 3 test-only changes:

- `PYTHONPATH=. ../.venv/bin/pytest -q tests/test_api_integration.py -k compliance_versions_and_review`: exit 0, `1 passed, 34 deselected, 3 warnings`.
- `PYTHONPATH=. ../.venv/bin/pytest -q tests/test_api_integration.py -k 'compliance_versions_and_review or manufacturing_pitch or exception_review'`: exit 0, `3 passed, 32 deselected, 3 warnings`; manufacturing retained 8 findings and emitted no GST-related finding.
- `PYTHONPATH=. ../.venv/bin/pytest -q`: exit 0, `232 passed, 4 warnings in 21.52s`.
- `npm run lint` from `frontend`: exit 0; `oxlint` emitted no diagnostics.
- `npm run build` from `frontend`: exit 0; TypeScript and Vite completed, 18 modules transformed.

TDD evidence: the existing persistence regression passed immediately as specified, so no failing red regression was manufactured and no production code was changed. Self-review found no correctness, scope, security, performance, or readability findings; `git diff --check` passed. The diff contains only `backend/tests/test_api_integration.py` and this execution record; models, migrations, router provenance, dependencies, frontend source, and unrelated `.codebase-memory/` remain untouched. No pre-existing test failures were observed; the four backend warnings are the existing FastAPI/Starlette/AnyIO deprecations.

## Deferred GST work — separate future plan

Resume only after a provider is selected and its documentation, representative redacted success/error responses, authentication requirements, and sandbox/test access are available. Then agree the response mapping, validation scope, timeout/error behavior, evidence retention, and limits of claims before implementing. Do not create adapter stubs, guessed fixtures, or speculative schemas now.

Any later addition of GST rules must introduce a new compliance rule-set version rather than silently changing `compliance-v1`. Keep historical runs on their stored versions and explicitly test review/fingerprint behavior across the upgrade. No deadline or provider payload is assumed here.

**Self-review:** Revised roadmap Task 1.1 is covered by Tasks 1–2; Task 1.2 by Task 1 and the exit gate. Task 3 verifies versioned findings through persistence. All GST implementation and format assumptions are deferred. Pack/version strings are consistent across tasks; the existing zero-threshold TDS behavior is explicitly characterized.
