from __future__ import annotations

import secrets
import string
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import pyotp

from app.core.config import get_settings
from app.security.password_utils import hash_password, verify_password
from app.services.firestore_admin import get_firestore_client


class SellerAccountError(RuntimeError):
    pass


class SellerAccountNotFoundError(SellerAccountError):
    pass


class SellerAccountConflictError(SellerAccountError):
    pass


DEFAULT_SELLER_PAYOUT_BASE_FEE_BRL = 6.0


@dataclass(slots=True)
class SellerPayoutRule:
    template_slug: str
    commission_mode: Literal["percent", "fixed"]
    commission_percent: float | None
    commission_fixed_brl: float | None
    template_name: str | None
    active: bool
    updated_at: str | None


@dataclass(slots=True)
class SellerPayoutConfig:
    base_fee_brl: float
    rules: list[SellerPayoutRule] = field(default_factory=list)
    updated_at: str | None = None


def _default_payout_config() -> SellerPayoutConfig:
    return SellerPayoutConfig(base_fee_brl=DEFAULT_SELLER_PAYOUT_BASE_FEE_BRL, rules=[])


@dataclass(slots=True)
class SellerAccount:
    email: str
    shop_name: str
    shop_slug: str
    password_hash: str
    must_change_password: bool
    two_factor_enabled: bool
    totp_secret: str | None
    status: str
    created_at: str | None
    updated_at: str | None
    created_by: str | None
    payout_config: SellerPayoutConfig = field(default_factory=_default_payout_config)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_email(value: str) -> str:
    return value.strip().lower()


def _slugify(value: str) -> str:
    safe = "".join(char.lower() if char.isalnum() else "-" for char in value.strip())
    safe = "-".join(part for part in safe.split("-") if part)
    return safe[:60] if safe else "seller"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _round_money(value: float) -> float:
    return round(value, 2)


def _parse_payout_rule(payload: Any) -> SellerPayoutRule | None:
    if not isinstance(payload, dict):
        return None

    raw_template_slug = str(payload.get("template_slug") or "").strip()
    if not raw_template_slug:
        return None
    safe_template_slug = _slugify(raw_template_slug)

    mode_raw = str(payload.get("commission_mode") or "").strip().lower()
    if mode_raw not in {"percent", "fixed"}:
        mode_raw = "percent"
    commission_mode: Literal["percent", "fixed"] = (
        "fixed" if mode_raw == "fixed" else "percent"
    )

    commission_percent: float | None = None
    commission_fixed_brl: float | None = None
    if commission_mode == "percent":
        commission_percent = min(
            100.0,
            max(0.0, _round_money(_safe_float(payload.get("commission_percent"), 0.0))),
        )
    else:
        commission_fixed_brl = max(
            0.0,
            _round_money(_safe_float(payload.get("commission_fixed_brl"), 0.0)),
        )

    template_name = str(payload.get("template_name") or "").strip() or None
    active = bool(payload.get("active", True))
    updated_at = str(payload.get("updated_at") or "").strip() or None

    return SellerPayoutRule(
        template_slug=safe_template_slug,
        commission_mode=commission_mode,
        commission_percent=commission_percent,
        commission_fixed_brl=commission_fixed_brl,
        template_name=template_name,
        active=active,
        updated_at=updated_at,
    )


def _parse_payout_config(payload: dict[str, Any]) -> SellerPayoutConfig:
    raw_config = payload.get("payout_config")
    if not isinstance(raw_config, dict):
        raw_config = {}

    base_fee_brl = max(
        0.0,
        _round_money(
            _safe_float(
                raw_config.get("base_fee_brl", DEFAULT_SELLER_PAYOUT_BASE_FEE_BRL),
                DEFAULT_SELLER_PAYOUT_BASE_FEE_BRL,
            )
        ),
    )
    updated_at = str(raw_config.get("updated_at") or "").strip() or None

    rules_by_slug: dict[str, SellerPayoutRule] = {}
    raw_rules = raw_config.get("rules")
    if isinstance(raw_rules, list):
        for item in raw_rules:
            parsed = _parse_payout_rule(item)
            if parsed is None:
                continue
            rules_by_slug[parsed.template_slug] = parsed

    return SellerPayoutConfig(
        base_fee_brl=base_fee_brl,
        rules=sorted(rules_by_slug.values(), key=lambda item: item.template_slug),
        updated_at=updated_at,
    )


def _serialize_payout_rule(rule: SellerPayoutRule) -> dict[str, Any]:
    return {
        "template_slug": rule.template_slug,
        "commission_mode": rule.commission_mode,
        "commission_percent": rule.commission_percent,
        "commission_fixed_brl": rule.commission_fixed_brl,
        "template_name": rule.template_name,
        "active": bool(rule.active),
        "updated_at": rule.updated_at or _now_iso(),
    }


def _serialize_payout_config(config: SellerPayoutConfig) -> dict[str, Any]:
    return {
        "base_fee_brl": _round_money(max(0.0, config.base_fee_brl)),
        "updated_at": config.updated_at or _now_iso(),
        "rules": [_serialize_payout_rule(rule) for rule in config.rules],
    }


def _doc_to_account(doc_id: str, payload: dict[str, Any]) -> SellerAccount:
    email = _normalize_email(str(payload.get("email") or doc_id))
    return SellerAccount(
        email=email,
        shop_name=str(payload.get("shop_name") or "Loja Seller").strip() or "Loja Seller",
        shop_slug=str(payload.get("shop_slug") or _slugify(email.split("@")[0])).strip(),
        password_hash=str(payload.get("password_hash") or ""),
        must_change_password=bool(payload.get("must_change_password", True)),
        two_factor_enabled=bool(payload.get("two_factor_enabled", False)),
        totp_secret=str(payload.get("totp_secret") or "").strip() or None,
        status=str(payload.get("status") or "active").strip().lower() or "active",
        created_at=str(payload.get("created_at") or "").strip() or None,
        updated_at=str(payload.get("updated_at") or "").strip() or None,
        created_by=str(payload.get("created_by") or "").strip() or None,
        payout_config=_parse_payout_config(payload),
    )


def _sellers_collection():
    settings = get_settings()
    client = get_firestore_client()
    return client.collection(settings.firestore_collection_seller_users)


def random_temporary_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%&*"
    return "".join(secrets.choice(alphabet) for _ in range(max(10, length)))


def list_seller_accounts() -> list[SellerAccount]:
    rows: list[SellerAccount] = []
    try:
        for doc in _sellers_collection().stream():
            payload = doc.to_dict() or {}
            rows.append(_doc_to_account(doc.id, payload))
    except Exception as exc:  # noqa: BLE001
        raise SellerAccountError(f"Falha ao listar sellers: {exc}") from exc
    return sorted(rows, key=lambda item: item.email)


def get_seller_account(email: str) -> SellerAccount | None:
    normalized = _normalize_email(email)
    if not normalized:
        return None

    try:
        snapshot = _sellers_collection().document(normalized).get()
    except Exception as exc:  # noqa: BLE001
        raise SellerAccountError(f"Falha ao consultar seller: {exc}") from exc

    if not snapshot.exists:
        return None
    return _doc_to_account(snapshot.id, snapshot.to_dict() or {})


def create_seller_account(
    *,
    email: str,
    shop_name: str,
    created_by: str,
    temporary_password: str | None = None,
) -> tuple[SellerAccount, str]:
    normalized_email = _normalize_email(email)
    if not normalized_email:
        raise SellerAccountError("E-mail do seller inválido.")

    existing = get_seller_account(normalized_email)
    if existing is not None:
        raise SellerAccountConflictError("Já existe seller com este e-mail.")

    generated_password = temporary_password or random_temporary_password()
    now_iso = _now_iso()
    shop_slug = (
        _slugify(shop_name)
        if shop_name.strip()
        else _slugify(normalized_email.split("@")[0])
    )
    payload = {
        "email": normalized_email,
        "shop_name": shop_name.strip() or "Loja Seller",
        "shop_slug": shop_slug,
        "password_hash": hash_password(generated_password),
        "must_change_password": True,
        "two_factor_enabled": False,
        "totp_secret": None,
        "status": "active",
        "created_at": now_iso,
        "updated_at": now_iso,
        "created_by": created_by.strip().lower() or None,
    }

    try:
        _sellers_collection().document(normalized_email).set(payload, merge=False)
    except Exception as exc:  # noqa: BLE001
        raise SellerAccountError(f"Falha ao criar seller: {exc}") from exc

    account = _doc_to_account(normalized_email, payload)
    return account, generated_password


def verify_seller_credentials(*, email: str, password: str) -> SellerAccount | None:
    account = get_seller_account(email)
    if account is None:
        return None
    if account.status != "active":
        return None
    if not account.password_hash:
        return None
    if not verify_password(password, account.password_hash):
        return None
    return account


def update_seller_status(
    *,
    email: str,
    status: Literal["active", "inactive"],
    updated_by: str | None = None,
    note: str | None = None,
) -> SellerAccount:
    account = get_seller_account(email)
    if account is None:
        raise SellerAccountNotFoundError("Seller não encontrado.")

    next_status = str(status).strip().lower()
    if next_status not in {"active", "inactive"}:
        raise SellerAccountError("Status de seller inválido.")

    now_iso = _now_iso()
    safe_note = (note or "").strip()[:240] or None
    payload: dict[str, Any] = {
        "status": next_status,
        "updated_at": now_iso,
        "status_updated_by": (updated_by or "").strip().lower() or None,
        "status_note": safe_note,
    }
    if next_status == "inactive":
        payload["deactivated_at"] = now_iso
    else:
        payload["deactivated_at"] = None

    try:
        _sellers_collection().document(account.email).set(payload, merge=True)
    except Exception as exc:  # noqa: BLE001
        raise SellerAccountError(f"Falha ao atualizar status do seller: {exc}") from exc

    refreshed = get_seller_account(account.email)
    if refreshed is None:
        raise SellerAccountNotFoundError("Seller não encontrado após atualizar status.")
    return refreshed


def ensure_seller_totp_secret(email: str) -> str:
    account = get_seller_account(email)
    if account is None:
        raise SellerAccountNotFoundError("Seller não encontrado.")

    if account.totp_secret:
        return account.totp_secret

    secret = pyotp.random_base32()
    try:
        _sellers_collection().document(account.email).set(
            {"totp_secret": secret, "updated_at": _now_iso()},
            merge=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise SellerAccountError(f"Falha ao persistir segredo 2FA do seller: {exc}") from exc
    return secret


def seller_totp_provisioning_uri(email: str, *, issuer_name: str) -> str:
    account = get_seller_account(email)
    if account is None:
        raise SellerAccountNotFoundError("Seller não encontrado.")

    secret = ensure_seller_totp_secret(account.email)
    return pyotp.TOTP(secret).provisioning_uri(name=account.email, issuer_name=issuer_name)


def verify_seller_totp_code(*, email: str, code: str) -> bool:
    account = get_seller_account(email)
    if account is None:
        return False
    if account.status != "active":
        return False

    secret = (account.totp_secret or "").strip()
    if not secret:
        return False

    otp = code.strip().replace(" ", "")
    if not otp.isdigit():
        return False

    return bool(pyotp.TOTP(secret).verify(otp, valid_window=1))


def complete_seller_onboarding(*, email: str, new_password: str, code: str) -> SellerAccount:
    account = get_seller_account(email)
    if account is None:
        raise SellerAccountNotFoundError("Seller não encontrado.")
    if account.status != "active":
        raise SellerAccountError("Seller inativo. Reative o acesso para concluir onboarding.")

    if len(new_password.strip()) < 8:
        raise SellerAccountError("Nova senha deve ter pelo menos 8 caracteres.")

    secret = ensure_seller_totp_secret(account.email)
    otp = code.strip().replace(" ", "")
    if not otp.isdigit() or not pyotp.TOTP(secret).verify(otp, valid_window=1):
        raise SellerAccountError("Código 2FA inválido.")

    now_iso = _now_iso()
    payload = {
        "password_hash": hash_password(new_password.strip()),
        "must_change_password": False,
        "two_factor_enabled": True,
        "updated_at": now_iso,
        "last_onboarding_at": now_iso,
    }
    try:
        _sellers_collection().document(account.email).set(payload, merge=True)
    except Exception as exc:  # noqa: BLE001
        raise SellerAccountError(f"Falha ao concluir onboarding do seller: {exc}") from exc

    updated = get_seller_account(account.email)
    if updated is None:
        raise SellerAccountNotFoundError("Seller não encontrado após onboarding.")
    return updated


def touch_seller_login(email: str) -> None:
    account = get_seller_account(email)
    if account is None:
        return
    try:
        _sellers_collection().document(account.email).set(
            {"last_login_at": _now_iso(), "updated_at": _now_iso()},
            merge=True,
        )
    except Exception:
        return


def get_seller_payout_config(email: str) -> SellerPayoutConfig:
    account = get_seller_account(email)
    if account is None:
        raise SellerAccountNotFoundError("Seller não encontrado.")
    return account.payout_config


def save_seller_payout_config(
    *,
    email: str,
    base_fee_brl: float,
    rules: list[dict[str, Any]],
) -> SellerPayoutConfig:
    account = get_seller_account(email)
    if account is None:
        raise SellerAccountNotFoundError("Seller não encontrado.")

    normalized_rules: dict[str, SellerPayoutRule] = {}
    for item in rules:
        parsed = _parse_payout_rule(item)
        if parsed is None:
            continue
        parsed.updated_at = _now_iso()
        normalized_rules[parsed.template_slug] = parsed

    payout_config = SellerPayoutConfig(
        base_fee_brl=max(0.0, _round_money(base_fee_brl)),
        rules=sorted(normalized_rules.values(), key=lambda item: item.template_slug),
        updated_at=_now_iso(),
    )

    payload = {
        "payout_config": _serialize_payout_config(payout_config),
        "updated_at": _now_iso(),
    }
    try:
        _sellers_collection().document(account.email).set(payload, merge=True)
    except Exception as exc:  # noqa: BLE001
        raise SellerAccountError(f"Falha ao salvar configuracao de repasse: {exc}") from exc

    refreshed = get_seller_account(account.email)
    if refreshed is None:
        raise SellerAccountNotFoundError("Seller não encontrado após salvar repasse.")
    return refreshed.payout_config
