from pydantic import BaseModel, Field


class StoreProduct(BaseModel):
    slug: str
    name: str
    product_type: str
    set_name: str | None = None
    rarity: str | None = None
    condition: str | None = None
    category: str
    season_tags: list[str] = Field(default_factory=list)
    accessory_kind: str | None = None
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
