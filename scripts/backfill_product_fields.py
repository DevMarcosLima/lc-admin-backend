from __future__ import annotations

from datetime import UTC, datetime

from app.core.config import get_settings
from app.services.firestore_admin import CATEGORY_SUBCOLLECTION, get_firestore_client

REQUIRED_FIELDS: dict[str, object] = {
    "pokemon_types": [],
    "description": None,
    "observations": None,
}


def main() -> None:
    settings = get_settings()
    client = get_firestore_client()

    scanned = 0
    updated = 0
    now_iso = datetime.now(UTC).isoformat()

    for snapshot in client.collection_group(CATEGORY_SUBCOLLECTION).stream():
        scanned += 1
        payload = snapshot.to_dict() or {}
        patch: dict[str, object] = {}

        for key, default_value in REQUIRED_FIELDS.items():
            if key not in payload:
                patch[key] = default_value

        if not patch:
            continue

        patch["updated_at"] = now_iso
        snapshot.reference.set(patch, merge=True)
        updated += 1

    print(
        "Backfill concluido",
        f"database={settings.firestore_database_id or '(default)'}",
        f"collection={settings.firestore_collection_products}",
        f"scanned={scanned}",
        f"updated={updated}",
    )


if __name__ == "__main__":
    main()
