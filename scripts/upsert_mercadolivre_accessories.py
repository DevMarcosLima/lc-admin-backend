from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.config import get_settings
from app.schemas.store import StoreProduct
from app.services.firestore_admin import CATEGORY_SUBCOLLECTION, get_firestore_client

SOURCE_NOTE_DATE = "2026-04-09"
MARKUP_MULTIPLIER = 1.25


@dataclass(frozen=True)
class MarketplaceAccessory:
    slug: str
    name: str
    accessory_kind: str
    source_price_brl: float
    source_url: str
    image_url: str
    stock: int


ACCESSORIES: list[MarketplaceAccessory] = [
    MarketplaceAccessory(
        slug="ml-pelucia-charmander-23cm",
        name="Pelúcia Charmander 23cm",
        accessory_kind="plush",
        source_price_brl=38.45,
        source_url=(
            "https://www.mercadolivre.com.br/pelucia-charmander-23cm-pokemon-premium-"
            "brinquedo-pokemon-go-cor-laranja/p/MLB65217427"
        ),
        image_url="https://http2.mlstatic.com/D_Q_NP_2X_964885-MLA105879846452_022026-E.webp",
        stock=18,
    ),
    MarketplaceAccessory(
        slug="ml-pelucia-bulbasaur-20cm",
        name="Pelúcia Bulbasaur 20cm",
        accessory_kind="plush",
        source_price_brl=41.13,
        source_url=(
            "https://www.mercadolivre.com.br/pelucia-pokemon-bulbassauro-bulbasaur-20cm-"
            "antialergico-bulbasaur/p/MLB66603513"
        ),
        image_url="https://http2.mlstatic.com/D_Q_NP_2X_819795-MLA107900833806_032026-E.webp",
        stock=16,
    ),
    MarketplaceAccessory(
        slug="ml-pelucia-mimikyu-20cm",
        name="Pelúcia Mimikyu 20cm",
        accessory_kind="plush",
        source_price_brl=52.00,
        source_url="https://www.mercadolivre.com.br/pelucia-pokemon-mimikyu-mimikkyu-20cm/p/MLB24784959",
        image_url="https://http2.mlstatic.com/D_Q_NP_2X_690335-MLA100093845405_122025-E.webp",
        stock=12,
    ),
    MarketplaceAccessory(
        slug="ml-caneca-pokemon-personagens",
        name="Caneca Pokémon Personagens",
        accessory_kind="cup",
        source_price_brl=22.21,
        source_url="https://www.mercadolivre.com.br/caneca-pokemon-personagens--mega-oferta/up/MLBU2723363818",
        image_url="https://http2.mlstatic.com/D_Q_NP_2X_838670-MLB75909188661_042024-E.webp",
        stock=35,
    ),
    MarketplaceAccessory(
        slug="ml-caneca-anime-ash-personagens",
        name="Caneca Anime Pokémon Ash e Personagens",
        accessory_kind="cup",
        source_price_brl=25.90,
        source_url="https://www.mercadolivre.com.br/caneca-anime-pokemon-ash-e-personagens/up/MLBU1989986302",
        image_url="https://http2.mlstatic.com/D_Q_NP_2X_645706-MLB74991868668_032024-E.webp",
        stock=30,
    ),
    MarketplaceAccessory(
        slug="ml-caneca-pikachu-325ml",
        name="Caneca Pikachu Pokémon 325ml",
        accessory_kind="cup",
        source_price_brl=29.99,
        source_url="https://www.mercadolivre.com.br/caneca-pikachu-pokemon-325ml-ceramica-xicara--caixa-brinde/up/MLBU1435574239",
        image_url="https://http2.mlstatic.com/D_Q_NP_2X_924571-MLB52222954971_102022-E.webp",
        stock=28,
    ),
    MarketplaceAccessory(
        slug="ml-kit-6-pins-pokemon",
        name="Kit 6 Pins Pokémon para Babuche",
        accessory_kind="pin",
        source_price_brl=20.00,
        source_url="https://www.mercadolivre.com.br/kit-6-pins-bottons-emborrachados-para-babuches-pokemon/p/MLB62478088",
        image_url="https://http2.mlstatic.com/D_Q_NP_2X_962014-MLA99299664915_112025-E.webp",
        stock=40,
    ),
    MarketplaceAccessory(
        slug="ml-pin-crocs-pokemon",
        name="Pin Pokémon para Crocs",
        accessory_kind="pin",
        source_price_brl=20.96,
        source_url="https://www.mercadolivre.com.br/pin-pokemon-para-crocs-botton-broche-botao-sapato-charm/up/MLBU3000874866",
        image_url="https://http2.mlstatic.com/D_Q_NP_2X_644632-MLB81973951890_022025-E.webp",
        stock=42,
    ),
    MarketplaceAccessory(
        slug="ml-bottons-eevee-kit-9",
        name="Bottons Eevee Evolutions (kit 9)",
        accessory_kind="pin",
        source_price_brl=27.99,
        source_url="https://www.mercadolivre.com.br/botons-eevee-eeveelutions-pokemons-broches-pins-button-kit-9/up/MLBU3823785186",
        image_url="https://http2.mlstatic.com/D_Q_NP_2X_600951-MLB107902941191_032026-E.webp",
        stock=24,
    ),
]


def _price_with_markup(value: float) -> float:
    return round(value * MARKUP_MULTIPLIER, 2)


def _to_store_product(item: MarketplaceAccessory) -> StoreProduct:
    store_price = _price_with_markup(item.source_price_brl)
    return StoreProduct(
        slug=item.slug,
        name=item.name,
        product_type="accessory",
        accessory_kind=item.accessory_kind,
        category="Acessórios Pokémon",
        season_tags=["acessorios", "mercado-livre", "reposicao-estoque"],
        stock=item.stock,
        price_brl=store_price,
        image_url=item.image_url,
        image_gallery=[],
        description=(
            "Produto de apoio para reposição de estoque. "
            f"Preço base Mercado Livre em {SOURCE_NOTE_DATE}: R$ {item.source_price_brl:.2f}."
        ),
        observations=(
            f"Link de compra (fornecedor): {item.source_url} | "
            f"Markup aplicado no site: +25% (R$ {store_price:.2f})."
        ),
    )


def _upsert_product(product: StoreProduct) -> None:
    settings = get_settings()
    client = get_firestore_client()
    catalog_ref = client.collection(settings.firestore_collection_products)

    now_iso = datetime.now(UTC).isoformat()
    bucket = "accessories"

    category_ref = catalog_ref.document(bucket)
    category_ref.set({"id": bucket, "updated_at": now_iso}, merge=True)

    doc_ref = category_ref.collection(CATEGORY_SUBCOLLECTION).document(product.slug)
    existing = doc_ref.get()
    created_at = str((existing.to_dict() or {}).get("created_at") or now_iso) if existing.exists else now_iso

    payload = product.model_dump()
    payload["bucket"] = bucket
    payload["created_at"] = created_at
    payload["updated_at"] = now_iso
    payload["source_marketplace"] = "mercado_livre"
    payload["source_price_brl"] = round(float(product.price_brl) / MARKUP_MULTIPLIER, 2)
    doc_ref.set(payload, merge=True)


def main() -> None:
    upserted = 0
    for item in ACCESSORIES:
        product = _to_store_product(item)
        _upsert_product(product)
        upserted += 1
        print(
            "upserted",
            product.slug,
            f"market=R$ {item.source_price_brl:.2f}",
            f"site=R$ {product.price_brl:.2f}",
        )

    print(f"Concluido: {upserted} acessorios atualizados com markup de 25%.")


if __name__ == "__main__":
    main()
