from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class StoreProduct(BaseModel):
    slug: str
    name: str
    product_type: str
    store_name: str = "Legacy Cards"
    store_slug: str = "legacy-cards"
    owner_type: Literal["admin", "seller"] = "admin"
    owner_seller_email: str | None = None
    source_template_slug: str | None = None
    seller_template_enabled: bool = True
    allow_seller_custom_image: bool = True
    lot_id: str | None = None
    set_name: str | None = None
    set_series: str | None = None
    rarity: str | None = None
    finish: str | None = None
    condition: str | None = None
    card_number: str | None = None
    regulation_mark: str | None = None
    set_code: str | None = None
    language: str | None = None
    release_year: int | None = Field(default=None, ge=1996, le=2100)
    pokemon_generation: str | None = None
    pokemon_types: list[str] = Field(default_factory=list)
    description: str | None = None
    observations: str | None = None
    category: str
    season_tags: list[str] = Field(default_factory=list)
    accessory_kind: str | None = None
    booster_pack_count: int | None = Field(default=None, ge=0)
    shipping_profile: str | None = None
    shipping_weight_grams: int | None = Field(default=None, ge=1, le=30000)
    shipping_length_cm: int | None = Field(default=None, ge=1, le=200)
    shipping_width_cm: int | None = Field(default=None, ge=1, le=200)
    shipping_height_cm: int | None = Field(default=None, ge=1, le=200)
    stock: int
    price_brl: float
    image_url: str
    image_gallery: list[str] = Field(default_factory=list)
    is_special: bool = False


class StoreProductListResponse(BaseModel):
    items: list[StoreProduct]


class SellerCreateRequest(BaseModel):
    email: str = Field(min_length=5, max_length=320)
    shop_name: str = Field(min_length=2, max_length=120)


class SellerAccountSummary(BaseModel):
    email: str
    shop_name: str
    shop_slug: str
    status: str
    must_change_password: bool
    two_factor_enabled: bool
    payout_base_fee_brl: float = 6.0
    payout_rules_count: int = 0
    created_at: str | None = None
    updated_at: str | None = None
    created_by: str | None = None


class SellerPayoutRuleConfig(BaseModel):
    template_slug: str = Field(min_length=1, max_length=180)
    template_name: str | None = Field(default=None, max_length=180)
    commission_mode: Literal["percent", "fixed"] = "percent"
    commission_percent: float | None = Field(default=None, ge=0, le=100)
    commission_fixed_brl: float | None = Field(default=None, ge=0)
    active: bool = True

    @model_validator(mode="after")
    def validate_mode_payload(self) -> "SellerPayoutRuleConfig":
        if self.commission_mode == "percent":
            if self.commission_percent is None:
                self.commission_percent = 0.0
            self.commission_fixed_brl = None
        else:
            if self.commission_fixed_brl is None:
                self.commission_fixed_brl = 0.0
            self.commission_percent = None
        return self


class SellerPayoutConfigResponse(BaseModel):
    seller_email: str
    base_fee_brl: float = Field(default=6.0, ge=0)
    rules: list[SellerPayoutRuleConfig] = Field(default_factory=list)
    updated_at: str | None = None


class SellerPayoutConfigUpdateRequest(BaseModel):
    base_fee_brl: float = Field(default=6.0, ge=0)
    rules: list[SellerPayoutRuleConfig] = Field(default_factory=list)


class SellerCreateResponse(BaseModel):
    account: SellerAccountSummary
    temporary_password: str


class SellerAccountListResponse(BaseModel):
    items: list[SellerAccountSummary]


class SellerStatusUpdateRequest(BaseModel):
    status: Literal["active", "inactive"] = "inactive"
    set_inventory_standby: bool = True
    zero_inventory: bool = True
    note: str | None = Field(default=None, max_length=240)


class SellerStatusUpdateResponse(BaseModel):
    account: SellerAccountSummary
    inventory_standby: bool
    seller_products_affected: int = 0
    seller_stock_removed: int = 0


class SellerPublishProductRequest(BaseModel):
    template_slug: str = Field(min_length=1)
    quantity: int = Field(ge=1, le=100000)
    use_template_image: bool = True
    custom_image_url: str | None = None
    price_brl: float | None = Field(default=None, gt=0)


class SellerWithdrawProductRequest(BaseModel):
    template_slug: str = Field(min_length=1)
    quantity: int = Field(ge=1, le=100000)


class SellerUpdateProductPriceRequest(BaseModel):
    template_slug: str = Field(min_length=1)
    price_brl: float = Field(gt=0)


class AdminMenuChildConfig(BaseModel):
    id: str
    label: str
    tab: str
    subtab: str | None = None
    enabled: bool = True


class AdminMenuItemConfig(BaseModel):
    id: str
    label: str
    tab: str
    subtab: str | None = None
    enabled: bool = True
    children: list[AdminMenuChildConfig] = Field(default_factory=list)


class AdminMenuConfigResponse(BaseModel):
    items: list[AdminMenuItemConfig]


class AdminMenuConfigUpdateRequest(BaseModel):
    items: list[AdminMenuItemConfig]


class AdminCategoryConfigResponse(BaseModel):
    items: list[str]


class AdminCategoryConfigUpdateRequest(BaseModel):
    items: list[str]


class AdminBrandingSlideTarget(BaseModel):
    slide_index: int = Field(ge=1, le=12)
    product_slug: str = Field(min_length=1, max_length=180)
    product_name: str | None = Field(default=None, max_length=180)


class AdminBrandingSlideAsset(BaseModel):
    slide_index: int = Field(ge=1, le=12)
    image_url: str | None = Field(default=None, max_length=2000)
    focus_x_percent: int = Field(default=52, ge=0, le=100)
    name: str | None = Field(default=None, max_length=180)
    category: str | None = Field(default=None, max_length=120)
    product_type: str | None = Field(default=None, max_length=120)
    price_brl: float | None = Field(default=None, ge=0, le=1_000_000)


class AdminBrandingConfigResponse(BaseModel):
    hero_logo_primary_url: str = "/logo.webp"
    hero_logo_secondary_url: str = "/logo.webp"
    hero_logo_primary_width: int = Field(default=140, ge=40, le=460)
    hero_logo_secondary_width: int = Field(default=140, ge=40, le=460)
    hero_slide_targets: list[AdminBrandingSlideTarget] = Field(default_factory=list)
    hero_slides: list[AdminBrandingSlideAsset] = Field(default_factory=list)
    updated_at: str | None = None


class AdminBrandingConfigUpdateRequest(BaseModel):
    hero_logo_primary_url: str = "/logo.webp"
    hero_logo_secondary_url: str = "/logo.webp"
    hero_logo_primary_width: int = Field(default=140, ge=40, le=460)
    hero_logo_secondary_width: int = Field(default=140, ge=40, le=460)
    hero_slide_targets: list[AdminBrandingSlideTarget] = Field(default_factory=list)
    hero_slides: list[AdminBrandingSlideAsset] = Field(default_factory=list)


class StoreDeleteResponse(BaseModel):
    slug: str
    deleted: bool


class AdminImageUploadResponse(BaseModel):
    url: str
    bucket: str
    object_name: str
    scope: Literal["cards", "products", "branding"]
    slot: Literal["primary", "gallery", "hero_logo_primary", "hero_logo_secondary", "hero_slide"]
    filename: str
    content_type: str
    size_bytes: int


class AnalyticsSummaryItem(BaseModel):
    endpoint: str
    count: int


class AnalyticsSummaryResponse(BaseModel):
    source: str
    period_days: int
    items: list[AnalyticsSummaryItem]


class SalesOrderItem(BaseModel):
    slug: str | None = None
    lot_slug: str | None = None
    lot_id: str | None = None
    name: str | None = None
    product_type: str | None = None
    store_name: str | None = None
    store_slug: str | None = None
    owner_type: str | None = None
    owner_seller_email: str | None = None
    quantity: int = 0
    unit_price_brl: float = 0.0
    total_price_brl: float = 0.0


class SalesOrderRecord(BaseModel):
    order_id: str
    external_reference: str | None = None
    payment_id: str | None = None
    uid: str | None = None
    user_email: str | None = None
    status: str
    status_detail: str | None = None
    payment_type_id: str | None = None
    payment_method_id: str | None = None
    subtotal_brl: float = 0.0
    shipping_brl: float = 0.0
    discount_brl: float = 0.0
    total_brl: float = 0.0
    total_items: int = 0
    coupon_code: str | None = None
    shipping_id: str | None = None
    shipping_zip_code: str | None = None
    shipping_provider: str | None = None
    shipping_carrier: str | None = None
    shipping_service_name: str | None = None
    shipping_service_code: str | None = None
    shipping_eta_label: str | None = None
    shipping_eta_days_min: int | None = None
    shipping_eta_days_max: int | None = None
    shipping_margin_percent: float = 0.0
    shipping_margin_brl: float = 0.0
    shipping_base_brl: float = 0.0
    shipping_cashback_credit_brl: float = 0.0
    shipping_packages_count: int = 0
    shipping_origin_cep: str | None = None
    shipping_destination_cep: str | None = None
    shipping_snapshot: dict[str, Any] | None = None
    source: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    date_approved: str | None = None
    date_last_updated: str | None = None
    inventory_sync_status: str | None = None
    webhook_last_received_at: str | None = None
    webhook_last_action: str | None = None
    fulfillment_status: str | None = None
    fulfillment_status_updated_at: str | None = None
    fulfillment_queue_entered_at: str | None = None
    fulfillment_cancel_reason: str | None = None
    fulfillment_tracking_code: str | None = None
    refund_status: str | None = None
    refund_id: str | None = None
    refund_updated_at: str | None = None
    items: list[SalesOrderItem] = Field(default_factory=list)


class SalesOrderListResponse(BaseModel):
    source: str
    page: int
    limit: int
    total_orders: int
    has_more: bool
    items: list[SalesOrderRecord]


class SalesOrderProcessUpdateRequest(BaseModel):
    fulfillment_status: Literal[
        "em_separacao",
        "em_preparacao",
        "separado",
        "rota_transportadora",
        "enviado",
        "cancelado",
    ]
    cancel_reason: str | None = Field(default=None, max_length=1200)
    tracking_code: str | None = Field(default=None, max_length=180)


class SalesStatusBreakdownItem(BaseModel):
    status: str
    count: int
    revenue_brl: float = 0.0


class SalesPaymentMethodBreakdownItem(BaseModel):
    payment_method: str
    count: int
    revenue_brl: float = 0.0


class SalesTopProductItem(BaseModel):
    slug: str
    name: str
    quantity: int
    revenue_brl: float


class SalesMetricsResponse(BaseModel):
    source: str
    period_days: int
    total_orders: int
    approved_orders: int
    pending_orders: int
    rejected_orders: int
    approved_revenue_brl: float
    total_revenue_brl: float
    average_ticket_brl: float
    status_breakdown: list[SalesStatusBreakdownItem] = Field(default_factory=list)
    payment_method_breakdown: list[SalesPaymentMethodBreakdownItem] = Field(default_factory=list)
    top_products: list[SalesTopProductItem] = Field(default_factory=list)


class WebhookEventRecord(BaseModel):
    event_id: str
    status: str
    event_name: str | None = None
    endpoint: str | None = None
    event_type: str | None = None
    action: str | None = None
    payment_id: str | None = None
    order_id: str | None = None
    external_reference: str | None = None
    resource_id: str | None = None
    client_ip: str | None = None
    user_agent: str | None = None
    created_at: str | None = None
    metadata: dict[str, Any] | None = None


class WebhookEventListResponse(BaseModel):
    source: str
    page: int
    limit: int
    total_events: int
    has_more: bool
    items: list[WebhookEventRecord]


class CardLookupItem(BaseModel):
    card_id: str
    name: str
    number: str
    local_number: str | None = None
    set_id: str
    set_name: str
    set_code: str | None = None
    set_series: str | None = None
    printed_total: int | None = None
    release_date: str | None = None
    release_year: int | None = None
    rarity: str | None = None
    regulation_mark: str | None = None
    image_small: str | None = None
    image_large: str | None = None
    suggested_price_usd: float | None = None
    suggested_price_brl: float | None = None
    suggested_price_currency: str | None = None
    suggested_price_source: str | None = None
    suggested_finish: str | None = None
    usd_brl_rate: float | None = None
    pokemon_generation: str | None = None
    pokemon_types: list[str] = Field(default_factory=list)


class CardLookupResponse(BaseModel):
    source: str
    query: str
    items: list[CardLookupItem]


class CardMetadataOptionsResponse(BaseModel):
    source: str
    rarity_options: list[str]
    set_name_options: list[str]
    set_series_options: list[str]
    finish_options: list[str]
    condition_options: list[str]
    year_options: list[int]
    generation_options: list[str]


class LotImportStartRequest(BaseModel):
    lot_payload: dict[str, Any]
    default_condition: str = "Near Mint (NM)"
    default_finish: str = "Normal"
    default_category: str = "Cartas avulsas"
    infer_regulation_mark_with_openai: bool = True


class LotImportStartResponse(BaseModel):
    job_id: str
    status: str
    total_cards: int


class LotImportEntryPreview(BaseModel):
    index: int
    status: str
    action: str | None = None
    message: str | None = None
    lot_id: str | None = None
    slug: str
    name: str
    card_number: str
    category: str
    language: str
    quantity: int
    condition: str | None = None
    finish: str | None = None
    set_name: str | None = None
    set_code: str | None = None
    rarity: str | None = None
    regulation_mark: str | None = None
    release_year: int | None = None
    pokemon_generation: str | None = None
    pokemon_types: list[str] = Field(default_factory=list)
    image_url: str | None = None
    price_brl: float = 0.0


class LotImportJobResponse(BaseModel):
    job_id: str
    status: str
    lot_id: str | None = None
    lot_name: str | None = None
    started_at: str
    finished_at: str | None = None
    total_cards: int
    prepared_cards: int
    processed_cards: int
    created_count: int
    updated_count: int
    error_count: int
    last_error: str | None = None
    entries: list[LotImportEntryPreview]


CatalogAssistantAction = Literal[
    "find_price_outliers",
    "find_card_inconsistencies",
    "refresh_market_prices",
]
CatalogAssistantSeverity = Literal["high", "medium", "low"]


class CatalogAssistantRunRequest(BaseModel):
    action: CatalogAssistantAction
    slugs: list[str] = Field(default_factory=list)
    include_non_cards: bool = False
    auto_apply: bool = False


class CatalogAssistantFinding(BaseModel):
    slug: str
    severity: CatalogAssistantSeverity
    title: str
    message: str
    current_price_brl: float | None = None
    suggested_price_brl: float | None = None
    delta_percent: float | None = None
    tags: list[str] = Field(default_factory=list)


class CatalogAssistantResponse(BaseModel):
    action: CatalogAssistantAction
    model: str | None = None
    selected_products: int
    scanned_products: int
    updated_count: int = 0
    findings: list[CatalogAssistantFinding] = Field(default_factory=list)
    ai_summary: str | None = None
    warnings: list[str] = Field(default_factory=list)
