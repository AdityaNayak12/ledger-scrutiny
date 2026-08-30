import os

from dotenv import load_dotenv

# Load environment variables from .env file before app initialization
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers.auth import router as auth_router
from app.routers.scrutiny import router as scrutiny_router
from app.auth.security import get_secret_key, get_google_client_id
from app.db.base import Base
from app.db.session import engine

app = FastAPI(
    title="Audit Scrutiny Engine API",
    description="API for Tally XML ingestion and trial balance scrutiny engine with JWT authentication and Google GIS.",
    version="1.0.0"
)


@app.get("/health", tags=["operations"])
def healthcheck():
    """Lightweight liveness endpoint for container orchestration."""
    return {"status": "ok", "service": "capex"}

# Enforce authentication configuration, then create tables on startup for fresh local databases.
@app.on_event("startup")
def on_startup():
    get_secret_key()
    get_google_client_id()
    Base.metadata.create_all(bind=engine)

# Explicit local origins keep credentialed browser requests standards-compliant.
cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(scrutiny_router)
