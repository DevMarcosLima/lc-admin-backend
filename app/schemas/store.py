from pydantic import BaseModel, Field


class StoreProduct(BaseModel):
    slug: str
    name: str
    product_type: str
    set_name: str | None = None
    set_series: str | None = None
    rarity: str | None = None
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
    image_small: str | None = None
    image_large: str | None = None
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
    condition_options: list[str]
    year_options: list[int]
    generation_options: list[str]
