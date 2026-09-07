# Task 2.6 — Final validation and pilot evidence

## Status

Automated validation passed at the final Phase 2 commit (`15aa2eb`). No
production code or tests were changed for Task 2.6; this report is the only
Task 2.6 artifact added. Browser file-driven scenarios and live Tally
compatibility remain unverified because the available browser surface cannot
set a native file chooser and no live Tally endpoint/topology was supplied.

The Task 2.6 brief and the full plan were read before validation:

- `.superpowers/sdd/2026-09-07-phase-2-reliable-ingestion/task-2.6-brief.md`
- `docs/superpowers/plans/2026-09-07-phase-2-reliable-ingestion.md`

## Fresh automated gates

All commands below were run after the final Phase 2 change, from the requested
directories.

### Backend

```text
cd backend
PYTHONPATH=. ../.venv/bin/pytest -q
```

```text
249 passed, 5 warnings in 22.76s
```

The five warnings were the existing Starlette/httpx and FastAPI lifecycle
deprecations, the existing `HTTP_422_UNPROCESSABLE_ENTITY` deprecation, and
the existing SQLAlchemy snapshot warning at
`backend/app/routers/scrutiny.py:1300`. No test failed.

### Frontend lint

```text
cd frontend
npm run lint
```

```text
> frontend@0.0.0 lint
> oxlint
```

Exit status: `0`.

### Frontend build

```text
npm run build
```

```text
vite v8.1.4 building client environment for production...
✓ 19 modules transformed.
✓ built in 374ms
```

Exit status: `0`.

### Supplied Q1 workbook

The supplied workbook was available locally and the conditional test ran:

```text
cd backend
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_xlsx_ingestion.py -k supplied_q1 -rs
```

```text
1 passed, 32 deselected, 3 warnings in 8.31s
```

Artifact evidence:

| Field | Observed value |
|---|---|
| Path | `/Users/adinayak18/Downloads/GL Dump Q1.XLSX` |
| Availability | Present |
| Size | `6,483,523` bytes |
| SHA-256 | `946ff760638f78854556f8a761506cbd5b4a853d6f6970f852d850743e3149d2` |
| Worksheet/range | `Sheet1`, `A1:U59169` |
| Accepted journal lines | `59,167` |
| Skipped rows | `1` — source row `59169`, `LIABILITY TOTAL`, summary/footer row |
| Rejected rows | `0` |
| Documents | `10,914` |
| Distinct accounts | `445` |
| Signed net | `0.00` |

These counts are the existing fixed-profile parser assertions and the measured
Task 2.3 reconciliation; the final conditional test passed without changing
the expected counts.

## Browser smoke

The configured browser inventory exposed only the isolated Codex In-app
Browser; Chrome DevTools MCP was not configured. The documented frontend-only
demo server was started on loopback after the sandbox denied the initial bind:

```text
cd frontend
npm run dev -- --host 127.0.0.1
```

Observed server URL: `http://127.0.0.1:5173/`. The page loaded in the in-app
browser.

Visible demo observations:

- Reset/demo workspace loaded the fictional Meridian workspace.
- Q1 showed source `XLSX_GL`, five material findings, three critical findings,
  40% review progress, and the existing warning/critical finding messages.
- Switching to FY 2024-25 showed one finding, `XLSX_TRIAL_BALANCE` source, and
  100% review progress.
- Clicking `Run Scrutiny Rules` visibly changed the control to the disabled
  `Running Rules...` state and then returned to `Run Scrutiny Rules` with the
  findings visible.
- The Excel import modal displayed the fixed required headers and period
  fields.

The in-app browser's `Choose File` control did not expose a native file chooser
or a settable file-input API. Therefore these file-driven browser gates were
not executed and are explicitly **unverified**:

- Tally success import;
- Tally failure/safe error;
- GL upload returning warnings and displayed counts/readiness/errors;
- missing baseline/coverage blocking scrutiny;
- failed replacement preserving the prior active dataset.

The demo page is frontend-only, so the visible mock state was not treated as
API parity evidence. The local dev-server output also surfaced two
`GSI_LOGGER` warnings about repeated Google initialization while switching
workspace modes; this is outside the Task 2.6 ingestion scope and was not
changed.

## Live Tally and topology

Live Tally compatibility is **unverified**. No approved endpoint, company,
period, credentials, backend deployment host, Tally host, listener port,
network owner, or managed private route was available/reachable for the
read-only pilot procedure in `docs/tally-connector.md`.

No public reachability, bridge, agent, private-address exception, or network
configuration was added. The connector document continues to mark the remote
topology and live pilot prerequisites as unverified. Mock connector tests and
the local Q1 workbook are not live-environment evidence.

## Plan acceptance checklist

| Exit-gate item | Evidence/status |
|---|---|
| Both ingestion paths preserve canonical data and provenance | Automated evidence passes in the full backend suite and the Task 2.2–2.5 reports; no new failure exposed. |
| Invalid replacements are atomic | Task 2.4 lifecycle regressions are included in the fresh full suite and passed; no new failure exposed. |
| Every source row has an explained outcome | Q1 conditional test passed; measured reconciliation is 59,167 accepted + 1 skipped + 0 rejected from 59,168 post-header rows. |
| Scrutiny only runs on eligible datasets | Task 2.4 readiness and Task 2.5 parity coverage are included in the fresh full suite and passed. |
| Live-environment claims have separate evidence | No live endpoint/topology was available; live Tally remains unverified. |
| Browser workflow evidence | Partial visible demo smoke only; file-driven paths remain unverified because of the exact file-chooser blocker above. |

## Diff, safety, and ownership review

The Phase 2 commit range reviewed was `a843412..15aa2eb`:

- `19` files changed, `2,451` insertions, `30` deletions;
- production changes are limited to existing baseline, batch lifecycle, XLSX
  normalization, and scrutiny-router boundaries;
- no frontend files are changed by the Phase 2 commit range;
- no schema/migration change, public endpoint, bridge/agent, network change,
  or new GST production integration was found;
- the active Phase 2 plan keeps GST provider work deferred, and the connector
  documentation prohibits logging credentials, request bodies, or raw XML;
- source hashes, counts, status, lineage, and safe validation metadata were
  recorded here; raw source bytes/XML were not copied into this report.

`git diff --check` passed with no whitespace errors.

Unrelated/user-owned worktree changes were preserved and not staged:

```text
D  .superpowers/sdd/2026-09-06-phase-1-compliance-foundation/task-1-report.md
M  docs/pitch-demo.md
M  frontend/src/App.tsx
M  frontend/src/components/XlsxUploadModal.tsx
M  frontend/src/mockData.ts
?? .codebase-memory/
?? backend/tests/test_demo_gl_contract.py
?? frontend/src/demo_gl_profile.json
```

The Task 2.6 report is intentionally separate from those changes. Its parent
directory is ignored by `.superpowers/sdd/.gitignore`, so it would need to be
force-added for a future evidence-only commit.

## Commit status and interruption

No commit was created, per the final instruction. The report remains an
unstaged, ignored worktree file.

The exact staging attempts were:

```text
git add -f -- .superpowers/sdd/2026-09-07-phase-2-reliable-ingestion/task-2.6-report.md
```

The sandbox attempt failed immediately with:

```text
fatal: Unable to create '/Users/adinayak18/Desktop/ledger-scrutiny/.git/index.lock': Operation not permitted
```

The escalated retry was aborted by the user after `98.0s` without a command
result. A final read-only check showed no `.git/index.lock` and an empty
`git diff --cached --name-status`. No application code was changed and no
commit was made.

## Remaining pilot blockers

1. Provide an approved private Tally endpoint, selected company/period, and
   deployment/network facts for the read-only live smoke.
2. Run the browser file-upload scenarios with a browser/DevTools surface that
   can set local files, then compare rendered API response fields directly.

Until those prerequisites are supplied, the automated gates and local fixture
evidence pass, but the full pilot exit gate is not a live-environment sign-off.
