"""Config — encrypted key-value store in DB (sync)."""
import json, os
from typing import Any, Optional
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session
from sqlalchemy import select
from loguru import logger
from backend.models.db_models import Config

_fernet: Optional[Fernet] = None

def _get_fernet() -> Fernet:
    global _fernet
    if _fernet: return _fernet
    key = os.environ.get("CONFIG_ENCRYPTION_KEY", "")
    try:
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        key = Fernet.generate_key().decode()
        os.environ["CONFIG_ENCRYPTION_KEY"] = key
        _fernet = Fernet(key.encode())
        logger.warning("Generated new Fernet key")
    return _fernet

def encrypt(v: str) -> str: return _get_fernet().encrypt(v.encode()).decode()
def decrypt(v: str) -> str: return _get_fernet().decrypt(v.encode()).decode()

def get_config(db: Session, key: str) -> Optional[str]:
    row = db.execute(select(Config).where(Config.key == key)).scalar_one_or_none()
    if row is None: return None
    if row.is_secret and row.value:
        try: return decrypt(row.value)
        except Exception: return None
    return row.value

def set_config(db: Session, key: str, value: Any, is_secret: bool = False):
    sv = json.dumps(value) if not isinstance(value, str) else value
    if is_secret and sv: sv = encrypt(sv)
    row = db.execute(select(Config).where(Config.key == key)).scalar_one_or_none()
    if row: row.value = sv; row.is_secret = is_secret
    else: db.add(Config(key=key, value=sv, is_secret=is_secret))

def get_all_config(db: Session) -> dict:
    rows = db.execute(select(Config)).scalars().all()
    return {r.key: ("***" if r.is_secret else r.value) for r in rows}

def get_all_config_plain(db: Session) -> dict:
    rows = db.execute(select(Config)).scalars().all()
    out = {}
    for r in rows:
        if r.is_secret and r.value:
            try: out[r.key] = decrypt(r.value)
            except: out[r.key] = ""
        else:
            out[r.key] = r.value or ""
    return out

def bulk_set_config(db: Session, data: dict, secret_keys: list = None):
    secret_keys = secret_keys or []
    for k, v in data.items():
        set_config(db, k, v, is_secret=(k in secret_keys))
    db.commit()

SECRET_KEYS = [
    "delta_api_key","delta_api_secret","tradingview_webhook_secret",
    "smtp_user","smtp_password",
]
