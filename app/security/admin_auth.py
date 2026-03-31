import hmac

from fastapi import Header, HTTPException

from app.core.config import get_settings


def require_admin_token(x_admin_token: str | None = Header(default=None)) -> None:
    settings = get_settings()
    if not x_admin_token:
        raise HTTPException(status_code=401, detail="Missing admin token")

    if not hmac.compare_digest(x_admin_token, settings.admin_api_token):
        raise HTTPException(status_code=401, detail="Invalid admin token")
