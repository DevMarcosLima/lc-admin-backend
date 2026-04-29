from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from app.core.config import get_settings


class MercadoPagoAdminError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _require_token() -> str:
    token = (get_settings().mercadopago_access_token or "").strip()
    if not token:
        raise MercadoPagoAdminError(
            "MERCADOPAGO_ACCESS_TOKEN nao configurado para estorno no painel admin.",
            status_code=503,
        )
    return token


def _build_url(path: str) -> str:
    base_url = get_settings().mercadopago_api_base_url.rstrip("/")
    normalized_path = path if path.startswith("/") else f"/{path}"
    candidate = f"{base_url}{normalized_path}"
    parsed = urlsplit(candidate)
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").strip("[]").lower()
    if scheme == "https" and hostname:
        return candidate
    if scheme == "http" and hostname in _LOCAL_HOSTS:
        return candidate
    raise MercadoPagoAdminError(
        "URL do Mercado Pago insegura na configuração do backend.",
        status_code=500,
    )


def _api_request(
    *,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    token = _require_token()
    url = _build_url(path)
    body = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
    request = Request(
        url=url,
        data=body,
        method=method.upper(),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    timeout_seconds = max(4.0, float(get_settings().mercadopago_timeout_seconds))

    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # nosec B310
            raw = response.read().decode("utf-8", errors="ignore")
            if not raw.strip():
                return {}
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
    except HTTPError as exc:
        raw_error = exc.read().decode("utf-8", errors="ignore")
        detail = None
        if raw_error.strip():
            try:
                parsed_error = json.loads(raw_error)
                if isinstance(parsed_error, dict):
                    detail = (
                        parsed_error.get("message")
                        or parsed_error.get("error")
                        or parsed_error.get("cause")
                    )
            except json.JSONDecodeError:
                detail = raw_error.strip()
        message = str(detail or "Mercado Pago retornou erro ao processar estorno.")
        raise MercadoPagoAdminError(message, status_code=502) from exc
    except URLError as exc:
        raise MercadoPagoAdminError(
            "Falha de conexao com o Mercado Pago para estorno.",
            status_code=502,
        ) from exc


def fetch_payment(payment_id: str) -> dict[str, Any]:
    safe_payment_id = str(payment_id or "").strip()
    if not safe_payment_id:
        raise MercadoPagoAdminError("payment_id invalido para consulta de pagamento.", status_code=422)
    return _api_request(method="GET", path=f"/v1/payments/{safe_payment_id}")


def create_full_refund(payment_id: str) -> dict[str, Any]:
    safe_payment_id = str(payment_id or "").strip()
    if not safe_payment_id:
        raise MercadoPagoAdminError("payment_id invalido para estorno.", status_code=422)
    return _api_request(method="POST", path=f"/v1/payments/{safe_payment_id}/refunds", payload={})
