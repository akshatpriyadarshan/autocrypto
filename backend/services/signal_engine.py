"""
Signal Engine — EMA crossover strategy.
One session for entire signal+trade pipeline — no DetachedInstanceError.
Fund falls back to configured starting capital if exchange unreachable.
"""
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from loguru import logger
import pandas as pd

from backend.db.database import get_session
from backend.models.db_models import (
    Signal, SignalSource, TradeDirection,
    Trade, TradeStatus, OrderType
)
from backend.config.config_manager import get_config
from backend.services.indicators import add_indicators
from backend.services.market_data import fetch_ohlcv
from sqlalchemy import select, and_

EMA_FAST = 9
EMA_SLOW = 21
RSI_OB   = 70
RSI_OS   = 30
VOL_MULT = 1.2
USD_TO_INR = 85.0

RECOMMENDED_PAIRS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"]
FUTURES_PAIRS     = ["DOGE/USDT", "LINK/USDT", "AVAX/USDT", "ADA/USDT"]


def get_indicator_snapshot(pair: str, interval: str = "15m") -> dict:
    """Live indicators for dashboard — no trade, no session needed."""
    df = fetch_ohlcv(pair, interval, limit=100)
    if df is None or len(df) < 30:
        return {"error": "No market data"}
    try:
        df = add_indicators(df)
    except Exception as e:
        return {"error": str(e)}
    if len(df) < 2:
        return {"error": "Not enough candles"}
    curr = df.iloc[-1]
    prev = df.iloc[-2]
    cross_up   = bool(prev["ema_fast"] <= prev["ema_slow"] and curr["ema_fast"] > curr["ema_slow"])
    cross_down = bool(prev["ema_fast"] >= prev["ema_slow"] and curr["ema_fast"] < curr["ema_slow"])
    vol_spike  = bool(float(curr["volume"]) > float(curr["vol_ma"]) * VOL_MULT)
    rsi        = round(float(curr["rsi"]), 2)
    would      = "BUY" if (cross_up and rsi < RSI_OB) else ("SELL" if (cross_down and rsi > RSI_OS) else "None")
    return {
        "pair": pair, "interval": interval,
        "price":    round(float(curr["close"]), 4),
        "ema_fast": round(float(curr["ema_fast"]), 4),
        "ema_slow": round(float(curr["ema_slow"]), 4),
        "rsi":      rsi,
        "atr":      round(float(curr["atr"]), 4),
        "vol_spike": vol_spike,
        "trend":    "BULL" if curr["ema_fast"] > curr["ema_slow"] else "BEAR",
        "cross":    ("↑ EMA Cross UP" if cross_up else "↓ EMA Cross DOWN" if cross_down else "No cross"),
        "would_signal": would,
    }


def run_signal_engine():
    """Analyse all configured pairs. Called by scheduler on candle close."""
    with get_session() as db:
        if get_config(db, "setup_complete") != "true":
            return
        if get_config(db, "bot_active") != "true":
            return
        pairs_raw = get_config(db, "trading_pairs") or ",".join(RECOMMENDED_PAIRS)
        interval  = get_config(db, "candle_interval") or "15m"
        pairs = [p.strip() for p in pairs_raw.split(",") if p.strip()]

    logger.info(f"Engine | pairs={pairs} interval={interval}")
    generated = 0
    for pair in pairs:
        try:
            if _analyse(pair, interval):
                generated += 1
        except Exception as e:
            logger.error(f"Engine error {pair}: {e}", exc_info=True)
    logger.info(f"Engine done | {generated}/{len(pairs)} signals fired")


def _analyse(pair: str, interval: str) -> bool:
    """
    One function, one session for the entire signal→trade pipeline.
    Returns True if signal+trade created successfully.
    """
    # ── Fetch + indicators ────────────────────────────────────────────────────
    df = fetch_ohlcv(pair, interval, limit=100)
    if df is None or len(df) < 30:
        logger.warning(f"{pair}: no data")
        return False

    df = add_indicators(df)
    if len(df) < 3:
        return False

    prev = df.iloc[-2]
    curr = df.iloc[-1]
    price     = float(curr["close"])
    atr_val   = float(curr["atr"])
    rsi       = float(curr["rsi"])
    vol_spike = float(curr["volume"]) > float(curr["vol_ma"]) * VOL_MULT
    cross_up  = bool(prev["ema_fast"] <= prev["ema_slow"] and curr["ema_fast"] > curr["ema_slow"])
    cross_dn  = bool(prev["ema_fast"] >= prev["ema_slow"] and curr["ema_fast"] < curr["ema_slow"])
    buy       = cross_up and rsi < RSI_OB
    sell      = cross_dn and rsi > RSI_OS

    logger.info(
        f"{pair} price={price:.2f} EMA9={curr['ema_fast']:.2f} "
        f"EMA21={curr['ema_slow']:.2f} RSI={rsi:.1f} "
        f"cross_up={cross_up} cross_dn={cross_dn} buy={buy} sell={sell}"
    )

    if not buy and not sell:
        return False

    direction     = TradeDirection.BUY if buy else TradeDirection.SELL
    direction_str = "BUY" if buy else "SELL"
    logger.info(f"*** SIGNAL {direction_str} {pair} @ ₹{price*USD_TO_INR:,.0f} ***")

    # ── ONE SESSION for signal + trade creation ───────────────────────────────
    with get_session() as db:

        # Cooldown check
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)
        recent = db.execute(select(Signal).where(
            and_(Signal.pair == pair.upper(),
                 Signal.received_at >= cutoff,
                 Signal.rejected == False)
        ).limit(1)).scalar_one_or_none()
        if recent:
            logger.info(f"{pair}: cooldown active")
            return False

        # Max trades check
        if buy:
            max_t  = int(get_config(db, "max_open_trades") or "3")
            open_c = db.execute(
                select(Trade).where(Trade.status == TradeStatus.OPEN)
            ).scalars().all()
            if len(open_c) >= max_t:
                logger.info(f"Max trades {max_t} reached")
                return False

        # Config for sizing
        sl_type = get_config(db, "stop_loss_type") or "fixed"
        sl_pct  = float(get_config(db, "stop_loss_fixed_pct") or "2")
        risk    = float(get_config(db, "risk_per_trade_pct") or "2")

        # Fund: try exchange, fall back to configured starting capital
        fund_usd = _get_fund_usd(db)
        if fund_usd <= 0:
            logger.warning(f"Fund = 0, skipping trade for {pair}")
            # Still save signal as processed=False so it's visible in UI
            db.add(Signal(
                source=SignalSource.SYSTEM, direction=direction,
                pair=pair.upper(), price=price, atr=atr_val,
                raw_payload=f"{interval}|rsi={rsi:.1f}|FUND_ZERO",
                rejected=True, reject_reason="Fund = 0 — sync balance first",
            ))
            return False

        # Position sizing
        from backend.services.position_sizer import (
            calculate_stop_loss, calculate_quantity, calculate_tp
        )
        sl  = calculate_stop_loss(direction_str, price, sl_type, sl_pct, atr_val)
        qty = calculate_quantity(fund_usd, risk, price, sl)
        tp  = calculate_tp(direction_str, price, sl)

        if qty <= 0:
            logger.warning(f"{pair}: qty=0 fund_usd={fund_usd:.2f} price={price:.2f} sl={sl:.2f}")
            return False

        # Save signal
        sig = Signal(
            source=SignalSource.SYSTEM, direction=direction,
            pair=pair.upper(), price=price, atr=atr_val,
            raw_payload=f"{interval}|rsi={rsi:.1f}|vol={vol_spike}",
            processed=False, rejected=False,
        )
        db.add(sig)
        db.flush()          # get sig.id assigned
        sig_id = sig.id     # extract as plain int NOW — before session closes

        # Create trade in SAME session
        trade = Trade(
            signal_id         = sig_id,
            pair              = pair.upper(),
            direction         = direction,
            order_type        = OrderType.MARKET,
            status            = TradeStatus.PENDING,
            quantity          = Decimal(str(round(qty, 8))),
            stop_loss_price   = Decimal(str(round(sl, 8))),
            take_profit_price = Decimal(str(round(tp, 8))),
            fund_at_entry     = Decimal(str(round(fund_usd, 4))),
            notes             = f"Engine|{interval}|rsi={rsi:.1f}",
        )
        db.add(trade)
        db.flush()
        trade_id = trade.id  # extract as plain int NOW

        # Mark signal processed
        sig.processed = True

    # Session committed. Now execute on exchange.
    logger.info(f"Signal #{sig_id} + Trade #{trade_id} saved | qty={qty:.6f} sl={sl:.4f}")

    try:
        from backend.services.trade_executor import execute_trade
        execute_trade(trade_id)
    except Exception as e:
        logger.error(f"execute_trade #{trade_id} failed: {e}", exc_info=True)

    return True


def _get_fund_usd(db) -> float:
    """
    Get available trading fund in USD.
    1. Try latest FundSnapshot (from exchange sync)
    2. Fall back to configured starting_capital (stored in INR → convert to USD)
    """
    from backend.models.db_models import FundSnapshot
    snap = db.execute(
        select(FundSnapshot).order_by(FundSnapshot.snapshot_at.desc()).limit(1)
    ).scalar_one_or_none()
    if snap and float(snap.available) > 0:
        return float(snap.available)

    # Fallback: starting capital in INR → USD
    cap_inr = float(get_config(db, "starting_capital") or "0")
    if cap_inr > 0:
        cap_usd = cap_inr / USD_TO_INR
        logger.info(f"Fund fallback: ₹{cap_inr:,.0f} → ${cap_usd:.2f} USD")
        return cap_usd

    return 0.0
