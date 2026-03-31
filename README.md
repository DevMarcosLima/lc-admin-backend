# LC Admin Backend

FastAPI admin API for Legacy Cards.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
cp .env.example .env
uvicorn app.main:app --reload
```

## Endpoints

- `GET /health`
- `GET /api/v1/admin/products` (requires `X-Admin-Token`)
- `POST /api/v1/admin/products` (requires `X-Admin-Token`)
- `PUT /api/v1/admin/products/{slug}` (requires `X-Admin-Token`)
- `DELETE /api/v1/admin/products/{slug}` (requires `X-Admin-Token`)
- `GET /api/v1/admin/analytics/summary?days=30` (requires `X-Admin-Token`)
