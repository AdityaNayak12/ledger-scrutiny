# Task 2.2 report — Verify Tally connectivity and safe failures

## Status and scope

Verification completed with no demonstrated production defect. The existing
Tally HTTP/parser/normalizer and protected API paths satisfy the listed cases,
so no test regression or production code change was made.

Files changed:

- `docs/tally-connector.md`: added the explicitly unverified remote-topology
  proposal and read-only pilot smoke procedure.
- `.superpowers/sdd/2026-09-07-phase-2-reliable-ingestion/task-2.2-report.md`:
  this report.

Not changed: `backend/app/ingestion/tally_http.py`,
`backend/app/ingestion/tally_parser.py`,
`backend/app/ingestion/tally_normalizer.py`,
`backend/app/routers/scrutiny.py`, the three focused test files, and the
verified `.env.example`. No remote deployment configuration was agreed, so no
environment value was added.

## Tests and outputs

The exact command from the brief was run before the documentation edit:

```text
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_tally_http.py tests/test_tally_ingestion.py tests/test_api_integration.py -k tally
66 passed, 23 deselected, 3 warnings in 3.99s
```

The same focused command was rerun after the documentation edit:

```text
66 passed, 23 deselected, 3 warnings in 3.40s
```

The full relevant three-file check was also run:

```text
PYTHONPATH=. ../.venv/bin/pytest -q tests/test_tally_http.py tests/test_tally_ingestion.py tests/test_api_integration.py
89 passed, 3 warnings in 7.81s
```

All three runs exited with status 0. The warnings are unchanged dependency or
framework deprecations: Starlette's `httpx` compatibility import and FastAPI
`on_event` startup handlers.

Inspected acceptance coverage:

- HTTP transport: timeout, connection/HTTP failure, disabled redirects, and
  safe status mapping in `tests/test_tally_http.py`.
- XML/parser: malformed XML, rejected status, absent status, absent closing
  balance, empty ledgers, empty vouchers, malformed amounts, unbalanced
  vouchers, selected company/period, dates, and stable source identifiers.
- Normalization: hard canonical failures for missing entity data, missing
  closing balance, empty canonical vouchers, unknown voucher ledgers, source
  period mismatch, and partial canonical records; successful multi-line rows,
  balances, dates, group hierarchy, replay, and provenance.
- Protected API: successful connector import retains raw response provenance
  and safe endpoint metadata; connector and parse failures return structured
  safe details without credentials, endpoint paths, or source XML; failed
  batches do not replace active data.
- Endpoint policy: invalid schemes, non-global/private/link-local/reserved
  targets, localhost without opt-in, exact configured hostnames, and no
  redirect bypass. Rejected endpoints are asserted not to make an HTTP call.

No listed acceptance case was missing. Therefore there is no RED/GREEN TDD
cycle to report; adding a synthetic regression or changing behavior would not
be justified by the evidence.

## Remote-connectivity proposal (unverified)

The repository cannot discover the live deployment topology. The proposed
pilot shape is:

- Backend: the approved pilot backend deployment and its execution network;
  host/location currently unverified.
- Tally: a customer-controlled TallyPrime workstation or server with the
  XML-over-HTTP listener enabled on an approved interface; host and port
  currently unverified.
- Network owner: the customer or its IT/network team controls the listener,
  firewall, and route approvals.
- Route: prefer an existing managed VPN, private link, or equivalent private
  route from the backend network to the Tally network; existence and firewall
  reachability are unverified.
- Supported endpoint/configuration: a concrete `http` or `https` URL with a
  valid host and port, for example
  `http://tally-pilot.internal:<confirmed-port>`, with the exact hostname in
  `TALLY_ALLOWED_HOSTS`. The hostname is a placeholder, not a verified fact;
  the current local example uses port `9000`. Deployed pilots leave
  `TALLY_ALLOW_LOCAL_ENDPOINTS` unset. Hostname validation is exact and does
  not perform DNS lookups; HTTP redirects are disabled.

Environment confirmation is still required for the backend host/network,
Tally host/listener port, network owner, managed private route/firewall, and
the selected company/period's availability through the read-only export. This
task intentionally adds no bridge, agent, public exposure, network access
change, or silent private-IP exception.

## Pilot procedure

The updated `docs/tally-connector.md` contains the runnable procedure. In
summary, record the approved company and inclusive period, configure only the
exact allowlisted Tally hostname, call the protected import route, and record
the response's `validation_report.ledger_count`,
`validation_report.document_count`, and `validation_report.accepted_rows`.
Verify active/readiness state, dates, source identifiers, empty errors, active
lineage, and retained raw response bytes. Then test a deliberately unavailable
listener port on the exact allowlisted host and expect a structured HTTP 400
with no credentials or source payload and no active-data replacement.

Live Tally compatibility remains unverified until this smoke run is executed
and its selected company/period, counts, and unavailable-endpoint result are
recorded.

## Source, provenance, and security review

- `fetch_trial_balance` preserves the required interface and returns
  `(parsed_data, raw_xml)`; it validates the endpoint before `httpx.post`,
  disables redirects, and maps transport failures to safe
  `TallyConnectorError` messages.
- The protected route passes the returned raw bytes into `_ingest_tally_batch`,
  which stores them as batch source provenance while retaining only scheme,
  host, and port as endpoint metadata.
- Parser and canonical normalizer failures remain hard failures for accounting
  completeness. Metadata/classification warnings remain non-fatal through the
  existing dataset/report path.
- HTTP exception causes are cleared at the connector boundary. The protected
  router also maps Tally failures to generic safe messages; focused tests cover
  credentials, endpoint paths, response bodies, status details, raw XML, and
  malformed values.

## Self-review and concerns

Self-review found no placeholders presented as deployment facts and no
contradiction with the exact endpoint policy or preserved connector interface.
The local `.env.example` exists but remains unchanged because remote topology
is unresolved.

Concerns are limited to the unverified live network/Tally prerequisites and
the three existing deprecation warnings above. No code or test gap was found
that this task should fix.
