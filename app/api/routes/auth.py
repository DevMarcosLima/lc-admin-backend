from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.config import get_settings
from app.schemas.auth import (
    Admin2FAVerifyRequest,
    AdminLoginRequest,
    AdminLoginResponse,
    AdminMeResponse,
)
from app.security.admin_auth import (
    AdminSession,
    create_2fa_challenge_token,
    create_access_token,
    decode_2fa_challenge,
    get_totp_setup_uri,
    login_rate_limiter,
    require_admin_session,
    verify_admin_credentials,
    verify_totp_code,
)

router = APIRouter(prefix="/auth", tags=["auth"])
admin_session_dependency = Depends(require_admin_session)


@router.post("/login", response_model=AdminLoginResponse)
def post_login(payload: AdminLoginRequest, request: Request) -> AdminLoginResponse:
    settings = get_settings()
    client_ip = request.client.host if request.client else None
    login_rate_limiter.assert_allowed(
        email=payload.email,
        ip=client_ip,
        max_attempts=max(settings.admin_auth_max_failed_attempts, 1),
        window_seconds=max(settings.admin_auth_failed_window_seconds, 60),
    )

    if not verify_admin_credentials(email=payload.email, password=payload.password):
        login_rate_limiter.register_failure(email=payload.email, ip=client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="E-mail ou senha invalidos.",
        )

    login_rate_limiter.clear(email=payload.email, ip=client_ip)

    if settings.admin_auth_2fa_enabled:
        challenge_token, expires_seconds = create_2fa_challenge_token(email=payload.email)
        return AdminLoginResponse(
            requires_2fa=True,
            challenge_token=challenge_token,
            expires_in_seconds=expires_seconds,
        )

    access_token, expires_seconds = create_access_token(email=payload.email)
    return AdminLoginResponse(
        requires_2fa=False,
        access_token=access_token,
        expires_in_seconds=expires_seconds,
    )


@router.post("/verify-2fa", response_model=AdminLoginResponse)
def post_verify_2fa(payload: Admin2FAVerifyRequest) -> AdminLoginResponse:
    claims = decode_2fa_challenge(token=payload.challenge_token)
    email = str(claims["sub"])

    if not verify_totp_code(code=payload.code):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Codigo 2FA invalido.",
        )

    access_token, expires_seconds = create_access_token(email=email)
    return AdminLoginResponse(
        requires_2fa=False,
        access_token=access_token,
        expires_in_seconds=expires_seconds,
    )


@router.get("/me", response_model=AdminMeResponse)
def get_me(session: AdminSession = admin_session_dependency) -> AdminMeResponse:
    settings = get_settings()
    return AdminMeResponse(email=session.email, two_factor_enabled=settings.admin_auth_2fa_enabled)


@router.get("/2fa/setup-uri")
def get_2fa_setup_uri(session: AdminSession = admin_session_dependency) -> dict[str, str]:
    _ = session
    return {"otpauth_uri": get_totp_setup_uri()}
