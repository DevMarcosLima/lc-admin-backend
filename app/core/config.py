from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field(default="LC Admin API", alias="APP_NAME")
    environment: str = Field(default="development", alias="ENVIRONMENT")
    api_prefix: str = Field(default="/api/v1", alias="API_PREFIX")
    backend_cors_origins: list[str] = Field(
        default=["http://localhost:5173"], alias="BACKEND_CORS_ORIGINS"
    )
    admin_api_token: str = Field(default="change-me-admin-token", alias="ADMIN_API_TOKEN")
    admin_auth_email: str = Field(default="admin@legacycards.local", alias="ADMIN_AUTH_EMAIL")
    admin_auth_password_hash: str = Field(default="", alias="ADMIN_AUTH_PASSWORD_HASH")
    admin_auth_jwt_secret: str = Field(default="", alias="ADMIN_AUTH_JWT_SECRET")
    admin_auth_jwt_algorithm: str = Field(default="HS256", alias="ADMIN_AUTH_JWT_ALGORITHM")
    admin_auth_jwt_expires_minutes: int = Field(default=480, alias="ADMIN_AUTH_JWT_EXPIRES_MINUTES")
    admin_auth_cookie_name: str = Field(
        default="lc_admin_session", alias="ADMIN_AUTH_COOKIE_NAME"
    )
    admin_auth_cookie_secure: bool = Field(default=True, alias="ADMIN_AUTH_COOKIE_SECURE")
    admin_auth_cookie_samesite: str = Field(default="lax", alias="ADMIN_AUTH_COOKIE_SAMESITE")
    admin_auth_2fa_enabled: bool = Field(default=True, alias="ADMIN_AUTH_2FA_ENABLED")
    admin_auth_totp_secret: str = Field(default="", alias="ADMIN_AUTH_TOTP_SECRET")
    admin_auth_totp_issuer: str = Field(
        default="Legacy Cards Admin", alias="ADMIN_AUTH_TOTP_ISSUER"
    )
    admin_auth_2fa_challenge_minutes: int = Field(
        default=5, alias="ADMIN_AUTH_2FA_CHALLENGE_MINUTES"
    )
    admin_auth_max_failed_attempts: int = Field(default=5, alias="ADMIN_AUTH_MAX_FAILED_ATTEMPTS")
    admin_auth_failed_window_seconds: int = Field(
        default=900, alias="ADMIN_AUTH_FAILED_WINDOW_SECONDS"
    )
    firestore_enabled: bool = Field(default=True, alias="FIRESTORE_ENABLED")
    firestore_project_id: str | None = Field(default=None, alias="FIRESTORE_PROJECT_ID")
    firestore_database_id: str = Field(default="(default)", alias="FIRESTORE_DATABASE_ID")
    firestore_collection_products: str = Field(
        default="store_products", alias="FIRESTORE_COLLECTION_PRODUCTS"
    )
    firestore_collection_analytics: str = Field(
        default="analytics_events", alias="FIRESTORE_COLLECTION_ANALYTICS"
    )
    firestore_collection_orders: str = Field(
        default="orders", alias="FIRESTORE_COLLECTION_ORDERS"
    )
    firestore_collection_webhook_events: str = Field(
        default="checkout_webhook_events",
        alias="FIRESTORE_COLLECTION_WEBHOOK_EVENTS",
    )
    firestore_collection_seller_users: str = Field(
        default="admin_seller_users", alias="FIRESTORE_COLLECTION_SELLER_USERS"
    )
    firestore_collection_panel_settings: str = Field(
        default="admin_panel_settings",
        alias="FIRESTORE_COLLECTION_PANEL_SETTINGS",
    )
    firestore_service_account_path: str = Field(
        default="service.json", alias="FIRESTORE_SERVICE_ACCOUNT_PATH"
    )
    asset_storage_bucket: str | None = Field(default=None, alias="ASSET_STORAGE_BUCKET")
    firebase_storage_bucket: str | None = Field(default=None, alias="FIREBASE_STORAGE_BUCKET")
    asset_storage_public_base_url: str | None = Field(
        default=None, alias="ASSET_STORAGE_PUBLIC_BASE_URL"
    )
    asset_storage_path_prefix: str = Field(default="catalog", alias="ASSET_STORAGE_PATH_PREFIX")
    asset_storage_max_image_bytes: int = Field(
        default=12 * 1024 * 1024, alias="ASSET_STORAGE_MAX_IMAGE_BYTES"
    )
    legacy_store_name: str = Field(default="Legacy Cards", alias="LEGACY_STORE_NAME")
    legacy_store_slug: str = Field(default="legacy-cards", alias="LEGACY_STORE_SLUG")
    bigquery_enabled: bool = Field(default=False, alias="BIGQUERY_ENABLED")
    bigquery_project_id: str | None = Field(default=None, alias="BIGQUERY_PROJECT_ID")
    bigquery_dataset: str = Field(default="legacy_cards_analytics", alias="BIGQUERY_DATASET")
    bigquery_events_table: str = Field(default="events", alias="BIGQUERY_EVENTS_TABLE")
    bigquery_service_account_path: str = Field(
        default="service.json", alias="BIGQUERY_SERVICE_ACCOUNT_PATH"
    )
    bigquery_location: str = Field(default="US", alias="BIGQUERY_LOCATION")
    bigquery_auto_create_dataset: bool = Field(
        default=True, alias="BIGQUERY_AUTO_CREATE_DATASET"
    )
    analytics_summary_fallback_firestore: bool = Field(
        default=True, alias="ANALYTICS_SUMMARY_FALLBACK_FIRESTORE"
    )
    pokemon_tcg_api_base_url: str = Field(
        default="https://api.pokemontcg.io/v2", alias="POKEMON_TCG_API_BASE_URL"
    )
    pokemon_tcg_api_key: str | None = Field(default=None, alias="POKEMON_TCG_API_KEY")
    pokemon_tcg_timeout_seconds: float = Field(
        default=20.0, alias="POKEMON_TCG_TIMEOUT_SECONDS"
    )
    pokemon_tcg_retry_attempts: int = Field(default=4, alias="POKEMON_TCG_RETRY_ATTEMPTS")
    pokemon_tcg_retry_base_delay_seconds: float = Field(
        default=0.6, alias="POKEMON_TCG_RETRY_BASE_DELAY_SECONDS"
    )
    pokemon_tcg_retry_max_delay_seconds: float = Field(
        default=6.0, alias="POKEMON_TCG_RETRY_MAX_DELAY_SECONDS"
    )
    pokemon_tcg_min_interval_seconds: float = Field(
        default=0.25, alias="POKEMON_TCG_MIN_INTERVAL_SECONDS"
    )
    awesomeapi_fx_url: str = Field(
        default="https://economia.awesomeapi.com.br/json/last/USD-BRL,EUR-BRL",
        alias="AWESOMEAPI_FX_URL",
    )
    awesomeapi_fx_key: str | None = Field(default=None, alias="AWESOMEAPI_FX_KEY")
    awesomeapi_fx_timeout_seconds: float = Field(
        default=8.0, alias="AWESOMEAPI_FX_TIMEOUT_SECONDS"
    )
    awesomeapi_fx_cache_seconds: int = Field(
        default=900, alias="AWESOMEAPI_FX_CACHE_SECONDS"
    )
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_regulation_model: str = Field(
        default="gpt-5-nano", alias="OPENAI_REGULATION_MODEL"
    )
    openai_regulation_batch_size: int = Field(
        default=40, alias="OPENAI_REGULATION_BATCH_SIZE"
    )
    openai_catalog_assistant_model: str = Field(
        default="gpt-5-nano", alias="OPENAI_CATALOG_ASSISTANT_MODEL"
    )
    openai_catalog_max_products: int = Field(
        default=250, alias="OPENAI_CATALOG_MAX_PRODUCTS"
    )
    openai_catalog_max_findings: int = Field(
        default=50, alias="OPENAI_CATALOG_MAX_FINDINGS"
    )
    mercadopago_access_token: str | None = Field(
        default=None, alias="MERCADOPAGO_ACCESS_TOKEN"
    )
    mercadopago_api_base_url: str = Field(
        default="https://api.mercadopago.com",
        alias="MERCADOPAGO_API_BASE_URL",
    )
    mercadopago_timeout_seconds: float = Field(
        default=20.0, alias="MERCADOPAGO_TIMEOUT_SECONDS"
    )
    lot_import_max_cards: int = Field(default=500, alias="LOT_IMPORT_MAX_CARDS")

    @property
    def backend_root(self) -> Path:
        return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
