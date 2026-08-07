import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models import User

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

security_bearer = HTTPBearer()


def get_secret_key() -> str:
    secret = os.getenv("SECRET_KEY")
    if not secret:
        raise RuntimeError(
            "SECRET_KEY environment variable is not set. "
            "A valid SECRET_KEY must be provided for security-critical JWT signing."
        )
    return secret


def hash_password(plain_password: str) -> str:
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(plain_password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_access_token(user_id: int, organization_id: int) -> str:
    secret_key = get_secret_key()
    now = datetime.now(timezone.utc)
    expire = now + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload: Dict[str, Any] = {
        "sub": str(user_id),
        "user_id": user_id,
        "organization_id": organization_id,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(payload, secret_key, algorithm=ALGORITHM)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security_bearer),
    db: Session = Depends(get_db)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    token = credentials.credentials
    try:
        secret_key = get_secret_key()
        payload = jwt.decode(token, secret_key, algorithms=[ALGORITHM])
        user_id = payload.get("user_id")
        if user_id is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception
        
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise credentials_exception
        
    return user
