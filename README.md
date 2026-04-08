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
- `POST /api/v1/auth/login` (email + senha)
- `POST /api/v1/auth/verify-2fa` (codigo TOTP)
- `GET /api/v1/auth/me` (requires `Authorization: Bearer <token>`)
- `GET /api/v1/admin/products` (requires bearer token)
- `POST /api/v1/admin/products` (requires bearer token)
- `PUT /api/v1/admin/products/{slug}` (requires bearer token)
- `DELETE /api/v1/admin/products/{slug}` (requires bearer token)
- `GET /api/v1/admin/cards/options` (requires bearer token)
- `GET /api/v1/admin/cards/lookup?query=031/094&limit=12` (requires bearer token)
- `POST /api/v1/admin/lots/import/start` (requires bearer token)
- `GET /api/v1/admin/lots/import/{job_id}` (requires bearer token)
- `GET /api/v1/admin/analytics/summary?days=30` (requires bearer token)

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
- `ADMIN_AUTH_EMAIL=marcos_dev@icloud.com`
- `ADMIN_AUTH_PASSWORD_HASH=pbkdf2_sha256$...`
- `ADMIN_AUTH_JWT_SECRET=<segredo-forte>`
- `ADMIN_AUTH_2FA_ENABLED=true`
- `ADMIN_AUTH_TOTP_SECRET=<segredo-base32-google-authenticator>`
- `ADMIN_AUTH_TOTP_ISSUER=Legacy Cards Admin`
- `BIGQUERY_ENABLED=true`
- `BIGQUERY_PROJECT_ID=legacy-cards-tcg`
- `BIGQUERY_DATASET=legacy_cards_analytics`
- `BIGQUERY_EVENTS_TABLE=events`
- `BIGQUERY_SERVICE_ACCOUNT_PATH=service.json`
- `BIGQUERY_LOCATION=US`
- `ANALYTICS_SUMMARY_FALLBACK_FIRESTORE=true`
