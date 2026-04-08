from typing import Any

from pydantic import BaseModel, Field


class StoreProduct(BaseModel):
    slug: str
    name: str
    product_type: str
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
    category: str
    season_tags: list[str] = Field(default_factory=list)
    accessory_kind: str | None = None
    booster_pack_count: int | None = Field(default=None, ge=0)
    stock: int
    price_brl: float
    image_url: str
    image_gallery: list[str] = Field(default_factory=list)
    is_special: bool = False


class StoreProductListResponse(BaseModel):
    items: list[StoreProduct]


class StoreDeleteResponse(BaseModel):
    slug: str
    deleted: bool


class AnalyticsSummaryItem(BaseModel):
    endpoint: str
    count: int


class AnalyticsSummaryResponse(BaseModel):
    source: str
    period_days: int
    items: list[AnalyticsSummaryItem]


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
