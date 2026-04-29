from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.security.admin_auth import hash_admin_password
from app.services.seller_accounts import SellerAccount, SellerPayoutConfig

client = TestClient(app)


def _auth_headers(monkeypatch) -> dict[str, str]:
    email = "marcos_dev@icloud.com"
    password = "StrongAdmin#2026!"

    monkeypatch.setenv("ADMIN_AUTH_EMAIL", email)
    monkeypatch.setenv("ADMIN_AUTH_PASSWORD_HASH", hash_admin_password(password))
    monkeypatch.setenv("ADMIN_AUTH_JWT_SECRET", "test-jwt-secret-key-legacy-cards-admin")
    monkeypatch.setenv("ADMIN_AUTH_2FA_ENABLED", "false")
    monkeypatch.setenv("ADMIN_AUTH_TOTP_SECRET", "")
    get_settings.cache_clear()

    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_patch_admin_seller_status_with_inventory_standby(monkeypatch) -> None:
    from app.api.routes import admin as admin_routes

    monkeypatch.setattr(
        admin_routes,
        "update_seller_status",
        lambda **_kwargs: SellerAccount(
            email="seller@loja.com",
            shop_name="Loja Seller",
            shop_slug="loja-seller",
            password_hash="hash",
            must_change_password=False,
            two_factor_enabled=True,
            totp_secret=None,
            status="inactive",
            created_at="2026-04-25T00:00:00+00:00",
            updated_at="2026-04-25T00:10:00+00:00",
            created_by="marcos_dev@icloud.com",
            payout_config=SellerPayoutConfig(base_fee_brl=6.0, rules=[]),
        ),
    )
    monkeypatch.setattr(
        admin_routes,
        "set_seller_inventory_mode",
        lambda **_kwargs: (3, 14),
    )

    response = client.patch(
        "/api/v1/admin/sellers/seller%40loja.com/status",
        headers=_auth_headers(monkeypatch),
        json={
            "status": "inactive",
            "set_inventory_standby": True,
            "zero_inventory": True,
            "note": "desativar para revisão",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["account"]["email"] == "seller@loja.com"
    assert payload["account"]["status"] == "inactive"
    assert payload["inventory_standby"] is True
    assert payload["seller_products_affected"] == 3
    assert payload["seller_stock_removed"] == 14

    get_settings.cache_clear()
