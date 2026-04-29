from __future__ import annotations

from datetime import UTC, datetime

from app.schemas.store import StoreProduct
from app.services.firestore_admin import CATEGORY_SUBCOLLECTION, fetch_products_from_firestore, get_firestore_client
from app.core.config import get_settings

# Slugs de produtos de exemplo antigos usados na fase inicial.
EXAMPLE_SLUGS = {
    "equilibrio-perfeito-premium-collection",
    "charizard-base-set-4-102",
    "blastoise-base-set-2-102",
    "venusaur-base-set-15-102",
    "booster-evolucoes-prismaticas",
    "booster-megaevolucao-rising",
    "blister-triplo-prismatic",
    "blister-duplo-mega",
    "box-colecionavel-mew-ex",
    "box-colecionavel-charizard-ex",
    "box-treinador-evolucoes-prismaticas",
    "box-treinador-megaevolucao",
    "lata-lendaria-miraidon",
    "lata-lendaria-koraidon",
    "pelucia-pikachu-classic",
    "boton-set-kanto",
    "copo-termico-pokemon",
}

SOURCE_RELEASE_URL = (
    "https://press.pokemon.com/en/releases/"
    "MEDIA-ALERT-Secret-Powers-Awaken-as-Pokemon-Trading-Card-Game-Mega-Evo"
)

BOOSTER_WRAP_CLEFABLE = (
    "https://imguscdn.gamespress.com/cdn/files/PokemonAmerica/2026/01/071358-822d09f7/"
    "Pokemon_TCG_Mega_Evolution%E2%80%94Perfect_Order_Booster_Wrap_Mega_Clefable.png"
    "?otf=y&lightbox=y&sky=029066bcd46af70e7b6acc73612c12667e645618e4c989be6de385d8fc4fda85"
)
BOOSTER_WRAP_STARMIE = (
    "https://imguscdn.gamespress.com/cdn/files/PokemonAmerica/2026/01/071358-822d09f7/"
    "Pokemon_TCG_Mega_Evolution%E2%80%94Perfect_Order_Booster_Wrap_Mega_Starmie.png"
    "?otf=y&lightbox=y&sky=a2f94a8ca8dd17ea6f5e2b07630afa6195504d87723b152b824298c8aa725eec"
)
BOOSTER_WRAP_ZYGARDE = (
    "https://imguscdn.gamespress.com/cdn/files/PokemonAmerica/2026/01/071358-822d09f7/"
    "Pokemon_TCG_Mega_Evolution%E2%80%94Perfect_Order_Booster_Wrap_Mega_Zygarde.png"
    "?otf=y&lightbox=y&sky=53ae647c287573fa60e30d7ce9935a95c6260a18b577ca6ab5e1320735b95651"
)
BOOSTER_WRAP_MEOWTH = (
    "https://imguscdn.gamespress.com/cdn/files/PokemonAmerica/2026/01/071358-822d09f7/"
    "Pokemon_TCG_Mega_Evolution%E2%80%94Perfect_Order_Booster_Wrap_Meowth.png"
    "?otf=y&lightbox=y&sky=589cde88ab3608b6e9c2c9bfb8a62d01d12b4596cdcff4d3c953b3082bc7650c"
)
ELITE_TRAINER_BOX = (
    "https://imguscdn.gamespress.com/cdn/files/PokemonAmerica/2026/01/071359-5355efab/"
    "Pokemon_TCG_Mega_Evolution%E2%80%94Perfect_Order_Elite_Trainer_Box.png"
    "?otf=y&lightbox=y&sky=3a788e3d4510f5d81b7773ef68178694d2f8ebe79508b0ef398e7ab2e1bc61cd"
)
KEY_ART = (
    "https://imguscdn.gamespress.com/cdn/files/PokemonAmerica/2026/03/252137-bf35d029/"
    "Pokemon_TCG_Live_Mega_Evolution%E2%80%94Perfect_Order_Key_Art.png"
    "?otf=y&lightbox=y&sky=3dd9ffdde98251ed9e2e00c7f1d9c4cfe31e461377c623f832621e70c63c4b88"
)
BATTLE_PASS_ART = (
    "https://imguscdn.gamespress.com/cdn/files/PokemonAmerica/2026/03/252137-bf35d029/"
    "Pokemon_TCG_Live_Mega_Evolution%E2%80%94Perfect_Order_Battle_Pass.png"
    "?otf=y&lightbox=y&sky=7c60c5cd320d4e73008ef9fb2b1f2ed03e351495acda8f440ce55664317e14a2"
)

PRODUCTS_TO_UPSERT = [
    StoreProduct(
        slug="megaevolucao-equilibrio-perfeito-booster-unidade",
        name="Booster Megaevolução — Equilíbrio Perfeito (unidade)",
        product_type="booster",
        category="Booster",
        season_tags=["equilibrio-perfeito", "megaevolucao", "perfect-order"],
        stock=120,
        price_brl=29.90,
        image_url=BOOSTER_WRAP_ZYGARDE,
        image_gallery=[BOOSTER_WRAP_CLEFABLE, BOOSTER_WRAP_STARMIE, BOOSTER_WRAP_MEOWTH],
        description=(
            "Booster unitário da coleção Megaevolução — Equilíbrio Perfeito. "
            "Arte oficial de mídia da The Pokémon Company International."
        ),
        observations=f"Fonte oficial de imagem: {SOURCE_RELEASE_URL}",
    ),
    StoreProduct(
        slug="megaevolucao-equilibrio-perfeito-colecao-treinador-avancado",
        name="Coleção Treinador Avançado Megaevolução — Equilíbrio Perfeito",
        product_type="trainer_box",
        category="Box de treinador",
        season_tags=["equilibrio-perfeito", "megaevolucao", "perfect-order"],
        stock=24,
        price_brl=399.90,
        image_url=ELITE_TRAINER_BOX,
        image_gallery=[],
        is_special=True,
        description=(
            "Coleção Treinador Avançado da expansão Megaevolução — Equilíbrio Perfeito. "
            "Imagem oficial de divulgação da The Pokémon Company International."
        ),
        observations=f"Fonte oficial de imagem: {SOURCE_RELEASE_URL}",
    ),
    StoreProduct(
        slug="megaevolucao-equilibrio-perfeito-box-colecionavel",
        name="Box Megaevolução — Equilíbrio Perfeito",
        product_type="collector_box",
        category="Box",
        season_tags=["equilibrio-perfeito", "megaevolucao", "perfect-order"],
        stock=12,
        price_brl=299.90,
        image_url=KEY_ART,
        image_gallery=[BATTLE_PASS_ART],
        description=(
            "Box da linha Megaevolução — Equilíbrio Perfeito para catálogo da loja. "
            "Imagem oficial de key art da The Pokémon Company International."
        ),
        observations=(
            "A mídia oficial disponível no press kit não inclui foto dedicada de booster display box; "
            "foi usado key art oficial da coleção. "
            f"Fonte: {SOURCE_RELEASE_URL}"
        ),
    ),
]


PRODUCT_BUCKET_MAP = {
    "single_card": "cards",
    "booster": "booster",
    "blister": "blister",
    "collector_box": "collector_box",
    "trainer_box": "trainer_box",
    "tin": "tin",
    "accessory": "accessories",
}


def _all_bucket_ids() -> list[str]:
    return sorted(set(PRODUCT_BUCKET_MAP.values()))


def _bucket_for_product(product: StoreProduct) -> str:
    return PRODUCT_BUCKET_MAP.get(product.product_type, product.product_type)


def _delete_slug_from_all_buckets(slug: str) -> bool:
    settings = get_settings()
    client = get_firestore_client()
    catalog_ref = client.collection(settings.firestore_collection_products)

    removed = False
    for bucket in _all_bucket_ids():
        doc_ref = catalog_ref.document(bucket).collection(CATEGORY_SUBCOLLECTION).document(slug)
        snapshot = doc_ref.get()
        if not snapshot.exists:
            continue
        doc_ref.delete()
        removed = True

    return removed


def _upsert_product(product: StoreProduct) -> None:
    settings = get_settings()
    client = get_firestore_client()
    catalog_ref = client.collection(settings.firestore_collection_products)

    now_iso = datetime.now(UTC).isoformat()
    bucket = _bucket_for_product(product)
    category_ref = catalog_ref.document(bucket)
    category_ref.set({"id": bucket, "updated_at": now_iso}, merge=True)

    doc_ref = category_ref.collection(CATEGORY_SUBCOLLECTION).document(product.slug)
    existing = doc_ref.get()
    created_at = str((existing.to_dict() or {}).get("created_at") or now_iso) if existing.exists else now_iso

    payload = product.model_dump()
    payload["bucket"] = bucket
    payload["created_at"] = created_at
    payload["updated_at"] = now_iso
    doc_ref.set(payload, merge=True)


def main() -> None:
    products = fetch_products_from_firestore()

    deleted = 0
    for product in products:
        if product.slug not in EXAMPLE_SLUGS:
            continue
        if _delete_slug_from_all_buckets(product.slug):
            deleted += 1

    upserted = 0
    for product in PRODUCTS_TO_UPSERT:
        _delete_slug_from_all_buckets(product.slug)
        _upsert_product(product)
        upserted += 1

    print(
        "Atualizacao de Equilibrio Perfeito concluida",
        f"deleted_examples={deleted}",
        f"upserted={upserted}",
    )


if __name__ == "__main__":
    main()
