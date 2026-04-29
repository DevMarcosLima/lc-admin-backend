from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4

from app.core.config import get_settings

ALLOWED_IMAGE_CONTENT_TYPES: set[str] = {
    "image/avif",
    "image/gif",
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}

CONTENT_TYPE_EXTENSION_MAP: dict[str, str] = {
    "image/avif": ".avif",
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


class AssetStorageConnectionError(RuntimeError):
    pass


class AssetStorageValidationError(RuntimeError):
    pass


@dataclass(slots=True)
class UploadedImage:
    url: str
    bucket: str
    object_name: str
    scope: Literal["cards", "products", "branding"]
    slot: Literal["primary", "gallery", "hero_logo_primary", "hero_logo_secondary", "hero_slide"]
    filename: str
    content_type: str
    size_bytes: int


def _resolve_service_account_path() -> Path:
    settings = get_settings()
    configured = Path(settings.firestore_service_account_path)
    if configured.is_absolute():
        return configured
    cwd_path = Path.cwd() / configured
    if cwd_path.exists():
        return cwd_path
    return settings.backend_root / configured


def _sanitize_bucket_name(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if raw.startswith("gs://"):
        raw = raw[5:]
    return raw.strip("/")


def _resolve_storage_bucket() -> str:
    settings = get_settings()
    candidates = [
        settings.asset_storage_bucket,
        settings.firebase_storage_bucket,
        f"{settings.firestore_project_id}.firebasestorage.app"
        if settings.firestore_project_id
        else None,
        f"{settings.firestore_project_id}.appspot.com"
        if settings.firestore_project_id
        else None,
    ]
    for candidate in candidates:
        normalized = _sanitize_bucket_name(candidate)
        if normalized:
            return normalized
    raise AssetStorageConnectionError(
        "Bucket de imagens não configurado. Defina ASSET_STORAGE_BUCKET."
    )


@lru_cache(maxsize=1)
def get_storage_client() -> Any:
    settings = get_settings()
    try:
        from google.cloud import storage
        from google.oauth2 import service_account
    except ModuleNotFoundError as exc:
        raise AssetStorageConnectionError("Dependência ausente: google-cloud-storage.") from exc

    service_account_path = _resolve_service_account_path()
    project_id = settings.firestore_project_id

    if service_account_path.exists():
        try:
            credentials = service_account.Credentials.from_service_account_file(
                str(service_account_path)
            )
            project_id = project_id or credentials.project_id
            if not project_id:
                raise AssetStorageConnectionError(
                    "Não foi possível identificar FIRESTORE_PROJECT_ID."
                )
            return storage.Client(project=project_id, credentials=credentials)
        except AssetStorageConnectionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AssetStorageConnectionError(
                f"Falha ao inicializar cliente de Storage: {exc}"
            ) from exc

    try:
        if project_id:
            return storage.Client(project=project_id)
        return storage.Client()
    except Exception as exc:  # noqa: BLE001
        raise AssetStorageConnectionError(
            f"Falha ao inicializar cliente de Storage: {exc}"
        ) from exc


def _sanitize_slug(value: str | None) -> str:
    raw = (value or "").strip().lower()
    safe = "".join(char if char.isalnum() else "-" for char in raw)
    safe = "-".join(part for part in safe.split("-") if part)
    return safe[:80]


def _sanitize_filename(value: str | None) -> str:
    name = Path((value or "imagem").strip()).stem
    if not name:
        name = "imagem"
    safe = "".join(char if char.isalnum() else "-" for char in name.lower())
    safe = "-".join(part for part in safe.split("-") if part)
    return safe[:64] or "imagem"


def _resolve_content_type(*, content_type: str | None, filename: str) -> str:
    normalized = (content_type or "").strip().lower()
    if normalized:
        return normalized
    guessed, _ = mimetypes.guess_type(filename)
    if guessed:
        return guessed.lower()
    return "application/octet-stream"


def _resolve_extension(*, content_type: str, filename: str) -> str:
    mapped = CONTENT_TYPE_EXTENSION_MAP.get(content_type)
    if mapped:
        return mapped
    suffix = Path(filename).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}:
        if suffix == ".jpeg":
            return ".jpg"
        return suffix
    return ".jpg"


def _resolve_public_url(bucket: str, object_name: str) -> str:
    settings = get_settings()
    custom_base = (settings.asset_storage_public_base_url or "").strip().rstrip("/")
    encoded_name = quote(object_name, safe="/")
    if custom_base:
        return f"{custom_base}/{encoded_name}"
    return f"https://storage.googleapis.com/{bucket}/{encoded_name}"


def upload_image_bytes(
    *,
    payload: bytes,
    source_filename: str,
    content_type: str | None,
    scope: Literal["cards", "products", "branding"],
    slot: Literal["primary", "gallery", "hero_logo_primary", "hero_logo_secondary", "hero_slide"],
    slug: str | None = None,
) -> UploadedImage:
    settings = get_settings()
    size_bytes = len(payload)
    if size_bytes == 0:
        raise AssetStorageValidationError("Arquivo vazio.")

    max_bytes = max(1, settings.asset_storage_max_image_bytes)
    if size_bytes > max_bytes:
        max_mb = max_bytes / (1024 * 1024)
        raise AssetStorageValidationError(
            f"Imagem excede o limite de {max_mb:.1f} MB."
        )

    resolved_content_type = _resolve_content_type(
        content_type=content_type,
        filename=source_filename,
    )
    if resolved_content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
        raise AssetStorageValidationError(
            "Formato de imagem não suportado. Use JPG, PNG, WEBP, GIF ou AVIF."
        )

    bucket_name = _resolve_storage_bucket()
    storage_client = get_storage_client()
    bucket = storage_client.bucket(bucket_name)

    now = datetime.now(UTC)
    safe_slug = _sanitize_slug(slug)
    safe_filename = _sanitize_filename(source_filename)
    extension = _resolve_extension(content_type=resolved_content_type, filename=source_filename)
    prefix = settings.asset_storage_path_prefix.strip().strip("/") or "catalog"
    object_key = f"{safe_filename}-{uuid4().hex}{extension}"
    if safe_slug:
        object_key = f"{safe_slug}-{object_key}"

    object_name = (
        f"{prefix}/{scope}/{slot}/{now:%Y/%m}/{object_key}"
    )

    try:
        blob = bucket.blob(object_name)
        blob.upload_from_string(payload, content_type=resolved_content_type)
    except Exception as exc:  # noqa: BLE001
        raise AssetStorageConnectionError(
            f"Falha ao enviar imagem para o Storage: {exc}"
        ) from exc

    return UploadedImage(
        url=_resolve_public_url(bucket_name, object_name),
        bucket=bucket_name,
        object_name=object_name,
        scope=scope,
        slot=slot,
        filename=source_filename,
        content_type=resolved_content_type,
        size_bytes=size_bytes,
    )
