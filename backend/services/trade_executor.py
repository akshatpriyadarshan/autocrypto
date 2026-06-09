"""
Delta Exchange India executor.
Uses ccxt.deltaindia — correct endpoint: api.india.delta.exchange
Keys are hardcoded and configured for Streamlit Cloud IPs.
"""
from decimal import Decimal
from datetime import datetime, timezone
from typing import Optional
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from backend.db.database import get_session
from backend.models.db_models import Trade, TradeStatus, TradeDirection
from backend.config.config_manager import get_config
from sqlalchemy import select

# Live keys — configured for Streamlit Cloud IPs on Delta Exchange India
DELTA_API_KEY    = "76wEBRrPbx64EUzphk43LIX1kCWrFb"
DELTA_API_SECRET = "3lJghi3DLRdgeoesLYxfBg5l9jH4Q0HEjLMOkN744dp9dOH4ddiHG6Mv09cH"

_exchange = None


def _get_exchange():
    global _exchange
    if _exchange:
        return _exchange

    import ccxt

    # Use deltaindia — correct class for India endpoint
    # api.india.delta.exchange, NOT api.delta.exchange (global)
    if hasattr(ccxt, 'deltaindia'):
        cls = ccxt.deltaindia
        logger.info("Using ccxt.deltaindia (India endpoint)")
    else:
        # Fallback: use ccxt.delta with manual India URL override
        cls = ccxt.delta
        logger.warning("ccxt.deltaindia not found — using ccxt.delta with India URL override")

    _exchange = cls({
        "apiKey": DELTA_API_KEY,
        "secret": DELTA_API_SECRET,
        "enableRateLimit": True,
    })

    # If using base ccxt.delta, override URLs to India endpoint
    if not hasattr(ccxt, 'deltaindia'):
        _exchange.urls['api'] = {
            'public':  'https://api.india.delta.exchange/v2',
            'private': 'https://api.india.delta.exchange/v2',
        }

    logger.info(f"Delta Exchange India: LIVE mode")
    return _exchange


def reset_exchange():
    global _exchange
    _exchange = None


def get_wallet_balance_sync() -> Optional[float]:
    """Returns USDT balance from Delta India. None if error."""
    try:
        ex = _get_exchange()
        bal = ex.fetch_balance()
        # Delta India returns balances under asset symbols
        for key in ["USDT", "usdt"]:
            if key in bal:
                val = bal[key].get("free") or bal[key].get("total") or 0
                if val:
                    return float(val)
        # Some Delta responses nest under 'total'
        total = bal.get("total", {})
        if "USDT" in total:
            return float(total["USDT"])
        return 0.0
    except Exception as e:
        logger.warning(f"Balance fetch: {e}")
        return None


def get_market_price_sync(pair: str) -> Optional[float]:
    try:
        ex = _get_exchange()
        t = ex.fetch_ticker(pair)
        return float(t.get("last") or t.get("bid") or 0) or None
    except Exception as e:
        logger.warning(f"Price {pair}: {e}")
        return None


def execute_trade(trade_id: int):
    with get_session() as db:
        trade = db.execute(
            select(Trade).where(Trade.id == trade_id)
        ).scalar_one_or_none()
        if not trade or trade.status != TradeStatus.PENDING:
            return
        try:
            order = _place_order(
                str(trade.pair),
                str(trade.direction.value),
                float(trade.quantity)
            )
            fill = float(order.get("average") or order.get("price") or 0)
            trade.exchange_order_id = str(order.get("id", ""))
            trade.status = TradeStatus.OPEN
            if fill > 0:
                trade.entry_price = Decimal(str(round(fill, 8)))
            logger.info(f"Trade {trade_id} OPEN fill={fill}")
        except Exception as e:
            trade.status = TradeStatus.FAILED
            trade.notes = f"FAILED: {e}"
            logger.error(f"execute_trade {trade_id}: {e}")

    try:
        from backend.services.fund_manager import take_fund_snapshot
        take_fund_snapshot()
    except Exception as e:
        logger.error(f"Snapshot after open: {e}")


def close_trade_market(trade_id: int, exit_price: float, reason: str = "signal"):
    with get_session() as db:
        trade = db.execute(
            select(Trade).where(Trade.id == trade_id)
        ).scalar_one_or_none()
        if not trade or trade.status != TradeStatus.OPEN:
            return
        try:
            close_dir = "sell" if trade.direction == TradeDirection.BUY else "buy"
            order  = _place_order(str(trade.pair), close_dir, float(trade.quantity))
            actual = float(order.get("average") or order.get("price") or exit_price)
            entry  = float(trade.entry_price or actual)
            pnl    = (actual - entry) * float(trade.quantity) \
                     if trade.direction == TradeDirection.BUY \
                     else (entry - actual) * float(trade.quantity)
            pnl_pct = ((actual - entry) / entry * 100) if entry > 0 else 0
            if trade.direction == TradeDirection.SELL:
                pnl_pct = -pnl_pct
            trade.status     = TradeStatus.CLOSED
            trade.exit_price = Decimal(str(round(actual, 8)))
            trade.pnl        = Decimal(str(round(pnl, 2)))
            trade.pnl_pct    = Decimal(str(round(pnl_pct, 4)))
            trade.closed_at  = datetime.now(timezone.utc)
            trade.notes      = (trade.notes or "") + f" | {reason}"
            logger.info(f"Trade {trade_id} CLOSED pnl=₹{pnl:.2f}")
        except Exception as e:
            logger.error(f"close_trade {trade_id}: {e}")

    try:
        from backend.services.fund_manager import take_fund_snapshot
        take_fund_snapshot()
    except Exception as e:
        logger.error(f"Snapshot after close: {e}")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10), reraise=True)
def _place_order(pair: str, side: str, qty: float) -> dict:
    ex = _get_exchange()
    logger.info(f"Order: {side.upper()} {pair} qty={qty}")
    return ex.create_order(pair, "market", side, qty)
