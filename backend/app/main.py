from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers.scrutiny import router as scrutiny_router

app = FastAPI(
    title="Audit Scrutiny Engine API",
    description="API for Tally XML ingestion and trial balance scrutiny engine.",
    version="1.0.0"
)

# Enable CORS for local dev environment
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(scrutiny_router)
