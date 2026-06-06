"""Config — encrypted key-value store in DB."""
import json, os
from typing import Any, Optional
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger
from backend.models.db_models import Config

_fernet: Optional[Fernet] = None

def _get_fernet() -> Fernet:
    global _fernet
    if _fernet:
        return _fernet
    key = os.environ.get("CONFIG_ENCRYPTION_KEY", "")
    try:
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        key = Fernet.generate_key().decode()
        os.environ["CONFIG_ENCRYPTION_KEY"] = key
        _fernet = Fernet(key.encode())
        logger.warning("Generated new Fernet key — add CONFIG_ENCRYPTION_KEY to .env")
    return _fernet

def encrypt(v: str) -> str: return _get_fernet().encrypt(v.encode()).decode()
def decrypt(v: str) -> str: return _get_fernet().decrypt(v.encode()).decode()

async def get_config(db: AsyncSession, key: str) -> Optional[str]:
    r = await db.execute(select(Config).where(Config.key == key))
    row = r.scalar_one_or_none()
    if row is None: return None
    if row.is_secret and row.value:
        try: return decrypt(row.value)
        except Exception: return None
    return row.value

async def set_config(db: AsyncSession, key: str, value: Any, is_secret: bool = False):
    sv = json.dumps(value) if not isinstance(value, str) else value
    if is_secret and sv: sv = encrypt(sv)
    r = await db.execute(select(Config).where(Config.key == key))
    row = r.scalar_one_or_none()
    if row: row.value = sv; row.is_secret = is_secret
    else: db.add(Config(key=key, value=sv, is_secret=is_secret))
    await db.flush()

async def get_all_config(db: AsyncSession) -> dict:
    r = await db.execute(select(Config))
    return {row.key: ("***" if row.is_secret else row.value) for row in r.scalars().all()}

async def get_all_config_plain(db: AsyncSession) -> dict:
    """Returns decrypted values — used for pre-filling setup form."""
    r = await db.execute(select(Config))
    out = {}
    for row in r.scalars().all():
        if row.is_secret and row.value:
            try: out[row.key] = decrypt(row.value)
            except Exception: out[row.key] = ""
        else:
            out[row.key] = row.value or ""
    return out

async def bulk_set_config(db: AsyncSession, data: dict, secret_keys: list = None):
    secret_keys = secret_keys or []
    for k, v in data.items():
        await set_config(db, k, v, is_secret=(k in secret_keys))
    await db.commit()

SECRET_KEYS = [
    "delta_api_key","delta_api_secret","tradingview_webhook_secret",
    "smtp_user","smtp_password",
]
CONFIG_KEYS = {
    "delta_api_key":"Delta API Key","delta_api_secret":"Delta API Secret",
    "delta_testnet":"Use Testnet","tradingview_webhook_secret":"Webhook Secret",
    "email_address":"Email","smtp_host":"SMTP Host","smtp_port":"SMTP Port",
    "smtp_user":"SMTP User","smtp_password":"SMTP Password","smtp_use_tls":"SMTP TLS",
    "starting_capital":"Starting Capital (INR)","risk_per_trade_pct":"Risk %",
    "stop_loss_type":"SL Type","stop_loss_fixed_pct":"Fixed SL %",
    "max_drawdown_pct":"Max Drawdown %","trading_pairs":"Trading Pairs",
    "max_open_trades":"Max Open Trades","profit_lock_threshold":"Profit Lock Threshold %",
    "profit_lock_pct":"Profit Lock %","candle_interval":"Candle Interval",
    "setup_complete":"Setup Complete","bot_active":"Bot Active",
}
