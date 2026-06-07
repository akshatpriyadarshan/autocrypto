"""
Signal Engine — sync, free, no TradingView needed.
Binance public data + pure pandas indicators.
Expanded pairs: BTC, ETH, SOL, XRP + news-driven picks.
"""
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from loguru import logger
import pandas as pd

from backend.db.database import get_session
from backend.models.db_models import Signal, SignalSource, TradeDirection, Trade, TradeStatus
from backend.config.config_manager import get_config
from backend.services.indicators import add_indicators
from backend.services.market_data import fetch_ohlcv
from sqlalchemy import select, and_

EMA_FAST=9; EMA_SLOW=21; RSI_OB=65; RSI_OS=35; VOL_MULT=1.5

# ── Recommended pairs based on market analysis ────────────────────────────────
# Delta Exchange India spot: BTC/USDT, ETH/USDT, SOL/USDT, XRP/USDT
# Perpetual futures: + DOGE, LINK, AVAX, MATIC, ADA, DOT
# News drivers (June 2025):
#   ETH  — Pectra upgrade, cup-and-handle breakout, ETF momentum
#   SOL  — Memecoin traffic, ETF speculation, $150-160 breakout zone
#   XRP  — Cross-border payments, US regulatory clarity
#   DOGE — Musk/X payments integration rumours
#   LINK — Oracle demand rising with DeFi TVL growth
RECOMMENDED_PAIRS = [
    "BTC/USDT",   # always — high liquidity, tight spreads
    "ETH/USDT",   # Pectra upgrade tailwind, strong spot ETF demand
    "SOL/USDT",   # memecoin activity driving on-chain revenue
    "XRP/USDT",   # regulatory clarity, cross-border payment adoption
]

FUTURES_PAIRS = [
    "DOGE/USDT",  # X payments integration + high retail interest
    "LINK/USDT",  # Oracle demand growing with DeFi
    "AVAX/USDT",  # subnet activity picking up
    "ADA/USDT",   # Chang hard fork improving developer activity
]


# fetch_ohlcv imported from market_data module below


def get_indicator_snapshot(pair: str, interval: str = "15m") -> dict:
    """Live indicator values — always available for dashboard."""
    df = fetch_ohlcv(pair, interval)
    if df is None or len(df) < 30:
        return {"error": "No market data"}
    try:
        df = add_indicators(df)
    except Exception as e:
        return {"error": str(e)}
    if len(df) < 2: return {"error": "Not enough candles"}
    curr = df.iloc[-1]; prev = df.iloc[-2]
    cross_up   = prev["ema_fast"] <= prev["ema_slow"] and curr["ema_fast"] > curr["ema_slow"]
    cross_down = prev["ema_fast"] >= prev["ema_slow"] and curr["ema_fast"] < curr["ema_slow"]
    vol_spike  = float(curr["volume"]) > float(curr["vol_ma"]) * VOL_MULT
    return {
        "pair": pair, "interval": interval,
        "price":    round(float(curr["close"]),4),
        "ema_fast": round(float(curr["ema_fast"]),4),
        "ema_slow": round(float(curr["ema_slow"]),4),
        "rsi":      round(float(curr["rsi"]),2),
        "atr":      round(float(curr["atr"]),4),
        "vol_spike": vol_spike,
        "trend":    "BULL" if curr["ema_fast"] > curr["ema_slow"] else "BEAR",
        "cross":    ("↑ EMA Cross UP" if cross_up else "↓ EMA Cross DOWN" if cross_down else "No cross"),
    }


def run_signal_engine():
    """Single tick — analyse all configured pairs."""
    with get_session() as db:
        if get_config(db,"setup_complete") != "true": return
        if get_config(db,"bot_active")     != "true": return
        pairs_raw = get_config(db,"trading_pairs") or ",".join(RECOMMENDED_PAIRS)
        interval  = get_config(db,"candle_interval") or "15m"
        pairs = [p.strip() for p in pairs_raw.split(",") if p.strip()]

    logger.info(f"Engine tick | pairs={pairs} interval={interval}")
    for pair in pairs:
        try: _analyse(pair, interval)
        except Exception as e: logger.error(f"Engine {pair}: {e}", exc_info=True)


def _analyse(pair: str, interval: str):
    df = fetch_ohlcv(pair, interval)
    if df is None or len(df) < 30: return
    df = add_indicators(df)
    if len(df) < 3: return

    prev=df.iloc[-2]; curr=df.iloc[-1]
    price=float(curr["close"]); atr_val=float(curr["atr"])
    vol_spike = float(curr["volume"]) > float(curr["vol_ma"]) * VOL_MULT
    cross_up   = prev["ema_fast"] <= prev["ema_slow"] and curr["ema_fast"] > curr["ema_slow"]
    cross_down = prev["ema_fast"] >= prev["ema_slow"] and curr["ema_fast"] < curr["ema_slow"]

    buy  = cross_up   and float(curr["rsi"]) < RSI_OB and vol_spike
    sell = cross_down and float(curr["rsi"]) > RSI_OS and vol_spike

    logger.debug(f"{pair} price={price:.2f} RSI={curr['rsi']:.1f} vol={vol_spike} buy={buy} sell={sell}")
    if not buy and not sell: return

    direction     = TradeDirection.BUY if buy else TradeDirection.SELL
    direction_str = "BUY" if buy else "SELL"
    logger.info(f"SIGNAL {direction_str} {pair} @ {price:.4f}")

    with get_session() as db:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)
        recent = db.execute(select(Signal).where(
            and_(Signal.pair==pair.upper(), Signal.received_at>=cutoff, Signal.rejected==False)
        ).limit(1)).scalar_one_or_none()
        if recent: logger.info(f"{pair}: cooldown"); return

        if buy:
            max_t = int(get_config(db,"max_open_trades") or "3")
            open_c = len(db.execute(select(Trade).where(Trade.status==TradeStatus.OPEN)).scalars().all())
            if open_c >= max_t: logger.info("Max trades"); return

        sig = Signal(
            source=SignalSource.SYSTEM, direction=direction,
            pair=pair.upper(), price=price, atr=atr_val,
            raw_payload=f"{interval}|rsi={curr['rsi']:.1f}|vol={vol_spike}",
            processed=False, rejected=False,
        )
        db.add(sig); db.flush(); db.commit(); db.refresh(sig)

    from backend.services.position_sizer import calculate_stop_loss, calculate_quantity, calculate_tp
    from backend.services.fund_manager import get_available_fund

    with get_session() as db:
        sl_type = get_config(db,"stop_loss_type") or "fixed"
        sl_pct  = float(get_config(db,"stop_loss_fixed_pct") or "2")
        risk    = float(get_config(db,"risk_per_trade_pct") or "2")

    fund = get_available_fund()
    sl   = calculate_stop_loss(direction_str, price, sl_type, sl_pct, atr_val)
    qty  = calculate_quantity(fund, risk, price, sl)
    tp   = calculate_tp(direction_str, price, sl)
    if qty <= 0: return

    with get_session() as db:
        from backend.models.db_models import OrderType
        trade = Trade(
            signal_id=sig.id, pair=pair.upper(), direction=direction,
            order_type=OrderType.MARKET, status=TradeStatus.PENDING,
            quantity=Decimal(str(qty)), stop_loss_price=Decimal(str(sl)),
            take_profit_price=Decimal(str(tp)),
            fund_at_entry=Decimal(str(round(fund,2))),
            notes=f"Engine|{interval}|rsi={curr['rsi']:.1f}",
        )
        db.add(trade); db.flush(); db.commit(); db.refresh(trade)

    try:
        from backend.services.trade_executor import execute_trade
        execute_trade(trade.id)
    except Exception as e:
        logger.error(f"Executor: {e}")