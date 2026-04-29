from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.core.config import get_settings
from app.schemas.auth import (
    Admin2FAVerifyRequest,
    AdminLoginRequest,
    AdminLoginResponse,
    AdminMeResponse,
    SellerOnboardingCompleteRequest,
)
from app.security.admin_auth import (
    AdminSession,
    create_2fa_challenge_token,
    create_access_token,
    create_seller_onboarding_challenge_token,
    decode_2fa_challenge,
    decode_seller_onboarding_challenge,
    get_totp_setup_uri,
    login_rate_limiter,
    require_admin_session,
    require_panel_session,
    verify_admin_credentials,
    verify_totp_code,
)
from app.services.seller_accounts import (
    SellerAccountError,
    complete_seller_onboarding,
    get_seller_account,
    seller_totp_provisioning_uri,
    touch_seller_login,
    verify_seller_credentials,
    verify_seller_totp_code,
)

router = APIRouter(prefix="/auth", tags=["auth"])
admin_session_dependency = Depends(require_admin_session)
panel_session_dependency = Depends(require_panel_session)


def _request_is_https(request: Request) -> bool:
    forwarded_proto = str(request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    return request.url.scheme == "https" or forwarded_proto == "https"


def _set_session_cookie(
    response: Response,
    request: Request,
    *,
    token: str,
    expires_in_seconds: int,
) -> None:
    settings = get_settings()
    same_site_raw = settings.admin_auth_cookie_samesite.strip().lower() or "lax"
    same_site = same_site_raw if same_site_raw in {"lax", "strict", "none"} else "lax"
    secure_cookie = bool(settings.admin_auth_cookie_secure and _request_is_https(request))
    if same_site == "none":
        secure_cookie = True
    response.set_cookie(
        key=settings.admin_auth_cookie_name,
        value=token,
        httponly=True,
        secure=secure_cookie,
        samesite=same_site,
        max_age=max(expires_in_seconds, 60),
        path="/",
    )


def _clear_session_cookie(response: Response, request: Request) -> None:
    settings = get_settings()
    same_site_raw = settings.admin_auth_cookie_samesite.strip().lower() or "lax"
    same_site = same_site_raw if same_site_raw in {"lax", "strict", "none"} else "lax"
    secure_cookie = bool(settings.admin_auth_cookie_secure and _request_is_https(request))
    if same_site == "none":
        secure_cookie = True
    response.delete_cookie(
        key=settings.admin_auth_cookie_name,
        secure=secure_cookie,
        samesite=same_site,
        path="/",
    )


@router.post("/login", response_model=AdminLoginResponse)
def post_login(payload: AdminLoginRequest, request: Request, response: Response) -> AdminLoginResponse:
    settings = get_settings()
    submitted_email = payload.email.strip().lower()
    client_ip = request.client.host if request.client else None
    login_rate_limiter.assert_allowed(
        email=submitted_email,
        ip=client_ip,
        max_attempts=max(settings.admin_auth_max_failed_attempts, 1),
        window_seconds=max(settings.admin_auth_failed_window_seconds, 60),
    )

    if verify_admin_credentials(email=submitted_email, password=payload.password):
        login_rate_limiter.clear(email=submitted_email, ip=client_ip)

        if settings.admin_auth_2fa_enabled:
            challenge_token, expires_seconds = create_2fa_challenge_token(
                email=submitted_email,
                role="admin",
            )
            return AdminLoginResponse(
                role="admin",
                email=submitted_email,
                requires_2fa=True,
                challenge_token=challenge_token,
                expires_in_seconds=expires_seconds,
            )

        access_token, expires_seconds = create_access_token(
            email=submitted_email,
            role="admin",
        )
        _set_session_cookie(
            response,
            request,
            token=access_token,
            expires_in_seconds=expires_seconds,
        )
        return AdminLoginResponse(
            role="admin",
            email=submitted_email,
            requires_2fa=False,
            access_token=access_token,
            expires_in_seconds=expires_seconds,
        )

    try:
        seller = verify_seller_credentials(email=submitted_email, password=payload.password)
    except SellerAccountError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if seller is None:
        login_rate_limiter.register_failure(email=submitted_email, ip=client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="E-mail ou senha invalidos.",
        )

    login_rate_limiter.clear(email=submitted_email, ip=client_ip)

    if seller.must_change_password or not seller.two_factor_enabled:
        try:
            onboarding_qr_uri = seller_totp_provisioning_uri(
                seller.email,
                issuer_name=settings.admin_auth_totp_issuer,
            )
        except SellerAccountError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        challenge_token, expires_seconds = create_seller_onboarding_challenge_token(
            email=seller.email,
            shop_name=seller.shop_name,
            shop_slug=seller.shop_slug,
        )
        return AdminLoginResponse(
            role="seller",
            email=seller.email,
            shop_name=seller.shop_name,
            shop_slug=seller.shop_slug,
            requires_2fa=False,
            requires_onboarding=True,
            challenge_token=challenge_token,
            onboarding_qr_uri=onboarding_qr_uri,
            expires_in_seconds=expires_seconds,
        )

    challenge_token, expires_seconds = create_2fa_challenge_token(
        email=seller.email,
        role="seller",
        shop_name=seller.shop_name,
        shop_slug=seller.shop_slug,
    )
    return AdminLoginResponse(
        role="seller",
        email=seller.email,
        shop_name=seller.shop_name,
        shop_slug=seller.shop_slug,
        requires_2fa=True,
        challenge_token=challenge_token,
        expires_in_seconds=expires_seconds,
    )


@router.post("/verify-2fa", response_model=AdminLoginResponse)
def post_verify_2fa(
    payload: Admin2FAVerifyRequest,
    request: Request,
    response: Response,
) -> AdminLoginResponse:
    claims = decode_2fa_challenge(token=payload.challenge_token)
    email = str(claims["sub"])
    role = str(claims.get("role") or "admin").strip().lower()
    shop_name = str(claims.get("shop_name") or "").strip() or None
    shop_slug = str(claims.get("shop_slug") or "").strip() or None

    if role == "admin":
        valid_code = verify_totp_code(code=payload.code)
    else:
        valid_code = verify_seller_totp_code(email=email, code=payload.code)

    if not valid_code:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Codigo 2FA invalido.",
        )

    access_token, expires_seconds = create_access_token(
        email=email,
        role="admin" if role == "admin" else "seller",
        shop_name=shop_name,
        shop_slug=shop_slug,
    )
    _set_session_cookie(
        response,
        request,
        token=access_token,
        expires_in_seconds=expires_seconds,
    )
    if role == "seller":
        touch_seller_login(email)

    return AdminLoginResponse(
        role="admin" if role == "admin" else "seller",
        email=email,
        shop_name=shop_name,
        shop_slug=shop_slug,
        requires_2fa=False,
        access_token=access_token,
        expires_in_seconds=expires_seconds,
    )


@router.post("/onboarding/complete", response_model=AdminLoginResponse)
def post_complete_seller_onboarding(
    payload: SellerOnboardingCompleteRequest,
    request: Request,
    response: Response,
) -> AdminLoginResponse:
    claims = decode_seller_onboarding_challenge(token=payload.challenge_token)
    email = str(claims["sub"])
    shop_name = str(claims.get("shop_name") or "").strip() or None
    shop_slug = str(claims.get("shop_slug") or "").strip() or None

    try:
        seller = complete_seller_onboarding(
            email=email,
            new_password=payload.new_password,
            code=payload.code,
        )
    except SellerAccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    access_token, expires_seconds = create_access_token(
        email=seller.email,
        role="seller",
        shop_name=seller.shop_name or shop_name,
        shop_slug=seller.shop_slug or shop_slug,
    )
    _set_session_cookie(
        response,
        request,
        token=access_token,
        expires_in_seconds=expires_seconds,
    )
    touch_seller_login(seller.email)
    return AdminLoginResponse(
        role="seller",
        email=seller.email,
        shop_name=seller.shop_name,
        shop_slug=seller.shop_slug,
        requires_2fa=False,
        requires_onboarding=False,
        access_token=access_token,
        expires_in_seconds=expires_seconds,
    )


@router.get("/me", response_model=AdminMeResponse)
def get_me(session: AdminSession = panel_session_dependency) -> AdminMeResponse:
    settings = get_settings()
    if session.role == "admin":
        return AdminMeResponse(
            email=session.email,
            role="admin",
            two_factor_enabled=settings.admin_auth_2fa_enabled,
            must_change_password=False,
        )

    seller_two_factor_enabled = False
    seller_must_change_password = session.must_change_password
    try:
        seller = get_seller_account(session.email)
        if seller is not None:
            seller_two_factor_enabled = seller.two_factor_enabled
            seller_must_change_password = seller.must_change_password
    except SellerAccountError:
        seller = None

    return AdminMeResponse(
        email=session.email,
        role="seller",
        shop_name=session.shop_name or (seller.shop_name if seller else None),
        shop_slug=session.shop_slug or (seller.shop_slug if seller else None),
        must_change_password=seller_must_change_password,
        two_factor_enabled=seller_two_factor_enabled,
    )


@router.get("/2fa/setup-uri")
def get_2fa_setup_uri(session: AdminSession = admin_session_dependency) -> dict[str, str]:
    _ = session
    return {"otpauth_uri": get_totp_setup_uri()}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def post_logout(request: Request, response: Response) -> Response:
    _clear_session_cookie(response, request)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
