import pyotp
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.security.admin_auth import create_access_token, hash_admin_password
from app.services.seller_accounts import SellerAccount, SellerPayoutConfig

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
    monkeypatch.setenv("ADMIN_AUTH_COOKIE_NAME", "lc_admin_session")
    monkeypatch.setenv("ADMIN_AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("ADMIN_AUTH_COOKIE_SAMESITE", "lax")
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


def test_login_sets_cookie_and_allows_cookie_auth(monkeypatch) -> None:
    email, password, _ = _configure_auth(monkeypatch, two_factor=False)

    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login_response.status_code == 200
    set_cookie_header = login_response.headers.get("set-cookie", "")
    assert "lc_admin_session=" in set_cookie_header
    assert "HttpOnly" in set_cookie_header

    me_response = client.get("/api/v1/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["email"] == email

    logout_response = client.post("/api/v1/auth/logout")
    assert logout_response.status_code == 204

    me_after_logout_response = client.get("/api/v1/auth/me")
    assert me_after_logout_response.status_code == 401

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


def test_inactive_seller_with_existing_token_is_blocked(monkeypatch) -> None:
    _configure_auth(monkeypatch, two_factor=False)

    from app.services import seller_accounts

    monkeypatch.setattr(
        seller_accounts,
        "get_seller_account",
        lambda _email: SellerAccount(
            email="seller@legacycards.com",
            shop_name="Loja Seller",
            shop_slug="loja-seller",
            password_hash="hash",
            must_change_password=False,
            two_factor_enabled=True,
            totp_secret=None,
            status="inactive",
            created_at=None,
            updated_at=None,
            created_by="marcos_dev@icloud.com",
            payout_config=SellerPayoutConfig(base_fee_brl=6.0, rules=[]),
        ),
    )

    token, _ = create_access_token(
        email="seller@legacycards.com",
        role="seller",
        shop_name="Loja Seller",
        shop_slug="loja-seller",
    )
    response = client.get(
        "/api/v1/seller/templates",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert "inativa" in response.json()["detail"].lower()

    get_settings.cache_clear()
