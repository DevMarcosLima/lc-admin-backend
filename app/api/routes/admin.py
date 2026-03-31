from fastapi import APIRouter, Depends, HTTPException, Query

from app.schemas.store import (
    AnalyticsSummaryItem,
    AnalyticsSummaryResponse,
    StoreDeleteResponse,
    StoreProduct,
    StoreProductListResponse,
)
from app.security.admin_auth import require_admin_token
from app.services.firestore_admin import (
    FirestoreConnectionError,
    analytics_summary_last_days,
    delete_product,
    fetch_products_from_firestore,
    upsert_product,
)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin_token)])


@router.get("/products", response_model=StoreProductListResponse)
def get_admin_products() -> StoreProductListResponse:
    try:
        items = sorted(fetch_products_from_firestore(), key=lambda item: item.slug)
        return StoreProductListResponse(items=items)
    except FirestoreConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/products", response_model=StoreProduct)
def post_admin_product(payload: StoreProduct) -> StoreProduct:
    try:
        return upsert_product(payload)
    except FirestoreConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.put("/products/{slug}", response_model=StoreProduct)
def put_admin_product(slug: str, payload: StoreProduct) -> StoreProduct:
    try:
        return upsert_product(payload.model_copy(update={"slug": slug}))
    except FirestoreConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/products/{slug}", response_model=StoreDeleteResponse)
def delete_admin_product(slug: str) -> StoreDeleteResponse:
    try:
        deleted = delete_product(slug)
    except FirestoreConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if not deleted:
        raise HTTPException(status_code=404, detail="Product not found")

    return StoreDeleteResponse(slug=slug, deleted=True)


@router.get("/analytics/summary", response_model=AnalyticsSummaryResponse)
def get_admin_analytics(days: int = Query(default=30, ge=1, le=365)) -> AnalyticsSummaryResponse:
    try:
        summary = analytics_summary_last_days(days=days)
    except FirestoreConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return AnalyticsSummaryResponse(
        source="firestore",
        period_days=days,
        items=[AnalyticsSummaryItem(endpoint=endpoint, count=count) for endpoint, count in summary],
    )
