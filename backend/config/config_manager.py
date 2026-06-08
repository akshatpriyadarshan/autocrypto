"""
Config — simple key-value store in DB.
Secrets encrypted with Fernet. No complexity.
"""
import json, os
from typing import Any, Optional
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session
from sqlalchemy import select
from loguru import logger
from backend.models.db_models import Config

_fernet: Optional[Fernet] = None
_last_key: str = ""


def _f() -> Fernet:
    global _fernet, _last_key
    key = os.environ.get("CONFIG_ENCRYPTION_KEY", "")
    if not key:
        key = Fernet.generate_key().decode()
        os.environ["CONFIG_ENCRYPTION_KEY"] = key
        logger.warning("Generated ephemeral Fernet key — add to Streamlit secrets")
    if key != _last_key:
        try:
            _fernet = Fernet(key.encode())
            _last_key = key
        except Exception:
            key = Fernet.generate_key().decode()
            os.environ["CONFIG_ENCRYPTION_KEY"] = key
            _fernet = Fernet(key.encode())
            _last_key = key
            logger.error("Bad Fernet key — regenerated")
    return _fernet


def reset_fernet():
    global _fernet, _last_key
    _fernet = None
    _last_key = ""


def enc(v: str) -> str:
    return _f().encrypt(v.encode()).decode()


def dec(v: str) -> str:
    try:
        return _f().decrypt(v.encode()).decode()
    except (InvalidToken, Exception):
        return ""


def get_config(db: Session, key: str) -> Optional[str]:
    row = db.execute(select(Config).where(Config.key == key)).scalar_one_or_none()
    if not row:
        return None
    if row.is_secret and row.value:
        return dec(row.value) or None
    return row.value


def set_config(db: Session, key: str, value: Any, is_secret: bool = False):
    sv = str(value) if not isinstance(value, str) else value
    if is_secret and sv:
        sv = enc(sv)
    row = db.execute(select(Config).where(Config.key == key)).scalar_one_or_none()
    if row:
        row.value = sv
        row.is_secret = is_secret
    else:
        db.add(Config(key=key, value=sv, is_secret=is_secret))


def get_all_plain(db: Session) -> dict:
    rows = db.execute(select(Config)).scalars().all()
    out = {}
    for r in rows:
        if r.is_secret and r.value:
            out[r.key] = dec(r.value) or ""
        else:
            out[r.key] = r.value or ""
    return out


SECRET_KEYS = ["delta_api_key", "delta_api_secret", "smtp_user", "smtp_password"]
