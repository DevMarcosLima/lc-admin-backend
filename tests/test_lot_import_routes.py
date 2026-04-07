from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_start_lot_import_route(monkeypatch) -> None:
    from app.api.routes import admin as admin_routes
    from app.schemas.store import LotImportStartResponse

    def fake_start(_payload):
        return LotImportStartResponse(job_id="lot-123", status="queued", total_cards=2)

    monkeypatch.setattr(admin_routes, "start_lot_import", fake_start)

    response = client.post(
        "/api/v1/admin/lots/import/start",
        json={
            "lot_payload": {
                "lot_id": "l1",
                "lot_name": "l1",
                "cards": [
                    {
                        "name": "Charizard",
                        "number": "004/102",
                        "language": "pt",
                        "category": "pokemon",
                        "details": "normal",
                        "quantity": 1,
                    }
                ],
            }
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == "lot-123"
    assert body["status"] == "queued"


def test_get_lot_import_route_not_found(monkeypatch) -> None:
    from app.api.routes import admin as admin_routes
    from app.services.lot_import import LotImportNotFound

    def fake_get(_job_id: str):
        raise LotImportNotFound("not found")

    monkeypatch.setattr(admin_routes, "get_lot_import", fake_get)

    response = client.get("/api/v1/admin/lots/import/lot-404")
    assert response.status_code == 404
