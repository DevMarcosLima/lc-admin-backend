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


def test_admin_upload_image_success(monkeypatch) -> None:
    from app.api.routes import admin as admin_routes
    from app.services.media_storage import UploadedImage

    def fake_upload_image_bytes(**kwargs):
        assert kwargs["scope"] == "cards"
        assert kwargs["slot"] == "primary"
        return UploadedImage(
            url="https://storage.googleapis.com/legacy-cards-product-images-sae1/catalog/cards/primary/2026/04/x.jpg",
            bucket="legacy-cards-product-images-sae1",
            object_name="catalog/cards/primary/2026/04/x.jpg",
            scope="cards",
            slot="primary",
            filename=kwargs["source_filename"],
            content_type="image/jpeg",
            size_bytes=len(kwargs["payload"]),
        )

    monkeypatch.setattr(admin_routes, "upload_image_bytes", fake_upload_image_bytes)
    headers = _auth_headers(monkeypatch)

    response = client.post(
        "/api/v1/admin/uploads/image",
        headers=headers,
        data={
            "scope": "cards",
            "slot": "primary",
            "slug": "charizard",
        },
        files={"file": ("charizard.jpg", b"fake-image", "image/jpeg")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "cards"
    assert body["slot"] == "primary"
    assert body["bucket"] == "legacy-cards-product-images-sae1"
    assert body["url"].startswith("https://storage.googleapis.com/")
    get_settings.cache_clear()


def test_admin_upload_image_validation_error(monkeypatch) -> None:
    from app.api.routes import admin as admin_routes
    from app.services.media_storage import AssetStorageValidationError

    def fake_upload_image_bytes(**_kwargs):
        raise AssetStorageValidationError("Arquivo vazio.")

    monkeypatch.setattr(admin_routes, "upload_image_bytes", fake_upload_image_bytes)
    headers = _auth_headers(monkeypatch)

    response = client.post(
        "/api/v1/admin/uploads/image",
        headers=headers,
        data={"scope": "products", "slot": "gallery"},
        files={"file": ("product.png", b"", "image/png")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Arquivo vazio."
    get_settings.cache_clear()


def test_admin_upload_image_branding_scope(monkeypatch) -> None:
    from app.api.routes import admin as admin_routes
    from app.services.media_storage import UploadedImage

    def fake_upload_image_bytes(**kwargs):
        assert kwargs["scope"] == "branding"
        assert kwargs["slot"] == "hero_logo_primary"
        return UploadedImage(
            url="https://storage.googleapis.com/legacy-cards/catalog/branding/hero_logo_primary/x.webp",
            bucket="legacy-cards",
            object_name="catalog/branding/hero_logo_primary/x.webp",
            scope="branding",
            slot="hero_logo_primary",
            filename=kwargs["source_filename"],
            content_type="image/webp",
            size_bytes=len(kwargs["payload"]),
        )

    monkeypatch.setattr(admin_routes, "upload_image_bytes", fake_upload_image_bytes)
    headers = _auth_headers(monkeypatch)

    response = client.post(
        "/api/v1/admin/uploads/image",
        headers=headers,
        data={
            "scope": "branding",
            "slot": "hero_logo_primary",
            "slug": "slide-logo",
        },
        files={"file": ("logo.webp", b"fake-image", "image/webp")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "branding"
    assert body["slot"] == "hero_logo_primary"
