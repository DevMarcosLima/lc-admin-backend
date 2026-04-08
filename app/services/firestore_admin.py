from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.core.config import get_settings
from app.schemas.store import StoreProduct
from app.services.bigquery_admin import (
    BigQueryConnectionError,
)
from app.services.bigquery_admin import (
    analytics_summary_last_days as bigquery_analytics_summary_last_days,
)


class FirestoreConnectionError(RuntimeError):
    pass


class FirestoreQuotaExceeded(FirestoreConnectionError):
    pass


CATEGORY_SUBCOLLECTION = "items"
MAX_ANALYTICS_DOCS = 2500

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


def _map_firestore_error(exc: Exception, context: str) -> FirestoreConnectionError:
    resource_exhausted_types: tuple[type[BaseException], ...] = ()
    try:
        from google.api_core.exceptions import ResourceExhausted

        resource_exhausted_types = (ResourceExhausted,)
    except ModuleNotFoundError:
        resource_exhausted_types = ()

    if resource_exhausted_types and isinstance(exc, resource_exhausted_types):
        return FirestoreQuotaExceeded(
            "Firestore quota exceeded (429). Aguarde alguns minutos ou ajuste a cota no GCP."
        )

    return FirestoreConnectionError(f"{context}: {exc}")


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
    database_id = settings.firestore_database_id or "(default)"

    if service_account_path.exists():
        try:
            credentials = service_account.Credentials.from_service_account_file(
                str(service_account_path)
            )
            project_id = project_id or credentials.project_id
            if not project_id:
                raise FirestoreConnectionError(
                    "Unable to resolve project id from service account"
                )
            return firestore.Client(
                project=project_id,
                credentials=credentials,
                database=database_id,
            )
        except FirestoreConnectionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _map_firestore_error(exc, "Failed to initialize Firestore client") from exc

    try:
        if project_id:
            return firestore.Client(project=project_id, database=database_id)
        return firestore.Client(database=database_id)
    except Exception as exc:  # noqa: BLE001
        raise _map_firestore_error(exc, "Failed to initialize Firestore client") from exc


def _bucket_for_product(product: StoreProduct) -> str:
    return PRODUCT_BUCKET_MAP.get(product.product_type, product.product_type)


def _all_bucket_ids() -> list[str]:
    return sorted(set(PRODUCT_BUCKET_MAP.values()))


def _find_product_document(slug: str) -> tuple[str, Any, dict[str, Any]] | None:
    settings = get_settings()
    client = get_firestore_client()
    catalog_ref = client.collection(settings.firestore_collection_products)

    try:
        for bucket in _all_bucket_ids():
            doc_ref = catalog_ref.document(bucket).collection(CATEGORY_SUBCOLLECTION).document(slug)
            snapshot = doc_ref.get()
            if not snapshot.exists:
                continue
            return bucket, doc_ref, snapshot.to_dict() or {}
    except Exception as exc:  # noqa: BLE001
        raise _map_firestore_error(exc, "Failed to locate product in Firestore") from exc

    return None


def fetch_products_from_firestore() -> list[StoreProduct]:
    settings = get_settings()
    client = get_firestore_client()
    catalog_ref = client.collection(settings.firestore_collection_products)

    products: list[StoreProduct] = []
    seen: set[str] = set()

    try:
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
    except Exception as exc:  # noqa: BLE001
        raise _map_firestore_error(exc, "Failed to fetch products from Firestore") from exc

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

    try:
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
    except Exception as exc:  # noqa: BLE001
        raise _map_firestore_error(exc, "Failed to upsert product in Firestore") from exc

    return product


def delete_product(slug: str) -> bool:
    existing = _find_product_document(slug)
    if not existing:
        return False

    _, doc_ref, _ = existing
    try:
        doc_ref.delete()
    except Exception as exc:  # noqa: BLE001
        raise _map_firestore_error(exc, "Failed to delete product from Firestore") from exc

    return True


def analytics_summary_last_days(days: int = 30) -> list[tuple[str, int]]:
    _, summary = analytics_summary_last_days_with_source(days=days)
    return summary


def analytics_summary_last_days_with_source(days: int = 30) -> tuple[str, list[tuple[str, int]]]:
    settings = get_settings()

    if settings.bigquery_enabled:
        try:
            summary = bigquery_analytics_summary_last_days(days=days)
            return "bigquery", summary
        except BigQueryConnectionError as exc:
            if not settings.analytics_summary_fallback_firestore:
                raise FirestoreConnectionError(str(exc)) from exc

    client = get_firestore_client()
    threshold = datetime.now(UTC) - timedelta(days=days)
    threshold_date = threshold.date().isoformat()

    counts: Counter[str] = Counter()

    try:
        query = client.collection(settings.firestore_collection_analytics).where(
            "date_utc", ">=", threshold_date
        )

        for index, doc in enumerate(query.stream()):
            if index >= MAX_ANALYTICS_DOCS:
                break

            payload = doc.to_dict() or {}
            endpoint = str(payload.get("endpoint") or "unknown")
            created_raw = str(payload.get("created_at") or "")
            date_raw = str(payload.get("date_utc") or "")

            created_at: datetime | None = None
            if created_raw:
                try:
                    created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                except ValueError:
                    created_at = None
            elif date_raw:
                try:
                    created_at = datetime.fromisoformat(f"{date_raw}T00:00:00+00:00")
                except ValueError:
                    created_at = None

            if not created_at or created_at < threshold:
                continue

            counts[endpoint] += 1
    except Exception as exc:  # noqa: BLE001
        raise _map_firestore_error(exc, "Failed to query analytics summary") from exc

    return "firestore", sorted(counts.items(), key=lambda item: item[1], reverse=True)
