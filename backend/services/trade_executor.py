"""Delta Exchange trade executor via ccxt."""
import os
from decimal import Decimal
from datetime import datetime, timezone
from typing import Optional
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from backend.db.database import AsyncSessionLocal
from backend.models.db_models import Trade, TradeStatus, TradeDirection, Alert, AlertLevel
from backend.config.config_manager import get_config
from sqlalchemy import select

_exchange = None

async def _get_exchange():
    global _exchange
    if _exchange:
        return _exchange
    import ccxt.async_support as ccxt
    async with AsyncSessionLocal() as db:
        key     = await get_config(db, "delta_api_key") or ""
        secret  = await get_config(db, "delta_api_secret") or ""
        testnet = await get_config(db, "delta_testnet") or "true"
    if not key or not secret:
        raise ValueError("Delta API credentials not set")
    _exchange = ccxt.delta({"apiKey": key, "secret": secret, "enableRateLimit": True})
    if testnet == "true":
        _exchange.set_sandbox_mode(True)
        logger.info("Delta: TESTNET")
    else:
        logger.info("Delta: LIVE")
    return _exchange

def reset_exchange():
    global _exchange
    _exchange = None

async def get_wallet_balance() -> Optional[float]:
    """Returns real USDT balance from Delta. None if unavailable."""
    try:
        ex = await _get_exchange()
        bal = await ex.fetch_balance()
        usdt = bal.get("USDT", {})
        val = usdt.get("free") or usdt.get("total") or 0
        return float(val)
    except Exception as e:
        logger.warning(f"wallet balance failed: {e}")
        return None

async def get_market_price(pair: str) -> Optional[float]:
    try:
        ex = await _get_exchange()
        t = await ex.fetch_ticker(pair)
        return float(t.get("last") or t.get("bid") or 0) or None
    except Exception as e:
        logger.warning(f"market price {pair}: {e}")
        return None

async def execute_trade(trade_id: int):
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(Trade).where(Trade.id == trade_id))
        trade = r.scalar_one_or_none()
        if not trade or trade.status != TradeStatus.PENDING:
            return
        try:
            order = await _place_order(str(trade.pair), str(trade.direction.value), float(trade.quantity))
            fill  = float(order.get("average") or order.get("price") or 0)
            trade.exchange_order_id = str(order.get("id",""))
            trade.status      = TradeStatus.OPEN
            if fill > 0:
                trade.entry_price = Decimal(str(round(fill, 8)))
            await db.commit()
            logger.info(f"Trade {trade_id} OPEN fill={fill}")
            from backend.services.fund_manager import take_fund_snapshot
            await take_fund_snapshot()
        except Exception as e:
            trade.status = TradeStatus.FAILED
            trade.notes  = f"FAILED: {e}"
            await db.commit()
            logger.error(f"execute_trade {trade_id}: {e}")

async def close_trade_market(trade_id: int, exit_price: float, reason: str = "signal"):
    async with AsyncSessionLocal() as db:
        r = await db.execute(select(Trade).where(Trade.id == trade_id))
        trade = r.scalar_one_or_none()
        if not trade or trade.status != TradeStatus.OPEN:
            return
        try:
            close_dir = "sell" if trade.direction == TradeDirection.BUY else "buy"
            order = await _place_order(str(trade.pair), close_dir, float(trade.quantity))
            actual = float(order.get("average") or order.get("price") or exit_price)
            entry  = float(trade.entry_price or actual)
            pnl    = (actual - entry) * float(trade.quantity) if trade.direction == TradeDirection.BUY else (entry - actual) * float(trade.quantity)
            pnl_pct = ((actual - entry) / entry * 100) if entry > 0 else 0
            if trade.direction == TradeDirection.SELL: pnl_pct = -pnl_pct
            trade.status    = TradeStatus.CLOSED
            trade.exit_price = Decimal(str(round(actual, 8)))
            trade.pnl       = Decimal(str(round(pnl, 2)))
            trade.pnl_pct   = Decimal(str(round(pnl_pct, 4)))
            trade.closed_at = datetime.now(timezone.utc)
            trade.notes     = (trade.notes or "") + f" | {reason}"
            await db.commit()
            logger.info(f"Trade {trade_id} CLOSED pnl={pnl:.2f}")
            from backend.services.fund_manager import take_fund_snapshot
            await take_fund_snapshot()
        except Exception as e:
            logger.error(f"close_trade {trade_id}: {e}")

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
async def _place_order(pair: str, side: str, qty: float) -> dict:
    ex = await _get_exchange()
    logger.info(f"Order: {side.upper()} {pair} qty={qty}")
    return await ex.create_order(pair, "market", side, qty)
