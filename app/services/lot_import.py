from __future__ import annotations

import copy
import json
import re
import threading
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.core.config import get_settings
from app.schemas.store import (
    LotImportEntryPreview,
    LotImportJobResponse,
    LotImportStartRequest,
    LotImportStartResponse,
    StoreProduct,
)
from app.services.card_catalog import CardCatalogError, search_cards
from app.services.firestore_admin import (
    FirestoreConnectionError,
    fetch_products_from_firestore,
    upsert_product,
)

_JOB_STORE: dict[str, dict[str, Any]] = {}
_JOB_LOCK = threading.Lock()

_VALID_REGULATION_MARK = re.compile(r"^[A-I]$")


class LotImportError(RuntimeError):
    pass


class LotImportNotFound(LotImportError):
    pass


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_text(value: str) -> str:
    return value.strip().lower()


def _normalize_card_number(value: str) -> str:
    compact = value.strip().replace(" ", "")
    if not compact:
        return ""

    if "/" not in compact:
        return compact

    local, total = compact.split("/", 1)
    local_digits = local.lstrip("0") or "0"
    total_digits = total.lstrip("0") or "0"
    return f"{local_digits}/{total_digits}"


def _slugify(value: str) -> str:
    lowered = value.lower().strip()
    normalized = re.sub(r"[^a-z0-9]+", "-", lowered)
    normalized = normalized.strip("-")
    return normalized[:90] if normalized else "item"


def _ensure_unique_slug(base_slug: str, used_slugs: set[str]) -> str:
    candidate = base_slug
    index = 2
    while candidate in used_slugs:
        candidate = f"{base_slug}-{index}"
        index += 1
    used_slugs.add(candidate)
    return candidate


def _map_finish(details: str | None, default_finish: str) -> str:
    raw = (details or "").strip().lower()
    if not raw:
        return default_finish

    if "master" in raw and "ball" in raw:
        return "Master Ball Reverse Holo"
    if ("poke" in raw or "pokeball" in raw) and "ball" in raw:
        return "Poke Ball Reverse Holo"
    if "reverse" in raw:
        return "Reverse Holo (Reverse Foil)"
    if "mirror" in raw:
        return "Mirror Foil"
    if "full" in raw and "art" in raw:
        return "Full Art"
    if "holo" in raw or "foil" in raw:
        return "Holo (Holofoil)"

    return default_finish


def _map_category(raw_category: str | None, default_category: str) -> str:
    category = (raw_category or "").strip().lower()
    if not category:
        return default_category
    if "treinador" in category or "item" in category:
        return "Treinadores Pokemon"
    if "pokemon" in category:
        return default_category
    return (raw_category or default_category).strip()


def _safe_positive_int(value: Any, fallback: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _extract_lot_cards(
    lot_payload: dict[str, Any],
    max_cards: int,
) -> tuple[str | None, str | None, list[dict[str, Any]]]:
    lot_id = str(lot_payload.get("lot_id") or "").strip() or None
    lot_name = str(lot_payload.get("lot_name") or "").strip() or lot_id

    raw_cards = lot_payload.get("cards")
    if not isinstance(raw_cards, list):
        raise LotImportError("JSON de lote invalido: campo 'cards' ausente ou invalido.")

    grouped: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for item in raw_cards:
        if not isinstance(item, dict):
            continue

        name = str(item.get("name") or "").strip()
        number = str(item.get("number") or "").strip()
        language = str(item.get("language") or "PT").strip().upper() or "PT"
        category = str(item.get("category") or "").strip()
        details = str(item.get("details") or "").strip()
        quantity = _safe_positive_int(item.get("quantity"), fallback=1)

        if not name or not number:
            continue

        key = (
            _normalize_text(name),
            _normalize_card_number(number),
            language,
            _normalize_text(category),
            _normalize_text(details),
        )

        if key not in grouped:
            grouped[key] = {
                "name": name,
                "number": number,
                "language": language,
                "category": category,
                "details": details,
                "quantity": quantity,
            }
            continue

        existing_qty = _safe_positive_int(grouped[key].get("quantity"), fallback=1)
        grouped[key]["quantity"] = existing_qty + quantity

    cards = list(grouped.values())
    if not cards:
        raise LotImportError("Nenhuma carta valida encontrada no lote JSON.")

    return lot_id, lot_name, cards[:max_cards]


def _identity_key(product: StoreProduct) -> str:
    return "|".join(
        [
            _normalize_text(product.name),
            _normalize_text(product.card_number or ""),
            _normalize_text(product.set_name or ""),
            _normalize_text(product.set_series or ""),
            _normalize_text(product.rarity or ""),
            _normalize_text(product.finish or ""),
            _normalize_text(product.condition or ""),
            _normalize_text(product.regulation_mark or ""),
            _normalize_text(product.set_code or ""),
            _normalize_text(product.language or ""),
            str(product.release_year or ""),
            _normalize_text(product.pokemon_generation or ""),
        ]
    )


def _season_tags_from_payload(payload: dict[str, Any]) -> list[str]:
    tags: list[str] = []

    set_code = str(payload.get("set_code") or "").strip().upper()
    set_name = str(payload.get("set_name") or "").strip()
    category = str(payload.get("category") or "").strip()
    language = str(payload.get("language") or "").strip().upper()

    if set_code:
        tags.append(f"set:{set_code.lower()}")
    if set_name:
        tags.append(f"set-name:{_slugify(set_name)}")
    if category:
        tags.append(f"category:{_slugify(category)}")
    if language:
        tags.append(f"lang:{language.lower()}")

    return tags


def _score_lookup_candidate(candidate: Any, wanted_name: str, wanted_number: str) -> int:
    score = 0

    wanted_name_norm = _normalize_text(wanted_name)
    wanted_number_norm = _normalize_card_number(wanted_number)

    candidate_name_norm = _normalize_text(candidate.name)
    candidate_number_norm = _normalize_card_number(candidate.number)
    candidate_local_norm = _normalize_card_number(candidate.local_number or "")

    if candidate_name_norm == wanted_name_norm:
        score += 4
    elif wanted_name_norm and wanted_name_norm in candidate_name_norm:
        score += 2

    if wanted_number_norm and candidate_local_norm == wanted_number_norm:
        score += 6
    elif wanted_number_norm and candidate_number_norm == wanted_number_norm:
        score += 5
    elif (
        wanted_number_norm
        and candidate_number_norm
        and wanted_number_norm.split("/")[0] == candidate_number_norm
    ):
        score += 3

    if candidate.set_name:
        score += 1
    if candidate.image_large or candidate.image_small:
        score += 1

    return score


def _lookup_best_card(name: str, number: str) -> Any | None:
    queries: list[str] = []
    normalized_number = number.strip()

    if normalized_number:
        queries.extend(
            [
                normalized_number,
                f"{normalized_number} {name}",
                f"{name} {normalized_number}",
            ]
        )

    if name.strip():
        queries.append(name.strip())

    seen_query: set[str] = set()
    best_candidate: Any | None = None
    best_score = -1

    for query in queries:
        if query in seen_query:
            continue
        seen_query.add(query)

        try:
            items = search_cards(query=query, limit=8)
        except CardCatalogError:
            continue

        for item in items:
            candidate_score = _score_lookup_candidate(item, name, number)
            if candidate_score > best_score:
                best_score = candidate_score
                best_candidate = item

        if best_score >= 10:
            break

    return best_candidate


def _extract_json_object(raw_text: str) -> dict[str, Any] | None:
    text = raw_text.strip()
    if not text:
        return None

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        if text.endswith("```"):
            text = text[:-3].strip()

    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    object_match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not object_match:
        return None

    try:
        parsed = json.loads(object_match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _infer_regulation_marks_with_openai(items: list[dict[str, Any]]) -> dict[int, str]:
    settings = get_settings()
    if not settings.openai_api_key or not items:
        return {}

    try:
        from openai import OpenAI
    except ModuleNotFoundError:
        return {}

    client = OpenAI(api_key=settings.openai_api_key)
    batch_size = max(1, settings.openai_regulation_batch_size)

    resolved: dict[int, str] = {}
    for offset in range(0, len(items), batch_size):
        batch = items[offset : offset + batch_size]
        compact_batch = [
            {
                "index": item["index"],
                "name": item["name"],
                "card_number": item["card_number"],
                "set_name": item.get("set_name"),
                "set_code": item.get("set_code"),
                "release_year": item.get("release_year"),
                "language": item.get("language"),
            }
            for item in batch
        ]

        try:
            response = client.responses.create(
                model=settings.openai_regulation_model,
                input=[
                    {
                        "role": "system",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    "Voce retorna apenas JSON valido. "
                                    "Tarefa: inferir regulation_mark de cartas Pokemon (A-I). "
                                    "Quando nao houver alta confianca, use null. "
                                    "Nunca invente valores fora de A-I."
                                ),
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    "Retorne JSON no formato: "
                                    '{"items":[{"index":1,"regulation_mark":"G"}]}. '
                                    "Entrada:\n"
                                    + json.dumps(compact_batch, ensure_ascii=False)
                                ),
                            }
                        ],
                    },
                ],
            )
        except Exception:  # noqa: BLE001
            continue

        payload = _extract_json_object(response.output_text)
        if not payload:
            continue

        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            continue

        for row in raw_items:
            if not isinstance(row, dict):
                continue

            index = row.get("index")
            mark = str(row.get("regulation_mark") or "").strip().upper()
            if not isinstance(index, int):
                continue
            if not _VALID_REGULATION_MARK.fullmatch(mark):
                continue
            resolved[index] = mark

    return resolved


def _build_product_payload(
    *,
    raw_card: dict[str, Any],
    metadata: Any | None,
    default_condition: str,
    default_finish: str,
    default_category: str,
) -> dict[str, Any]:
    name = str(raw_card.get("name") or "").strip()
    card_number = str(raw_card.get("number") or "").strip()
    quantity = _safe_positive_int(raw_card.get("quantity"), fallback=1)
    language = str(raw_card.get("language") or "PT").strip().upper() or "PT"

    category = _map_category(str(raw_card.get("category") or "").strip(), default_category)
    finish = _map_finish(str(raw_card.get("details") or "").strip(), default_finish)

    set_name = metadata.set_name if metadata else None
    set_series = metadata.set_series if metadata else None
    set_code = metadata.set_code if metadata else None
    rarity = metadata.rarity if metadata else None
    release_year = metadata.release_year if metadata else None
    generation = metadata.pokemon_generation if metadata else None
    regulation_mark = metadata.regulation_mark if metadata else None
    image_url = (metadata.image_large or metadata.image_small) if metadata else None
    image_gallery: list[str] = []
    if metadata and metadata.image_small and metadata.image_small != image_url:
        image_gallery = [metadata.image_small]

    price_brl = metadata.suggested_price_brl if metadata and metadata.suggested_price_brl else 0.0
    slug_seed = "-".join(
        part
        for part in [
            name,
            set_code or "",
            card_number.replace("/", "-"),
            language,
            finish,
        ]
        if part
    )
    slug = _slugify(slug_seed)

    payload = {
        "slug": slug,
        "name": name,
        "product_type": "single_card",
        "set_name": set_name,
        "set_series": set_series,
        "rarity": rarity,
        "finish": finish,
        "condition": default_condition,
        "card_number": card_number,
        "regulation_mark": regulation_mark,
        "set_code": set_code,
        "language": language,
        "release_year": release_year,
        "pokemon_generation": generation,
        "category": category,
        "season_tags": [],
        "accessory_kind": None,
        "booster_pack_count": None,
        "stock": quantity,
        "price_brl": float(price_brl),
        "image_url": image_url or "",
        "image_gallery": image_gallery,
        "is_special": False,
    }
    payload["season_tags"] = _season_tags_from_payload(payload)
    return payload


def _initialize_job(
    lot_id: str | None,
    lot_name: str | None,
    cards: list[dict[str, Any]],
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for index, card in enumerate(cards, start=1):
        name = str(card.get("name") or "").strip()
        number = str(card.get("number") or "").strip()
        language = str(card.get("language") or "PT").strip().upper() or "PT"
        quantity = _safe_positive_int(card.get("quantity"), fallback=1)

        entries.append(
            {
                "index": index,
                "status": "queued",
                "action": None,
                "message": None,
                "slug": _slugify(f"{name}-{number.replace('/', '-')}-{language}"),
                "name": name,
                "card_number": number,
                "category": str(card.get("category") or "").strip() or "Cartas avulsas",
                "language": language,
                "quantity": quantity,
                "condition": None,
                "finish": None,
                "set_name": None,
                "set_code": None,
                "rarity": None,
                "regulation_mark": None,
                "release_year": None,
                "pokemon_generation": None,
                "image_url": None,
                "price_brl": 0.0,
            }
        )

    return {
        "job_id": f"lot-{uuid4().hex}",
        "status": "queued",
        "lot_id": lot_id,
        "lot_name": lot_name,
        "started_at": _now_iso(),
        "finished_at": None,
        "total_cards": len(cards),
        "prepared_cards": 0,
        "processed_cards": 0,
        "created_count": 0,
        "updated_count": 0,
        "error_count": 0,
        "last_error": None,
        "entries": entries,
    }


def _set_job_fields(job_id: str, **fields: Any) -> None:
    with _JOB_LOCK:
        job = _JOB_STORE.get(job_id)
        if not job:
            return
        job.update(fields)


def _inc_job_fields(job_id: str, **increments: int) -> None:
    with _JOB_LOCK:
        job = _JOB_STORE.get(job_id)
        if not job:
            return
        for key, amount in increments.items():
            job[key] = int(job.get(key, 0)) + amount


def _set_entry_fields(job_id: str, entry_index: int, **fields: Any) -> None:
    with _JOB_LOCK:
        job = _JOB_STORE.get(job_id)
        if not job:
            return
        entries = job.get("entries")
        if not isinstance(entries, list) or entry_index < 1 or entry_index > len(entries):
            return
        entry = entries[entry_index - 1]
        if not isinstance(entry, dict):
            return
        entry.update(fields)


def _set_job_failed(job_id: str, message: str) -> None:
    _set_job_fields(
        job_id,
        status="failed",
        last_error=message,
        finished_at=_now_iso(),
    )


def _to_job_response(job: dict[str, Any]) -> LotImportJobResponse:
    return LotImportJobResponse(
        job_id=job["job_id"],
        status=job["status"],
        lot_id=job.get("lot_id"),
        lot_name=job.get("lot_name"),
        started_at=job["started_at"],
        finished_at=job.get("finished_at"),
        total_cards=job["total_cards"],
        prepared_cards=job["prepared_cards"],
        processed_cards=job["processed_cards"],
        created_count=job["created_count"],
        updated_count=job["updated_count"],
        error_count=job["error_count"],
        last_error=job.get("last_error"),
        entries=[LotImportEntryPreview.model_validate(entry) for entry in job["entries"]],
    )


def _run_import_job(
    job_id: str,
    cards: list[dict[str, Any]],
    request: LotImportStartRequest,
) -> None:
    _set_job_fields(job_id, status="running")

    try:
        existing_products = fetch_products_from_firestore()
    except FirestoreConnectionError as exc:
        _set_job_failed(job_id, f"Falha ao conectar no Firestore: {exc}")
        return

    identity_map: dict[str, StoreProduct] = {}
    used_slugs: set[str] = set()
    for product in existing_products:
        used_slugs.add(product.slug)
        if product.product_type == "single_card":
            identity_map[_identity_key(product)] = product

    prepared_payloads: list[dict[str, Any]] = []

    for idx, raw_card in enumerate(cards, start=1):
        _set_entry_fields(
            job_id,
            idx,
            status="preparing",
            message="Buscando metadados",
        )

        metadata = _lookup_best_card(
            name=str(raw_card.get("name") or "").strip(),
            number=str(raw_card.get("number") or "").strip(),
        )

        payload = _build_product_payload(
            raw_card=raw_card,
            metadata=metadata,
            default_condition=request.default_condition,
            default_finish=request.default_finish,
            default_category=request.default_category,
        )
        prepared_payloads.append(payload)

        _set_entry_fields(
            job_id,
            idx,
            status="ready",
            message="Pronto para salvar",
            slug=payload["slug"],
            category=payload["category"],
            condition=payload["condition"],
            finish=payload["finish"],
            set_name=payload["set_name"],
            set_code=payload["set_code"],
            rarity=payload["rarity"],
            regulation_mark=payload["regulation_mark"],
            release_year=payload["release_year"],
            pokemon_generation=payload["pokemon_generation"],
            image_url=payload["image_url"],
            price_brl=payload["price_brl"],
        )
        _set_job_fields(job_id, prepared_cards=idx)

    if request.infer_regulation_mark_with_openai:
        missing_items: list[dict[str, Any]] = []
        for idx, payload in enumerate(prepared_payloads, start=1):
            mark = str(payload.get("regulation_mark") or "").strip().upper()
            if _VALID_REGULATION_MARK.fullmatch(mark):
                continue
            missing_items.append(
                {
                    "index": idx,
                    "name": payload.get("name"),
                    "card_number": payload.get("card_number"),
                    "set_name": payload.get("set_name"),
                    "set_code": payload.get("set_code"),
                    "release_year": payload.get("release_year"),
                    "language": payload.get("language"),
                }
            )

        resolved_marks = _infer_regulation_marks_with_openai(missing_items)
        for idx, mark in resolved_marks.items():
            if idx < 1 or idx > len(prepared_payloads):
                continue
            prepared_payloads[idx - 1]["regulation_mark"] = mark
            _set_entry_fields(job_id, idx, regulation_mark=mark)

    for idx, payload in enumerate(prepared_payloads, start=1):
        try:
            candidate_product = StoreProduct.model_validate(payload)
        except Exception as exc:  # noqa: BLE001
            _set_entry_fields(
                job_id,
                idx,
                status="error",
                action="error",
                message=f"Payload invalido: {exc}",
            )
            _inc_job_fields(job_id, error_count=1, processed_cards=1)
            continue

        key = _identity_key(candidate_product)
        existing = identity_map.get(key)

        if existing:
            merged_gallery = sorted(
                set((existing.image_gallery or []) + (candidate_product.image_gallery or []))
            )
            merged_tags = sorted(
                set((existing.season_tags or []) + (candidate_product.season_tags or []))
            )
            merged_price = (
                existing.price_brl
                if existing.price_brl > 0
                else candidate_product.price_brl
            )
            to_save = existing.model_copy(
                update={
                    "stock": existing.stock + candidate_product.stock,
                    "price_brl": merged_price,
                    "image_url": existing.image_url or candidate_product.image_url,
                    "image_gallery": merged_gallery,
                    "season_tags": merged_tags,
                }
            )
            action = "updated"
        else:
            unique_slug = _ensure_unique_slug(candidate_product.slug, used_slugs)
            to_save = candidate_product.model_copy(update={"slug": unique_slug})
            action = "created"

        try:
            saved = upsert_product(to_save)
        except FirestoreConnectionError as exc:
            _set_entry_fields(
                job_id,
                idx,
                status="error",
                action="error",
                message=f"Erro ao salvar: {exc}",
            )
            _inc_job_fields(job_id, error_count=1, processed_cards=1)
            continue

        identity_map[key] = saved
        _set_entry_fields(
            job_id,
            idx,
            status="saved",
            action=action,
            slug=saved.slug,
            message="Salvo com sucesso",
        )

        if action == "created":
            _inc_job_fields(job_id, created_count=1, processed_cards=1)
        else:
            _inc_job_fields(job_id, updated_count=1, processed_cards=1)

    with _JOB_LOCK:
        job = _JOB_STORE.get(job_id)
        if not job:
            return
        job["status"] = "completed_with_errors" if job["error_count"] > 0 else "completed"
        job["finished_at"] = _now_iso()


def start_lot_import(request: LotImportStartRequest) -> LotImportStartResponse:
    settings = get_settings()
    max_cards = max(1, settings.lot_import_max_cards)

    lot_id, lot_name, cards = _extract_lot_cards(request.lot_payload, max_cards=max_cards)
    job = _initialize_job(lot_id=lot_id, lot_name=lot_name, cards=cards)
    job_id = str(job["job_id"])

    with _JOB_LOCK:
        _JOB_STORE[job_id] = job

    worker = threading.Thread(
        target=_run_import_job,
        args=(job_id, cards, request),
        daemon=True,
        name=f"lot-import-{job_id[:8]}",
    )
    worker.start()

    return LotImportStartResponse(job_id=job_id, status="queued", total_cards=len(cards))


def get_lot_import(job_id: str) -> LotImportJobResponse:
    with _JOB_LOCK:
        job = _JOB_STORE.get(job_id)
        if not job:
            raise LotImportNotFound(f"Job {job_id} nao encontrado")
        snapshot = copy.deepcopy(job)

    return _to_job_response(snapshot)
