"""
Signal Engine — analyses charts every candle close.
Uses Binance free data + pandas-ta. No TradingView subscription needed.
"""
import asyncio
from decimal import Decimal
from datetime import datetime, timezone
from typing import Optional
import pandas as pd
import pandas_ta_classic as ta
from loguru import logger

from backend.db.database import AsyncSessionLocal
from backend.models.db_models import Signal, SignalSource, TradeDirection, Trade, TradeStatus
from backend.config.config_manager import get_config
from backend.services.market_data import fetch_ohlcv

EMA_FAST = 9; EMA_SLOW = 21; RSI_PERIOD = 14
RSI_OB = 65; RSI_OS = 35; ATR_PERIOD = 14
VOL_MA = 20; VOL_MULT = 1.5


async def run_signal_engine():
    """Run one full analysis cycle across all configured pairs."""
    async with AsyncSessionLocal() as db:
        if await get_config(db, "setup_complete") != "true": return
        if await get_config(db, "bot_active") != "true": return
        pairs_raw = await get_config(db, "trading_pairs") or "BTC/USDT"
        interval  = await get_config(db, "candle_interval") or "15m"
        pairs = [p.strip() for p in pairs_raw.split(",") if p.strip()]

    logger.info(f"Signal engine tick | pairs={pairs} interval={interval}")
    for pair in pairs:
        try:
            await _analyse(pair, interval)
        except Exception as e:
            logger.error(f"Engine error {pair}: {e}", exc_info=True)


async def get_indicator_snapshot(pair: str, interval: str = "15m") -> dict:
    """
    Returns current indicator values without needing a signal.
    Used by dashboard to show live chart data always.
    """
    df = await fetch_ohlcv(pair, interval, limit=100)
    if df is None or len(df) < 30:
        return {"error": "No data"}
    df["ema_fast"] = ta.ema(df["close"], length=EMA_FAST)
    df["ema_slow"] = ta.ema(df["close"], length=EMA_SLOW)
    df["rsi"]      = ta.rsi(df["close"], length=RSI_PERIOD)
    df["atr"]      = ta.atr(df["high"], df["low"], df["close"], length=ATR_PERIOD)
    df["vol_ma"]   = df["volume"].rolling(VOL_MA).mean()
    df = df.dropna().reset_index(drop=True)
    if len(df) < 2:
        return {"error": "Not enough data after indicators"}
    curr = df.iloc[-1]
    prev = df.iloc[-2]
    trend = "BULL" if curr["ema_fast"] > curr["ema_slow"] else "BEAR"
    cross = "↑ Cross UP" if (prev["ema_fast"] <= prev["ema_slow"] and curr["ema_fast"] > curr["ema_slow"]) \
            else ("↓ Cross DOWN" if (prev["ema_fast"] >= prev["ema_slow"] and curr["ema_fast"] < curr["ema_slow"]) \
            else "No cross")
    return {
        "pair":      pair,
        "interval":  interval,
        "price":     round(float(curr["close"]), 4),
        "ema_fast":  round(float(curr["ema_fast"]), 4),
        "ema_slow":  round(float(curr["ema_slow"]), 4),
        "rsi":       round(float(curr["rsi"]), 2),
        "atr":       round(float(curr["atr"]), 4),
        "vol_spike": float(curr["volume"]) > float(curr["vol_ma"]) * VOL_MULT,
        "trend":     trend,
        "cross":     cross,
        "candles":   len(df),
        "updated_at": str(curr["timestamp"]),
    }


async def _analyse(pair: str, interval: str):
    df = await fetch_ohlcv(pair, interval, limit=100)
    if df is None or len(df) < 30:
        logger.warning(f"Not enough data for {pair}")
        return

    df["ema_fast"] = ta.ema(df["close"], length=EMA_FAST)
    df["ema_slow"] = ta.ema(df["close"], length=EMA_SLOW)
    df["rsi"]      = ta.rsi(df["close"], length=RSI_PERIOD)
    df["atr"]      = ta.atr(df["high"], df["low"], df["close"], length=ATR_PERIOD)
    df["vol_ma"]   = df["volume"].rolling(VOL_MA).mean()
    df = df.dropna().reset_index(drop=True)
    if len(df) < 3: return

    prev = df.iloc[-2]; curr = df.iloc[-1]
    price = float(curr["close"]); atr = float(curr["atr"])
    vol_spike = float(curr["volume"]) > float(curr["vol_ma"]) * VOL_MULT

    cross_up   = prev["ema_fast"] <= prev["ema_slow"] and curr["ema_fast"] > curr["ema_slow"]
    cross_down = prev["ema_fast"] >= prev["ema_slow"] and curr["ema_fast"] < curr["ema_slow"]

    buy  = cross_up   and float(curr["rsi"]) < RSI_OB and vol_spike
    sell = cross_down and float(curr["rsi"]) > RSI_OS and vol_spike

    if not buy and not sell:
        logger.debug(f"{pair}: no signal | RSI={curr['rsi']:.1f} vol_spike={vol_spike} cross_up={cross_up} cross_down={cross_down}")
        return

    direction     = TradeDirection.BUY if buy else TradeDirection.SELL
    direction_str = "BUY" if buy else "SELL"
    logger.info(f"SIGNAL {direction_str} {pair} @ {price:.4f} RSI={curr['rsi']:.1f}")

    async with AsyncSessionLocal() as db:
        # Cooldown check
        from datetime import timedelta
        from sqlalchemy import and_
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)
        from sqlalchemy import select
        r = await db.execute(select(Signal).where(
            and_(Signal.pair == pair.upper(), Signal.received_at >= cutoff, Signal.rejected == False)
        ).limit(1))
        if r.scalar_one_or_none():
            logger.info(f"{pair}: cooldown active — skipping")
            return

        # Max open trades check (BUY only)
        if buy:
            max_t = int(await get_config(db, "max_open_trades") or "3")
            open_r = await db.execute(select(Trade).where(Trade.status == TradeStatus.OPEN))
            if len(open_r.scalars().all()) >= max_t:
                logger.info(f"Max trades reached — skipping BUY")
                return

        # Save signal
        sig = Signal(
            source=SignalSource.SYSTEM, direction=direction,
            pair=pair.upper(), price=price, atr=atr,
            raw_payload=f"{interval}|rsi={curr['rsi']:.1f}|vol={vol_spike}",
            processed=False, rejected=False,
        )
        db.add(sig)
        await db.flush(); await db.commit(); await db.refresh(sig)

    logger.info(f"Signal #{sig.id} saved — processing")

    # Size and create trade
    from backend.services.position_sizer import calculate_stop_loss, calculate_quantity, calculate_tp
    from backend.services.fund_manager import get_available_fund

    async with AsyncSessionLocal() as db:
        sl_type = await get_config(db, "stop_loss_type") or "fixed"
        sl_pct  = float(await get_config(db, "stop_loss_fixed_pct") or "2")
        risk    = float(await get_config(db, "risk_per_trade_pct") or "2")

    fund = await get_available_fund()
    sl   = calculate_stop_loss(direction_str, price, sl_type, sl_pct, atr)
    qty  = calculate_quantity(fund, risk, price, sl)
    tp   = calculate_tp(direction_str, price, sl)

    if qty <= 0:
        logger.warning(f"qty=0 for {pair} — skipping trade")
        return

    async with AsyncSessionLocal() as db:
        from backend.models.db_models import OrderType
        trade = Trade(
            signal_id=sig.id, pair=pair.upper(), direction=direction,
            order_type=OrderType.MARKET, status=TradeStatus.PENDING,
            quantity=Decimal(str(qty)), stop_loss_price=Decimal(str(sl)),
            take_profit_price=Decimal(str(tp)), fund_at_entry=Decimal(str(round(fund, 2))),
            notes=f"Engine|{interval}|rsi={curr['rsi']:.1f}",
        )
        db.add(trade)
        await db.flush(); await db.commit(); await db.refresh(trade)

    logger.info(f"Trade #{trade.id} PENDING — sending to executor")
    try:
        from backend.services.trade_executor import execute_trade
        await execute_trade(trade.id)
    except Exception as e:
        logger.error(f"Executor error: {e}")
