# LedgerScrutiny pitch demo

## Product promise

LedgerScrutiny turns accounting data into a materiality-aware, review-ready
exception register using deterministic and explainable checks.

The built-in Meridian Components workspace is fictional demonstration data.
Never replace it with real client names, GSTINs, or financial information for
an external presentation.

## Start locally

The guaranteed demo path needs only the frontend:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. Use **Reset Demo** immediately before presenting.

For the live API path, copy `.env.example` to `.env`, set a strong secret, and
start all services:

```bash
docker compose up --build
```

The backend container applies Alembic migrations before FastAPI starts. Verify
`http://localhost:8000/health` returns `{"status":"ok","service":"ledger-scrutiny"}`.

## Three-minute walkthrough

1. State that Meridian Components and every displayed amount are fictional.
2. Point out the FY 2024-25 and FY 2025-26 periods and ₹1,00,000 materiality.
3. Run the five deterministic scrutiny checks.
4. Open the Plant & Machinery continuity finding and explain the ₹4,25,000 gap.
5. Add an auditor note and mark the finding Reviewed.
6. Show the review-progress update and the preserved prior-year workpaper.
7. Mention that live workspaces add authentication, organization isolation,
   immutable imports, rerunnable scrutiny, XML/XLSX ingestion, and the tested
   TallyPrime HTTP connector.

Use this boundary statement verbatim:

> This MVP focuses on pre-audit ledger scrutiny and review documentation.
> Vouching, GST/TDS reconciliation, and full sector-specific compliance
> modules are on the roadmap, not claimed as current capabilities.

## Recovery

- **Demo changed during rehearsal:** click **Reset Demo**.
- **Frontend does not start:** run `npm install`, then `npm run dev` in
  `frontend/` and confirm port 5173 is free.
- **Live API cannot be reached:** keep presenting the fictional demo workspace;
  it deliberately has no backend or internet dependency.
- **Database reports a missing column:** run
  `PYTHONPATH=. ../.venv/bin/alembic upgrade head` from `backend/` before
  starting FastAPI.
- **TallyPrime is unavailable:** show `docs/tally-connector.md` and the passing
  connector tests; do not make live Tally part of the critical walkthrough.

## Verification commands

```bash
cd backend
PYTHONPATH=. ../.venv/bin/alembic upgrade head
PYTHONPATH=. ../.venv/bin/pytest -q

cd ../frontend
npm run lint
npm run build
```
