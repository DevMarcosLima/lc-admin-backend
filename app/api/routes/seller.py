from fastapi import APIRouter, Depends, HTTPException, Query

from app.schemas.store import (
    SalesMetricsResponse,
    SalesOrderListResponse,
    SalesOrderProcessUpdateRequest,
    SalesOrderRecord,
    SellerPayoutConfigResponse,
    SellerPayoutRuleConfig,
    SellerPublishProductRequest,
    SellerUpdateProductPriceRequest,
    SellerWithdrawProductRequest,
    StoreProduct,
    StoreProductListResponse,
)
from app.security.admin_auth import AdminSession, require_seller_session
from app.services.firestore_admin import (
    FirestoreConnectionError,
    FirestoreQuotaExceeded,
    OrderProcessingForbiddenError,
    OrderProcessingNotFoundError,
    OrderProcessingValidationError,
    list_products_by_seller,
    list_sales_orders,
    list_seller_templates,
    publish_seller_product_from_template,
    update_sales_order_fulfillment,
    sales_metrics_last_days,
    update_seller_product_price_from_template,
    withdraw_seller_product_stock_from_template,
)
from app.services.seller_accounts import (
    SellerAccountError,
    SellerAccountNotFoundError,
    get_seller_payout_config,
)

seller_session_dependency = Depends(require_seller_session)
router = APIRouter(
    prefix="/seller",
    tags=["seller"],
    dependencies=[seller_session_dependency],
)


def _raise_firestore_http_error(exc: FirestoreConnectionError) -> None:
    if isinstance(exc, FirestoreQuotaExceeded):
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    raise HTTPException(status_code=503, detail=str(exc)) from exc


def _map_seller_payout_response(
    *,
    seller_email: str,
    payout_config,
) -> SellerPayoutConfigResponse:
    return SellerPayoutConfigResponse(
        seller_email=seller_email,
        base_fee_brl=payout_config.base_fee_brl,
        rules=[
            SellerPayoutRuleConfig(
                template_slug=rule.template_slug,
                template_name=rule.template_name,
                commission_mode=rule.commission_mode,
                commission_percent=rule.commission_percent,
                commission_fixed_brl=rule.commission_fixed_brl,
                active=rule.active,
            )
            for rule in payout_config.rules
        ],
        updated_at=payout_config.updated_at,
    )


@router.get("/templates", response_model=StoreProductListResponse)
def get_seller_templates(
    session: AdminSession = seller_session_dependency,
) -> StoreProductListResponse:
    _ = session
    try:
        return StoreProductListResponse(items=list_seller_templates())
    except FirestoreConnectionError as exc:
        _raise_firestore_http_error(exc)


@router.post("/products/publish", response_model=StoreProduct)
def post_seller_publish_product(
    payload: SellerPublishProductRequest,
    session: AdminSession = seller_session_dependency,
) -> StoreProduct:
    try:
        return publish_seller_product_from_template(
            seller_email=session.email,
            template_slug=payload.template_slug,
            quantity=payload.quantity,
            use_template_image=payload.use_template_image,
            custom_image_url=payload.custom_image_url,
            price_brl=payload.price_brl,
        )
    except FirestoreConnectionError as exc:
        _raise_firestore_http_error(exc)


@router.post("/products/price", response_model=StoreProduct)
def post_seller_update_product_price(
    payload: SellerUpdateProductPriceRequest,
    session: AdminSession = seller_session_dependency,
) -> StoreProduct:
    try:
        return update_seller_product_price_from_template(
            seller_email=session.email,
            template_slug=payload.template_slug,
            price_brl=payload.price_brl,
        )
    except FirestoreConnectionError as exc:
        _raise_firestore_http_error(exc)


@router.post("/products/withdraw", response_model=StoreProduct)
def post_seller_withdraw_product(
    payload: SellerWithdrawProductRequest,
    session: AdminSession = seller_session_dependency,
) -> StoreProduct:
    try:
        return withdraw_seller_product_stock_from_template(
            seller_email=session.email,
            template_slug=payload.template_slug,
            quantity=payload.quantity,
        )
    except FirestoreConnectionError as exc:
        _raise_firestore_http_error(exc)


@router.get("/products", response_model=StoreProductListResponse)
def get_seller_products(
    session: AdminSession = seller_session_dependency,
) -> StoreProductListResponse:
    try:
        return StoreProductListResponse(items=list_products_by_seller(seller_email=session.email))
    except FirestoreConnectionError as exc:
        _raise_firestore_http_error(exc)


@router.get("/payout-config", response_model=SellerPayoutConfigResponse)
def get_seller_payout(
    session: AdminSession = seller_session_dependency,
) -> SellerPayoutConfigResponse:
    try:
        payout_config = get_seller_payout_config(session.email)
    except SellerAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SellerAccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _map_seller_payout_response(
        seller_email=session.email.strip().lower(),
        payout_config=payout_config,
    )


@router.get("/sales/orders", response_model=SalesOrderListResponse)
def get_seller_sales_orders(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(default=None),
    query: str | None = Query(default=None),
    session: AdminSession = seller_session_dependency,
) -> SalesOrderListResponse:
    try:
        return list_sales_orders(
            page=page,
            limit=limit,
            status=status,
            query=query,
            store_slug=session.shop_slug,
            owner_seller_email=session.email,
        )
    except FirestoreConnectionError as exc:
        _raise_firestore_http_error(exc)


@router.patch("/sales/orders/{order_id}/process", response_model=SalesOrderRecord)
def patch_seller_sales_order_process(
    order_id: str,
    payload: SalesOrderProcessUpdateRequest,
    session: AdminSession = seller_session_dependency,
) -> SalesOrderRecord:
    try:
        return update_sales_order_fulfillment(
            order_id=order_id,
            fulfillment_status=payload.fulfillment_status,
            cancel_reason=payload.cancel_reason,
            tracking_code=payload.tracking_code,
            actor_email=session.email,
            actor_role=session.role,
            store_slug=session.shop_slug,
            owner_seller_email=session.email,
        )
    except OrderProcessingNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except OrderProcessingForbiddenError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except OrderProcessingValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FirestoreConnectionError as exc:
        _raise_firestore_http_error(exc)


@router.get("/sales/metrics", response_model=SalesMetricsResponse)
def get_seller_sales_metrics(
    days: int = Query(default=30, ge=1, le=3650),
    session: AdminSession = seller_session_dependency,
) -> SalesMetricsResponse:
    try:
        return sales_metrics_last_days(
            days=days,
            store_slug=session.shop_slug,
            owner_seller_email=session.email,
        )
    except FirestoreConnectionError as exc:
        _raise_firestore_http_error(exc)
