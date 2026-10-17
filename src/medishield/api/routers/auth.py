import hmac
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from jose import jwt

from medishield.api.deps import (
    ROLE_ADMIN,
    ROLE_CLAIMER,
    ROLE_MANAGER,
    ROLE_REVIEWER,
    AuthUser,
    get_current_user,
)
from medishield.api.schemas.auth import LoginRequest, LoginResponse, UserMeResponse
from medishield.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Seeded Authoritative Persona Accounts (ADR-012, ADR-013)
REGISTERED_ACCOUNTS = {
    "claimer": {
        "password": "claimer123",
        "role": ROLE_CLAIMER,
        "user_id": "claimer-01",
    },
    "reviewer": {
        "password": "reviewer123",
        "role": ROLE_REVIEWER,
        "user_id": "rev-01",
    },
    "admin": {
        "password": "admin123",
        "role": ROLE_ADMIN,
        "user_id": "admin-01",
    },
    "manager": {
        "password": "manager123",
        "role": ROLE_MANAGER,
        "user_id": "mgr-01",
    },
}


def _verify_password(provided: str, stored: str) -> bool:
    """Constant-time comparison to prevent timing attacks."""
    return hmac.compare_digest(provided.encode("utf-8"), stored.encode("utf-8"))


def _create_access_token(user_id: str, username: str, role: str) -> str:
    """Generates an authoritative signed HS256 JWT access token."""
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=24)).timestamp()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Authenticate persona credentials and obtain bearer JWT",
)
def login(request: LoginRequest) -> LoginResponse:
    """
    Authenticates a user by username and password.
    Returns signed JWT access token and active persona profile.
    """
    username = request.username.strip().lower()
    account = REGISTERED_ACCOUNTS.get(username)

    if not account or not _verify_password(request.password, account["password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = _create_access_token(
        user_id=account["user_id"],
        username=username,
        role=account["role"],
    )

    logger.info(
        "User authenticated successfully",
        extra={"username": username, "role": account["role"]},
    )

    return LoginResponse(
        access_token=token,
        token_type="bearer",
        role=account["role"],
        username=username,
        user_id=account["user_id"],
    )


@router.get(
    "/me",
    response_model=UserMeResponse,
    summary="Get current authenticated user profile",
)
def get_me(user: AuthUser = Depends(get_current_user)) -> UserMeResponse:
    """Returns the authenticated user details for the active session."""
    return UserMeResponse(
        user_id=user.user_id,
        username=user.username,
        role=user.role,
    )

