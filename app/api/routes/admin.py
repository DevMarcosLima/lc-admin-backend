from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from app.schemas.store import (
    AdminBrandingConfigResponse,
    AdminBrandingConfigUpdateRequest,
    AdminCategoryConfigResponse,
    AdminCategoryConfigUpdateRequest,
    AdminImageUploadResponse,
    AdminMenuConfigResponse,
    AdminMenuConfigUpdateRequest,
    AnalyticsSummaryItem,
    AnalyticsSummaryResponse,
    CardLookupResponse,
    CardMetadataOptionsResponse,
    CatalogAssistantResponse,
    CatalogAssistantRunRequest,
    LotImportJobResponse,
    LotImportStartRequest,
    LotImportStartResponse,
    SalesMetricsResponse,
    SalesOrderListResponse,
    SalesOrderProcessUpdateRequest,
    SalesOrderRecord,
    SellerAccountListResponse,
    SellerAccountSummary,
    SellerCreateRequest,
    SellerCreateResponse,
    SellerStatusUpdateRequest,
    SellerStatusUpdateResponse,
    SellerPayoutConfigResponse,
    SellerPayoutConfigUpdateRequest,
    SellerPayoutRuleConfig,
    StoreDeleteResponse,
    StoreProduct,
    StoreProductListResponse,
    WebhookEventListResponse,
)
from app.security.admin_auth import AdminSession, require_admin_session
from app.services.card_catalog import CardCatalogError, fetch_card_metadata_options, search_cards
from app.services.catalog_assistant import CatalogAssistantError, run_catalog_assistant
from app.services.firestore_admin import (
    FirestoreConnectionError,
    FirestoreQuotaExceeded,
    OrderProcessingForbiddenError,
    OrderProcessingNotFoundError,
    OrderProcessingValidationError,
    analytics_summary_last_days_with_source,
    delete_product,
    fetch_products_from_firestore,
    get_panel_branding_config,
    get_panel_categories_config,
    get_panel_menu_config,
    list_sales_orders,
    list_seller_templates,
    set_seller_inventory_mode,
    list_webhook_events,
    sales_metrics_last_days,
    update_sales_order_fulfillment,
    upsert_panel_categories_config,
    upsert_panel_branding_config,
    upsert_panel_menu_config,
    upsert_product,
)
from app.services.lot_import import (
    LotImportError,
    LotImportNotFound,
    get_lot_import,
    start_lot_import,
)
from app.services.media_storage import (
    AssetStorageConnectionError,
    AssetStorageValidationError,
    upload_image_bytes,
)
from app.services.seller_accounts import (
    SellerAccountConflictError,
    SellerAccountError,
    SellerAccountNotFoundError,
    create_seller_account,
    get_seller_payout_config,
    list_seller_accounts,
    save_seller_payout_config,
    update_seller_status,
)

admin_session_dependency = Depends(require_admin_session)
router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[admin_session_dependency])
admin_image_upload_file = File(...)
admin_image_upload_scope = Form(...)
admin_image_upload_slot = Form("primary")
admin_image_upload_slug = Form(default=None)


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


@router.get("/products", response_model=StoreProductListResponse)
def get_admin_products() -> StoreProductListResponse:
    try:
        items = sorted(fetch_products_from_firestore(), key=lambda item: item.slug)
        return StoreProductListResponse(items=items)
    except FirestoreConnectionError as exc:
        _raise_firestore_http_error(exc)


@router.get("/products/templates", response_model=StoreProductListResponse)
def get_admin_product_templates(
    store_slug: str | None = Query(default=None),
) -> StoreProductListResponse:
    try:
        return StoreProductListResponse(items=list_seller_templates(store_slug=store_slug))
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


@router.post("/uploads/image", response_model=AdminImageUploadResponse)
async def post_admin_upload_image(
    file: UploadFile = admin_image_upload_file,
    scope: Literal["cards", "products", "branding"] = admin_image_upload_scope,
    slot: Literal[
        "primary",
        "gallery",
        "hero_logo_primary",
        "hero_logo_secondary",
        "hero_slide",
    ] = admin_image_upload_slot,
    slug: str | None = admin_image_upload_slug,
) -> AdminImageUploadResponse:
    filename = (file.filename or "imagem").strip() or "imagem"
    payload = await file.read()
    await file.close()

    try:
        uploaded = upload_image_bytes(
            payload=payload,
            source_filename=filename,
            content_type=file.content_type,
            scope=scope,
            slot=slot,
            slug=slug,
        )
    except AssetStorageValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AssetStorageConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return AdminImageUploadResponse(
        url=uploaded.url,
        bucket=uploaded.bucket,
        object_name=uploaded.object_name,
        scope=uploaded.scope,
        slot=uploaded.slot,
        filename=uploaded.filename,
        content_type=uploaded.content_type,
        size_bytes=uploaded.size_bytes,
    )


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


@router.post("/lots/import/start", response_model=LotImportStartResponse)
def post_start_lot_import(payload: LotImportStartRequest) -> LotImportStartResponse:
    try:
        return start_lot_import(payload)
    except LotImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/lots/import/{job_id}", response_model=LotImportJobResponse)
def get_lot_import_status(job_id: str) -> LotImportJobResponse:
    try:
        return get_lot_import(job_id)
    except LotImportNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/analytics/summary", response_model=AnalyticsSummaryResponse)
def get_admin_analytics(days: int = Query(default=30, ge=1, le=365)) -> AnalyticsSummaryResponse:
    try:
        source, summary = analytics_summary_last_days_with_source(days=days)
    except FirestoreConnectionError as exc:
        _raise_firestore_http_error(exc)

    return AnalyticsSummaryResponse(
        source=source,
        period_days=days,
        items=[AnalyticsSummaryItem(endpoint=endpoint, count=count) for endpoint, count in summary],
    )


@router.get("/sales/orders", response_model=SalesOrderListResponse)
def get_admin_sales_orders(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(default=None),
    query: str | None = Query(default=None),
    store_slug: str | None = Query(default=None),
    owner_seller_email: str | None = Query(default=None),
) -> SalesOrderListResponse:
    try:
        return list_sales_orders(
            page=page,
            limit=limit,
            status=status,
            query=query,
            store_slug=store_slug,
            owner_seller_email=owner_seller_email,
        )
    except FirestoreConnectionError as exc:
        _raise_firestore_http_error(exc)


@router.patch("/sales/orders/{order_id}/process", response_model=SalesOrderRecord)
def patch_admin_sales_order_process(
    order_id: str,
    payload: SalesOrderProcessUpdateRequest,
    session: AdminSession = admin_session_dependency,
) -> SalesOrderRecord:
    try:
        return update_sales_order_fulfillment(
            order_id=order_id,
            fulfillment_status=payload.fulfillment_status,
            cancel_reason=payload.cancel_reason,
            tracking_code=payload.tracking_code,
            actor_email=session.email,
            actor_role=session.role,
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
def get_admin_sales_metrics(
    days: int = Query(default=30, ge=1, le=3650),
    store_slug: str | None = Query(default=None),
    owner_seller_email: str | None = Query(default=None),
) -> SalesMetricsResponse:
    try:
        return sales_metrics_last_days(
            days=days,
            store_slug=store_slug,
            owner_seller_email=owner_seller_email,
        )
    except FirestoreConnectionError as exc:
        _raise_firestore_http_error(exc)


@router.get("/webhooks/events", response_model=WebhookEventListResponse)
def get_admin_webhook_events(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=30, ge=1, le=200),
    status: str | None = Query(default=None),
    payment_id: str | None = Query(default=None),
    order_id: str | None = Query(default=None),
    search: str | None = Query(default=None),
) -> WebhookEventListResponse:
    try:
        return list_webhook_events(
            page=page,
            limit=limit,
            status=status,
            payment_id=payment_id,
            order_id=order_id,
            search=search,
        )
    except FirestoreConnectionError as exc:
        _raise_firestore_http_error(exc)


@router.post("/catalog/assistant/run", response_model=CatalogAssistantResponse)
def post_catalog_assistant_run(payload: CatalogAssistantRunRequest) -> CatalogAssistantResponse:
    try:
        return run_catalog_assistant(payload)
    except CatalogAssistantError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CardCatalogError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except FirestoreConnectionError as exc:
        _raise_firestore_http_error(exc)


@router.get("/sellers", response_model=SellerAccountListResponse)
def get_admin_sellers() -> SellerAccountListResponse:
    try:
        accounts = list_seller_accounts()
    except SellerAccountError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return SellerAccountListResponse(
        items=[
            SellerAccountSummary(
                email=item.email,
                shop_name=item.shop_name,
                shop_slug=item.shop_slug,
                status=item.status,
                must_change_password=item.must_change_password,
                two_factor_enabled=item.two_factor_enabled,
                payout_base_fee_brl=item.payout_config.base_fee_brl,
                payout_rules_count=len(item.payout_config.rules),
                created_at=item.created_at,
                updated_at=item.updated_at,
                created_by=item.created_by,
            )
            for item in accounts
        ]
    )


@router.post("/sellers", response_model=SellerCreateResponse, status_code=201)
def post_admin_seller(
    payload: SellerCreateRequest,
    session=admin_session_dependency,
) -> SellerCreateResponse:
    try:
        account, temporary_password = create_seller_account(
            email=payload.email,
            shop_name=payload.shop_name,
            created_by=session.email,
        )
    except SellerAccountConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SellerAccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return SellerCreateResponse(
        account=SellerAccountSummary(
            email=account.email,
            shop_name=account.shop_name,
            shop_slug=account.shop_slug,
            status=account.status,
            must_change_password=account.must_change_password,
            two_factor_enabled=account.two_factor_enabled,
            payout_base_fee_brl=account.payout_config.base_fee_brl,
            payout_rules_count=len(account.payout_config.rules),
            created_at=account.created_at,
            updated_at=account.updated_at,
            created_by=account.created_by,
        ),
        temporary_password=temporary_password,
    )


@router.patch("/sellers/{seller_email}/status", response_model=SellerStatusUpdateResponse)
def patch_admin_seller_status(
    seller_email: str,
    payload: SellerStatusUpdateRequest,
    session: AdminSession = admin_session_dependency,
) -> SellerStatusUpdateResponse:
    try:
        account = update_seller_status(
            email=seller_email,
            status=payload.status,
            updated_by=session.email,
            note=payload.note,
        )
    except SellerAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SellerAccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    inventory_standby = payload.status == "inactive" and payload.set_inventory_standby
    products_affected = 0
    stock_removed = 0
    if payload.set_inventory_standby:
        try:
            products_affected, stock_removed = set_seller_inventory_mode(
                seller_email=account.email,
                standby=inventory_standby,
                zero_stock=payload.zero_inventory and inventory_standby,
            )
        except FirestoreConnectionError as exc:
            _raise_firestore_http_error(exc)

    return SellerStatusUpdateResponse(
        account=SellerAccountSummary(
            email=account.email,
            shop_name=account.shop_name,
            shop_slug=account.shop_slug,
            status=account.status,
            must_change_password=account.must_change_password,
            two_factor_enabled=account.two_factor_enabled,
            payout_base_fee_brl=account.payout_config.base_fee_brl,
            payout_rules_count=len(account.payout_config.rules),
            created_at=account.created_at,
            updated_at=account.updated_at,
            created_by=account.created_by,
        ),
        inventory_standby=inventory_standby,
        seller_products_affected=products_affected,
        seller_stock_removed=stock_removed,
    )


@router.get("/sellers/{seller_email}/payout-config", response_model=SellerPayoutConfigResponse)
def get_admin_seller_payout_config(seller_email: str) -> SellerPayoutConfigResponse:
    try:
        payout_config = get_seller_payout_config(seller_email)
    except SellerAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SellerAccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _map_seller_payout_response(
        seller_email=seller_email.strip().lower(),
        payout_config=payout_config,
    )


@router.put("/sellers/{seller_email}/payout-config", response_model=SellerPayoutConfigResponse)
def put_admin_seller_payout_config(
    seller_email: str,
    payload: SellerPayoutConfigUpdateRequest,
) -> SellerPayoutConfigResponse:
    try:
        payout_config = save_seller_payout_config(
            email=seller_email,
            base_fee_brl=payload.base_fee_brl,
            rules=[item.model_dump() for item in payload.rules],
        )
    except SellerAccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SellerAccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _map_seller_payout_response(
        seller_email=seller_email.strip().lower(),
        payout_config=payout_config,
    )


@router.get("/settings/menu", response_model=AdminMenuConfigResponse)
def get_admin_settings_menu() -> AdminMenuConfigResponse:
    try:
        return AdminMenuConfigResponse(items=get_panel_menu_config())
    except FirestoreConnectionError as exc:
        _raise_firestore_http_error(exc)


@router.put("/settings/menu", response_model=AdminMenuConfigResponse)
def put_admin_settings_menu(payload: AdminMenuConfigUpdateRequest) -> AdminMenuConfigResponse:
    try:
        return AdminMenuConfigResponse(
            items=upsert_panel_menu_config([item.model_dump() for item in payload.items])
        )
    except FirestoreConnectionError as exc:
        _raise_firestore_http_error(exc)


@router.get("/settings/categories", response_model=AdminCategoryConfigResponse)
def get_admin_settings_categories() -> AdminCategoryConfigResponse:
    try:
        return AdminCategoryConfigResponse(items=get_panel_categories_config())
    except FirestoreConnectionError as exc:
        _raise_firestore_http_error(exc)


@router.put("/settings/categories", response_model=AdminCategoryConfigResponse)
def put_admin_settings_categories(
    payload: AdminCategoryConfigUpdateRequest,
) -> AdminCategoryConfigResponse:
    try:
        return AdminCategoryConfigResponse(items=upsert_panel_categories_config(payload.items))
    except FirestoreConnectionError as exc:
        _raise_firestore_http_error(exc)


@router.get("/settings/branding", response_model=AdminBrandingConfigResponse)
def get_admin_settings_branding() -> AdminBrandingConfigResponse:
    try:
        payload = get_panel_branding_config()
        return AdminBrandingConfigResponse(**payload)
    except FirestoreConnectionError as exc:
        _raise_firestore_http_error(exc)


@router.put("/settings/branding", response_model=AdminBrandingConfigResponse)
def put_admin_settings_branding(
    payload: AdminBrandingConfigUpdateRequest,
) -> AdminBrandingConfigResponse:
    try:
        saved_payload = upsert_panel_branding_config(payload.model_dump())
        return AdminBrandingConfigResponse(**saved_payload)
    except FirestoreConnectionError as exc:
        _raise_firestore_http_error(exc)
