from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from app.schemas.store import StoreProduct
from app.services import firestore_admin
from app.services.seller_accounts import SellerAccount


class _IndexRequiredQueryError(RuntimeError):
    pass


@dataclass
class FakeDocumentSnapshot:
    client: FakeFirestoreClient
    path: tuple[str, ...]
    payload: dict[str, Any] | None

    @property
    def id(self) -> str:
        return self.path[-1]

    @property
    def exists(self) -> bool:
        return self.payload is not None

    @property
    def reference(self) -> FakeDocumentRef:
        return FakeDocumentRef(self.client, self.path)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload or {})


@dataclass
class FakeDocumentRef:
    client: FakeFirestoreClient
    path: tuple[str, ...]

    @property
    def id(self) -> str:
        return self.path[-1]

    def collection(self, name: str) -> FakeCollectionRef:
        return FakeCollectionRef(self.client, self.path + (name,))

    def set(self, payload: dict[str, Any], merge: bool = False) -> None:
        existing = self.client.docs.get(self.path)
        if merge and isinstance(existing, dict):
            updated = dict(existing)
            updated.update(payload)
            self.client.docs[self.path] = updated
            return
        self.client.docs[self.path] = dict(payload)

    def get(self) -> FakeDocumentSnapshot:
        payload = self.client.docs.get(self.path)
        return FakeDocumentSnapshot(self.client, self.path, payload)

    def delete(self) -> None:
        self.client.docs.pop(self.path, None)


@dataclass
class FakeCollectionRef:
    client: FakeFirestoreClient
    path: tuple[str, ...]

    def document(self, doc_id: str) -> FakeDocumentRef:
        return FakeDocumentRef(self.client, self.path + (doc_id,))

    def stream(self):
        for doc_path, payload in sorted(self.client.docs.items()):
            if len(doc_path) == len(self.path) + 1 and doc_path[:-1] == self.path:
                yield FakeDocumentSnapshot(self.client, doc_path, payload)


@dataclass
class FakeCollectionGroupQuery:
    subcollection: str

    def where(self, *_args, **_kwargs):
        raise _IndexRequiredQueryError(
            "The query requires a COLLECTION_GROUP_ASC index for collection items and field slug."
        )

    def limit(self, _value: int):
        return self

    def stream(self):
        raise _IndexRequiredQueryError(
            "The query requires a COLLECTION_GROUP_ASC index for collection items and field slug."
        )


class FakeFirestoreClient:
    def __init__(self) -> None:
        self.docs: dict[tuple[str, ...], dict[str, Any]] = {}

    def collection(self, name: str) -> FakeCollectionRef:
        return FakeCollectionRef(self, (name,))

    def collection_group(self, subcollection: str) -> FakeCollectionGroupQuery:
        return FakeCollectionGroupQuery(subcollection)


def _fake_settings() -> SimpleNamespace:
    return SimpleNamespace(
        firestore_collection_products="store_products",
        firestore_collection_panel_settings="admin_panel_settings",
        legacy_store_name="Legacy Cards",
        legacy_store_slug="legacy-cards",
    )


def test_find_product_fallback_without_collection_group_index(monkeypatch) -> None:
    fake_client = FakeFirestoreClient()
    monkeypatch.setattr(firestore_admin, "get_settings", _fake_settings)
    monkeypatch.setattr(firestore_admin, "get_firestore_client", lambda: fake_client)

    created = firestore_admin.upsert_product(
        StoreProduct(
            slug="teste-blister-equilibrio",
            name="Blister Equilibrio",
            product_type="blister",
            category="Blister",
            stock=15,
            price_brl=29.9,
            image_url="https://example.com/blister.png",
            seller_template_enabled=True,
        )
    )
    located = firestore_admin._find_product_document(created.slug)
    assert located is not None
    bucket, _doc_ref, payload = located
    assert bucket == "blister"
    assert payload["slug"] == created.slug


def test_admin_template_to_seller_publish_flow(monkeypatch) -> None:
    fake_client = FakeFirestoreClient()
    monkeypatch.setattr(firestore_admin, "get_settings", _fake_settings)
    monkeypatch.setattr(firestore_admin, "get_firestore_client", lambda: fake_client)

    from app.services import seller_accounts

    monkeypatch.setattr(
        seller_accounts,
        "get_seller_account",
        lambda email: SellerAccount(
            email=email,
            shop_name="Loja Seller Alpha",
            shop_slug="seller-alpha",
            password_hash="hash",
            must_change_password=False,
            two_factor_enabled=True,
            totp_secret=None,
            status="active",
            created_at=None,
            updated_at=None,
            created_by="marcos_dev@icloud.com",
        ),
    )

    template = firestore_admin.upsert_product(
        StoreProduct(
            slug="blister-unidade-equilibrio",
            name="Blister unidade Equilibrio",
            product_type="blister",
            category="Blister",
            stock=50,
            price_brl=34.9,
            image_url="https://example.com/template.png",
            seller_template_enabled=True,
            allow_seller_custom_image=True,
        )
    )

    templates = firestore_admin.list_seller_templates()
    assert any(item.slug == template.slug for item in templates)

    published = firestore_admin.publish_seller_product_from_template(
        seller_email="seller.alpha@legacycards.com",
        template_slug=template.slug,
        quantity=3,
        use_template_image=False,
        custom_image_url="https://example.com/seller-own.png",
        price_brl=39.5,
    )
    assert published.owner_type == "seller"
    assert published.owner_seller_email == "seller.alpha@legacycards.com"
    assert published.source_template_slug == template.slug
    assert published.stock == 3
    assert published.image_url == "https://example.com/seller-own.png"
    assert published.store_name == "Loja Seller Alpha"
    assert published.store_slug == "seller-alpha"
    assert published.price_brl == 39.5

    seller_products = firestore_admin.list_products_by_seller(
        seller_email="seller.alpha@legacycards.com"
    )
    assert len(seller_products) == 1
    assert seller_products[0].slug == published.slug
    assert seller_products[0].stock == 3
    assert seller_products[0].price_brl == 39.5


def test_update_seller_product_price_from_template(monkeypatch) -> None:
    fake_client = FakeFirestoreClient()
    monkeypatch.setattr(firestore_admin, "get_settings", _fake_settings)
    monkeypatch.setattr(firestore_admin, "get_firestore_client", lambda: fake_client)

    from app.services import seller_accounts

    monkeypatch.setattr(
        seller_accounts,
        "get_seller_account",
        lambda email: SellerAccount(
            email=email,
            shop_name="Loja Seller Alpha",
            shop_slug="seller-alpha",
            password_hash="hash",
            must_change_password=False,
            two_factor_enabled=True,
            totp_secret=None,
            status="active",
            created_at=None,
            updated_at=None,
            created_by="marcos_dev@icloud.com",
        ),
    )

    template = firestore_admin.upsert_product(
        StoreProduct(
            slug="blister-unidade-equilibrio",
            name="Blister unidade Equilibrio",
            product_type="blister",
            category="Blister",
            stock=50,
            price_brl=34.9,
            image_url="https://example.com/template.png",
            seller_template_enabled=True,
            allow_seller_custom_image=True,
        )
    )

    firestore_admin.publish_seller_product_from_template(
        seller_email="seller.alpha@legacycards.com",
        template_slug=template.slug,
        quantity=5,
        use_template_image=True,
    )

    updated = firestore_admin.update_seller_product_price_from_template(
        seller_email="seller.alpha@legacycards.com",
        template_slug=template.slug,
        price_brl=49.9,
    )
    assert updated.price_brl == 49.9

    seller_products = firestore_admin.list_products_by_seller(
        seller_email="seller.alpha@legacycards.com"
    )
    assert len(seller_products) == 1
    assert seller_products[0].price_brl == 49.9


def test_withdraw_seller_stock_from_template(monkeypatch) -> None:
    fake_client = FakeFirestoreClient()
    monkeypatch.setattr(firestore_admin, "get_settings", _fake_settings)
    monkeypatch.setattr(firestore_admin, "get_firestore_client", lambda: fake_client)

    from app.services import seller_accounts

    monkeypatch.setattr(
        seller_accounts,
        "get_seller_account",
        lambda email: SellerAccount(
            email=email,
            shop_name="Loja Seller Alpha",
            shop_slug="seller-alpha",
            password_hash="hash",
            must_change_password=False,
            two_factor_enabled=True,
            totp_secret=None,
            status="active",
            created_at=None,
            updated_at=None,
            created_by="marcos_dev@icloud.com",
        ),
    )

    template = firestore_admin.upsert_product(
        StoreProduct(
            slug="blister-unidade-equilibrio",
            name="Blister unidade Equilibrio",
            product_type="blister",
            category="Blister",
            stock=50,
            price_brl=34.9,
            image_url="https://example.com/template.png",
            seller_template_enabled=True,
            allow_seller_custom_image=True,
        )
    )

    firestore_admin.publish_seller_product_from_template(
        seller_email="seller.alpha@legacycards.com",
        template_slug=template.slug,
        quantity=5,
        use_template_image=True,
    )

    updated = firestore_admin.withdraw_seller_product_stock_from_template(
        seller_email="seller.alpha@legacycards.com",
        template_slug=template.slug,
        quantity=2,
    )
    assert updated.stock == 3

    seller_products = firestore_admin.list_products_by_seller(
        seller_email="seller.alpha@legacycards.com"
    )
    assert len(seller_products) == 1
    assert seller_products[0].stock == 3


def test_withdraw_seller_stock_validates_available_quantity(monkeypatch) -> None:
    fake_client = FakeFirestoreClient()
    monkeypatch.setattr(firestore_admin, "get_settings", _fake_settings)
    monkeypatch.setattr(firestore_admin, "get_firestore_client", lambda: fake_client)

    from app.services import seller_accounts

    monkeypatch.setattr(
        seller_accounts,
        "get_seller_account",
        lambda email: SellerAccount(
            email=email,
            shop_name="Loja Seller Alpha",
            shop_slug="seller-alpha",
            password_hash="hash",
            must_change_password=False,
            two_factor_enabled=True,
            totp_secret=None,
            status="active",
            created_at=None,
            updated_at=None,
            created_by="marcos_dev@icloud.com",
        ),
    )

    template = firestore_admin.upsert_product(
        StoreProduct(
            slug="blister-unidade-equilibrio",
            name="Blister unidade Equilibrio",
            product_type="blister",
            category="Blister",
            stock=50,
            price_brl=34.9,
            image_url="https://example.com/template.png",
            seller_template_enabled=True,
            allow_seller_custom_image=True,
        )
    )

    firestore_admin.publish_seller_product_from_template(
        seller_email="seller.alpha@legacycards.com",
        template_slug=template.slug,
        quantity=1,
        use_template_image=True,
    )

    try:
        firestore_admin.withdraw_seller_product_stock_from_template(
            seller_email="seller.alpha@legacycards.com",
            template_slug=template.slug,
            quantity=3,
        )
        assert False, "era esperado erro por quantidade acima do estoque do seller"
    except firestore_admin.FirestoreConnectionError as exc:
        assert "estoque atual" in str(exc).lower()
