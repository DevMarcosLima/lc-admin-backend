from types import SimpleNamespace

from app.services.lot_import import _build_product_payload, _candidate_number_match_score


def _metadata_stub(**overrides):
    base = {
        "name": "Absol",
        "number": "63",
        "local_number": "063/094",
        "printed_total": 94,
        "set_name": "Phantasmal Flames",
        "set_series": "Scarlet & Violet",
        "set_code": "pfl",
        "rarity": "Rare",
        "release_year": 2025,
        "pokemon_generation": "generation-ix",
        "pokemon_types": ["Darkness"],
        "regulation_mark": "H",
        "image_large": "https://example.com/absol-large.jpg",
        "image_small": "https://example.com/absol-small.jpg",
        "suggested_price_brl": 6.5,
        "suggested_price_usd": None,
        "usd_brl_rate": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_candidate_number_match_score_prioritizes_full_number() -> None:
    metadata = _metadata_stub()
    assert _candidate_number_match_score(metadata, "063/094") == 4
    assert _candidate_number_match_score(metadata, "63/94") == 4
    assert _candidate_number_match_score(metadata, "063") >= 2


def test_build_product_payload_uses_api_name_when_number_matches() -> None:
    metadata = _metadata_stub(name="Absol")
    payload = _build_product_payload(
        raw_card={
            "name": "Carta Errada",
            "number": "063/094",
            "language": "PT",
            "quantity": 2,
            "details": "reverse holo",
        },
        lot_id="lote-1",
        metadata=metadata,
        default_condition="Near Mint (NM)",
        default_finish="Normal",
        default_category="Cartas avulsas",
    )

    assert payload["name"] == "Absol"
    assert payload["card_number"] == "063/094"
    assert payload["stock"] == 2


def test_build_product_payload_keeps_original_name_when_number_does_not_match() -> None:
    metadata = _metadata_stub(
        number="120",
        local_number="120/165",
        printed_total=165,
        name="Pikachu",
    )
    payload = _build_product_payload(
        raw_card={
            "name": "Carta Lote Original",
            "number": "063/094",
            "language": "PT",
            "quantity": 1,
            "details": "normal",
        },
        lot_id="lote-2",
        metadata=metadata,
        default_condition="Near Mint (NM)",
        default_finish="Normal",
        default_category="Cartas avulsas",
    )

    assert payload["name"] == "Carta Lote Original"
