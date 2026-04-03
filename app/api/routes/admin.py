from fastapi import APIRouter, HTTPException, Query

from app.schemas.store import (
    AnalyticsSummaryItem,
    AnalyticsSummaryResponse,
    CardLookupResponse,
    CardMetadataOptionsResponse,
    StoreDeleteResponse,
    StoreProduct,
    StoreProductListResponse,
)

# from app.security.admin_auth import require_admin_token
from app.services.card_catalog import CardCatalogError, fetch_card_metadata_options, search_cards
from app.services.firestore_admin import (
    FirestoreConnectionError,
    FirestoreQuotaExceeded,
    analytics_summary_last_days,
    delete_product,
    fetch_products_from_firestore,
    upsert_product,
)

# TEST MODE (TEMPORARIO): auth desabilitada para facilitar testes locais.
# router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin_token)])
router = APIRouter(prefix="/admin", tags=["admin"])


def _raise_firestore_http_error(exc: FirestoreConnectionError) -> None:
    if isinstance(exc, FirestoreQuotaExceeded):
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/products", response_model=StoreProductListResponse)
def get_admin_products() -> StoreProductListResponse:
    try:
        items = sorted(fetch_products_from_firestore(), key=lambda item: item.slug)
        return StoreProductListResponse(items=items)
    except FirestoreConnectionError as exc:
        _raise_firestore_http_error(exc)


@router.post("/products", response_model=StoreProduct)
def post_admin_product(payload: StoreProduct) -> StoreProduct:
    try:
        return upsert_product(payload)
    except FirestoreConnectionError as exc:
        _raise_firestore_http_error(exc)


@router.put("/products/{slug}", response_model=StoreProduct)
def put_admin_product(slug: str, payload: StoreProduct) -> StoreProduct:
    try:
        return upsert_product(payload.model_copy(update={"slug": slug}))
    except FirestoreConnectionError as exc:
        _raise_firestore_http_error(exc)


@router.delete("/products/{slug}", response_model=StoreDeleteResponse)
def delete_admin_product(slug: str) -> StoreDeleteResponse:
    try:
        deleted = delete_product(slug)
    except FirestoreConnectionError as exc:
        _raise_firestore_http_error(exc)

    if not deleted:
        raise HTTPException(status_code=404, detail="Product not found")

    return StoreDeleteResponse(slug=slug, deleted=True)


@router.get("/cards/options", response_model=CardMetadataOptionsResponse)
def get_card_options() -> CardMetadataOptionsResponse:
    try:
        return fetch_card_metadata_options()
    except CardCatalogError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/cards/lookup", response_model=CardLookupResponse)
def lookup_cards(
    query: str = Query(min_length=1, description="Nome da carta ou numero no formato 031/094"),
    limit: int = Query(default=12, ge=1, le=50),
) -> CardLookupResponse:
    try:
        items = search_cards(query=query, limit=limit)
    except CardCatalogError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return CardLookupResponse(source="pokemontcg.io", query=query, items=items)


@router.get("/analytics/summary", response_model=AnalyticsSummaryResponse)
def get_admin_analytics(days: int = Query(default=30, ge=1, le=365)) -> AnalyticsSummaryResponse:
    try:
        summary = analytics_summary_last_days(days=days)
    except FirestoreConnectionError as exc:
        _raise_firestore_http_error(exc)

    return AnalyticsSummaryResponse(
        source="firestore",
        period_days=days,
        items=[AnalyticsSummaryItem(endpoint=endpoint, count=count) for endpoint, count in summary],
    )
