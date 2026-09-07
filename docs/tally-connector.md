# TallyPrime HTTP connector

## Scope

Connector reads a running TallyPrime XML-over-HTTP endpoint. It does not read
native `*.1800` company-data files. Endpoint reachability, company selection,
and server configuration belong to the deployment; this document defines the
data contract, not provider-specific UI steps.

## Import request

Existing protected route accepts the following request fields:

```http
POST /entities/{entity_id}/import-from-tally
Content-Type: application/json
Authorization: Bearer <token>

{
  "endpoint": "http://host:port",
  "company_name": "Configured company name",
  "period_start": "2025-04-01",
  "period_end": "2026-03-31"
}
```

`endpoint`, `company_name`, and inclusive period dates identify the source
request. The adapter sends a read-only XML request and retains the raw response
as source provenance. It must not log request bodies, credentials, or raw XML.

## Endpoint safety

The connector accepts global IP literals. Hostnames must be listed exactly in
the comma-separated `TALLY_ALLOWED_HOSTS` deployment setting; validation does
not perform DNS lookups. Loopback endpoints, including the route default
`http://localhost:9000`, require the explicit local-development setting
`TALLY_ALLOW_LOCAL_ENDPOINTS=1`. Private, link-local, reserved, and other
non-global IP literals remain blocked, and HTTP redirects are not followed.

## Remote topology proposal (unverified)

The repository has not verified a remote Tally deployment or network path. The
proposed pilot topology is:

- **Backend host:** the approved pilot backend deployment; its host and
  execution network are not yet confirmed.
- **Tally host:** a customer-controlled TallyPrime workstation or server with
  its XML-over-HTTP listener enabled on an approved interface.
- **Network owner:** the customer or its IT/network team owns the Tally-side
  listener, firewall, and route decisions.
- **Managed private route:** use an existing customer-approved VPN, private
  link, or equivalent managed route from the backend network to the Tally
  network, if one exists. Availability is unverified.
- **Supported remote configuration:** use a concrete endpoint such as
  `http://tally-pilot.internal:<confirmed-port>` only after the host and port
  are confirmed, and set `TALLY_ALLOWED_HOSTS=tally-pilot.internal` with the
  exact hostname. Leave `TALLY_ALLOW_LOCAL_ENDPOINTS` unset in the deployed
  backend. The hostname is an example placeholder, not a verified environment
  fact; the current local-development example uses port `9000`.

This task adds no bridge, agent, public exposure, network access change, or
silent private-address exception. The following facts remain environment gates
before a live pilot: backend host/network, Tally host and listener port, network
owner, existing managed private route, firewall policy, and confirmation that
the selected Tally company and period are available through the read-only XML
export. Real Tally compatibility remains unverified until the pilot smoke run.

## Read-only pilot smoke procedure

Record the selected company name and inclusive period dates before running the
test. For example: company `Acme Audited Corp`, period `2025-04-01` through
`2026-03-31`; replace these with the approved pilot values.

1. From the backend deployment, confirm the approved private route reaches the
   exact Tally hostname and configured listener port. Configure
   `TALLY_ALLOWED_HOSTS` with that hostname only; do not enable local endpoint
   access for the deployed pilot.
2. Call the protected route with the recorded company, period, and exact
   endpoint:

   ```bash
   curl -sS -X POST "$API_BASE_URL/entities/$ENTITY_ID/import-from-tally" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"endpoint":"http://tally-pilot.internal:9000","company_name":"Acme Audited Corp","period_start":"2025-04-01","period_end":"2026-03-31"}'
   ```

   Substitute the confirmed hostname, port, company, and period; the values
   above are illustrative and unverified.
3. Save the response metadata and verify `status=ACTIVE`, the expected
   readiness state, and the selected period. Record
   `validation_report.ledger_count`, `validation_report.document_count`, and
   `validation_report.accepted_rows` as the reported ledger, document, and
   voucher-line counts. Also check that `errors` is empty, dates and source
   identifiers are present, and the returned `source_lineage` identifies the
   active batch.
4. Confirm the stored batch retains the raw response bytes and safe endpoint
   metadata only; do not put credentials, request bodies, or raw XML into
   logs or screenshots.
5. Run one deliberate unavailable-endpoint check using the exact allowlisted
   Tally hostname with a confirmed stopped/unused listener port. Expect a
   structured HTTP 400 with a safe actionable message, no endpoint credentials
   or source payload in the response, and no active-data replacement. Treat
   this as a failure-path check, not a compatibility result.

The mock-based connector and integration tests are evidence for these
contracts only. Mark live Tally compatibility **unverified** until the smoke
run records the selected company/period, reported counts, and unavailable
endpoint result.

## Normalized response contract

The adapter returns `(parsed_data, raw_xml)`. `parsed_data` uses this shape:

```python
{
    "entity": {
        "name": str,
        "financial_year_start": date,
        "financial_year_end": date,
    },
    "ledgers": [
        {
            "name": str,
            "external_id": str | None,
            "group_name": str,
            "group_hierarchy": list[str],
            "group_hierarchy_records": [
                {"name": str, "external_id": str | None, "parent_name": str | None},
            ],
            "opening_balance": Decimal,
            "closing_balance": Decimal,
        },
    ],
    "vouchers": [
        {
            "date": date,
            "document_date": date | None,
            "voucher_type": str,
            "voucher_number": str | None,
            "source_voucher_id": str,
            "narration": str | None,
            "entries": [
                {
                    "ledger_name": str,
                    "type": "debit" | "credit",
                    "amount": Decimal,
                    "source_row_number": int,
                    "source_line_id": str | None,
                },
            ],
        },
    ],
}
```

Adapter output must include account master/group hierarchy, opening and
closing balances, voucher headers, every voucher ledger line, and stable Tally
identifiers when available. The shared pipeline converts entries to canonical
`Decimal` amounts: debit-positive, credit-negative. It preserves document and
source-line provenance and validates each voucher to zero within `₹0.01`.

## Failure behavior

Network, HTTP, invalid XML, rejected Tally status, missing balances, and empty
account responses become `TallyConnectorError` with an actionable message.
Failure during replacement leaves the prior active dataset unchanged. The
connector remains independent of FastAPI and SQLAlchemy; persistence and
lifecycle activation happen in the shared ingestion pipeline.

## Fixture

`backend/tests/fixtures/golden_tally.xml` is a compact response fixture with
three balanced documents, five accounts, opening/closing balances, and six
voucher lines. Tests assert document and account counts plus zero-balance
invariants. It is not a claim about any particular Tally deployment.
