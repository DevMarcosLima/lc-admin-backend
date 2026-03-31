from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app

client = TestClient(app)


def test_admin_requires_token(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_API_TOKEN", "token-test")
    get_settings.cache_clear()

    response = client.get("/api/v1/admin/products")

    assert response.status_code == 401
    get_settings.cache_clear()
