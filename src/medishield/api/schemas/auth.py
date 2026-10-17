from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Credentials payload for persona authentication."""
    username: str = Field(..., description="Username for authentication")
    password: str = Field(..., description="User password")


class LoginResponse(BaseModel):
    """JWT Token and authorized persona profile."""
    access_token: str = Field(..., description="Bearer JWT access token")
    token_type: str = Field("bearer", description="Token scheme")
    role: str = Field(..., description="Authoritative RBAC role")
    username: str = Field(..., description="Authenticated username")
    user_id: str = Field(..., description="Unique user identifier")


class UserMeResponse(BaseModel):
    """Current authenticated user profile and privileges."""
    user_id: str
    username: str
    role: str

