# TallyPrime HTTP connector

CApex reads a running TallyPrime instance through its supported
XML-over-HTTP interface. It does **not** read native `*.1800` company-data
files.

## Prepare TallyPrime

1. Copy/open the company-data folder in TallyPrime and select the required
   company (for the supplied sample, Tally will identify the companies behind
   `100001` and `100003`).
2. In TallyPrime, enable the HTTP Server in **F1 (Help) > Settings > Advanced
   Configuration**. The usual local endpoint is `http://localhost:9000`.
3. Keep TallyPrime running and ensure the selected company is loaded.

## Import a period

Call the protected endpoint from the same machine as TallyPrime:

```http
POST /entities/{entity_id}/import-from-tally
Content-Type: application/json
Authorization: Bearer <token>

{
  "endpoint": "http://localhost:9000",
  "company_name": "Exact Tally company name",
  "period_start": "2025-04-01",
  "period_end": "2026-03-31"
}
```

The connector requests the ledger collection with name, parent group, opening
balance, and closing balance. The response becomes a `tally_http` import
batch, after which the normal scrutiny endpoint can be run.

For a hosted deployment, use a small authenticated desktop agent on the
client/Tally machine. Do not expose Tally port 9000 directly to the internet.
