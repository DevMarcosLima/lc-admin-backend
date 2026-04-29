from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.security.admin_auth import hash_admin_password

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


def test_get_admin_branding_settings(monkeypatch) -> None:
    from app.api.routes import admin as admin_routes

    monkeypatch.setattr(
        admin_routes,
        "get_panel_branding_config",
        lambda: {
            "hero_logo_primary_url": "https://example.com/logo-1.webp",
            "hero_logo_secondary_url": "https://example.com/logo-2.webp",
            "hero_logo_primary_width": 180,
            "hero_logo_secondary_width": 120,
            "hero_slide_targets": [
                {
                    "slide_index": 1,
                    "product_slug": "produto-x",
                    "product_name": "Produto X",
                }
            ],
            "hero_slides": [
                {
                    "slide_index": 1,
                    "image_url": "https://example.com/slide-1.webp",
                    "focus_x_percent": 55,
                    "name": "Slide Um",
                    "category": "Pré-venda",
                    "product_type": "Box",
                    "price_brl": 129.9,
                }
            ],
            "updated_at": "2026-04-21T12:00:00+00:00",
        },
    )

    response = client.get(
        "/api/v1/admin/settings/branding",
        headers=_auth_headers(monkeypatch),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["hero_logo_primary_url"].endswith("logo-1.webp")
    assert body["hero_logo_secondary_width"] == 120
    assert body["hero_slide_targets"][0]["slide_index"] == 1
    assert body["hero_slide_targets"][0]["product_slug"] == "produto-x"
    assert body["hero_slides"][0]["slide_index"] == 1
    assert body["hero_slides"][0]["focus_x_percent"] == 55
    assert body["hero_slides"][0]["name"] == "Slide Um"
    assert body["hero_slides"][0]["price_brl"] == 129.9


def test_put_admin_branding_settings(monkeypatch) -> None:
    from app.api.routes import admin as admin_routes

    def fake_upsert(payload: dict[str, object]) -> dict[str, object]:
        return {
            **payload,
            "updated_at": "2026-04-21T13:00:00+00:00",
        }

    monkeypatch.setattr(admin_routes, "upsert_panel_branding_config", fake_upsert)

    response = client.put(
        "/api/v1/admin/settings/branding",
        headers=_auth_headers(monkeypatch),
        json={
            "hero_logo_primary_url": "https://storage.googleapis.com/x/primary.webp",
            "hero_logo_secondary_url": "https://storage.googleapis.com/x/secondary.webp",
            "hero_logo_primary_width": 160,
            "hero_logo_secondary_width": 120,
            "hero_slide_targets": [
                {
                    "slide_index": 2,
                    "product_slug": "produto-y",
                    "product_name": "Produto Y",
                }
            ],
            "hero_slides": [
                {
                    "slide_index": 2,
                    "image_url": "https://storage.googleapis.com/x/slide-2.webp",
                    "focus_x_percent": 49,
                    "name": "Slide Dois",
                    "category": "Coleção especial",
                    "product_type": "Booster",
                    "price_brl": 18.5,
                }
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["hero_logo_primary_width"] == 160
    assert body["hero_slide_targets"][0]["slide_index"] == 2
    assert body["hero_slide_targets"][0]["product_slug"] == "produto-y"
    assert body["hero_slides"][0]["slide_index"] == 2
    assert body["hero_slides"][0]["product_type"] == "Booster"
    assert body["hero_slides"][0]["price_brl"] == 18.5
    assert body["updated_at"] == "2026-04-21T13:00:00+00:00"
