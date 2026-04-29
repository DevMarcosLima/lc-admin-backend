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


def test_catalog_assistant_route(monkeypatch) -> None:
    from app.api.routes import admin as admin_routes
    from app.schemas.store import CatalogAssistantResponse

    def fake_run(_payload):
        return CatalogAssistantResponse(
            action="find_price_outliers",
            model="gpt-5-nano",
            selected_products=12,
            scanned_products=10,
            updated_count=0,
            findings=[],
            ai_summary="ok",
            warnings=[],
        )

    monkeypatch.setattr(admin_routes, "run_catalog_assistant", fake_run)
    headers = _auth_headers(monkeypatch)

    response = client.post(
        "/api/v1/admin/catalog/assistant/run",
        headers=headers,
        json={"action": "find_price_outliers", "slugs": [], "include_non_cards": False},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["action"] == "find_price_outliers"
    assert payload["scanned_products"] == 10
    get_settings.cache_clear()
