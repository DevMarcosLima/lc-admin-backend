from typing import Literal

from pydantic import BaseModel, Field


class AdminLoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=320)
    password: str = Field(min_length=8, max_length=256)


class AdminLoginResponse(BaseModel):
    role: Literal["admin", "seller"] | None = None
    email: str | None = None
    shop_name: str | None = None
    shop_slug: str | None = None
    requires_2fa: bool
    requires_onboarding: bool = False
    token_type: str = "bearer"
    access_token: str | None = None
    challenge_token: str | None = None
    onboarding_qr_uri: str | None = None
    expires_in_seconds: int


class Admin2FAVerifyRequest(BaseModel):
    challenge_token: str = Field(min_length=20)
    code: str = Field(min_length=6, max_length=8)


class SellerOnboardingCompleteRequest(BaseModel):
    challenge_token: str = Field(min_length=20)
    new_password: str = Field(min_length=8, max_length=256)
    code: str = Field(min_length=6, max_length=8)


class AdminMeResponse(BaseModel):
    email: str
    role: Literal["admin", "seller"]
    shop_name: str | None = None
    shop_slug: str | None = None
    must_change_password: bool = False
    two_factor_enabled: bool
