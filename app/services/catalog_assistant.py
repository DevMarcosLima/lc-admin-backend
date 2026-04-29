from __future__ import annotations

import json
import re
import statistics
from collections import defaultdict
from typing import Any

from app.core.config import get_settings
from app.schemas.store import (
    CardLookupItem,
    CatalogAssistantAction,
    CatalogAssistantFinding,
    CatalogAssistantResponse,
    CatalogAssistantRunRequest,
    CatalogAssistantSeverity,
    StoreProduct,
)
from app.services.card_catalog import CardCatalogError, search_cards
from app.services.firestore_admin import fetch_products_from_firestore, upsert_product

_VALID_REGULATION_MARK = re.compile(r"^[A-I]$")
_VALID_CARD_NUMBER = re.compile(r"^\d{1,4}/\d{1,4}$")

_SPECIAL_FINISH_KEYWORDS = (
    "master ball",
    "pokeball",
    "poke ball",
    "reverse holo element",
    "full art",
)


class CatalogAssistantError(RuntimeError):
    pass


def _normalize_text(value: str | None) -> str:
    return (value or "").strip().lower()


def _normalize_card_number(value: str | None) -> str:
    raw = (value or "").strip().replace(" ", "")
    if not raw:
        return ""
    if "/" not in raw:
        return raw.lstrip("0") or "0"

    local, total = raw.split("/", 1)
    return f"{local.lstrip('0') or '0'}/{total.lstrip('0') or '0'}"


def _card_base_key(product: StoreProduct) -> str:
    return "|".join(
        [
            _normalize_text(product.name),
            _normalize_card_number(product.card_number),
            _normalize_text(product.set_code),
            _normalize_text(product.language),
        ]
    )


def _is_card(product: StoreProduct) -> bool:
    return product.product_type == "single_card"


def _is_special_finish(finish_raw: str | None) -> bool:
    normalized = _normalize_text(finish_raw)
    if not normalized:
        return False
    return any(keyword in normalized for keyword in _SPECIAL_FINISH_KEYWORDS)


def _safe_delta_percent(current: float, suggested: float) -> float | None:
    if current <= 0:
        return None
    return round(((suggested - current) / current) * 100.0, 2)


def _price_delta_severity(delta_percent: float | None) -> CatalogAssistantSeverity:
    if delta_percent is None:
        return "medium"
    absolute = abs(delta_percent)
    if absolute >= 35:
        return "high"
    if absolute >= 15:
        return "medium"
    return "low"


def _build_finding(
    *,
    slug: str,
    severity: CatalogAssistantSeverity,
    title: str,
    message: str,
    current_price_brl: float | None = None,
    suggested_price_brl: float | None = None,
    delta_percent: float | None = None,
    tags: list[str] | None = None,
) -> CatalogAssistantFinding:
    return CatalogAssistantFinding(
        slug=slug,
        severity=severity,
        title=title,
        message=message,
        current_price_brl=current_price_brl,
        suggested_price_brl=suggested_price_brl,
        delta_percent=delta_percent,
        tags=tags or [],
    )


def _dedupe_findings(findings: list[CatalogAssistantFinding]) -> list[CatalogAssistantFinding]:
    seen: set[str] = set()
    output: list[CatalogAssistantFinding] = []
    for finding in findings:
        key = "|".join([finding.slug, finding.title, finding.message])
        if key in seen:
            continue
        seen.add(key)
        output.append(finding)
    return output


def _sort_findings(findings: list[CatalogAssistantFinding]) -> list[CatalogAssistantFinding]:
    severity_order = {"high": 0, "medium": 1, "low": 2}
    return sorted(
        findings,
        key=lambda item: (
            severity_order.get(item.severity, 3),
            item.slug,
            item.title,
        ),
    )


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


def _compact_product_for_ai(product: StoreProduct) -> dict[str, Any]:
    return {
        "slug": product.slug,
        "name": product.name,
        "card_number": product.card_number,
        "set_name": product.set_name,
        "set_code": product.set_code,
        "rarity": product.rarity,
        "finish": product.finish,
        "condition": product.condition,
        "language": product.language,
        "stock": product.stock,
        "price_brl": product.price_brl,
        "lot_id": product.lot_id,
        "category": product.category,
    }


def _run_ai_review(
    *,
    action: CatalogAssistantAction,
    products: list[StoreProduct],
    existing_findings: list[CatalogAssistantFinding],
) -> tuple[list[CatalogAssistantFinding], str | None, list[str]]:
    settings = get_settings()
    if not settings.openai_api_key:
        return [], None, ["OPENAI_API_KEY ausente. Rodando apenas heuristicas locais."]

    try:
        from openai import OpenAI
    except ModuleNotFoundError:
        return [], None, ["SDK da OpenAI nao instalada no backend."]

    model = settings.openai_catalog_assistant_model.strip() or "gpt-5-nano"
    max_products = max(20, int(settings.openai_catalog_max_products))
    max_findings = max(5, int(settings.openai_catalog_max_findings))

    compact_products = [_compact_product_for_ai(item) for item in products[:max_products]]
    truncated = len(products) > len(compact_products)

    compact_findings = [
        {
            "slug": item.slug,
            "severity": item.severity,
            "title": item.title,
            "message": item.message,
            "delta_percent": item.delta_percent,
        }
        for item in existing_findings[: max_findings * 2]
    ]

    client = OpenAI(api_key=settings.openai_api_key)
    warnings: list[str] = []
    try:
        response = client.responses.create(
            model=model,
            input=[
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Retorne somente JSON valido. "
                                "Voce e um auditor de catalogo Pokemon TCG para ecommerce. "
                                "Nao invente dados externos. "
                                "Foque em inconsistencias de nome, set, "
                                "raridade, acabamento, idioma, estoque e preco. "
                                "Use severidades high/medium/low."
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
                                "Acao: "
                                + action
                                + "\n"
                                + "Retorne JSON no formato:\n"
                                + '{"summary":"texto","findings":[{"slug":"...","severity":"high",'
                                + '"title":"...","message":"...","suggested_price_brl":null,'
                                + '"tags":["tag"]}]}\n'
                                + "Produtos selecionados:\n"
                                + json.dumps(compact_products, ensure_ascii=False)
                                + "\nHeuristicas locais ja encontradas:\n"
                                + json.dumps(compact_findings, ensure_ascii=False)
                            ),
                        }
                    ],
                },
            ],
        )
    except Exception as exc:  # noqa: BLE001
        return [], None, [f"Falha ao consultar IA ({model}): {exc}"]

    payload = _extract_json_object(response.output_text)
    if not payload:
        return [], None, [f"IA ({model}) retornou resposta sem JSON aproveitavel."]

    summary_raw = payload.get("summary")
    summary = str(summary_raw).strip() if isinstance(summary_raw, str) else None
    ai_findings_payload = payload.get("findings")
    if not isinstance(ai_findings_payload, list):
        ai_findings_payload = []

    allowed_slugs = {item.slug for item in products}
    ai_findings: list[CatalogAssistantFinding] = []
    for row in ai_findings_payload[:max_findings]:
        if not isinstance(row, dict):
            continue
        slug = str(row.get("slug") or "").strip()
        if not slug or slug not in allowed_slugs:
            continue

        severity_raw = str(row.get("severity") or "low").strip().lower()
        severity: CatalogAssistantSeverity = (
            severity_raw if severity_raw in {"high", "medium", "low"} else "low"
        )
        title = str(row.get("title") or "Achado de IA").strip()[:120] or "Achado de IA"
        message = str(row.get("message") or "").strip()
        if not message:
            continue

        suggested_price = row.get("suggested_price_brl")
        suggested_price_brl = (
            float(suggested_price)
            if isinstance(suggested_price, (int, float)) and float(suggested_price) > 0
            else None
        )
        tags_raw = row.get("tags")
        tags = (
            [str(item).strip() for item in tags_raw if str(item).strip()]
            if isinstance(tags_raw, list)
            else []
        )

        ai_findings.append(
            _build_finding(
                slug=slug,
                severity=severity,
                title=title,
                message=message,
                suggested_price_brl=suggested_price_brl,
                tags=tags,
            )
        )

    if truncated:
        warnings.append(
            "Amostra enviada para IA foi truncada para "
            f"{len(compact_products)} produtos por custo/performance."
        )

    return ai_findings, summary, warnings


def _run_price_outliers(
    products: list[StoreProduct],
) -> tuple[list[CatalogAssistantFinding], list[str]]:
    findings: list[CatalogAssistantFinding] = []
    warnings: list[str] = []

    cards = [item for item in products if _is_card(item)]
    by_base: dict[str, list[StoreProduct]] = defaultdict(list)
    for card in cards:
        by_base[_card_base_key(card)].append(card)

    for card in cards:
        if card.stock > 0 and card.price_brl <= 0:
            findings.append(
                _build_finding(
                    slug=card.slug,
                    severity="high",
                    title="Preco zerado com estoque ativo",
                    message=(
                        "Carta com estoque maior que zero e preco atual em R$ 0,00. "
                        "Isso pode gerar venda com valor incorreto."
                    ),
                    current_price_brl=card.price_brl,
                    tags=["price_zero", "stock_active"],
                )
            )

    for base_items in by_base.values():
        if len(base_items) < 2:
            continue

        normal_items = [
            item
            for item in base_items
            if "normal" in _normalize_text(item.finish) or not _normalize_text(item.finish)
        ]
        if not normal_items:
            continue

        normal_prices = [item.price_brl for item in normal_items if item.price_brl > 0]
        if not normal_prices:
            continue
        normal_reference = statistics.median(normal_prices)
        if normal_reference <= 0:
            continue

        for item in base_items:
            if item in normal_items:
                continue

            finish_label = item.finish or "Acabamento especial"
            current_price = float(item.price_brl or 0.0)
            if current_price <= 0:
                continue

            if _is_special_finish(item.finish) and current_price <= (normal_reference * 1.05):
                delta = _safe_delta_percent(normal_reference, current_price)
                findings.append(
                    _build_finding(
                        slug=item.slug,
                        severity="high",
                        title="Acabamento especial com preco muito proximo do normal",
                        message=(
                            f"{finish_label} com preco de {current_price:.2f} e referencia normal "
                            f"de {normal_reference:.2f}."
                        ),
                        current_price_brl=current_price,
                        suggested_price_brl=round(normal_reference * 1.2, 2),
                        delta_percent=delta,
                        tags=["special_finish", "pricing_gap"],
                    )
                )
            elif current_price < normal_reference:
                delta = _safe_delta_percent(normal_reference, current_price)
                findings.append(
                    _build_finding(
                        slug=item.slug,
                        severity="medium",
                        title="Variante abaixo do preco da versao normal",
                        message=(
                            f"{finish_label} esta abaixo da referencia normal para a mesma carta. "
                            "Vale revisar para evitar subprecificacao."
                        ),
                        current_price_brl=current_price,
                        suggested_price_brl=round(normal_reference, 2),
                        delta_percent=delta,
                        tags=["variant_pricing"],
                    )
                )

    buckets: dict[str, list[StoreProduct]] = defaultdict(list)
    for card in cards:
        if card.price_brl > 0:
            bucket_key = "|".join(
                [
                    _normalize_text(card.set_name),
                    _normalize_text(card.rarity),
                    _normalize_text(card.finish),
                ]
            )
            buckets[bucket_key].append(card)

    for bucket_items in buckets.values():
        if len(bucket_items) < 6:
            continue
        prices = [item.price_brl for item in bucket_items if item.price_brl > 0]
        if len(prices) < 6:
            continue
        median_price = statistics.median(prices)
        if median_price <= 0:
            continue
        for item in bucket_items:
            deviation = abs(item.price_brl - median_price) / median_price
            if deviation < 2.2:
                continue
            findings.append(
                _build_finding(
                    slug=item.slug,
                    severity="low",
                    title="Preco fora da faixa da categoria",
                    message=(
                        "Preco muito distante da mediana para set/raridade/acabamento "
                        "equivalentes. Pode ser excecao valida, mas vale revisar."
                    ),
                    current_price_brl=item.price_brl,
                    suggested_price_brl=round(median_price, 2),
                    delta_percent=round((deviation * 100.0), 2),
                    tags=["bucket_outlier"],
                )
            )

    if not cards:
        warnings.append("Nenhuma carta avulsa encontrada para analisar preco.")

    return findings, warnings


def _run_card_inconsistencies(
    products: list[StoreProduct],
) -> tuple[list[CatalogAssistantFinding], list[str]]:
    findings: list[CatalogAssistantFinding] = []
    warnings: list[str] = []
    cards = [item for item in products if _is_card(item)]

    for card in cards:
        if not _normalize_text(card.card_number):
            findings.append(
                _build_finding(
                    slug=card.slug,
                    severity="high",
                    title="Carta sem numeracao",
                    message="Preencha `card_number` para manter rastreabilidade no catalogo.",
                    tags=["missing_card_number"],
                )
            )
        elif not _VALID_CARD_NUMBER.fullmatch((card.card_number or "").strip()):
            findings.append(
                _build_finding(
                    slug=card.slug,
                    severity="medium",
                    title="Formato de numeracao fora do padrao",
                    message="Use formato `XXX/YYY` na numeracao da carta.",
                    tags=["invalid_card_number_format"],
                )
            )

        if not _normalize_text(card.set_name):
            findings.append(
                _build_finding(
                    slug=card.slug,
                    severity="medium",
                    title="Carta sem nome de set",
                    message="Set vazio dificulta filtros e comparacao de mercado.",
                    tags=["missing_set_name"],
                )
            )

        if not _normalize_text(card.finish):
            findings.append(
                _build_finding(
                    slug=card.slug,
                    severity="medium",
                    title="Carta sem acabamento",
                    message="Defina o acabamento (Normal, Holo, Reverse etc.).",
                    tags=["missing_finish"],
                )
            )

        if not _normalize_text(card.condition):
            findings.append(
                _build_finding(
                    slug=card.slug,
                    severity="medium",
                    title="Carta sem condicao",
                    message="Defina condicao (NM, MP, HP...) para transparencia de venda.",
                    tags=["missing_condition"],
                )
            )

        mark = _normalize_text(card.regulation_mark).upper()
        if mark and not _VALID_REGULATION_MARK.fullmatch(mark):
            findings.append(
                _build_finding(
                    slug=card.slug,
                    severity="low",
                    title="Regulation mark invalido",
                    message="Regulation mark deve ficar entre A e I ou vazio.",
                    tags=["invalid_regulation_mark"],
                )
            )

        if not _normalize_text(card.lot_id):
            findings.append(
                _build_finding(
                    slug=card.slug,
                    severity="low",
                    title="Carta sem lot_id",
                    message="Sem lote fica mais dificil separar fisicamente no estoque.",
                    tags=["missing_lot_id"],
                )
            )

    identities: dict[str, list[StoreProduct]] = defaultdict(list)
    for card in cards:
        identity_key = "|".join(
            [
                _normalize_card_number(card.card_number),
                _normalize_text(card.set_code),
                _normalize_text(card.language),
                _normalize_text(card.finish),
                _normalize_text(card.condition),
            ]
        )
        if identity_key:
            identities[identity_key].append(card)

    for same_cards in identities.values():
        if len(same_cards) < 2:
            continue

        name_set = {_normalize_text(item.name) for item in same_cards}
        rarity_set = {
            _normalize_text(item.rarity)
            for item in same_cards
            if _normalize_text(item.rarity)
        }

        if len(name_set) > 1:
            for item in same_cards:
                findings.append(
                    _build_finding(
                        slug=item.slug,
                        severity="high",
                        title="Identidade igual com nomes divergentes",
                        message=(
                            "Mesmo numero/set/idioma/acabamento/condicao com nomes diferentes. "
                            "Revisar possivel inconsistencia de cadastro."
                        ),
                        tags=["identity_name_conflict"],
                    )
                )

        if len(rarity_set) > 1:
            for item in same_cards:
                findings.append(
                    _build_finding(
                        slug=item.slug,
                        severity="medium",
                        title="Identidade igual com raridades divergentes",
                        message=(
                            "Mesmo cadastro base com raridades diferentes. "
                            "Verifique qual valor esta correto."
                        ),
                        tags=["identity_rarity_conflict"],
                    )
                )

    if not cards:
        warnings.append("Nenhuma carta avulsa encontrada para validar consistencia.")

    return findings, warnings


def _score_lookup_candidate(product: StoreProduct, candidate: CardLookupItem) -> int:
    score = 0
    product_name = _normalize_text(product.name)
    candidate_name = _normalize_text(candidate.name)
    if product_name == candidate_name:
        score += 6
    elif product_name and product_name in candidate_name:
        score += 3

    wanted_number = _normalize_card_number(product.card_number)
    candidate_local = _normalize_card_number(candidate.local_number)
    candidate_number = _normalize_card_number(candidate.number)
    if wanted_number and wanted_number == candidate_local:
        score += 8
    elif wanted_number and wanted_number == candidate_number:
        score += 6
    elif (
        wanted_number
        and "/" in wanted_number
        and wanted_number.split("/", 1)[0] == candidate_number
    ):
        score += 4

    if _normalize_text(product.set_code) and _normalize_text(product.set_code) == _normalize_text(
        candidate.set_code
    ):
        score += 4

    if _normalize_text(product.set_name) and _normalize_text(product.set_name) == _normalize_text(
        candidate.set_name
    ):
        score += 3

    if candidate.suggested_price_brl and candidate.suggested_price_brl > 0:
        score += 1

    return score


def _run_refresh_market_prices(
    products: list[StoreProduct], auto_apply: bool
) -> tuple[list[CatalogAssistantFinding], list[str], int]:
    findings: list[CatalogAssistantFinding] = []
    warnings: list[str] = []
    updated_count = 0

    cards = [item for item in products if _is_card(item)]
    if not cards:
        return [], ["Selecione cartas avulsas para atualizar preco de mercado."], 0

    for card in cards:
        query_parts = [card.name.strip()]
        if card.card_number:
            query_parts.append(card.card_number.strip())
        if card.set_code:
            query_parts.append(card.set_code.strip())
        query = " ".join(part for part in query_parts if part)
        if not query:
            warnings.append(f"{card.slug}: sem dados minimos para consulta de mercado.")
            continue

        try:
            candidates = search_cards(query=query, limit=8)
        except CardCatalogError as exc:
            warnings.append(f"{card.slug}: erro ao consultar mercado ({exc}).")
            continue

        if not candidates:
            warnings.append(f"{card.slug}: sem resultados de mercado.")
            continue

        best = max(candidates, key=lambda item: _score_lookup_candidate(card, item))
        suggested_price = float(best.suggested_price_brl or 0.0)
        if suggested_price <= 0:
            warnings.append(f"{card.slug}: API nao retornou preco sugerido para essa carta.")
            continue

        current_price = float(card.price_brl or 0.0)
        delta = _safe_delta_percent(current_price, suggested_price)
        tags = ["market_price"]

        if auto_apply and (delta is None or abs(delta) >= 1):
            updated = card.model_copy(update={"price_brl": round(suggested_price, 2)})
            upsert_product(updated)
            updated_count += 1
            tags.append("updated")

        findings.append(
            _build_finding(
                slug=card.slug,
                severity=_price_delta_severity(delta),
                title="Preco de mercado consultado",
                message=(
                    f"Referencia via {best.suggested_price_source or 'API'} para "
                    f"{best.name} ({best.local_number or best.number})."
                ),
                current_price_brl=current_price,
                suggested_price_brl=round(suggested_price, 2),
                delta_percent=delta,
                tags=tags,
            )
        )

    return findings, warnings, updated_count


def run_catalog_assistant(request: CatalogAssistantRunRequest) -> CatalogAssistantResponse:
    all_products = fetch_products_from_firestore()
    selected_slug_set = {slug.strip() for slug in request.slugs if slug.strip()}

    if selected_slug_set:
        selected_products = [item for item in all_products if item.slug in selected_slug_set]
    else:
        selected_products = list(all_products)

    if not request.include_non_cards and request.action != "refresh_market_prices":
        scanned_products = [item for item in selected_products if _is_card(item)]
    else:
        scanned_products = list(selected_products)

    findings: list[CatalogAssistantFinding]
    warnings: list[str]
    updated_count = 0
    ai_summary: str | None = None
    model: str | None = None

    if request.action == "find_price_outliers":
        findings, warnings = _run_price_outliers(scanned_products)
    elif request.action == "find_card_inconsistencies":
        findings, warnings = _run_card_inconsistencies(scanned_products)
    elif request.action == "refresh_market_prices":
        findings, warnings, updated_count = _run_refresh_market_prices(
            products=[item for item in selected_products if _is_card(item)],
            auto_apply=request.auto_apply,
        )
    else:
        raise CatalogAssistantError("Acao de assistente invalida.")

    if request.action in {"find_price_outliers", "find_card_inconsistencies"}:
        model = get_settings().openai_catalog_assistant_model
        ai_findings, ai_summary, ai_warnings = _run_ai_review(
            action=request.action,
            products=scanned_products,
            existing_findings=findings,
        )
        findings.extend(ai_findings)
        warnings.extend(ai_warnings)

    normalized_findings = _sort_findings(_dedupe_findings(findings))
    return CatalogAssistantResponse(
        action=request.action,
        model=model,
        selected_products=len(selected_products),
        scanned_products=len(scanned_products),
        updated_count=updated_count,
        findings=normalized_findings,
        ai_summary=ai_summary,
        warnings=warnings,
    )
