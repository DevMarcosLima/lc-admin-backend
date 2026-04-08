from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
import pyotp
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings

bearer_scheme = HTTPBearer(auto_error=False)
bearer_dependency = Depends(bearer_scheme)


@dataclass(slots=True)
class AdminSession:
    email: str
    issued_at: datetime
    expires_at: datetime


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


def _b64_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("utf-8").rstrip("=")


def _b64_decode(value: str) -> bytes:
    padded = value + "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(padded.encode("utf-8"))


def hash_admin_password(password: str, *, iterations: int = 390000) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64_encode(salt)}${_b64_encode(digest)}"


def verify_admin_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, iteration_text, salt_text, hash_text = stored_hash.split("$", maxsplit=3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iteration_text)
        salt = _b64_decode(salt_text)
        expected = _b64_decode(hash_text)
    except (TypeError, ValueError):
        return False

    computed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(computed, expected)


def _require_auth_config() -> None:
    settings = get_settings()
    missing: list[str] = []
    if not settings.admin_auth_password_hash.strip():
        missing.append("ADMIN_AUTH_PASSWORD_HASH")
    if not settings.admin_auth_jwt_secret.strip():
        missing.append("ADMIN_AUTH_JWT_SECRET")
    if settings.admin_auth_2fa_enabled and not settings.admin_auth_totp_secret.strip():
        missing.append("ADMIN_AUTH_TOTP_SECRET")

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


def _encode_token(*, subject: str, purpose: str, expires_minutes: int) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": subject.strip().lower(),
        "purpose": purpose,
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
            options={"require": ["exp", "iat", "sub", "purpose"]},
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


def create_access_token(*, email: str) -> tuple[str, int]:
    _require_auth_config()
    settings = get_settings()
    ttl_minutes = max(settings.admin_auth_jwt_expires_minutes, 1)
    token = _encode_token(subject=email, purpose="admin_access", expires_minutes=ttl_minutes)
    return token, ttl_minutes * 60


def create_2fa_challenge_token(*, email: str) -> tuple[str, int]:
    _require_auth_config()
    settings = get_settings()
    ttl_minutes = max(settings.admin_auth_2fa_challenge_minutes, 1)
    token = _encode_token(
        subject=email,
        purpose="admin_2fa_challenge",
        expires_minutes=ttl_minutes,
    )
    return token, ttl_minutes * 60


def decode_2fa_challenge(*, token: str) -> dict:
    return _decode_token(token=token, expected_purpose="admin_2fa_challenge")


def decode_access_token(*, token: str) -> dict:
    return _decode_token(token=token, expected_purpose="admin_access")


def require_admin_session(
    credentials: HTTPAuthorizationCredentials | None = bearer_dependency,
) -> AdminSession:
    _require_auth_config()

    if credentials is None or not credentials.credentials.strip():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token ausente.")

    payload = decode_access_token(token=credentials.credentials.strip())
    email = str(payload.get("sub", "")).strip().lower()
    iat = datetime.fromtimestamp(int(payload["iat"]), tz=UTC)
    exp = datetime.fromtimestamp(int(payload["exp"]), tz=UTC)
    return AdminSession(email=email, issued_at=iat, expires_at=exp)


def get_totp_setup_uri() -> str:
    settings = get_settings()
    _require_auth_config()
    return pyotp.TOTP(settings.admin_auth_totp_secret).provisioning_uri(
        name=settings.admin_auth_email,
        issuer_name=settings.admin_auth_totp_issuer,
    )
