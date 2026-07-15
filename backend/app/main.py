from fastapi import FastAPI
from app.routers.scrutiny import router as scrutiny_router

app = FastAPI(
    title="Audit Scrutiny Engine API",
    description="API for Tally XML ingestion and trial balance scrutiny engine.",
    version="1.0.0"
)

app.include_router(scrutiny_router)
