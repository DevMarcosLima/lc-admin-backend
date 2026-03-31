from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.core.config import get_settings
from app.schemas.store import StoreProduct


class FirestoreConnectionError(RuntimeError):
    pass


CATEGORY_SUBCOLLECTION = "items"

PRODUCT_BUCKET_MAP: dict[str, str] = {
    "single_card": "cards",
    "booster": "booster",
    "blister": "blister",
    "collector_box": "collector_box",
    "trainer_box": "trainer_box",
    "tin": "tin",
    "accessory": "accessories",
}


def _resolve_service_account_path() -> Path:
    settings = get_settings()
    configured = Path(settings.firestore_service_account_path)
    if configured.is_absolute():
        return configured
    cwd_path = Path.cwd() / configured
    if cwd_path.exists():
        return cwd_path
    return settings.backend_root / configured


@lru_cache(maxsize=1)
def get_firestore_client() -> Any:
    settings = get_settings()
    if not settings.firestore_enabled:
        raise FirestoreConnectionError("Firestore disabled")

    try:
        from google.cloud import firestore
        from google.oauth2 import service_account
    except ModuleNotFoundError as exc:
        raise FirestoreConnectionError("google-cloud-firestore is not installed") from exc

    service_account_path = _resolve_service_account_path()
    project_id = settings.firestore_project_id

    if service_account_path.exists():
        credentials = service_account.Credentials.from_service_account_file(
            str(service_account_path)
        )
        project_id = project_id or credentials.project_id
        if not project_id:
            raise FirestoreConnectionError("Unable to resolve project id from service account")
        return firestore.Client(project=project_id, credentials=credentials)

    if project_id:
        return firestore.Client(project=project_id)
    return firestore.Client()


def _bucket_for_product(product: StoreProduct) -> str:
    return PRODUCT_BUCKET_MAP.get(product.product_type, product.product_type)


def _all_bucket_ids() -> list[str]:
    return sorted(set(PRODUCT_BUCKET_MAP.values()))


def _find_product_document(slug: str) -> tuple[str, Any, dict[str, Any]] | None:
    settings = get_settings()
    client = get_firestore_client()
    catalog_ref = client.collection(settings.firestore_collection_products)

    for bucket in _all_bucket_ids():
        doc_ref = catalog_ref.document(bucket).collection(CATEGORY_SUBCOLLECTION).document(slug)
        snapshot = doc_ref.get()
        if not snapshot.exists:
            continue
        return bucket, doc_ref, snapshot.to_dict() or {}

    return None


def fetch_products_from_firestore() -> list[StoreProduct]:
    settings = get_settings()
    client = get_firestore_client()
    catalog_ref = client.collection(settings.firestore_collection_products)

    products: list[StoreProduct] = []
    seen: set[str] = set()

    for category_doc in catalog_ref.stream():
        for item_doc in category_doc.reference.collection(CATEGORY_SUBCOLLECTION).stream():
            payload = item_doc.to_dict() or {}
            payload.setdefault("slug", item_doc.id)
            try:
                product = StoreProduct.model_validate(payload)
            except ValidationError:
                continue
            if product.slug in seen:
                continue
            seen.add(product.slug)
            products.append(product)

    return products


def upsert_product(product: StoreProduct) -> StoreProduct:
    settings = get_settings()
    client = get_firestore_client()
    catalog_ref = client.collection(settings.firestore_collection_products)

    now_iso = datetime.now(UTC).isoformat()
    target_bucket = _bucket_for_product(product)

    existing = _find_product_document(product.slug)
    created_at = now_iso
    if existing:
        _, _, existing_payload = existing
        created_at = str(existing_payload.get("created_at") or now_iso)

    category_ref = catalog_ref.document(target_bucket)
    category_ref.set({"id": target_bucket, "updated_at": now_iso}, merge=True)

    doc_ref = category_ref.collection(CATEGORY_SUBCOLLECTION).document(product.slug)
    payload = product.model_dump()
    payload["bucket"] = target_bucket
    payload["created_at"] = created_at
    payload["updated_at"] = now_iso
    doc_ref.set(payload, merge=True)

    if existing:
        current_bucket, current_doc_ref, _ = existing
        if current_bucket != target_bucket:
            current_doc_ref.delete()

    return product


def delete_product(slug: str) -> bool:
    existing = _find_product_document(slug)
    if not existing:
        return False
    _, doc_ref, _ = existing
    doc_ref.delete()
    return True


def analytics_summary_last_days(days: int = 30) -> list[tuple[str, int]]:
    settings = get_settings()
    client = get_firestore_client()
    threshold = datetime.now(UTC) - timedelta(days=days)

    counts: Counter[str] = Counter()
    docs = client.collection(settings.firestore_collection_analytics).stream()
    for doc in docs:
        payload = doc.to_dict() or {}
        endpoint = str(payload.get("endpoint") or "unknown")
        created_raw = str(payload.get("created_at") or "")
        if not created_raw:
            continue
        try:
            created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if created_at < threshold:
            continue
        counts[endpoint] += 1

    return sorted(counts.items(), key=lambda item: item[1], reverse=True)
