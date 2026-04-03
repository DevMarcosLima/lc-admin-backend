from __future__ import annotations

import json
import re
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


def _base_url(path: str) -> str:
    settings = get_settings()
    return f"{settings.pokemon_tcg_api_base_url.rstrip('/')}/{path.lstrip('/')}"


def _request_json(url: str) -> dict[str, Any]:
    settings = get_settings()
    headers = {"Accept": "application/json", "User-Agent": "legacy-cards-admin/1.0"}
    if settings.pokemon_tcg_api_key:
        headers["X-Api-Key"] = settings.pokemon_tcg_api_key

    request = Request(url=url, headers=headers)
    try:
        with urlopen(request, timeout=20) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        raise CardCatalogError(f"Pokemon card API error ({exc.code})") from exc
    except URLError as exc:
        raise CardCatalogError("Pokemon card API indisponivel no momento") from exc

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise CardCatalogError("Pokemon card API retornou resposta invalida") from exc


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

    safe = normalized.replace('"', "")
    return [f'name:*{safe}*', f'set.name:*{safe}*']


def search_cards(query: str, limit: int = 12) -> list[CardLookupItem]:
    query_text = query.strip()
    if not query_text:
        return []

    safe_limit = max(1, min(limit, 50))
    select_fields = "id,name,number,rarity,images,set,nationalPokedexNumbers"

    cards_data: list[dict[str, Any]] = []
    for candidate in _query_candidates(query_text):
        params = {
            "q": candidate,
            "pageSize": str(safe_limit),
            "orderBy": "-set.releaseDate,name",
            "select": select_fields,
        }
        url = f"{_base_url('/cards')}?{urlencode(params)}"
        payload = _request_json(url)
        data = payload.get("data")
        if isinstance(data, list) and data:
            cards_data = [item for item in data if isinstance(item, dict)]
            break

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
                image_small=str(images_payload.get("small") or "").strip() or None,
                image_large=str(images_payload.get("large") or "").strip() or None,
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
        condition_options=DEFAULT_CONDITION_OPTIONS,
        year_options=sorted(year_options, reverse=True),
        generation_options=DEFAULT_GENERATION_OPTIONS,
    )
