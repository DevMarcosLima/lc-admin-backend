import pyotp
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.security.admin_auth import hash_admin_password

client = TestClient(app)


def _configure_auth(monkeypatch, *, two_factor: bool = False) -> tuple[str, str, str]:
    email = "marcos_dev@icloud.com"
    password = "StrongAdmin#2026!"
    jwt_secret = "test-jwt-secret-key-legacy-cards-admin"
    password_hash = hash_admin_password(password)

    monkeypatch.setenv("ADMIN_AUTH_EMAIL", email)
    monkeypatch.setenv("ADMIN_AUTH_PASSWORD_HASH", password_hash)
    monkeypatch.setenv("ADMIN_AUTH_JWT_SECRET", jwt_secret)
    monkeypatch.setenv("ADMIN_AUTH_JWT_EXPIRES_MINUTES", "30")
    monkeypatch.setenv("ADMIN_AUTH_2FA_ENABLED", "true" if two_factor else "false")
    monkeypatch.setenv("ADMIN_AUTH_2FA_CHALLENGE_MINUTES", "5")
    monkeypatch.setenv("ADMIN_AUTH_MAX_FAILED_ATTEMPTS", "5")
    monkeypatch.setenv("ADMIN_AUTH_FAILED_WINDOW_SECONDS", "900")
    if two_factor:
        monkeypatch.setenv("ADMIN_AUTH_TOTP_SECRET", pyotp.random_base32())
    else:
        monkeypatch.setenv("ADMIN_AUTH_TOTP_SECRET", "")

    get_settings.cache_clear()
    settings = get_settings()
    return email, password, settings.admin_auth_totp_secret


def test_admin_requires_bearer_token(monkeypatch) -> None:
    _configure_auth(monkeypatch, two_factor=False)
    response = client.get("/api/v1/admin/products")
    assert response.status_code == 401

    get_settings.cache_clear()


def test_login_without_2fa(monkeypatch) -> None:
    email, password, _ = _configure_auth(monkeypatch, two_factor=False)

    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login_response.status_code == 200

    payload = login_response.json()
    assert payload["requires_2fa"] is False
    assert payload["access_token"]

    me_response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {payload['access_token']}"},
    )
    assert me_response.status_code == 200
    assert me_response.json()["email"] == email
    assert me_response.json()["two_factor_enabled"] is False

    get_settings.cache_clear()


def test_login_with_2fa(monkeypatch) -> None:
    email, password, totp_secret = _configure_auth(monkeypatch, two_factor=True)

    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login_response.status_code == 200
    payload = login_response.json()
    assert payload["requires_2fa"] is True
    assert payload["challenge_token"]
    assert payload["access_token"] is None

    otp = pyotp.TOTP(totp_secret).now()
    verify_response = client.post(
        "/api/v1/auth/verify-2fa",
        json={"challenge_token": payload["challenge_token"], "code": otp},
    )
    assert verify_response.status_code == 200
    verify_payload = verify_response.json()
    assert verify_payload["requires_2fa"] is False
    assert verify_payload["access_token"]

    me_response = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {verify_payload['access_token']}"},
    )
    assert me_response.status_code == 200
    assert me_response.json()["email"] == email
    assert me_response.json()["two_factor_enabled"] is True

    get_settings.cache_clear()
