from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
import unicodedata
import re
import math

from pydantic import ValidationError

from app.core.config import get_settings
from app.schemas.store import (
    SalesMetricsResponse,
    SalesOrderListResponse,
    SalesOrderRecord,
    SalesPaymentMethodBreakdownItem,
    SalesStatusBreakdownItem,
    SalesTopProductItem,
    StoreProduct,
    WebhookEventListResponse,
    WebhookEventRecord,
)
from app.services.bigquery_admin import (
    BigQueryConnectionError,
)
from app.services.bigquery_admin import (
    analytics_summary_last_days as bigquery_analytics_summary_last_days,
)
from app.services.mercadopago_admin import (
    MercadoPagoAdminError,
    create_full_refund,
    fetch_payment,
)


class FirestoreConnectionError(RuntimeError):
    pass


class FirestoreQuotaExceeded(FirestoreConnectionError):
    pass


class OrderProcessingValidationError(RuntimeError):
    pass


class OrderProcessingNotFoundError(RuntimeError):
    pass


class OrderProcessingForbiddenError(RuntimeError):
    pass


CATEGORY_SUBCOLLECTION = "items"
MAX_ANALYTICS_DOCS = 2500
MAX_SALES_DOCS = 10000
MAX_WEBHOOK_DOCS = 10000
PROCESSABLE_PAYMENT_STATUS = {"approved"}
FULFILLMENT_STATUS_SET = {
    "em_separacao",
    "em_preparacao",
    "separado",
    "rota_transportadora",
    "enviado",
    "cancelado",
}

PRODUCT_BUCKET_MAP: dict[str, str] = {
    "single_card": "cards",
    "booster": "booster",
    "blister": "blister",
    "collector_box": "collector_box",
    "trainer_box": "trainer_box",
    "tin": "tin",
    "accessory": "accessories",
}

ACCESSORY_CATEGORY_BY_KEY: dict[str, str] = {
    "plush": "Pelúcia",
    "pin": "Boton",
    "cup": "Copo",
}

ACCESSORY_KEY_ALIASES: dict[str, str] = {
    "plush": "plush",
    "pelucia": "plush",
    "pelucia pokemon": "plush",
    "pin": "pin",
    "boton": "pin",
    "botao": "pin",
    "broche": "pin",
    "cup": "cup",
    "copo": "cup",
    "caneca": "cup",
    "mug": "cup",
}

GENERIC_ACCESSORY_CATEGORY_KEYS = {
    "acessorio",
    "acessorios",
    "acessorios pokemon",
    "acessorio pokemon",
}

PANEL_SETTINGS_MENU_DOC = "store_menu"
PANEL_SETTINGS_CATEGORIES_DOC = "store_categories"
PANEL_SETTINGS_BRANDING_DOC = "store_branding"
DEFAULT_PANEL_MENU_CONFIG: list[dict[str, Any]] = [
    {
        "id": "special",
        "label": "Em alta",
        "tab": "special",
        "subtab": None,
        "enabled": True,
        "children": [],
    },
    {
        "id": "cards",
        "label": "Cartas Pokemon",
        "tab": "cards",
        "subtab": None,
        "enabled": True,
        "children": [
            {
                "id": "single_card",
                "label": "Cartas avulsas",
                "tab": "cards",
                "subtab": "single_card",
                "enabled": True,
            },
            {
                "id": "booster",
                "label": "Booster",
                "tab": "cards",
                "subtab": "booster",
                "enabled": True,
            },
            {
                "id": "blister",
                "label": "Blister",
                "tab": "cards",
                "subtab": "blister",
                "enabled": True,
            },
            {
                "id": "collector_box",
                "label": "Box",
                "tab": "cards",
                "subtab": "collector_box",
                "enabled": True,
            },
            {
                "id": "trainer_box",
                "label": "Box de treinador",
                "tab": "cards",
                "subtab": "trainer_box",
                "enabled": True,
            },
            {
                "id": "tin",
                "label": "Lata",
                "tab": "cards",
                "subtab": "tin",
                "enabled": True,
            },
        ],
    },
    {
        "id": "accessories",
        "label": "Acessorios Pokemon",
        "tab": "accessories",
        "subtab": None,
        "enabled": True,
        "children": [
            {
                "id": "plush",
                "label": "Pelucia",
                "tab": "accessories",
                "subtab": "plush",
                "enabled": True,
            },
            {
                "id": "pin",
                "label": "Boton",
                "tab": "accessories",
                "subtab": "pin",
                "enabled": True,
            },
            {
                "id": "cup",
                "label": "Copo",
                "tab": "accessories",
                "subtab": "cup",
                "enabled": True,
            },
        ],
    },
    {
        "id": "market",
        "label": "Market",
        "tab": "market",
        "subtab": None,
        "enabled": True,
        "children": [],
    },
]
DEFAULT_PANEL_BRANDING_CONFIG: dict[str, Any] = {
    "hero_logo_primary_url": "/logo.webp",
    "hero_logo_secondary_url": "/logo.webp",
    "hero_logo_primary_width": 140,
    "hero_logo_secondary_width": 140,
    "hero_slide_targets": [],
    "hero_slides": [],
}

CARD_MENU_CHILD_CONFIG: list[tuple[str, str]] = [
    ("single_card", "Cartas avulsas"),
    ("booster", "Booster"),
    ("blister", "Blister"),
    ("collector_box", "Box"),
    ("trainer_box", "Box de treinador"),
    ("tin", "Lata"),
]

KNOWN_ACCESSORY_ORDER: list[str] = ["plush", "pin", "cup"]


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


def _settings_collection_ref() -> Any:
    settings = get_settings()
    client = get_firestore_client()
    return client.collection(settings.firestore_collection_panel_settings)


def _slugify(value: str) -> str:
    safe = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
    safe = "-".join(part for part in safe.split("-") if part)
    return safe or "item"


def _normalize_text_key(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    normalized = unicodedata.normalize("NFKD", raw)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _route_token_from_text(value: str | None) -> str | None:
    normalized = _normalize_text_key(value)
    if not normalized:
        return None
    token = re.sub(r"[^a-z0-9]+", "-", normalized)
    token = re.sub(r"-{2,}", "-", token).strip("-")
    return token or None


def _title_from_route_token(token: str | None) -> str | None:
    safe_token = (token or "").strip().lower()
    if not safe_token:
        return None
    return " ".join(part.capitalize() for part in safe_token.split("-") if part) or None


def _infer_accessory_key_from_text(text: str | None) -> str | None:
    key = _normalize_text_key(text)
    if not key:
        return None

    if any(token in key for token in ("pelucia", "plush", "boneco")):
        return "plush"
    if any(token in key for token in ("boton", "botao", "pin", "broche")):
        return "pin"
    if any(token in key for token in ("copo", "caneca", "mug", "tumbler")):
        return "cup"
    return None


def _canonicalize_accessory_category(
    *,
    category: str | None,
    accessory_kind: str | None,
    product_name: str | None,
    product_slug: str | None,
) -> tuple[str | None, str | None]:
    safe_category = _safe_str(category)
    safe_accessory_kind = _safe_str(accessory_kind)
    category_key = _normalize_text_key(safe_category)
    accessory_key = _normalize_text_key(safe_accessory_kind)

    resolved_key = ACCESSORY_KEY_ALIASES.get(category_key)
    if not resolved_key and category_key in GENERIC_ACCESSORY_CATEGORY_KEYS:
        resolved_key = ACCESSORY_KEY_ALIASES.get(accessory_key)

    if not resolved_key:
        resolved_key = ACCESSORY_KEY_ALIASES.get(accessory_key)

    if not resolved_key and category_key and category_key not in GENERIC_ACCESSORY_CATEGORY_KEYS:
        resolved_key = _route_token_from_text(safe_category)

    if not resolved_key and accessory_key:
        resolved_key = _route_token_from_text(safe_accessory_kind)

    if not resolved_key:
        resolved_key = _infer_accessory_key_from_text(product_name)

    if not resolved_key:
        resolved_key = _infer_accessory_key_from_text(product_slug)

    if not resolved_key:
        return (safe_category or None), None

    if safe_category and category_key not in GENERIC_ACCESSORY_CATEGORY_KEYS:
        resolved_category = safe_category
    else:
        resolved_category = ACCESSORY_CATEGORY_BY_KEY.get(resolved_key) or _title_from_route_token(
            resolved_key
        )

    return resolved_category, resolved_key


def _snapshot_to_product(snapshot: Any) -> StoreProduct | None:
    payload = snapshot.to_dict() or {}
    payload.setdefault("slug", snapshot.id)
    settings = get_settings()
    payload.setdefault("store_name", settings.legacy_store_name)
    payload.setdefault("store_slug", settings.legacy_store_slug)
    payload.setdefault("owner_type", "admin")
    payload.setdefault("seller_template_enabled", True)
    payload.setdefault("allow_seller_custom_image", True)
    if _normalize_text_key(str(payload.get("product_type") or "")) == "accessory":
        normalized_category, resolved_key = _canonicalize_accessory_category(
            category=str(payload.get("category") or ""),
            accessory_kind=str(payload.get("accessory_kind") or ""),
            product_name=str(payload.get("name") or ""),
            product_slug=str(payload.get("slug") or snapshot.id),
        )
        if normalized_category:
            payload["category"] = normalized_category
        if resolved_key:
            payload["accessory_kind"] = resolved_key
    try:
        return StoreProduct.model_validate(payload)
    except ValidationError:
        return None


def _find_product_document(slug: str) -> tuple[str, Any, dict[str, Any]] | None:
    settings = get_settings()
    client = get_firestore_client()
    catalog_ref = client.collection(settings.firestore_collection_products)
    visited_buckets: set[str] = set()
    collection_group_error: Exception | None = None

    try:
        query = client.collection_group(CATEGORY_SUBCOLLECTION).where("slug", "==", slug).limit(1)
        for snapshot in query.stream():
            parent_doc = snapshot.reference.parent.parent
            bucket = parent_doc.id if parent_doc is not None else ""
            if bucket:
                visited_buckets.add(bucket)
            return bucket, snapshot.reference, snapshot.to_dict() or {}
    except Exception as exc:  # noqa: BLE001
        # Some projects may not have collection-group index for `items.slug` yet.
        collection_group_error = exc

    try:
        for bucket in _all_bucket_ids():
            visited_buckets.add(bucket)
            doc_ref = catalog_ref.document(bucket).collection(CATEGORY_SUBCOLLECTION).document(slug)
            snapshot = doc_ref.get()
            if not snapshot.exists:
                continue
            return bucket, doc_ref, snapshot.to_dict() or {}

        for category_doc in catalog_ref.stream():
            bucket = str(category_doc.id)
            if bucket in visited_buckets:
                continue
            doc_ref = category_doc.reference.collection(CATEGORY_SUBCOLLECTION).document(slug)
            snapshot = doc_ref.get()
            if not snapshot.exists:
                continue
            return bucket, doc_ref, snapshot.to_dict() or {}
    except Exception as exc:  # noqa: BLE001
        raise _map_firestore_error(exc, "Failed to locate product in Firestore") from exc

    _ = collection_group_error
    return None


def fetch_products_from_firestore() -> list[StoreProduct]:
    settings = get_settings()
    client = get_firestore_client()
    catalog_ref = client.collection(settings.firestore_collection_products)

    products: list[StoreProduct] = []
    seen: set[str] = set()
    collection_group_error: Exception | None = None

    try:
        for item_doc in client.collection_group(CATEGORY_SUBCOLLECTION).stream():
            product = _snapshot_to_product(item_doc)
            if not product:
                continue
            if product.slug in seen:
                continue
            seen.add(product.slug)
            products.append(product)
    except Exception as exc:  # noqa: BLE001
        # Fallback to nested traversal when collection-group index is missing.
        collection_group_error = exc

    if products:
        return products

    try:
        for category_doc in catalog_ref.stream():
            for item_doc in category_doc.reference.collection(CATEGORY_SUBCOLLECTION).stream():
                product = _snapshot_to_product(item_doc)
                if not product:
                    continue
                if product.slug in seen:
                    continue
                seen.add(product.slug)
                products.append(product)
    except Exception as exc:  # noqa: BLE001
        raise _map_firestore_error(exc, "Failed to fetch products from Firestore") from exc

    _ = collection_group_error
    return products


def upsert_product(product: StoreProduct) -> StoreProduct:
    settings = get_settings()
    client = get_firestore_client()
    catalog_ref = client.collection(settings.firestore_collection_products)

    now_iso = datetime.now(UTC).isoformat()
    prepared_product = product.model_copy(
        update={
            "store_name": product.store_name.strip() or settings.legacy_store_name,
            "store_slug": _slugify(product.store_slug or settings.legacy_store_slug),
            "owner_type": (product.owner_type or "admin").strip().lower() or "admin",
            "seller_template_enabled": bool(product.seller_template_enabled),
            "allow_seller_custom_image": bool(product.allow_seller_custom_image),
        }
    )
    if _normalize_text_key(prepared_product.product_type) == "accessory":
        normalized_category, resolved_key = _canonicalize_accessory_category(
            category=prepared_product.category,
            accessory_kind=prepared_product.accessory_kind,
            product_name=prepared_product.name,
            product_slug=prepared_product.slug,
        )
        ensured_kind = resolved_key or _route_token_from_text(prepared_product.category) or "accessory"
        ensured_category = (
            normalized_category
            or _safe_str(prepared_product.category)
            or _title_from_route_token(ensured_kind)
            or "Acessorio"
        )
        prepared_product = prepared_product.model_copy(
            update={
                "category": ensured_category,
                "accessory_kind": ensured_kind,
            }
        )
    target_bucket = _bucket_for_product(prepared_product)

    existing = _find_product_document(product.slug)
    created_at = now_iso
    if existing:
        _, _, existing_payload = existing
        created_at = str(existing_payload.get("created_at") or now_iso)

    try:
        category_ref = catalog_ref.document(target_bucket)
        category_ref.set({"id": target_bucket, "updated_at": now_iso}, merge=True)

        doc_ref = category_ref.collection(CATEGORY_SUBCOLLECTION).document(prepared_product.slug)
        payload = prepared_product.model_dump()
        payload["bucket"] = target_bucket
        payload["created_at"] = created_at
        payload["updated_at"] = now_iso
        doc_ref.set(payload, merge=True)

        if existing:
            current_bucket, current_doc_ref, _ = existing
            if current_bucket != target_bucket:
                current_doc_ref.delete()
                previous_category_ref = current_doc_ref.parent.parent
                if previous_category_ref is not None:
                    previous_category_ref.set(
                        {
                            "id": current_bucket,
                            "updated_at": now_iso,
                        },
                        merge=True,
                    )
    except Exception as exc:  # noqa: BLE001
        raise _map_firestore_error(exc, "Failed to upsert product in Firestore") from exc

    # Mantém categorias/menu do painel sincronizados automaticamente com o catálogo.
    _sync_panel_catalog_settings(fetch_products_from_firestore())

    return prepared_product


def delete_product(slug: str) -> bool:
    existing = _find_product_document(slug)
    if not existing:
        return False

    bucket, doc_ref, _ = existing
    now_iso = datetime.now(UTC).isoformat()
    try:
        doc_ref.delete()
        category_ref = doc_ref.parent.parent
        if category_ref is not None:
            category_ref.set(
                {
                    "id": bucket,
                    "updated_at": now_iso,
                },
                merge=True,
            )
    except Exception as exc:  # noqa: BLE001
        raise _map_firestore_error(exc, "Failed to delete product from Firestore") from exc

    _sync_panel_catalog_settings(fetch_products_from_firestore())

    return True


def list_seller_templates(*, store_slug: str | None = None) -> list[StoreProduct]:
    safe_store_slug = _slugify(store_slug or "") if store_slug else ""
    templates = [
        product
        for product in fetch_products_from_firestore()
        if product.owner_type == "admin" and product.seller_template_enabled
    ]
    if safe_store_slug:
        templates = [
            product for product in templates if _slugify(product.store_slug) == safe_store_slug
        ]
    return sorted(
        templates,
        key=lambda item: (item.category.lower(), item.name.lower(), item.slug.lower()),
    )


def list_products_by_seller(*, seller_email: str) -> list[StoreProduct]:
    safe_email = _safe_str(seller_email).lower()
    if not safe_email:
        return []
    products = [
        product
        for product in fetch_products_from_firestore()
        if product.owner_type == "seller"
        and _safe_str(product.owner_seller_email).lower() == safe_email
    ]
    return sorted(
        products,
        key=lambda item: (item.category.lower(), item.name.lower(), item.slug.lower()),
    )


def set_seller_inventory_mode(
    *,
    seller_email: str,
    standby: bool,
    zero_stock: bool,
) -> tuple[int, int]:
    safe_email = _safe_str(seller_email).lower()
    if not safe_email:
        raise FirestoreConnectionError("Seller invalido para modo de estoque.")

    now_iso = datetime.now(UTC).isoformat()
    touched_buckets: set[str] = set()
    products_affected = 0
    stock_removed = 0

    products = list_products_by_seller(seller_email=safe_email)
    for product in products:
        existing = _find_product_document(product.slug)
        if not existing:
            continue
        bucket, doc_ref, payload = existing
        current_stock = max(0, _safe_int(payload.get("stock"), 0))
        backup_stock = max(0, _safe_int(payload.get("seller_inventory_standby_stock_backup"), 0))
        next_stock = current_stock

        if standby:
            if zero_stock:
                next_stock = 0
        else:
            if current_stock == 0 and backup_stock > 0:
                next_stock = backup_stock

        if next_stock < current_stock:
            stock_removed += current_stock - next_stock

        update_payload: dict[str, Any] = {
            "stock": next_stock,
            "seller_inventory_standby": bool(standby),
            "updated_at": now_iso,
        }
        if standby:
            update_payload["seller_inventory_standby_since"] = now_iso
            if zero_stock:
                update_payload["seller_inventory_standby_stock_backup"] = current_stock
        else:
            update_payload["seller_inventory_standby_since"] = None
            update_payload["seller_inventory_standby_stock_backup"] = None

        try:
            doc_ref.set(update_payload, merge=True)
        except Exception as exc:  # noqa: BLE001
            raise _map_firestore_error(exc, "Falha ao atualizar modo de estoque do seller") from exc

        touched_buckets.add(bucket)
        products_affected += 1

    if touched_buckets:
        client = get_firestore_client()
        settings = get_settings()
        catalog_ref = client.collection(settings.firestore_collection_products)
        for bucket in touched_buckets:
            try:
                catalog_ref.document(bucket).set(
                    {
                        "id": bucket,
                        "updated_at": now_iso,
                    },
                    merge=True,
                )
            except Exception as exc:  # noqa: BLE001
                raise _map_firestore_error(exc, "Falha ao atualizar bucket de produto") from exc

    return products_affected, stock_removed


def publish_seller_product_from_template(
    *,
    seller_email: str,
    template_slug: str,
    quantity: int,
    use_template_image: bool,
    custom_image_url: str | None = None,
    price_brl: float | None = None,
) -> StoreProduct:
    from app.services.seller_accounts import SellerAccountError, get_seller_account

    safe_email = _safe_str(seller_email).lower()
    if not safe_email:
        raise FirestoreConnectionError("Seller invalido para publicar produto.")

    try:
        seller = get_seller_account(safe_email)
    except SellerAccountError as exc:
        raise FirestoreConnectionError(str(exc)) from exc

    if seller is None or seller.status != "active":
        raise FirestoreConnectionError("Seller nao encontrado ou inativo.")

    existing_template = _find_product_document(template_slug)
    if not existing_template:
        raise FirestoreConnectionError("Template de produto nao encontrado.")

    _, _, template_payload = existing_template
    template_payload = dict(template_payload)
    template_payload.setdefault("slug", template_slug)
    template_payload.setdefault("store_name", get_settings().legacy_store_name)
    template_payload.setdefault("store_slug", get_settings().legacy_store_slug)
    template_payload.setdefault("owner_type", "admin")
    template_payload.setdefault("seller_template_enabled", True)
    template_payload.setdefault("allow_seller_custom_image", True)

    try:
        template = StoreProduct.model_validate(template_payload)
    except ValidationError as exc:
        raise FirestoreConnectionError("Template de produto com schema invalido.") from exc

    if not template.seller_template_enabled:
        raise FirestoreConnectionError("Template nao habilitado para sellers.")

    seller_slug = _slugify(seller.shop_slug)
    target_slug = f"seller-{seller_slug}-{_slugify(template.slug)}"
    existing_seller_product = _find_product_document(target_slug)
    existing_stock = 0
    existing_price_brl = template.price_brl
    if existing_seller_product:
        _, _, existing_payload = existing_seller_product
        existing_stock = max(0, _safe_int(existing_payload.get("stock"), 0))
        existing_price_candidate = round(
            _safe_float(existing_payload.get("price_brl"), template.price_brl),
            2,
        )
        if existing_price_candidate > 0:
            existing_price_brl = existing_price_candidate

    requested_quantity = max(1, quantity)
    chosen_custom_image = (custom_image_url or "").strip() or None
    image_url = template.image_url
    if not use_template_image and template.allow_seller_custom_image and chosen_custom_image:
        image_url = chosen_custom_image

    resolved_price_brl = existing_price_brl
    if price_brl is not None:
        custom_price_brl = round(_safe_float(price_brl, template.price_brl), 2)
        if custom_price_brl <= 0:
            raise FirestoreConnectionError("Preco do seller deve ser maior que zero.")
        resolved_price_brl = custom_price_brl

    seller_product = template.model_copy(
        update={
            "slug": target_slug,
            "stock": existing_stock + requested_quantity,
            "price_brl": resolved_price_brl,
            "store_name": seller.shop_name,
            "store_slug": seller.shop_slug,
            "owner_type": "seller",
            "owner_seller_email": seller.email,
            "source_template_slug": template.slug,
            "seller_template_enabled": False,
            "allow_seller_custom_image": template.allow_seller_custom_image,
            "image_url": image_url,
        }
    )
    return upsert_product(seller_product)


def withdraw_seller_product_stock_from_template(
    *,
    seller_email: str,
    template_slug: str,
    quantity: int,
) -> StoreProduct:
    safe_email = _safe_str(seller_email).lower()
    if not safe_email:
        raise FirestoreConnectionError("Seller invalido para ajuste de estoque.")

    safe_template_slug = _safe_str(template_slug).lower()
    if not safe_template_slug:
        raise FirestoreConnectionError("Template invalido para ajuste de estoque.")

    requested_quantity = max(1, quantity)

    matching_products = [
        product
        for product in list_products_by_seller(seller_email=safe_email)
        if _safe_str(product.source_template_slug).lower() == safe_template_slug
    ]

    if not matching_products:
        raise FirestoreConnectionError("Produto consignado nao encontrado para este template.")

    available_total = sum(max(0, product.stock) for product in matching_products)
    if requested_quantity > available_total:
        raise FirestoreConnectionError(
            f"Quantidade para retirada excede seu estoque atual ({available_total})."
        )

    remaining = requested_quantity
    updated_products: dict[str, StoreProduct] = {}

    for seller_product in sorted(
        matching_products,
        key=lambda item: (item.slug.lower(), item.name.lower()),
    ):
        if remaining <= 0:
            break

        current_stock = max(0, seller_product.stock)
        if current_stock <= 0:
            continue

        to_consume = min(current_stock, remaining)
        next_stock = current_stock - to_consume
        updated = upsert_product(seller_product.model_copy(update={"stock": next_stock}))
        updated_products[updated.slug] = updated
        remaining -= to_consume

    if remaining > 0:
        raise FirestoreConnectionError("Falha ao retirar estoque do seller para o template informado.")

    updated_matching_products = sorted(
        [
            updated_products.get(product.slug, product)
            for product in matching_products
        ],
        key=lambda item: (-max(0, item.stock), item.slug.lower()),
    )

    return updated_matching_products[0]


def update_seller_product_price_from_template(
    *,
    seller_email: str,
    template_slug: str,
    price_brl: float,
) -> StoreProduct:
    safe_email = _safe_str(seller_email).lower()
    if not safe_email:
        raise FirestoreConnectionError("Seller invalido para ajuste de preco.")

    safe_template_slug = _safe_str(template_slug).lower()
    if not safe_template_slug:
        raise FirestoreConnectionError("Template invalido para ajuste de preco.")

    next_price_brl = round(_safe_float(price_brl, -1), 2)
    if next_price_brl <= 0:
        raise FirestoreConnectionError("Preco do seller deve ser maior que zero.")

    matching_products = [
        product
        for product in list_products_by_seller(seller_email=safe_email)
        if _safe_str(product.source_template_slug).lower() == safe_template_slug
    ]
    if not matching_products:
        raise FirestoreConnectionError(
            "Produto consignado nao encontrado para este template. Adicione estoque antes de ajustar preco."
        )

    updated_products = [
        upsert_product(
            seller_product.model_copy(
                update={
                    "price_brl": next_price_brl,
                }
            )
        )
        for seller_product in matching_products
    ]

    updated_products.sort(key=lambda item: (-max(0, item.stock), item.slug.lower()))
    return updated_products[0]


def _resolve_accessory_kind_for_product(product: StoreProduct) -> str | None:
    _normalized_category, resolved_key = _canonicalize_accessory_category(
        category=product.category,
        accessory_kind=product.accessory_kind,
        product_name=product.name,
        product_slug=product.slug,
    )
    if resolved_key:
        return resolved_key

    fallback = _route_token_from_text(product.category) or _route_token_from_text(product.name)
    return fallback


def _build_auto_panel_categories(products: list[StoreProduct]) -> list[str]:
    dedup: dict[str, str] = {}
    for product in products:
        category = _safe_str(product.category)
        if product.product_type == "accessory":
            normalized_category, _resolved_key = _canonicalize_accessory_category(
                category=product.category,
                accessory_kind=product.accessory_kind,
                product_name=product.name,
                product_slug=product.slug,
            )
            category = _safe_str(normalized_category) or category
        if not category:
            continue
        dedup.setdefault(category.lower(), category)
    return sorted(dedup.values(), key=lambda value: value.lower())


def _build_auto_panel_menu_config(products: list[StoreProduct]) -> list[dict[str, Any]]:
    now_iso = datetime.now(UTC).isoformat()
    items: list[dict[str, Any]] = []

    if any(product.is_special for product in products):
        items.append(
            {
                "id": "special",
                "label": "Em alta",
                "tab": "special",
                "subtab": None,
                "enabled": True,
                "children": [],
                "updated_at": now_iso,
            }
        )

    card_children: list[dict[str, Any]] = []
    for child_id, child_label in CARD_MENU_CHILD_CONFIG:
        has_products = any(product.product_type == child_id for product in products)
        if not has_products:
            continue
        card_children.append(
            {
                "id": child_id,
                "label": child_label,
                "tab": "cards",
                "subtab": child_id,
                "enabled": True,
            }
        )
    if card_children:
        items.append(
            {
                "id": "cards",
                "label": "Cartas Pokemon",
                "tab": "cards",
                "subtab": None,
                "enabled": True,
                "children": card_children,
                "updated_at": now_iso,
            }
        )

    accessory_label_by_kind: dict[str, str] = {}
    for product in products:
        if product.product_type != "accessory":
            continue
        kind = _resolve_accessory_kind_for_product(product)
        if not kind:
            continue
        normalized_category, _resolved_key = _canonicalize_accessory_category(
            category=product.category,
            accessory_kind=kind,
            product_name=product.name,
            product_slug=product.slug,
        )
        label = _safe_str(normalized_category) or _title_from_route_token(kind) or kind
        if kind not in accessory_label_by_kind:
            accessory_label_by_kind[kind] = label

    ordered_accessory_kinds = [kind for kind in KNOWN_ACCESSORY_ORDER if kind in accessory_label_by_kind]
    ordered_accessory_kinds.extend(
        sorted(
            [kind for kind in accessory_label_by_kind if kind not in KNOWN_ACCESSORY_ORDER],
            key=lambda kind: accessory_label_by_kind[kind].lower(),
        )
    )

    if ordered_accessory_kinds:
        items.append(
            {
                "id": "accessories",
                "label": "Acessorios Pokemon",
                "tab": "accessories",
                "subtab": None,
                "enabled": True,
                "children": [
                    {
                        "id": kind,
                        "label": accessory_label_by_kind[kind],
                        "tab": "accessories",
                        "subtab": kind,
                        "enabled": True,
                    }
                    for kind in ordered_accessory_kinds
                ],
                "updated_at": now_iso,
            }
        )

    items.append(
        {
            "id": "market",
            "label": "Market",
            "tab": "market",
            "subtab": None,
            "enabled": True,
            "children": [],
            "updated_at": now_iso,
        }
    )

    return items


def _sync_panel_catalog_settings(products: list[StoreProduct]) -> tuple[list[dict[str, Any]], list[str]]:
    menu_items = _build_auto_panel_menu_config(products)
    category_items = _build_auto_panel_categories(products)
    now_iso = datetime.now(UTC).isoformat()
    try:
        settings_ref = _settings_collection_ref()
        settings_ref.document(PANEL_SETTINGS_MENU_DOC).set(
            {
                "items": menu_items,
                "updated_at": now_iso,
                "mode": "auto_catalog",
            },
            merge=True,
        )
        settings_ref.document(PANEL_SETTINGS_CATEGORIES_DOC).set(
            {
                "items": category_items,
                "updated_at": now_iso,
                "mode": "auto_catalog",
            },
            merge=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_firestore_error(exc, "Failed to sync catalog settings") from exc

    return menu_items, category_items


def _normalize_menu_child(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    child_id = _slugify(_safe_str(payload.get("id")))
    tab = _safe_str(payload.get("tab"))
    if not child_id or not tab:
        return None
    return {
        "id": child_id,
        "label": _safe_str(payload.get("label")) or child_id,
        "tab": tab,
        "subtab": _safe_str(payload.get("subtab")) or None,
        "enabled": bool(payload.get("enabled", True)),
    }


def _normalize_menu_item(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    item_id = _slugify(_safe_str(payload.get("id")))
    tab = _safe_str(payload.get("tab"))
    if not item_id or not tab:
        return None

    raw_children = payload.get("children")
    children: list[dict[str, Any]] = []
    if isinstance(raw_children, list):
        for child in raw_children:
            normalized_child = _normalize_menu_child(child)
            if normalized_child:
                children.append(normalized_child)

    return {
        "id": item_id,
        "label": _safe_str(payload.get("label")) or item_id,
        "tab": tab,
        "subtab": _safe_str(payload.get("subtab")) or None,
        "enabled": bool(payload.get("enabled", True)),
        "children": children,
    }


def get_panel_menu_config() -> list[dict[str, Any]]:
    products = fetch_products_from_firestore()
    menu_items, _category_items = _sync_panel_catalog_settings(products)
    normalized_items: list[dict[str, Any]] = []
    for item in menu_items:
        normalized = _normalize_menu_item(item)
        if normalized:
            normalized_items.append(normalized)
    return normalized_items or list(DEFAULT_PANEL_MENU_CONFIG)


def upsert_panel_menu_config(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_items: list[dict[str, Any]] = []
    for item in items:
        normalized = _normalize_menu_item(item)
        if normalized:
            normalized_items.append(normalized)

    if not normalized_items:
        normalized_items = list(DEFAULT_PANEL_MENU_CONFIG)

    now_iso = datetime.now(UTC).isoformat()
    payload = {
        "items": normalized_items,
        "updated_at": now_iso,
    }
    try:
        _settings_collection_ref().document(PANEL_SETTINGS_MENU_DOC).set(payload, merge=True)
    except Exception as exc:  # noqa: BLE001
        raise _map_firestore_error(exc, "Failed to save panel menu config") from exc

    return normalized_items


def _normalize_categories(values: list[Any]) -> list[str]:
    normalized = [
        _safe_str(item)
        for item in values
        if isinstance(item, str) and _safe_str(item)
    ]
    dedup: list[str] = []
    seen: set[str] = set()
    for item in normalized:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        dedup.append(item)
    return sorted(dedup, key=lambda value: value.lower())


def get_panel_categories_config() -> list[str]:
    products = fetch_products_from_firestore()
    _menu_items, category_items = _sync_panel_catalog_settings(products)
    return _normalize_categories(category_items)


def upsert_panel_categories_config(items: list[str]) -> list[str]:
    normalized_items = _normalize_categories(items)
    now_iso = datetime.now(UTC).isoformat()
    payload = {
        "items": normalized_items,
        "updated_at": now_iso,
    }
    try:
        _settings_collection_ref().document(PANEL_SETTINGS_CATEGORIES_DOC).set(payload, merge=True)
    except Exception as exc:  # noqa: BLE001
        raise _map_firestore_error(exc, "Failed to save panel categories config") from exc
    return normalized_items


def _normalize_branding_logo_url(value: Any, fallback: str) -> str:
    safe = _safe_str(value)
    return safe or fallback


def _normalize_branding_width(value: Any, fallback: int) -> int:
    safe = _safe_int(value, fallback)
    return max(40, min(460, safe))


def _normalize_branding_product_slug(value: Any) -> str:
    raw = _safe_str(value).lower()
    if not raw:
        return ""
    normalized = re.sub(r"[^a-z0-9._-]+", "-", raw)
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
    return normalized[:180]


def _normalize_branding_slide_targets(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []

    dedup: dict[int, dict[str, Any]] = {}
    for raw_item in values:
        if not isinstance(raw_item, dict):
            continue

        slide_index = _safe_int(raw_item.get("slide_index"), 0)
        if slide_index < 1 or slide_index > 12:
            continue

        product_slug = _normalize_branding_product_slug(raw_item.get("product_slug"))
        if not product_slug:
            continue

        product_name = _safe_str(raw_item.get("product_name"))
        dedup[slide_index] = {
            "slide_index": slide_index,
            "product_slug": product_slug,
            "product_name": product_name or None,
        }

    return [dedup[index] for index in sorted(dedup.keys())]


def _normalize_branding_focus_x_percent(value: Any, fallback: int = 52) -> int:
    safe = _safe_int(value, fallback)
    return max(0, min(100, safe))


def _normalize_branding_slide_text(value: Any, *, max_length: int) -> str | None:
    safe = _safe_str(value)
    if not safe:
        return None
    return safe[:max_length]


def _normalize_branding_slide_price(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    normalized = value
    if isinstance(value, str):
        normalized = value.replace(",", ".").strip()
    try:
        parsed = float(normalized)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    if parsed < 0:
        return 0.0
    return round(parsed, 2)


def _normalize_branding_slide_assets(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []

    dedup: dict[int, dict[str, Any]] = {}
    for raw_item in values:
        if not isinstance(raw_item, dict):
            continue

        slide_index = _safe_int(raw_item.get("slide_index"), 0)
        if slide_index < 1 or slide_index > 12:
            continue

        image_url = _safe_str(raw_item.get("image_url")) or None
        focus_x_percent = _normalize_branding_focus_x_percent(raw_item.get("focus_x_percent"), 52)
        name = _normalize_branding_slide_text(raw_item.get("name"), max_length=180)
        category = _normalize_branding_slide_text(raw_item.get("category"), max_length=120)
        product_type = _normalize_branding_slide_text(raw_item.get("product_type"), max_length=120)
        price_brl = _normalize_branding_slide_price(raw_item.get("price_brl"))

        if (
            image_url is None
            and focus_x_percent == 52
            and name is None
            and category is None
            and product_type is None
            and price_brl is None
        ):
            continue

        dedup[slide_index] = {
            "slide_index": slide_index,
            "image_url": image_url,
            "focus_x_percent": focus_x_percent,
            "name": name,
            "category": category,
            "product_type": product_type,
            "price_brl": price_brl,
        }

    return [dedup[index] for index in sorted(dedup.keys())]


def _normalize_branding_config(payload: dict[str, Any] | None) -> dict[str, Any]:
    source = payload if isinstance(payload, dict) else {}
    defaults = DEFAULT_PANEL_BRANDING_CONFIG
    return {
        "hero_logo_primary_url": _normalize_branding_logo_url(
            source.get("hero_logo_primary_url"),
            defaults["hero_logo_primary_url"],
        ),
        "hero_logo_secondary_url": _normalize_branding_logo_url(
            source.get("hero_logo_secondary_url"),
            defaults["hero_logo_secondary_url"],
        ),
        "hero_logo_primary_width": _normalize_branding_width(
            source.get("hero_logo_primary_width"),
            defaults["hero_logo_primary_width"],
        ),
        "hero_logo_secondary_width": _normalize_branding_width(
            source.get("hero_logo_secondary_width"),
            defaults["hero_logo_secondary_width"],
        ),
        "hero_slide_targets": _normalize_branding_slide_targets(
            source.get("hero_slide_targets"),
        ),
        "hero_slides": _normalize_branding_slide_assets(
            source.get("hero_slides"),
        ),
    }


def get_panel_branding_config() -> dict[str, Any]:
    try:
        snapshot = _settings_collection_ref().document(PANEL_SETTINGS_BRANDING_DOC).get()
    except Exception as exc:  # noqa: BLE001
        raise _map_firestore_error(exc, "Failed to load panel branding config") from exc

    payload = snapshot.to_dict() if snapshot.exists else {}
    normalized = _normalize_branding_config(payload)
    updated_at = _safe_str((payload or {}).get("updated_at")) if isinstance(payload, dict) else ""
    normalized["updated_at"] = updated_at or None
    return normalized


def upsert_panel_branding_config(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_branding_config(payload)
    now_iso = datetime.now(UTC).isoformat()
    firestore_payload = {
        **normalized,
        "updated_at": now_iso,
    }
    try:
        _settings_collection_ref().document(PANEL_SETTINGS_BRANDING_DOC).set(
            firestore_payload,
            merge=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_firestore_error(exc, "Failed to save panel branding config") from exc

    return {
        **normalized,
        "updated_at": now_iso,
    }


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


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_fulfillment_status(value: Any) -> str:
    normalized = _safe_str(value).lower()
    if not normalized:
        return ""

    aliases = {
        "em separacao": "em_separacao",
        "em_separacao": "em_separacao",
        "em preparação": "em_preparacao",
        "em preparacao": "em_preparacao",
        "em_preparacao": "em_preparacao",
        "separado": "separado",
        "rota para transportadora": "rota_transportadora",
        "rota_transportadora": "rota_transportadora",
        "enviado": "enviado",
        "cancelado": "cancelado",
    }

    if normalized in aliases:
        return aliases[normalized]

    return normalized.replace(" ", "_")


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC)

    text = _safe_str(value)
    if not text:
        return None

    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).astimezone(UTC)
    except ValueError:
        return None


def _order_timestamp(payload: dict[str, Any]) -> datetime:
    for candidate in (
        payload.get("created_at"),
        payload.get("updated_at"),
        payload.get("date_last_updated"),
        payload.get("date_approved"),
    ):
        parsed = _parse_timestamp(candidate)
        if parsed is not None:
            return parsed
    return datetime.fromtimestamp(0, tz=UTC)


def _timestamp_or_epoch(value: Any) -> datetime:
    parsed = _parse_timestamp(value)
    if parsed is not None:
        return parsed
    return datetime.fromtimestamp(0, tz=UTC)


def _fetch_orders_payloads() -> list[dict[str, Any]]:
    settings = get_settings()
    client = get_firestore_client()
    rows: list[dict[str, Any]] = []

    try:
        order_stream = client.collection(settings.firestore_collection_orders).stream()
        for index, doc in enumerate(order_stream):
            if index >= MAX_SALES_DOCS:
                break
            payload = doc.to_dict() or {}
            payload.setdefault("order_id", doc.id)
            rows.append(payload)
    except Exception as exc:  # noqa: BLE001
        raise _map_firestore_error(exc, "Failed to fetch orders from Firestore") from exc

    return rows


def _map_sales_order_record(payload: dict[str, Any]) -> SalesOrderRecord:
    raw_items = payload.get("items")
    mapped_items = []
    if isinstance(raw_items, list):
        for item in raw_items:
            if not isinstance(item, dict):
                continue

            quantity = max(0, _safe_int(item.get("quantity"), 0))
            unit_price = round(_safe_float(item.get("unit_price_brl"), 0.0), 2)
            total_price = round(_safe_float(item.get("total_price_brl"), unit_price * quantity), 2)
            mapped_items.append(
                {
                    "slug": _safe_str(item.get("slug")) or None,
                    "lot_slug": _safe_str(item.get("lot_slug")) or None,
                    "lot_id": _safe_str(item.get("lot_id")) or None,
                    "name": _safe_str(item.get("name")) or None,
                    "product_type": _safe_str(item.get("product_type")) or None,
                    "store_name": _safe_str(item.get("store_name")) or None,
                    "store_slug": _safe_str(item.get("store_slug")) or None,
                    "owner_type": _safe_str(item.get("owner_type")) or None,
                    "owner_seller_email": _safe_str(item.get("owner_seller_email")) or None,
                    "quantity": quantity,
                    "unit_price_brl": unit_price,
                    "total_price_brl": total_price,
                }
            )

    payment_status = _safe_str(payload.get("status")).lower() or "unknown"
    inventory_sync = payload.get("inventory_sync")
    inventory_status = (
        _safe_str(inventory_sync.get("status"))
        if isinstance(inventory_sync, dict)
        else ""
    )
    fulfillment_payload = payload.get("fulfillment") if isinstance(payload.get("fulfillment"), dict) else {}
    fulfillment_status = (
        _normalize_fulfillment_status(payload.get("fulfillment_status"))
        or _normalize_fulfillment_status(fulfillment_payload.get("status"))
    )
    if not fulfillment_status and payment_status in PROCESSABLE_PAYMENT_STATUS:
        fulfillment_status = "em_separacao"

    fulfillment_status_updated_at = (
        _safe_str(payload.get("fulfillment_status_updated_at"))
        or _safe_str(fulfillment_payload.get("updated_at"))
        or None
    )
    fulfillment_queue_entered_at = (
        _safe_str(payload.get("fulfillment_queue_entered_at"))
        or _safe_str(fulfillment_payload.get("queue_entered_at"))
        or None
    )
    if not fulfillment_queue_entered_at and payment_status in PROCESSABLE_PAYMENT_STATUS:
        fulfillment_queue_entered_at = (
            _safe_str(payload.get("date_approved"))
            or _safe_str(payload.get("updated_at"))
            or _safe_str(payload.get("created_at"))
            or None
        )

    refund_payload = (
        fulfillment_payload.get("refund")
        if isinstance(fulfillment_payload.get("refund"), dict)
        else {}
    )
    refund_status = (
        _safe_str(payload.get("refund_status"))
        or _safe_str(refund_payload.get("status"))
        or None
    )
    refund_id = (
        _safe_str(payload.get("refund_id"))
        or _safe_str(refund_payload.get("refund_id"))
        or None
    )
    refund_updated_at = (
        _safe_str(payload.get("refund_updated_at"))
        or _safe_str(refund_payload.get("updated_at"))
        or None
    )

    return SalesOrderRecord.model_validate(
        {
            "order_id": _safe_str(payload.get("order_id")),
            "external_reference": _safe_str(payload.get("external_reference")) or None,
            "payment_id": _safe_str(payload.get("payment_id")) or None,
            "uid": _safe_str(payload.get("uid")) or None,
            "user_email": _safe_str(payload.get("user_email")) or None,
            "status": payment_status,
            "status_detail": _safe_str(payload.get("status_detail")) or None,
            "payment_type_id": _safe_str(payload.get("payment_type_id")) or None,
            "payment_method_id": _safe_str(payload.get("payment_method_id")) or None,
            "subtotal_brl": round(_safe_float(payload.get("subtotal_brl"), 0.0), 2),
            "shipping_brl": round(_safe_float(payload.get("shipping_brl"), 0.0), 2),
            "discount_brl": round(_safe_float(payload.get("discount_brl"), 0.0), 2),
            "total_brl": round(_safe_float(payload.get("total_brl"), 0.0), 2),
            "total_items": max(0, _safe_int(payload.get("total_items"), 0)),
            "coupon_code": _safe_str(payload.get("coupon_code")) or None,
            "shipping_id": _safe_str(payload.get("shipping_id")) or None,
            "shipping_zip_code": _safe_str(payload.get("shipping_zip_code")) or None,
            "shipping_provider": _safe_str(payload.get("shipping_provider")) or None,
            "shipping_carrier": _safe_str(payload.get("shipping_carrier")) or None,
            "shipping_service_name": _safe_str(payload.get("shipping_service_name")) or None,
            "shipping_service_code": _safe_str(payload.get("shipping_service_code")) or None,
            "shipping_eta_label": _safe_str(payload.get("shipping_eta_label")) or None,
            "shipping_eta_days_min": (
                _safe_int(payload.get("shipping_eta_days_min"), 0)
                if payload.get("shipping_eta_days_min") is not None
                else None
            ),
            "shipping_eta_days_max": (
                _safe_int(payload.get("shipping_eta_days_max"), 0)
                if payload.get("shipping_eta_days_max") is not None
                else None
            ),
            "shipping_margin_percent": round(
                _safe_float(payload.get("shipping_margin_percent"), 0.0), 2
            ),
            "shipping_margin_brl": round(_safe_float(payload.get("shipping_margin_brl"), 0.0), 2),
            "shipping_base_brl": round(_safe_float(payload.get("shipping_base_brl"), 0.0), 2),
            "shipping_cashback_credit_brl": round(
                _safe_float(payload.get("shipping_cashback_credit_brl"), 0.0), 2
            ),
            "shipping_packages_count": max(0, _safe_int(payload.get("shipping_packages_count"), 0)),
            "shipping_origin_cep": _safe_str(payload.get("shipping_origin_cep")) or None,
            "shipping_destination_cep": _safe_str(payload.get("shipping_destination_cep")) or None,
            "shipping_snapshot": (
                payload.get("shipping_snapshot")
                if isinstance(payload.get("shipping_snapshot"), dict)
                else None
            ),
            "source": _safe_str(payload.get("source")) or None,
            "created_at": _safe_str(payload.get("created_at")) or None,
            "updated_at": _safe_str(payload.get("updated_at")) or None,
            "date_approved": _safe_str(payload.get("date_approved")) or None,
            "date_last_updated": _safe_str(payload.get("date_last_updated")) or None,
            "inventory_sync_status": inventory_status or None,
            "webhook_last_received_at": _safe_str(payload.get("webhook_last_received_at")) or None,
            "webhook_last_action": _safe_str(payload.get("webhook_last_action")) or None,
            "fulfillment_status": fulfillment_status or None,
            "fulfillment_status_updated_at": fulfillment_status_updated_at,
            "fulfillment_queue_entered_at": fulfillment_queue_entered_at,
            "fulfillment_cancel_reason": (
                _safe_str(payload.get("fulfillment_cancel_reason"))
                or _safe_str(fulfillment_payload.get("cancellation_reason"))
                or None
            ),
            "fulfillment_tracking_code": (
                _safe_str(payload.get("fulfillment_tracking_code"))
                or _safe_str(fulfillment_payload.get("tracking_code"))
                or None
            ),
            "refund_status": refund_status,
            "refund_id": refund_id,
            "refund_updated_at": refund_updated_at,
            "items": mapped_items,
        }
    )


def _scope_order_for_store(
    order: SalesOrderRecord,
    *,
    store_slug: str,
    owner_seller_email: str,
) -> SalesOrderRecord | None:
    safe_store_slug = _safe_str(store_slug).lower()
    safe_owner_email = _safe_str(owner_seller_email).lower()
    if not safe_store_slug and not safe_owner_email:
        return order

    scoped_items = []
    for item in order.items:
        item_store_slug = _safe_str(item.store_slug).lower()
        item_owner_email = _safe_str(item.owner_seller_email).lower()
        if safe_store_slug and item_store_slug != safe_store_slug:
            continue
        if safe_owner_email and item_owner_email != safe_owner_email:
            continue
        scoped_items.append(item)

    if not scoped_items:
        return None

    scoped_total_items = sum(max(0, _safe_int(item.quantity, 0)) for item in scoped_items)
    scoped_total_brl = round(
        sum(max(0.0, _safe_float(item.total_price_brl, 0.0)) for item in scoped_items),
        2,
    )

    return order.model_copy(
        update={
            "items": scoped_items,
            "total_items": scoped_total_items,
            "subtotal_brl": scoped_total_brl,
            "discount_brl": 0.0,
            "shipping_brl": 0.0,
            "total_brl": scoped_total_brl,
        }
    )


def list_sales_orders(
    *,
    page: int = 1,
    limit: int = 20,
    status: str | None = None,
    query: str | None = None,
    store_slug: str | None = None,
    owner_seller_email: str | None = None,
) -> SalesOrderListResponse:
    safe_page = max(1, page)
    safe_limit = max(1, min(limit, 100))
    status_filter = _safe_str(status).lower()
    query_filter = _safe_str(query).lower()
    store_filter = _safe_str(store_slug).lower()
    seller_filter = _safe_str(owner_seller_email).lower()

    rows = _fetch_orders_payloads()
    mapped_orders = [_map_sales_order_record(row) for row in rows]
    if store_filter or seller_filter:
        scoped_orders: list[SalesOrderRecord] = []
        for order in mapped_orders:
            scoped = _scope_order_for_store(
                order,
                store_slug=store_filter,
                owner_seller_email=seller_filter,
            )
            if scoped is not None:
                scoped_orders.append(scoped)
        mapped_orders = scoped_orders

    if status_filter and status_filter != "all":
        mapped_orders = [item for item in mapped_orders if item.status.lower() == status_filter]

    if query_filter:
        mapped_orders = [
            item
            for item in mapped_orders
            if query_filter
            in " ".join(
                [
                    (item.order_id or ""),
                    (item.external_reference or ""),
                    (item.payment_id or ""),
                    (item.user_email or ""),
                    (item.uid or ""),
                    (item.payment_method_id or ""),
                    (item.status or ""),
                ]
            ).lower()
        ]

    mapped_orders.sort(key=lambda item: _timestamp_or_epoch(item.created_at), reverse=True)
    total_orders = len(mapped_orders)
    start = (safe_page - 1) * safe_limit
    end = start + safe_limit
    page_items = mapped_orders[start:end]
    has_more = end < total_orders

    return SalesOrderListResponse(
        source="firestore",
        page=safe_page,
        limit=safe_limit,
        total_orders=total_orders,
        has_more=has_more,
        items=page_items,
    )


def _get_order_document_by_id(order_id: str) -> tuple[str, dict[str, Any], Any]:
    safe_order_id = _safe_str(order_id)
    if not safe_order_id:
        raise OrderProcessingValidationError("order_id invalido para processamento.")

    settings = get_settings()
    client = get_firestore_client()
    try:
        reference = client.collection(settings.firestore_collection_orders).document(safe_order_id)
        snapshot = reference.get()
    except Exception as exc:  # noqa: BLE001
        raise _map_firestore_error(exc, "Failed to fetch order from Firestore") from exc

    if not snapshot.exists:
        raise OrderProcessingNotFoundError("Pedido nao encontrado para processamento.")

    payload = snapshot.to_dict() or {}
    payload.setdefault("order_id", safe_order_id)
    return safe_order_id, payload, reference


def _persist_order_payload(
    *,
    order_id: str,
    payload: dict[str, Any],
) -> None:
    settings = get_settings()
    client = get_firestore_client()
    try:
        client.collection(settings.firestore_collection_orders).document(order_id).set(payload, merge=True)
    except Exception as exc:  # noqa: BLE001
        raise _map_firestore_error(exc, "Failed to update order on Firestore") from exc


def update_sales_order_fulfillment(
    *,
    order_id: str,
    fulfillment_status: str,
    actor_email: str,
    actor_role: str,
    cancel_reason: str | None = None,
    tracking_code: str | None = None,
    store_slug: str | None = None,
    owner_seller_email: str | None = None,
) -> SalesOrderRecord:
    safe_order_id, order_payload, _ = _get_order_document_by_id(order_id)
    current_order = _map_sales_order_record(order_payload)

    safe_store_slug = _safe_str(store_slug).lower()
    safe_owner_email = _safe_str(owner_seller_email).lower()
    if safe_store_slug or safe_owner_email:
        scoped = _scope_order_for_store(
            current_order,
            store_slug=safe_store_slug,
            owner_seller_email=safe_owner_email,
        )
        if scoped is None:
            raise OrderProcessingForbiddenError(
                "Este pedido nao pertence a loja/seller autenticado para processamento."
            )

    if (current_order.status or "").lower() not in PROCESSABLE_PAYMENT_STATUS:
        raise OrderProcessingValidationError(
            "Somente pedidos com pagamento aprovado podem ser processados nesta fila."
        )

    next_status = _normalize_fulfillment_status(fulfillment_status)
    if next_status not in FULFILLMENT_STATUS_SET:
        raise OrderProcessingValidationError("Status de processamento invalido.")

    safe_cancel_reason = _safe_str(cancel_reason)
    safe_tracking_code = _safe_str(tracking_code)

    if next_status == "cancelado" and len(safe_cancel_reason) < 3:
        raise OrderProcessingValidationError("Cancelamento exige motivo com pelo menos 3 caracteres.")

    if next_status == "enviado" and len(safe_tracking_code) < 4:
        raise OrderProcessingValidationError(
            "Status Enviado exige codigo de rastreio valido."
        )

    now_iso = datetime.now(UTC).isoformat()
    queue_entered_at = (
        _safe_str(order_payload.get("fulfillment_queue_entered_at"))
        or _safe_str(order_payload.get("date_approved"))
        or _safe_str(order_payload.get("updated_at"))
        or _safe_str(order_payload.get("created_at"))
        or now_iso
    )

    fulfillment_payload = (
        order_payload.get("fulfillment")
        if isinstance(order_payload.get("fulfillment"), dict)
        else {}
    )
    next_fulfillment_payload = dict(fulfillment_payload)
    next_fulfillment_payload.update(
        {
            "status": next_status,
            "updated_at": now_iso,
            "queue_entered_at": queue_entered_at,
            "updated_by": _safe_str(actor_email) or None,
            "updated_by_role": _safe_str(actor_role) or None,
        }
    )

    update_payload: dict[str, Any] = {
        "order_id": safe_order_id,
        "updated_at": now_iso,
        "fulfillment_status": next_status,
        "fulfillment_status_updated_at": now_iso,
        "fulfillment_queue_entered_at": queue_entered_at,
        "fulfillment_tracking_code": safe_tracking_code or None,
        "fulfillment_cancel_reason": safe_cancel_reason or None,
    }

    if safe_tracking_code:
        next_fulfillment_payload["tracking_code"] = safe_tracking_code
    elif next_status != "enviado":
        next_fulfillment_payload.pop("tracking_code", None)
        update_payload["fulfillment_tracking_code"] = None

    if safe_cancel_reason:
        next_fulfillment_payload["cancellation_reason"] = safe_cancel_reason
    elif next_status != "cancelado":
        next_fulfillment_payload.pop("cancellation_reason", None)
        update_payload["fulfillment_cancel_reason"] = None

    if next_status == "cancelado":
        payment_id = _safe_str(order_payload.get("payment_id"))
        if not payment_id:
            raise OrderProcessingValidationError(
                "Pedido sem payment_id para estorno automatico."
            )

        refund_payload: dict[str, Any]
        try:
            payment_info = fetch_payment(payment_id)
            payment_status = _safe_str(payment_info.get("status")).lower()
            if payment_status in {"refunded", "cancelled", "charged_back"}:
                refund_payload = {
                    "status": "already_refunded",
                    "updated_at": now_iso,
                    "payment_status": payment_status or "refunded",
                }
            else:
                refund_response = create_full_refund(payment_id)
                refund_payload = {
                    "status": "refunded",
                    "updated_at": now_iso,
                    "refund_id": _safe_str(refund_response.get("id")) or None,
                    "raw": refund_response,
                }
        except MercadoPagoAdminError as exc:
            raise OrderProcessingValidationError(
                f"Nao foi possivel estornar o pagamento: {exc.message}"
            ) from exc

        update_payload["status"] = "refunded"
        update_payload["status_detail"] = "cancelado_com_estorno"
        update_payload["refund_status"] = _safe_str(refund_payload.get("status")) or "refunded"
        update_payload["refund_id"] = _safe_str(refund_payload.get("refund_id")) or None
        update_payload["refund_updated_at"] = now_iso
        next_fulfillment_payload["refund"] = refund_payload

    update_payload["fulfillment"] = next_fulfillment_payload

    history_entry: dict[str, Any] = {
        "status": next_status,
        "updated_at": now_iso,
        "updated_by": _safe_str(actor_email) or None,
        "updated_by_role": _safe_str(actor_role) or None,
    }
    if safe_tracking_code:
        history_entry["tracking_code"] = safe_tracking_code
    if safe_cancel_reason:
        history_entry["cancel_reason"] = safe_cancel_reason

    current_history = order_payload.get("fulfillment_history")
    if isinstance(current_history, list):
        next_history = [entry for entry in current_history if isinstance(entry, dict)]
    else:
        next_history = []
    next_history.append(history_entry)
    update_payload["fulfillment_history"] = next_history[-50:]

    _persist_order_payload(order_id=safe_order_id, payload=update_payload)
    merged_payload = dict(order_payload)
    merged_payload.update(update_payload)
    return _map_sales_order_record(merged_payload)


def sales_metrics_last_days(
    days: int = 30,
    *,
    store_slug: str | None = None,
    owner_seller_email: str | None = None,
) -> SalesMetricsResponse:
    safe_days = max(1, min(days, 3650))
    threshold = datetime.now(UTC) - timedelta(days=safe_days)
    store_filter = _safe_str(store_slug).lower()
    seller_filter = _safe_str(owner_seller_email).lower()
    rows = _fetch_orders_payloads()
    mapped_orders = [_map_sales_order_record(row) for row in rows]
    if store_filter or seller_filter:
        scoped_orders: list[SalesOrderRecord] = []
        for order in mapped_orders:
            scoped = _scope_order_for_store(
                order,
                store_slug=store_filter,
                owner_seller_email=seller_filter,
            )
            if scoped is not None:
                scoped_orders.append(scoped)
        mapped_orders = scoped_orders
    scoped_orders = [
        item
        for item in mapped_orders
        if _timestamp_or_epoch(item.created_at) >= threshold
    ]

    approved_statuses = {"approved"}
    pending_statuses = {"pending", "in_process", "authorized", "in_mediation"}
    rejected_statuses = {"rejected", "cancelled", "cancelled_by_user", "charged_back", "refunded"}

    status_count: Counter[str] = Counter()
    status_revenue: Counter[str] = Counter()
    method_count: Counter[str] = Counter()
    method_revenue: Counter[str] = Counter()
    top_product_quantity: Counter[str] = Counter()
    top_product_revenue: Counter[str] = Counter()
    top_product_name: dict[str, str] = {}

    approved_revenue = 0.0
    total_revenue = 0.0

    for order in scoped_orders:
        status = (order.status or "unknown").lower()
        order_total = round(_safe_float(order.total_brl, 0.0), 2)
        total_revenue += order_total
        status_count[status] += 1

        method_key = (order.payment_method_id or order.payment_type_id or "desconhecido").lower()
        method_count[method_key] += 1

        if status in approved_statuses:
            approved_revenue += order_total
            status_revenue[status] += order_total
            method_revenue[method_key] += order_total

            for item in order.items:
                product_key = item.slug or item.lot_slug or item.name or "produto-sem-slug"
                quantity = max(0, _safe_int(item.quantity, 0))
                fallback_revenue = _safe_float(item.unit_price_brl, 0.0) * quantity
                revenue = round(
                    _safe_float(item.total_price_brl, fallback_revenue),
                    2,
                )
                top_product_quantity[product_key] += quantity
                top_product_revenue[product_key] += revenue
                if product_key not in top_product_name:
                    top_product_name[product_key] = item.name or product_key
        elif status in pending_statuses or status in rejected_statuses:
            status_revenue[status] += 0.0

    approved_orders = sum(status_count[item] for item in approved_statuses)
    pending_orders = sum(status_count[item] for item in pending_statuses)
    rejected_orders = sum(status_count[item] for item in rejected_statuses)
    average_ticket = round(approved_revenue / approved_orders, 2) if approved_orders > 0 else 0.0

    status_breakdown = [
        SalesStatusBreakdownItem(
            status=status,
            count=count,
            revenue_brl=round(status_revenue.get(status, 0.0), 2),
        )
        for status, count in sorted(status_count.items(), key=lambda item: item[1], reverse=True)
    ]

    payment_method_breakdown = [
        SalesPaymentMethodBreakdownItem(
            payment_method=method,
            count=count,
            revenue_brl=round(method_revenue.get(method, 0.0), 2),
        )
        for method, count in sorted(method_count.items(), key=lambda item: item[1], reverse=True)
    ]

    ranked_products = sorted(
        top_product_revenue.items(),
        key=lambda item: (item[1], top_product_quantity.get(item[0], 0)),
        reverse=True,
    )
    top_products = [
        SalesTopProductItem(
            slug=slug,
            name=top_product_name.get(slug, slug),
            quantity=max(0, top_product_quantity.get(slug, 0)),
            revenue_brl=round(revenue, 2),
        )
        for slug, revenue in ranked_products[:10]
    ]

    return SalesMetricsResponse(
        source="firestore",
        period_days=safe_days,
        total_orders=len(scoped_orders),
        approved_orders=approved_orders,
        pending_orders=pending_orders,
        rejected_orders=rejected_orders,
        approved_revenue_brl=round(approved_revenue, 2),
        total_revenue_brl=round(total_revenue, 2),
        average_ticket_brl=average_ticket,
        status_breakdown=status_breakdown,
        payment_method_breakdown=payment_method_breakdown,
        top_products=top_products,
    )


def _fetch_webhook_events_payloads() -> list[dict[str, Any]]:
    settings = get_settings()
    client = get_firestore_client()
    rows: list[dict[str, Any]] = []

    try:
        for index, doc in enumerate(
            client.collection(settings.firestore_collection_webhook_events).stream()
        ):
            if index >= MAX_WEBHOOK_DOCS:
                break
            payload = doc.to_dict() or {}
            payload.setdefault("event_id", doc.id)
            rows.append(payload)
    except Exception as exc:  # noqa: BLE001
        raise _map_firestore_error(exc, "Failed to fetch webhook events from Firestore") from exc

    return rows


def _map_webhook_event_record(payload: dict[str, Any]) -> WebhookEventRecord:
    return WebhookEventRecord.model_validate(
        {
            "event_id": _safe_str(payload.get("event_id")),
            "status": _safe_str(payload.get("status")).lower() or "unknown",
            "event_name": _safe_str(payload.get("event_name")) or None,
            "endpoint": _safe_str(payload.get("endpoint")) or None,
            "event_type": _safe_str(payload.get("event_type")) or None,
            "action": _safe_str(payload.get("action")) or None,
            "payment_id": _safe_str(payload.get("payment_id")) or None,
            "order_id": _safe_str(payload.get("order_id")) or None,
            "external_reference": _safe_str(payload.get("external_reference")) or None,
            "resource_id": _safe_str(payload.get("resource_id")) or None,
            "client_ip": _safe_str(payload.get("client_ip")) or None,
            "user_agent": _safe_str(payload.get("user_agent")) or None,
            "created_at": _safe_str(payload.get("created_at")) or None,
            "metadata": (
                payload.get("metadata")
                if isinstance(payload.get("metadata"), dict)
                else None
            ),
        }
    )


def list_webhook_events(
    *,
    page: int = 1,
    limit: int = 30,
    status: str | None = None,
    payment_id: str | None = None,
    order_id: str | None = None,
    search: str | None = None,
) -> WebhookEventListResponse:
    safe_page = max(1, page)
    safe_limit = max(1, min(limit, 200))
    status_filter = _safe_str(status).lower()
    payment_filter = _safe_str(payment_id).lower()
    order_filter = _safe_str(order_id).lower()
    search_filter = _safe_str(search).lower()

    rows = _fetch_webhook_events_payloads()
    events = [_map_webhook_event_record(row) for row in rows]

    if status_filter and status_filter != "all":
        events = [item for item in events if item.status.lower() == status_filter]

    if payment_filter:
        events = [item for item in events if (item.payment_id or "").lower() == payment_filter]

    if order_filter:
        events = [item for item in events if (item.order_id or "").lower() == order_filter]

    if search_filter:
        events = [
            item
            for item in events
            if search_filter
            in " ".join(
                [
                    item.event_id,
                    item.status or "",
                    item.action or "",
                    item.event_type or "",
                    item.payment_id or "",
                    item.order_id or "",
                    item.external_reference or "",
                    item.resource_id or "",
                ]
            ).lower()
        ]

    events.sort(key=lambda item: _timestamp_or_epoch(item.created_at), reverse=True)
    total_events = len(events)
    start = (safe_page - 1) * safe_limit
    end = start + safe_limit
    page_items = events[start:end]
    has_more = end < total_events

    return WebhookEventListResponse(
        source="firestore",
        page=safe_page,
        limit=safe_limit,
        total_events=total_events,
        has_more=has_more,
        items=page_items,
    )
