from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services import firestore_admin


class FakeDocumentSnapshot:
    def __init__(self, path: tuple[str, ...], payload: dict[str, Any] | None) -> None:
        self._path = path
        self._payload = payload

    @property
    def exists(self) -> bool:
        return self._payload is not None

    def to_dict(self) -> dict[str, Any]:
        return dict(self._payload or {})


class FakeDocumentRef:
    def __init__(self, client: "FakeFirestoreClient", path: tuple[str, ...]) -> None:
        self.client = client
        self.path = path

    def get(self) -> FakeDocumentSnapshot:
        return FakeDocumentSnapshot(self.path, self.client.docs.get(self.path))

    def set(self, payload: dict[str, Any], merge: bool = False) -> None:
        current = self.client.docs.get(self.path)
        if merge and isinstance(current, dict):
            merged = dict(current)
            merged.update(payload)
            self.client.docs[self.path] = merged
            return
        self.client.docs[self.path] = dict(payload)


class FakeCollectionRef:
    def __init__(self, client: "FakeFirestoreClient", path: tuple[str, ...]) -> None:
        self.client = client
        self.path = path

    def document(self, doc_id: str) -> FakeDocumentRef:
        return FakeDocumentRef(self.client, self.path + (doc_id,))


class FakeFirestoreClient:
    def __init__(self) -> None:
        self.docs: dict[tuple[str, ...], dict[str, Any]] = {}

    def collection(self, name: str) -> FakeCollectionRef:
        return FakeCollectionRef(self, (name,))


def _fake_settings() -> SimpleNamespace:
    return SimpleNamespace(
        firestore_collection_orders="orders",
    )


def _seed_order(
    fake_client: FakeFirestoreClient,
    *,
    order_id: str = "legacy-order-1",
    status: str = "approved",
    payment_id: str = "payment-123",
    item_store_slug: str = "seller-alpha",
    item_owner_email: str = "seller.alpha@legacycards.com",
) -> None:
    fake_client.docs[("orders", order_id)] = {
        "order_id": order_id,
        "status": status,
        "payment_id": payment_id,
        "created_at": "2026-04-17T15:00:00+00:00",
        "updated_at": "2026-04-17T15:00:00+00:00",
        "date_approved": "2026-04-17T15:00:00+00:00",
        "total_brl": 10.0,
        "total_items": 1,
        "items": [
            {
                "slug": "produto-exemplo",
                "name": "Produto Exemplo",
                "quantity": 1,
                "unit_price_brl": 10.0,
                "total_price_brl": 10.0,
                "store_slug": item_store_slug,
                "owner_seller_email": item_owner_email,
            }
        ],
    }


def test_update_sales_order_fulfillment_updates_status(monkeypatch) -> None:
    fake_client = FakeFirestoreClient()
    _seed_order(fake_client)
    monkeypatch.setattr(firestore_admin, "get_settings", _fake_settings)
    monkeypatch.setattr(firestore_admin, "get_firestore_client", lambda: fake_client)

    updated = firestore_admin.update_sales_order_fulfillment(
        order_id="legacy-order-1",
        fulfillment_status="em_preparacao",
        actor_email="marcos_dev@icloud.com",
        actor_role="admin",
    )

    assert updated.fulfillment_status == "em_preparacao"
    stored = fake_client.docs[("orders", "legacy-order-1")]
    assert stored["fulfillment_status"] == "em_preparacao"
    assert stored["fulfillment"]["status"] == "em_preparacao"
    assert stored["fulfillment_queue_entered_at"] == "2026-04-17T15:00:00+00:00"
    assert isinstance(stored["fulfillment_history"], list)
    assert stored["fulfillment_history"][-1]["status"] == "em_preparacao"


def test_update_sales_order_fulfillment_requires_approved_payment(monkeypatch) -> None:
    fake_client = FakeFirestoreClient()
    _seed_order(fake_client, status="pending")
    monkeypatch.setattr(firestore_admin, "get_settings", _fake_settings)
    monkeypatch.setattr(firestore_admin, "get_firestore_client", lambda: fake_client)

    with pytest.raises(
        firestore_admin.OrderProcessingValidationError,
        match="pagamento aprovado",
    ):
        firestore_admin.update_sales_order_fulfillment(
            order_id="legacy-order-1",
            fulfillment_status="separado",
            actor_email="marcos_dev@icloud.com",
            actor_role="admin",
        )


def test_update_sales_order_fulfillment_cancel_refunds_order(monkeypatch) -> None:
    fake_client = FakeFirestoreClient()
    _seed_order(fake_client, payment_id="pay-987")
    monkeypatch.setattr(firestore_admin, "get_settings", _fake_settings)
    monkeypatch.setattr(firestore_admin, "get_firestore_client", lambda: fake_client)

    calls: list[tuple[str, str]] = []

    def fake_fetch_payment(payment_id: str) -> dict[str, Any]:
        calls.append(("fetch", payment_id))
        return {"id": payment_id, "status": "approved"}

    def fake_create_full_refund(payment_id: str) -> dict[str, Any]:
        calls.append(("refund", payment_id))
        return {"id": "refund-123"}

    monkeypatch.setattr(firestore_admin, "fetch_payment", fake_fetch_payment)
    monkeypatch.setattr(firestore_admin, "create_full_refund", fake_create_full_refund)

    updated = firestore_admin.update_sales_order_fulfillment(
        order_id="legacy-order-1",
        fulfillment_status="cancelado",
        cancel_reason="Item indisponivel no estoque.",
        actor_email="marcos_dev@icloud.com",
        actor_role="admin",
    )

    assert calls == [("fetch", "pay-987"), ("refund", "pay-987")]
    assert updated.status == "refunded"
    assert updated.refund_status == "refunded"
    assert updated.fulfillment_status == "cancelado"
    assert updated.fulfillment_cancel_reason == "Item indisponivel no estoque."

    stored = fake_client.docs[("orders", "legacy-order-1")]
    assert stored["status"] == "refunded"
    assert stored["status_detail"] == "cancelado_com_estorno"
    assert stored["refund_status"] == "refunded"
    assert stored["fulfillment"]["refund"]["status"] == "refunded"


def test_update_sales_order_fulfillment_enviado_requires_tracking(monkeypatch) -> None:
    fake_client = FakeFirestoreClient()
    _seed_order(fake_client)
    monkeypatch.setattr(firestore_admin, "get_settings", _fake_settings)
    monkeypatch.setattr(firestore_admin, "get_firestore_client", lambda: fake_client)

    with pytest.raises(
        firestore_admin.OrderProcessingValidationError,
        match="rastreio",
    ):
        firestore_admin.update_sales_order_fulfillment(
            order_id="legacy-order-1",
            fulfillment_status="enviado",
            actor_email="marcos_dev@icloud.com",
            actor_role="admin",
        )


def test_update_sales_order_fulfillment_checks_seller_scope(monkeypatch) -> None:
    fake_client = FakeFirestoreClient()
    _seed_order(
        fake_client,
        item_store_slug="legacy-cards",
        item_owner_email="",
    )
    monkeypatch.setattr(firestore_admin, "get_settings", _fake_settings)
    monkeypatch.setattr(firestore_admin, "get_firestore_client", lambda: fake_client)

    with pytest.raises(
        firestore_admin.OrderProcessingForbiddenError,
        match="nao pertence",
    ):
        firestore_admin.update_sales_order_fulfillment(
            order_id="legacy-order-1",
            fulfillment_status="separado",
            actor_email="seller.beta@legacycards.com",
            actor_role="seller",
            store_slug="seller-beta",
            owner_seller_email="seller.beta@legacycards.com",
        )
