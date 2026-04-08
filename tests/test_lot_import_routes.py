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


def test_start_lot_import_route(monkeypatch) -> None:
    from app.api.routes import admin as admin_routes
    from app.schemas.store import LotImportStartResponse

    def fake_start(_payload):
        return LotImportStartResponse(job_id="lot-123", status="queued", total_cards=2)

    monkeypatch.setattr(admin_routes, "start_lot_import", fake_start)
    headers = _auth_headers(monkeypatch)

    response = client.post(
        "/api/v1/admin/lots/import/start",
        headers=headers,
        json={
            "lot_payload": {
                "lot_id": "l1",
                "lot_name": "l1",
                "cards": [
                    {
                        "name": "Charizard",
                        "number": "004/102",
                        "language": "pt",
                        "category": "pokemon",
                        "details": "normal",
                        "quantity": 1,
                    }
                ],
            }
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == "lot-123"
    assert body["status"] == "queued"
    get_settings.cache_clear()


def test_get_lot_import_route_not_found(monkeypatch) -> None:
    from app.api.routes import admin as admin_routes
    from app.services.lot_import import LotImportNotFound

    def fake_get(_job_id: str):
        raise LotImportNotFound("not found")

    monkeypatch.setattr(admin_routes, "get_lot_import", fake_get)
    headers = _auth_headers(monkeypatch)

    response = client.get("/api/v1/admin/lots/import/lot-404", headers=headers)
    assert response.status_code == 404
    get_settings.cache_clear()
