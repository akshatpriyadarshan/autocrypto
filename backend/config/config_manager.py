"""Config — encrypted key-value store in DB (sync)."""
import json, os
from typing import Any, Optional
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session
from sqlalchemy import select, delete
from loguru import logger
from backend.models.db_models import Config

# Cache fernet instance + the key it was built from
_fernet: Optional[Fernet] = None
_fernet_key_used: str = ""


def _get_fernet() -> Fernet:
    """
    Always read CONFIG_ENCRYPTION_KEY from env fresh.
    Invalidate cached instance if key changed (e.g. Streamlit rerun loaded secrets late).
    """
    global _fernet, _fernet_key_used
    current_key = os.environ.get("CONFIG_ENCRYPTION_KEY", "")

    # Invalidate if key changed since last call
    if _fernet is not None and current_key != _fernet_key_used:
        logger.info("Fernet key changed — rebuilding cipher")
        _fernet = None

    if _fernet is not None:
        return _fernet

    if not current_key:
        current_key = Fernet.generate_key().decode()
        os.environ["CONFIG_ENCRYPTION_KEY"] = current_key
        logger.warning("No CONFIG_ENCRYPTION_KEY — generated ephemeral key. Set it in Streamlit secrets!")

    try:
        _fernet = Fernet(current_key.encode() if isinstance(current_key, str) else current_key)
        _fernet_key_used = current_key
    except Exception as e:
        logger.error(f"Invalid Fernet key: {e} — generating new one")
        current_key = Fernet.generate_key().decode()
        os.environ["CONFIG_ENCRYPTION_KEY"] = current_key
        _fernet = Fernet(current_key.encode())
        _fernet_key_used = current_key

    return _fernet


def reset_fernet():
    """Call this after updating CONFIG_ENCRYPTION_KEY in env."""
    global _fernet, _fernet_key_used
    _fernet = None
    _fernet_key_used = ""


def encrypt(v: str) -> str:
    return _get_fernet().encrypt(v.encode()).decode()


def decrypt(v: str) -> str:
    try:
        return _get_fernet().decrypt(v.encode()).decode()
    except Exception as e:
        logger.error(f"Decrypt failed: {e}")
        return ""


def get_config(db: Session, key: str) -> Optional[str]:
    row = db.execute(select(Config).where(Config.key == key)).scalar_one_or_none()
    if row is None:
        return None
    if row.is_secret and row.value:
        val = decrypt(row.value)
        return val if val else None
    return row.value


def set_config(db: Session, key: str, value: Any, is_secret: bool = False):
    """Upsert a single config value. Does NOT commit — caller must commit."""
    sv = json.dumps(value) if not isinstance(value, str) else value
    if is_secret and sv:
        sv = encrypt(sv)
    row = db.execute(select(Config).where(Config.key == key)).scalar_one_or_none()
    if row:
        row.value    = sv
        row.is_secret = is_secret
    else:
        db.add(Config(key=key, value=sv, is_secret=is_secret))


def get_all_config(db: Session) -> dict:
    rows = db.execute(select(Config)).scalars().all()
    return {r.key: ("***" if r.is_secret else r.value) for r in rows}


def get_all_config_plain(db: Session) -> dict:
    """Returns decrypted values — for pre-filling setup form."""
    rows = db.execute(select(Config)).scalars().all()
    out = {}
    for r in rows:
        if r.is_secret and r.value:
            out[r.key] = decrypt(r.value) or ""
        else:
            out[r.key] = r.value or ""
    return out


def bulk_set_config(db: Session, data: dict, secret_keys: list = None):
    """
    Save all config values — NO internal commit.
    Caller's context manager (get_session) handles commit on exit.
    This prevents double-commit SQLAlchemy errors on Python 3.14.
    """
    secret_keys = secret_keys or []
    for k, v in data.items():
        set_config(db, k, v, is_secret=(k in secret_keys))
    db.flush()  # flush to DB without committing — let caller commit
    logger.info(f"Staged {len(data)} config keys for commit")


SECRET_KEYS = [
    "delta_api_key", "delta_api_secret", "tradingview_webhook_secret",
    "smtp_user", "smtp_password",
]
