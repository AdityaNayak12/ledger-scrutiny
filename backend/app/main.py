from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers.scrutiny import router as scrutiny_router
from app.db.base import Base
from app.db.session import engine

app = FastAPI(
    title="Audit Scrutiny Engine API",
    description="API for Tally XML ingestion and trial balance scrutiny engine.",
    version="1.0.0"
)

# Create tables on startup event
@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)

# Enable CORS for local dev environment
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(scrutiny_router)
