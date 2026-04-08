from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import get_settings


class BigQueryConnectionError(RuntimeError):
    pass


def _resolve_service_account_path() -> Path:
    settings = get_settings()
    configured = Path(settings.bigquery_service_account_path or "service.json")
    if configured.is_absolute():
        return configured
    cwd_path = Path.cwd() / configured
    if cwd_path.exists():
        return cwd_path
    return settings.backend_root / configured


@lru_cache(maxsize=1)
def get_bigquery_client() -> Any:
    settings = get_settings()
    if not settings.bigquery_enabled:
        raise BigQueryConnectionError("BigQuery desabilitado (BIGQUERY_ENABLED=false).")

    try:
        from google.cloud import bigquery
        from google.oauth2 import service_account
    except ModuleNotFoundError as exc:
        raise BigQueryConnectionError("google-cloud-bigquery is not installed") from exc

    project_id = settings.bigquery_project_id
    service_account_path = _resolve_service_account_path()

    if service_account_path.exists():
        credentials = service_account.Credentials.from_service_account_file(
            str(service_account_path)
        )
        project_id = project_id or credentials.project_id
        if not project_id:
            raise BigQueryConnectionError("Unable to resolve BIGQUERY_PROJECT_ID from credentials.")
        return bigquery.Client(
            project=project_id,
            credentials=credentials,
            location=settings.bigquery_location,
        )

    if project_id:
        return bigquery.Client(project=project_id, location=settings.bigquery_location)
    return bigquery.Client(location=settings.bigquery_location)


def _table_id() -> str:
    settings = get_settings()
    project_id = settings.bigquery_project_id or get_bigquery_client().project
    if not project_id:
        raise BigQueryConnectionError("BIGQUERY_PROJECT_ID is not configured.")
    return f"{project_id}.{settings.bigquery_dataset}.{settings.bigquery_events_table}"


def analytics_summary_last_days(days: int = 30) -> list[tuple[str, int]]:
    settings = get_settings()
    if not settings.bigquery_enabled:
        return []

    safe_days = max(1, min(days, 365))
    table_name = _table_id()

    try:
        from google.cloud import bigquery
    except ModuleNotFoundError as exc:
        raise BigQueryConnectionError("google-cloud-bigquery is not installed") from exc

    query = f"""
        SELECT
          COALESCE(NULLIF(endpoint, ''), 'unknown') AS endpoint,
          COUNT(1) AS total
        FROM `{table_name}`
        WHERE event_timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
        GROUP BY endpoint
        ORDER BY total DESC
    """

    try:
        client = get_bigquery_client()
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("days", "INT64", safe_days)]
        )
        rows = client.query(query, job_config=job_config).result()
    except Exception as exc:  # noqa: BLE001
        raise BigQueryConnectionError("Failed to query analytics summary from BigQuery") from exc

    return [(str(row.endpoint), int(row.total)) for row in rows]
