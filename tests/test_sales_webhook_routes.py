from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.security.admin_auth import hash_admin_password

client = TestClient(app)


def _auth_headers(monkeypatch) -> dict[str, str]:
    email = "marcos_dev@icloud.com"
    password = "StrongAdmin#2026!"

    monkeypatch.setenv("ADMIN_AUTH_EMAIL", email)
    monkeypatch.setenv("ADMIN_AUTH_PASSWORD_HASH", hash_admin_password(password))
    monkeypatch.setenv("ADMIN_AUTH_JWT_SECRET", "test-jwt-secret-key-legacy-cards-admin")
    monkeypatch.setenv("ADMIN_AUTH_2FA_ENABLED", "false")
    monkeypatch.setenv("ADMIN_AUTH_TOTP_SECRET", "")
    get_settings.cache_clear()

    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_sales_orders_route(monkeypatch) -> None:
    from app.api.routes import admin as admin_routes
    from app.schemas.store import SalesOrderListResponse

    def fake_list_sales_orders(**_kwargs):
        return SalesOrderListResponse(
            source="firestore",
            page=1,
            limit=20,
            total_orders=1,
            has_more=False,
            items=[
                {
                    "order_id": "legacy-order-1",
                    "status": "approved",
                    "total_brl": 149.9,
                    "total_items": 2,
                    "items": [],
                }
            ],
        )

    monkeypatch.setattr(admin_routes, "list_sales_orders", fake_list_sales_orders)
    headers = _auth_headers(monkeypatch)
    response = client.get("/api/v1/admin/sales/orders?page=1&limit=20", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_orders"] == 1
    assert payload["items"][0]["order_id"] == "legacy-order-1"
    get_settings.cache_clear()


def test_sales_metrics_route(monkeypatch) -> None:
    from app.api.routes import admin as admin_routes
    from app.schemas.store import SalesMetricsResponse

    def fake_sales_metrics_last_days(**_kwargs):
        return SalesMetricsResponse(
            source="firestore",
            period_days=30,
            total_orders=3,
            approved_orders=2,
            pending_orders=1,
            rejected_orders=0,
            approved_revenue_brl=399.8,
            total_revenue_brl=549.7,
            average_ticket_brl=199.9,
            status_breakdown=[],
            payment_method_breakdown=[],
            top_products=[],
        )

    monkeypatch.setattr(admin_routes, "sales_metrics_last_days", fake_sales_metrics_last_days)
    headers = _auth_headers(monkeypatch)
    response = client.get("/api/v1/admin/sales/metrics?days=30", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["approved_orders"] == 2
    assert payload["approved_revenue_brl"] == 399.8
    get_settings.cache_clear()


def test_webhook_events_route(monkeypatch) -> None:
    from app.api.routes import admin as admin_routes
    from app.schemas.store import WebhookEventListResponse

    def fake_list_webhook_events(**_kwargs):
        return WebhookEventListResponse(
            source="firestore",
            page=1,
            limit=30,
            total_events=1,
            has_more=False,
            items=[
                {
                    "event_id": "mpw-123",
                    "status": "updated:approved",
                    "payment_id": "998877",
                    "order_id": "legacy-order-1",
                }
            ],
        )

    monkeypatch.setattr(admin_routes, "list_webhook_events", fake_list_webhook_events)
    headers = _auth_headers(monkeypatch)
    response = client.get("/api/v1/admin/webhooks/events?page=1&limit=30", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_events"] == 1
    assert payload["items"][0]["status"] == "updated:approved"
    get_settings.cache_clear()


def test_sales_order_process_route(monkeypatch) -> None:
    from app.api.routes import admin as admin_routes

    def fake_update_sales_order_fulfillment(**kwargs):
        assert kwargs["order_id"] == "legacy-order-1"
        assert kwargs["fulfillment_status"] == "enviado"
        assert kwargs["tracking_code"] == "BR123456789"
        return {
            "order_id": "legacy-order-1",
            "status": "approved",
            "fulfillment_status": "enviado",
            "fulfillment_tracking_code": "BR123456789",
            "items": [],
        }

    monkeypatch.setattr(
        admin_routes,
        "update_sales_order_fulfillment",
        fake_update_sales_order_fulfillment,
    )
    headers = _auth_headers(monkeypatch)
    response = client.patch(
        "/api/v1/admin/sales/orders/legacy-order-1/process",
        headers=headers,
        json={
            "fulfillment_status": "enviado",
            "tracking_code": "BR123456789",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["order_id"] == "legacy-order-1"
    assert payload["fulfillment_status"] == "enviado"
    assert payload["fulfillment_tracking_code"] == "BR123456789"
    get_settings.cache_clear()


def test_sales_order_process_route_maps_validation_error(monkeypatch) -> None:
    from app.api.routes import admin as admin_routes

    def fake_update_sales_order_fulfillment(**_kwargs):
        raise admin_routes.OrderProcessingValidationError("Status invalido para pedido.")

    monkeypatch.setattr(
        admin_routes,
        "update_sales_order_fulfillment",
        fake_update_sales_order_fulfillment,
    )
    headers = _auth_headers(monkeypatch)
    response = client.patch(
        "/api/v1/admin/sales/orders/legacy-order-1/process",
        headers=headers,
        json={"fulfillment_status": "separado"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Status invalido para pedido."
    get_settings.cache_clear()
