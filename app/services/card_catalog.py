from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.core.config import get_settings
from app.schemas.store import CardLookupItem, CardMetadataOptionsResponse

DEFAULT_CONDITION_OPTIONS = [
    "Mint (M)",
    "Near Mint (NM)",
    "Excellent (EX)",
    "Lightly Played (LP)",
    "Moderately Played (MP)",
    "Heavily Played (HP)",
    "Played (PL)",
    "Good (GD)",
    "Poor (PR)",
    "Damaged (DMG)",
]

DEFAULT_RARITY_OPTIONS = [
    "Common",
    "Uncommon",
    "Rare",
    "Rare Holo",
    "Rare Holo EX",
    "Rare Ultra",
    "Rare Secret",
    "Rare Rainbow",
    "Illustration Rare",
    "Special Illustration Rare",
    "Promo",
]

DEFAULT_FINISH_OPTIONS = [
    "Normal",
    "Holo (Holofoil)",
    "Reverse Holo (Reverse Foil)",
    "Poke Ball Reverse Holo",
    "Master Ball Reverse Holo",
    "Mirror Foil",
    "Full Art",
]

DEFAULT_GENERATION_OPTIONS = [
    "generation-i",
    "generation-ii",
    "generation-iii",
    "generation-iv",
    "generation-v",
    "generation-vi",
    "generation-vii",
    "generation-viii",
    "generation-ix",
]

_GENERATION_RANGES: list[tuple[int, int, str]] = [
    (1, 151, "generation-i"),
    (152, 251, "generation-ii"),
    (252, 386, "generation-iii"),
    (387, 493, "generation-iv"),
    (494, 649, "generation-v"),
    (650, 721, "generation-vi"),
    (722, 809, "generation-vii"),
    (810, 905, "generation-viii"),
    (906, 1200, "generation-ix"),
]


class CardCatalogError(RuntimeError):
    pass


_FX_CACHE: dict[str, float] | None = None
_FX_CACHE_EXPIRES_AT: datetime | None = None
_LOGGER = logging.getLogger(__name__)
_POKEMON_TCG_REQUEST_LOCK = threading.Lock()
_LAST_POKEMON_TCG_REQUEST_AT = 0.0
_RETRYABLE_HTTP_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}

_TCGPLAYER_PRICE_TYPE_PRIORITY = [
    "normal",
    "holofoil",
    "reverseHolofoil",
    "1stEditionHolofoil",
    "1stEditionNormal",
]

_TCGPLAYER_METRIC_PRIORITY = ["market", "mid", "low", "high", "directLow"]
_CARDMARKET_METRIC_PRIORITY = ["suggestedPrice", "trendPrice", "averageSellPrice", "avg7", "avg30"]
_NUMBER_TOTAL_PATTERN = re.compile(r"(\d{1,4})\s*/\s*(\d{1,4})")


def _base_url(path: str) -> str:
    settings = get_settings()
    return f"{settings.pokemon_tcg_api_base_url.rstrip('/')}/{path.lstrip('/')}"


def _parse_retry_after_seconds(raw_value: str | None) -> float | None:
    value = (raw_value or "").strip()
    if not value:
        return None

    if value.isdigit():
        return max(0.0, float(int(value)))

    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None

    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)

    now = datetime.now(UTC)
    delta = (retry_at - now).total_seconds()
    return max(0.0, delta)


def _wait_for_pokemon_tcg_slot(min_interval_seconds: float) -> None:
    global _LAST_POKEMON_TCG_REQUEST_AT

    safe_interval = max(0.0, min_interval_seconds)
    if safe_interval <= 0:
        return

    with _POKEMON_TCG_REQUEST_LOCK:
        now = time.monotonic()
        remaining = safe_interval - (now - _LAST_POKEMON_TCG_REQUEST_AT)
        if remaining > 0:
            time.sleep(remaining)
            now = time.monotonic()
        _LAST_POKEMON_TCG_REQUEST_AT = now


def _backoff_delay(
    *,
    attempt: int,
    base_delay_seconds: float,
    max_delay_seconds: float,
    retry_after_seconds: float | None,
) -> float:
    if retry_after_seconds is not None and retry_after_seconds > 0:
        return min(max_delay_seconds, retry_after_seconds)

    exponential = base_delay_seconds * (2 ** max(0, attempt - 1))
    return min(max_delay_seconds, exponential)


def _request_json(url: str) -> dict[str, Any]:
    settings = get_settings()
    headers = {"Accept": "application/json", "User-Agent": "legacy-cards-admin/1.0"}
    if settings.pokemon_tcg_api_key:
        headers["X-Api-Key"] = settings.pokemon_tcg_api_key

    timeout_seconds = max(1.0, float(settings.pokemon_tcg_timeout_seconds))
    max_attempts = max(1, int(settings.pokemon_tcg_retry_attempts))
    base_delay = max(0.05, float(settings.pokemon_tcg_retry_base_delay_seconds))
    max_delay = max(base_delay, float(settings.pokemon_tcg_retry_max_delay_seconds))
    min_interval = max(0.0, float(settings.pokemon_tcg_min_interval_seconds))

    request = Request(url=url, headers=headers)
    payload = ""

    for attempt in range(1, max_attempts + 1):
        _wait_for_pokemon_tcg_slot(min_interval)

        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                payload = response.read().decode("utf-8")
            break
        except HTTPError as exc:
            should_retry = (
                attempt < max_attempts and exc.code in _RETRYABLE_HTTP_STATUS_CODES
            )
            retry_after = _parse_retry_after_seconds(
                exc.headers.get("Retry-After") if exc.headers else None
            )

            _LOGGER.warning(
                "Pokemon API HTTP %s em %s (tentativa %s/%s)%s",
                exc.code,
                url,
                attempt,
                max_attempts,
                " - nova tentativa" if should_retry else "",
            )

            if not should_retry:
                raise CardCatalogError(f"Pokemon card API error ({exc.code})") from exc

            time.sleep(
                _backoff_delay(
                    attempt=attempt,
                    base_delay_seconds=base_delay,
                    max_delay_seconds=max_delay,
                    retry_after_seconds=retry_after,
                )
            )
        except URLError as exc:
            should_retry = attempt < max_attempts
            _LOGGER.warning(
                "Pokemon API indisponivel em %s (tentativa %s/%s): %s%s",
                url,
                attempt,
                max_attempts,
                exc,
                " - nova tentativa" if should_retry else "",
            )

            if not should_retry:
                raise CardCatalogError("Pokemon card API indisponivel no momento") from exc

            time.sleep(
                _backoff_delay(
                    attempt=attempt,
                    base_delay_seconds=base_delay,
                    max_delay_seconds=max_delay,
                    retry_after_seconds=None,
                )
            )
    else:
        raise CardCatalogError("Pokemon card API indisponivel no momento")

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        _LOGGER.warning("Pokemon card API retornou JSON invalido para URL: %s", url)
        raise CardCatalogError("Pokemon card API retornou resposta invalida") from exc


def _to_positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None

    if parsed <= 0:
        return None

    return round(parsed, 2)


def _extract_tcgplayer_price_usd(card_payload: dict[str, Any]) -> tuple[float | None, str | None]:
    tcgplayer_payload = (
        card_payload.get("tcgplayer") if isinstance(card_payload.get("tcgplayer"), dict) else {}
    )
    prices_payload = (
        tcgplayer_payload.get("prices")
        if isinstance(tcgplayer_payload.get("prices"), dict)
        else {}
    )

    for price_type in _TCGPLAYER_PRICE_TYPE_PRIORITY:
        price_type_payload = (
            prices_payload.get(price_type)
            if isinstance(prices_payload.get(price_type), dict)
            else {}
        )
        for metric in _TCGPLAYER_METRIC_PRIORITY:
            candidate = _to_positive_float(price_type_payload.get(metric))
            if candidate is None:
                continue
            return candidate, f"tcgplayer.{price_type}.{metric}"

    return None, None


def _extract_cardmarket_price_eur(card_payload: dict[str, Any]) -> tuple[float | None, str | None]:
    cardmarket_payload = (
        card_payload.get("cardmarket")
        if isinstance(card_payload.get("cardmarket"), dict)
        else {}
    )
    prices_payload = (
        cardmarket_payload.get("prices")
        if isinstance(cardmarket_payload.get("prices"), dict)
        else {}
    )

    for metric in _CARDMARKET_METRIC_PRIORITY:
        candidate = _to_positive_float(prices_payload.get(metric))
        if candidate is None:
            continue
        return candidate, f"cardmarket.{metric}"

    return None, None


def _fetch_fx_rates() -> dict[str, float]:
    settings = get_settings()
    headers = {"Accept": "application/json", "User-Agent": "legacy-cards-admin/1.0"}
    if settings.awesomeapi_fx_key:
        headers["X-API-KEY"] = settings.awesomeapi_fx_key

    request = Request(url=settings.awesomeapi_fx_url, headers=headers)
    timeout = max(1.0, float(settings.awesomeapi_fx_timeout_seconds))

    try:
        with urlopen(request, timeout=timeout) as response:
            payload_raw = response.read().decode("utf-8")
    except (HTTPError, URLError):
        return {}

    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError:
        return {}

    if not isinstance(payload, dict):
        return {}

    usd_brl_payload = payload.get("USDBRL") if isinstance(payload.get("USDBRL"), dict) else {}
    eur_brl_payload = payload.get("EURBRL") if isinstance(payload.get("EURBRL"), dict) else {}

    usd_brl = _to_positive_float(usd_brl_payload.get("bid"))
    eur_brl = _to_positive_float(eur_brl_payload.get("bid"))

    rates: dict[str, float] = {}
    if usd_brl is not None:
        rates["USD_BRL"] = usd_brl
    if eur_brl is not None:
        rates["EUR_BRL"] = eur_brl

    return rates


def _get_fx_rates_cached() -> dict[str, float]:
    global _FX_CACHE
    global _FX_CACHE_EXPIRES_AT

    now = datetime.now(UTC)
    if _FX_CACHE is not None and _FX_CACHE_EXPIRES_AT is not None and now < _FX_CACHE_EXPIRES_AT:
        return _FX_CACHE

    rates = _fetch_fx_rates()
    cache_seconds = max(30, int(get_settings().awesomeapi_fx_cache_seconds))
    _FX_CACHE = rates
    _FX_CACHE_EXPIRES_AT = now + timedelta(seconds=cache_seconds)
    return rates


def _extract_suggested_prices(
    card_payload: dict[str, Any],
    fx_rates: dict[str, float],
) -> tuple[float | None, float | None, str | None, str | None, float | None]:
    usd_brl_rate = fx_rates.get("USD_BRL")
    eur_brl_rate = fx_rates.get("EUR_BRL")

    tcg_usd, tcg_source = _extract_tcgplayer_price_usd(card_payload)
    if tcg_usd is not None:
        suggested_brl = round(tcg_usd * usd_brl_rate, 2) if usd_brl_rate else None
        return tcg_usd, suggested_brl, "USD", tcg_source, usd_brl_rate

    market_eur, eur_source = _extract_cardmarket_price_eur(card_payload)
    if market_eur is None:
        return None, None, None, None, usd_brl_rate

    suggested_brl = round(market_eur * eur_brl_rate, 2) if eur_brl_rate else None
    suggested_usd: float | None = None
    if suggested_brl is not None and usd_brl_rate:
        suggested_usd = round(suggested_brl / usd_brl_rate, 2)

    return suggested_usd, suggested_brl, "EUR", eur_source, usd_brl_rate


def _infer_finish_from_source(source: str | None) -> str | None:
    normalized = (source or "").lower()
    if not normalized:
        return None

    if "1steditionholofoil" in normalized:
        return "Holo (Holofoil)"
    if "reverseholofoil" in normalized:
        return "Reverse Holo (Reverse Foil)"
    if "holofoil" in normalized:
        return "Holo (Holofoil)"

    return None


def _extract_release_year(value: str | None) -> int | None:
    if not value:
        return None
    match = re.match(r"^(\d{4})", value.strip())
    if not match:
        return None
    return int(match.group(1))


def _infer_generation(national_dex_numbers: Any) -> str | None:
    if not isinstance(national_dex_numbers, list) or not national_dex_numbers:
        return None

    first = national_dex_numbers[0]
    if not isinstance(first, int):
        return None

    for lower, upper, generation in _GENERATION_RANGES:
        if lower <= first <= upper:
            return generation

    return None


def _normalize_number_with_total(number: str, printed_total: int | None) -> str | None:
    normalized = number.strip()
    if not normalized:
        return None
    if "/" in normalized or printed_total is None:
        return normalized

    if normalized.isdigit() and len(normalized) < 3:
        normalized = normalized.zfill(3)

    return f"{normalized}/{printed_total}"


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _sanitize_for_wildcard(value: str) -> str:
    without_quotes = value.replace('"', " ").replace("'", " ").replace("’", " ")
    sanitized = re.sub(r"[^\w\s-]", " ", without_quotes, flags=re.UNICODE)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    return sanitized


def _wildcard_pattern(value: str) -> str:
    tokens = [token for token in _sanitize_for_wildcard(value).split(" ") if token]
    return "*".join(tokens)


def _query_candidates(raw_query: str) -> list[str]:
    normalized = raw_query.strip()
    local_and_total_match = re.fullmatch(r"(\d{1,4})\s*/\s*(\d{1,4})", normalized)
    if local_and_total_match:
        raw_local = local_and_total_match.group(1)
        normalized_local = raw_local.lstrip("0") or "0"
        total = local_and_total_match.group(2).lstrip("0") or "0"
        return [
            f'(number:"{raw_local}" OR number:"{normalized_local}") set.printedTotal:{total}',
            f'(number:"{raw_local}" OR number:"{normalized_local}") set.total:{total}',
        ]

    if re.fullmatch(r"\d{1,4}", normalized):
        normalized_number = normalized.lstrip("0") or "0"
        return [f'(number:"{normalized}" OR number:"{normalized_number}")']

    mixed_number_match = _NUMBER_TOTAL_PATTERN.search(normalized)
    if mixed_number_match:
        raw_local = mixed_number_match.group(1)
        normalized_local = raw_local.lstrip("0") or "0"
        total = mixed_number_match.group(2).lstrip("0") or "0"
        return [
            f'(number:"{raw_local}" OR number:"{normalized_local}") set.printedTotal:{total}',
            f'(number:"{raw_local}" OR number:"{normalized_local}") set.total:{total}',
        ]

    wildcard = _wildcard_pattern(normalized)
    if not wildcard:
        return []
    return [f'name:*{wildcard}*', f'set.name:*{wildcard}*']


def search_cards(query: str, limit: int = 12) -> list[CardLookupItem]:
    query_text = query.strip()
    if not query_text:
        return []

    safe_limit = max(1, min(limit, 50))
    select_fields = (
        "id,name,number,rarity,regulationMark,images,set,nationalPokedexNumbers,tcgplayer,cardmarket"
    )

    cards_data: list[dict[str, Any]] = []
    last_error: CardCatalogError | None = None
    for candidate in _query_candidates(query_text):
        params = {
            "q": candidate,
            "pageSize": str(safe_limit),
            "orderBy": "-set.releaseDate,name",
            "select": select_fields,
        }
        url = f"{_base_url('/cards')}?{urlencode(params)}"
        try:
            payload = _request_json(url)
        except CardCatalogError as exc:
            last_error = exc
            _LOGGER.warning(
                "Falha em candidato de busca Pokemon API (query=%s, candidate=%s): %s",
                query_text,
                candidate,
                exc,
            )
            continue
        data = payload.get("data")
        if isinstance(data, list) and data:
            cards_data = [item for item in data if isinstance(item, dict)]
            break

    if not cards_data and last_error is not None:
        raise last_error

    fx_rates = _get_fx_rates_cached()
    results: list[CardLookupItem] = []
    for card_payload in cards_data:
        set_payload = card_payload.get("set") if isinstance(card_payload.get("set"), dict) else {}
        images_payload = (
            card_payload.get("images") if isinstance(card_payload.get("images"), dict) else {}
        )

        number = str(card_payload.get("number") or "").strip()
        set_id = str(set_payload.get("id") or "").strip()
        set_name = str(set_payload.get("name") or "").strip()
        if not number or not set_id or not set_name:
            continue

        printed_total = _coerce_int(set_payload.get("printedTotal"))
        release_date = str(set_payload.get("releaseDate") or "").strip() or None
        release_year = _extract_release_year(release_date)
        set_code_raw = str(set_payload.get("ptcgoCode") or set_id).strip()
        set_code = set_code_raw.upper() if set_code_raw else None
        suggested_usd, suggested_brl, suggested_currency, suggested_source, usd_brl_rate = (
            _extract_suggested_prices(card_payload, fx_rates)
        )
        suggested_finish = _infer_finish_from_source(suggested_source)

        results.append(
            CardLookupItem(
                card_id=str(card_payload.get("id") or "").strip() or f"{set_id}-{number}",
                name=str(card_payload.get("name") or "").strip() or "Carta",
                number=number,
                local_number=_normalize_number_with_total(number, printed_total),
                set_id=set_id,
                set_name=set_name,
                set_code=set_code,
                set_series=str(set_payload.get("series") or "").strip() or None,
                printed_total=printed_total,
                release_date=release_date,
                release_year=release_year,
                rarity=str(card_payload.get("rarity") or "").strip() or None,
                regulation_mark=str(card_payload.get("regulationMark") or "").strip() or None,
                image_small=str(images_payload.get("small") or "").strip() or None,
                image_large=str(images_payload.get("large") or "").strip() or None,
                suggested_price_usd=suggested_usd,
                suggested_price_brl=suggested_brl,
                suggested_price_currency=suggested_currency,
                suggested_price_source=suggested_source,
                suggested_finish=suggested_finish,
                usd_brl_rate=usd_brl_rate,
                pokemon_generation=_infer_generation(card_payload.get("nationalPokedexNumbers")),
            )
        )

    return results


def fetch_card_metadata_options() -> CardMetadataOptionsResponse:
    rarities_url = _base_url("/rarities")
    rarities_payload = _request_json(rarities_url)
    rarity_options = {
        str(item).strip()
        for item in rarities_payload.get("data", [])
        if isinstance(item, str) and item.strip()
    }
    if not rarity_options:
        rarity_options = set(DEFAULT_RARITY_OPTIONS)

    set_params = {
        "pageSize": 250,
        "orderBy": "-releaseDate",
        "select": "name,series,releaseDate",
    }
    sets_url = f"{_base_url('/sets')}?{urlencode(set_params)}"
    sets_payload = _request_json(sets_url)

    set_names: set[str] = set()
    set_series: set[str] = set()
    year_options: set[int] = set()

    for set_item in sets_payload.get("data", []):
        if not isinstance(set_item, dict):
            continue

        name = str(set_item.get("name") or "").strip()
        if name:
            set_names.add(name)

        series = str(set_item.get("series") or "").strip()
        if series:
            set_series.add(series)

        release_year = _extract_release_year(str(set_item.get("releaseDate") or "").strip())
        if release_year:
            year_options.add(release_year)

    return CardMetadataOptionsResponse(
        source="pokemontcg.io",
        rarity_options=sorted(rarity_options, key=str.lower),
        set_name_options=sorted(set_names, key=str.lower),
        set_series_options=sorted(set_series, key=str.lower),
        finish_options=DEFAULT_FINISH_OPTIONS,
        condition_options=DEFAULT_CONDITION_OPTIONS,
        year_options=sorted(year_options, reverse=True),
        generation_options=DEFAULT_GENERATION_OPTIONS,
    )
