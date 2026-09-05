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
