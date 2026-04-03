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
    firestore_enabled: bool = Field(default=True, alias="FIRESTORE_ENABLED")
    firestore_project_id: str | None = Field(default=None, alias="FIRESTORE_PROJECT_ID")
    firestore_collection_products: str = Field(
        default="store_products", alias="FIRESTORE_COLLECTION_PRODUCTS"
    )
    firestore_collection_analytics: str = Field(
        default="analytics_events", alias="FIRESTORE_COLLECTION_ANALYTICS"
    )
    firestore_service_account_path: str = Field(
        default="service.json", alias="FIRESTORE_SERVICE_ACCOUNT_PATH"
    )
    pokemon_tcg_api_base_url: str = Field(
        default="https://api.pokemontcg.io/v2", alias="POKEMON_TCG_API_BASE_URL"
    )
    pokemon_tcg_api_key: str | None = Field(default=None, alias="POKEMON_TCG_API_KEY")

    @property
    def backend_root(self) -> Path:
        return Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
