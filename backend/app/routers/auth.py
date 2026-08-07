from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models import Organization, User
from app.auth.security import hash_password, verify_password, create_access_token

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    organization_name: str
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


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
