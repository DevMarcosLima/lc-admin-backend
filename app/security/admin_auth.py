from __future__ import annotations

import hmac
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

import jwt
import pyotp
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings
from app.security.password_utils import hash_password, verify_password

bearer_scheme = HTTPBearer(auto_error=False)
bearer_dependency = Depends(bearer_scheme)


@dataclass(slots=True)
class AdminSession:
    email: str
    role: Literal["admin", "seller"]
    issued_at: datetime
    expires_at: datetime
    shop_name: str | None = None
    shop_slug: str | None = None
    must_change_password: bool = False


class LoginRateLimiter:
    def __init__(self) -> None:
        self._attempts: dict[str, list[datetime]] = {}
        self._lock = threading.Lock()

    def _key(self, *, email: str, ip: str | None) -> str:
        return f"{email.strip().lower()}::{(ip or 'unknown').strip()}"

    def _purge(self, *, key: str, now: datetime, window_seconds: int) -> list[datetime]:
        attempts = self._attempts.get(key, [])
        window_start = now - timedelta(seconds=window_seconds)
        fresh = [value for value in attempts if value >= window_start]
        if fresh:
            self._attempts[key] = fresh
        else:
            self._attempts.pop(key, None)
        return fresh

    def assert_allowed(
        self,
        *,
        email: str,
        ip: str | None,
        max_attempts: int,
        window_seconds: int,
    ) -> None:
        key = self._key(email=email, ip=ip)
        now = datetime.now(UTC)
        with self._lock:
            fresh = self._purge(key=key, now=now, window_seconds=window_seconds)
            if len(fresh) >= max_attempts:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Muitas tentativas de login. Aguarde alguns minutos.",
                )

    def register_failure(self, *, email: str, ip: str | None) -> None:
        key = self._key(email=email, ip=ip)
        with self._lock:
            self._attempts.setdefault(key, []).append(datetime.now(UTC))

    def clear(self, *, email: str, ip: str | None) -> None:
        key = self._key(email=email, ip=ip)
        with self._lock:
            self._attempts.pop(key, None)


login_rate_limiter = LoginRateLimiter()


def hash_admin_password(password: str, *, iterations: int = 390000) -> str:
    return hash_password(password, iterations=iterations)


def verify_admin_password(password: str, stored_hash: str) -> bool:
    return verify_password(password, stored_hash)


def _require_jwt_config() -> None:
    settings = get_settings()
    if not settings.admin_auth_jwt_secret.strip():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Configuracao de autenticacao incompleta: ADMIN_AUTH_JWT_SECRET",
        )


def _require_admin_login_config() -> None:
    settings = get_settings()
    missing: list[str] = []
    if not settings.admin_auth_email.strip():
        missing.append("ADMIN_AUTH_EMAIL")
    if not settings.admin_auth_password_hash.strip():
        missing.append("ADMIN_AUTH_PASSWORD_HASH")
    if settings.admin_auth_2fa_enabled and not settings.admin_auth_totp_secret.strip():
        missing.append("ADMIN_AUTH_TOTP_SECRET")
    if not settings.admin_auth_jwt_secret.strip():
        missing.append("ADMIN_AUTH_JWT_SECRET")

    if missing:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Configuracao de autenticacao incompleta: {', '.join(missing)}",
        )


def verify_admin_credentials(*, email: str, password: str) -> bool:
    settings = get_settings()
    expected_email = settings.admin_auth_email.strip().lower()
    submitted_email = email.strip().lower()
    if not expected_email or not hmac.compare_digest(submitted_email, expected_email):
        return False

    password_hash = settings.admin_auth_password_hash.strip()
    if not password_hash:
        return False

    return verify_admin_password(password, password_hash)


def verify_totp_code(*, code: str) -> bool:
    settings = get_settings()
    if not settings.admin_auth_2fa_enabled:
        return True

    secret = settings.admin_auth_totp_secret.strip()
    if not secret:
        return False

    otp = code.strip().replace(" ", "")
    if not otp.isdigit():
        return False

    totp = pyotp.TOTP(secret)
    return bool(totp.verify(otp, valid_window=1))


def _encode_token(
    *,
    subject: str,
    purpose: str,
    role: Literal["admin", "seller"],
    expires_minutes: int,
    shop_name: str | None = None,
    shop_slug: str | None = None,
    must_change_password: bool = False,
) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": subject.strip().lower(),
        "purpose": purpose,
        "role": role,
        "shop_name": (shop_name or "").strip() or None,
        "shop_slug": (shop_slug or "").strip() or None,
        "must_change_password": bool(must_change_password),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),
        "iss": settings.admin_auth_totp_issuer,
    }
    return jwt.encode(
        payload,
        settings.admin_auth_jwt_secret,
        algorithm=settings.admin_auth_jwt_algorithm,
    )


def _decode_token(*, token: str, expected_purpose: str) -> dict:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.admin_auth_jwt_secret,
            algorithms=[settings.admin_auth_jwt_algorithm],
            options={"require": ["exp", "iat", "sub", "purpose", "role"]},
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalido.",
        ) from exc

    purpose = str(payload.get("purpose", ""))
    if not hmac.compare_digest(purpose, expected_purpose):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token com proposito invalido.",
        )
    return payload


def create_access_token(
    *,
    email: str,
    role: Literal["admin", "seller"],
    shop_name: str | None = None,
    shop_slug: str | None = None,
    must_change_password: bool = False,
) -> tuple[str, int]:
    _require_jwt_config()
    settings = get_settings()
    ttl_minutes = max(settings.admin_auth_jwt_expires_minutes, 60)
    token = _encode_token(
        subject=email,
        role=role,
        purpose="panel_access",
        expires_minutes=ttl_minutes,
        shop_name=shop_name,
        shop_slug=shop_slug,
        must_change_password=must_change_password,
    )
    return token, ttl_minutes * 60


def create_2fa_challenge_token(
    *,
    email: str,
    role: Literal["admin", "seller"],
    shop_name: str | None = None,
    shop_slug: str | None = None,
) -> tuple[str, int]:
    _require_jwt_config()
    settings = get_settings()
    ttl_minutes = max(settings.admin_auth_2fa_challenge_minutes, 1)
    token = _encode_token(
        subject=email,
        role=role,
        purpose="panel_2fa_challenge",
        expires_minutes=ttl_minutes,
        shop_name=shop_name,
        shop_slug=shop_slug,
    )
    return token, ttl_minutes * 60


def create_seller_onboarding_challenge_token(
    *,
    email: str,
    shop_name: str | None,
    shop_slug: str | None,
) -> tuple[str, int]:
    _require_jwt_config()
    settings = get_settings()
    ttl_minutes = max(settings.admin_auth_2fa_challenge_minutes * 3, 10)
    token = _encode_token(
        subject=email,
        role="seller",
        purpose="seller_onboarding_challenge",
        expires_minutes=ttl_minutes,
        shop_name=shop_name,
        shop_slug=shop_slug,
        must_change_password=True,
    )
    return token, ttl_minutes * 60


def decode_2fa_challenge(*, token: str) -> dict:
    return _decode_token(token=token, expected_purpose="panel_2fa_challenge")


def decode_seller_onboarding_challenge(*, token: str) -> dict:
    return _decode_token(token=token, expected_purpose="seller_onboarding_challenge")


def decode_access_token(*, token: str) -> dict:
    return _decode_token(token=token, expected_purpose="panel_access")


def require_panel_session(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = bearer_dependency,
) -> AdminSession:
    _require_jwt_config()

    settings = get_settings()
    token = ""
    if credentials is not None and credentials.credentials.strip():
        token = credentials.credentials.strip()
    else:
        token = (request.cookies.get(settings.admin_auth_cookie_name) or "").strip()

    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token ausente.")

    payload = decode_access_token(token=token)
    role = str(payload.get("role") or "").strip().lower()
    if role not in {"admin", "seller"}:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Role de token invalida.",
        )
    role_value: Literal["admin", "seller"] = "admin" if role == "admin" else "seller"

    email = str(payload.get("sub", "")).strip().lower()
    iat = datetime.fromtimestamp(int(payload["iat"]), tz=UTC)
    exp = datetime.fromtimestamp(int(payload["exp"]), tz=UTC)
    return AdminSession(
        email=email,
        role=role_value,
        shop_name=(str(payload.get("shop_name") or "").strip() or None),
        shop_slug=(str(payload.get("shop_slug") or "").strip() or None),
        must_change_password=bool(payload.get("must_change_password", False)),
        issued_at=iat,
        expires_at=exp,
    )


panel_session_dependency = Depends(require_panel_session)


def require_admin_session(session: AdminSession = panel_session_dependency) -> AdminSession:
    _require_admin_login_config()
    if session.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso restrito ao admin.",
        )
    return session


def require_seller_session(session: AdminSession = panel_session_dependency) -> AdminSession:
    _require_jwt_config()
    if session.role != "seller":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso restrito ao seller.",
        )
    try:
        from app.services.seller_accounts import SellerAccountError, get_seller_account

        seller = get_seller_account(session.email)
    except SellerAccountError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    if seller is None or seller.status != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Conta seller inativa. Acesso temporariamente bloqueado.",
        )

    return session


def get_totp_setup_uri() -> str:
    settings = get_settings()
    _require_admin_login_config()
    return pyotp.TOTP(settings.admin_auth_totp_secret).provisioning_uri(
        name=settings.admin_auth_email,
        issuer_name=settings.admin_auth_totp_issuer,
    )
