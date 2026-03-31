# Brute Force Program (Admin Backend)

## Current controls
- No username/password endpoint in this API.
- All admin routes require `X-Admin-Token`.
- Token validation uses constant-time comparison (`hmac.compare_digest`).

## Protected surface
- `GET /api/v1/admin/products`
- `POST /api/v1/admin/products`
- `PUT /api/v1/admin/products/{slug}`
- `DELETE /api/v1/admin/products/{slug}`
- `GET /api/v1/admin/analytics/summary`

## Recommended edge controls
- Apply IP rate limiting in API Gateway / Cloud Armor / Load Balancer.
- Enforce secret rotation cadence for admin token.
- Restrict CORS origins to trusted admin frontend domains only.

## Update protocol
Whenever admin auth changes, update:
1. `BRUTE_FORCE.md` (this repo)
2. `README.md` auth section
3. Workspace `BRUTE_FORCE.md`
