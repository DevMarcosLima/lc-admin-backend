from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_security_headers_present_on_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("referrer-policy") == "no-referrer"
    assert response.headers.get("permissions-policy") == "geolocation=(), microphone=(), camera=()"
    assert response.headers.get("content-security-policy") == "default-src 'none'; frame-ancestors 'none'"
