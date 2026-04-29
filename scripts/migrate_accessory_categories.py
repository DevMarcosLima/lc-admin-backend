from __future__ import annotations

import argparse
from datetime import UTC, datetime

from app.core.config import get_settings
from app.services.firestore_admin import (
    CATEGORY_SUBCOLLECTION,
    _canonicalize_accessory_category,
    _normalize_text_key,
    get_firestore_client,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Migra produtos de acessorio para usar categoria canonica "
            "(Pelúcia/Boton/Copo) e normaliza accessory_kind (plush/pin/cup)."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Aplica alteracoes no Firestore. Sem essa flag roda em dry-run.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limita a quantidade de docs processados (debug).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Exibe detalhes por documento alterado.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = get_settings()
    client = get_firestore_client()
    now_iso = datetime.now(UTC).isoformat()

    scanned = 0
    accessories_scanned = 0
    changed = 0
    unchanged = 0
    skipped_without_mapping = 0
    updated = 0

    unresolved_slugs: list[str] = []

    catalog_ref = client.collection(settings.firestore_collection_products)
    for bucket_doc in catalog_ref.stream(timeout=90):
        items_ref = bucket_doc.reference.collection(CATEGORY_SUBCOLLECTION)
        for snapshot in items_ref.stream(timeout=90):
            if args.limit is not None and scanned >= args.limit:
                break

            scanned += 1
            payload = snapshot.to_dict() or {}
            product_type = _normalize_text_key(str(payload.get("product_type") or ""))
            if product_type != "accessory":
                continue

            accessories_scanned += 1
            slug = str(payload.get("slug") or snapshot.id)
            current_category = str(payload.get("category") or "").strip()
            current_accessory_kind = payload.get("accessory_kind")

            next_category, inferred_kind = _canonicalize_accessory_category(
                category=current_category,
                accessory_kind=str(current_accessory_kind or ""),
                product_name=str(payload.get("name") or ""),
                product_slug=slug,
            )

            if not next_category:
                skipped_without_mapping += 1
                unresolved_slugs.append(slug)
                if args.verbose:
                    print(
                        "[sem-mapeamento]",
                        f"slug={slug}",
                        f"path={snapshot.reference.path}",
                        f"category={current_category!r}",
                        f"accessory_kind={current_accessory_kind!r}",
                    )
                continue

        patch: dict[str, object] = {}
        if current_category != next_category:
            patch["category"] = next_category

        if (str(current_accessory_kind or "").strip().lower() != str(inferred_kind or "").strip().lower()):
            patch["accessory_kind"] = inferred_kind

            if not patch:
                unchanged += 1
                continue

            changed += 1
            if args.verbose:
                print(
                    "[alteracao]",
                    f"slug={slug}",
                    f"path={snapshot.reference.path}",
                    f"category: {current_category!r} -> {next_category!r}",
                    f"inferido={inferred_kind!r}",
                    f"accessory_kind: {current_accessory_kind!r} -> {inferred_kind!r}",
                )

            if not args.apply:
                continue

            patch["updated_at"] = now_iso
            snapshot.reference.set(patch, merge=True)
            updated += 1

        if args.limit is not None and scanned >= args.limit:
            break

    print(
        "Migracao de categorias de acessorio concluida.",
        f"modo={'apply' if args.apply else 'dry-run'}",
        f"scanned={scanned}",
        f"accessories={accessories_scanned}",
        f"changed={changed}",
        f"updated={updated}",
        f"unchanged={unchanged}",
        f"skipped_without_mapping={skipped_without_mapping}",
    )

    if unresolved_slugs:
        unique_slugs = sorted(set(unresolved_slugs))
        preview = ", ".join(unique_slugs[:20])
        suffix = " ..." if len(unique_slugs) > 20 else ""
        print(
            "Itens sem mapeamento automatico:",
            f"total={len(unique_slugs)}",
            f"slugs={preview}{suffix}",
        )


if __name__ == "__main__":
    main()
