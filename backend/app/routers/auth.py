from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests

from app.db.session import get_db
from app.db.models import Organization, User
from app.auth.security import hash_password, verify_password, create_access_token, get_google_client_id

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    organization_name: str
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class GoogleAuthRequest(BaseModel):
    id_token: str
    organization_name: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    organization_id: int
    email: str
    organization_name: str


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    existing_user = db.query(User).filter(User.email == req.email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User with this email already exists"
        )

    org = Organization(name=req.organization_name)
    db.add(org)
    db.flush()

    hashed = hash_password(req.password)
    user = User(
        organization_id=org.id,
        email=req.email,
        hashed_password=hashed
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.refresh(org)

    token = create_access_token(user_id=user.id, organization_id=org.id)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user_id=user.id,
        organization_id=org.id,
        email=user.email,
        organization_name=org.name
    )


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    generic_invalid_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid credentials"
    )

    user = db.query(User).filter(User.email == req.email).first()
    if not user:
        raise generic_invalid_exception

    if user.hashed_password is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This account uses Google Sign-In, please use Continue with Google"
        )

    if not verify_password(req.password, user.hashed_password):
        raise generic_invalid_exception

    token = create_access_token(user_id=user.id, organization_id=user.organization_id)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user_id=user.id,
        organization_id=user.organization_id,
        email=user.email,
        organization_name=user.organization.name
    )


@router.post("/google", response_model=TokenResponse)
def auth_google(req: GoogleAuthRequest, db: Session = Depends(get_db)):
    google_client_id = get_google_client_id()

    try:
        id_info = google_id_token.verify_oauth2_token(
            req.id_token,
            google_requests.Request(),
            google_client_id
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google token"
        )

    email = id_info.get("email")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Google token: email not found"
        )

    user = db.query(User).filter(User.email == email).first()

    if user:
        org = db.query(Organization).filter(Organization.id == user.organization_id).first()
        token = create_access_token(user_id=user.id, organization_id=user.organization_id)
        return TokenResponse(
            access_token=token,
            token_type="bearer",
            user_id=user.id,
            organization_id=user.organization_id,
            email=user.email,
            organization_name=org.name if org else ""
        )

    if not req.organization_name or not req.organization_name.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="New account: organization_name is required"
        )

    org = Organization(name=req.organization_name.strip())
    db.add(org)
    db.flush()

    user = User(
        organization_id=org.id,
        email=email,
        hashed_password=None
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.refresh(org)

    token = create_access_token(user_id=user.id, organization_id=org.id)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user_id=user.id,
        organization_id=org.id,
        email=user.email,
        organization_name=org.name
    )
