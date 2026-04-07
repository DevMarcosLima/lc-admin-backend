# LC Admin Backend

FastAPI admin API for Legacy Cards.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
cp .env.example .env
uvicorn app.main:app --reload --port 8001
```

## Endpoints

- `GET /health`
- `GET /api/v1/admin/products` (requires `X-Admin-Token`)
- `POST /api/v1/admin/products` (requires `X-Admin-Token`)
- `PUT /api/v1/admin/products/{slug}` (requires `X-Admin-Token`)
- `DELETE /api/v1/admin/products/{slug}` (requires `X-Admin-Token`)
- `GET /api/v1/admin/cards/options` (requires `X-Admin-Token`)
- `GET /api/v1/admin/cards/lookup?query=031/094&limit=12` (requires `X-Admin-Token`)
- `POST /api/v1/admin/lots/import/start` (requires `X-Admin-Token`)
- `GET /api/v1/admin/lots/import/{job_id}` (requires `X-Admin-Token`)
- `GET /api/v1/admin/analytics/summary?days=30` (requires `X-Admin-Token`)

## Integracoes gratuitas usadas

- Pokemon TCG API (`https://api.pokemontcg.io/v2`) para set, raridade, imagem e ano.
- Opcional: configure `POKEMON_TCG_API_KEY` no `.env` para limites maiores.

## Variaveis novas no .env

- `POKEMON_TCG_API_BASE_URL=https://api.pokemontcg.io/v2`
- `POKEMON_TCG_API_KEY=`
- `OPENAI_API_KEY=` (opcional para inferir `regulation_mark` quando API nao retornar)
- `OPENAI_REGULATION_MODEL=gpt-5-nano`
- `OPENAI_REGULATION_BATCH_SIZE=40`
- `LOT_IMPORT_MAX_CARDS=500`
