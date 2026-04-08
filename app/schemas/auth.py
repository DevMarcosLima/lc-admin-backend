from pydantic import BaseModel, Field


class AdminLoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=320)
    password: str = Field(min_length=8, max_length=256)


class AdminLoginResponse(BaseModel):
    requires_2fa: bool
    token_type: str = "bearer"
    access_token: str | None = None
    challenge_token: str | None = None
    expires_in_seconds: int


class Admin2FAVerifyRequest(BaseModel):
    challenge_token: str = Field(min_length=20)
    code: str = Field(min_length=6, max_length=8)


class AdminMeResponse(BaseModel):
    email: str
    two_factor_enabled: bool
