"""Authentication schemas."""

from pydantic import BaseModel, Field


class AuthStatusResponse(BaseModel):
    """FSM authentication status."""

    authenticated: bool = Field(..., description="Whether a token is present")
    valid: bool = Field(..., description="Whether the token is actively verified against FSM")
    has_credentials: bool = Field(..., description="Whether login credentials are configured")
    fsm_base_url: str = Field(..., description="Target FSM Base URL")
    email: str | None = Field(default=None, description="Configured user email")
    has_api_key: bool = Field(..., description="Whether an FSM API key is present")
    token_preview: str | None = Field(default=None, description="Masked token preview")


class LoginRequest(BaseModel):
    """Manual login / credential override request."""

    email: str | None = Field(default=None, description="Optional email override")
    password: str | None = Field(default=None, description="Optional password override")


class LoginResponse(BaseModel):
    """Login response with fresh token."""

    success: bool = Field(..., description="Whether login was successful")
    message: str = Field(..., description="Status message")
    token_preview: str | None = Field(default=None, description="Masked token preview")
