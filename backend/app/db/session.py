import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

# Get database URL from environment or default to local SQLite instance for easy local dev
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./ledger_scrutiny.db"
)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """FastAPI dependency to provide a database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
