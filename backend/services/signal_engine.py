"""
Signal Engine — mirrors TradingView EMA crossover strategy.
Loosened conditions to match TradingView signal frequency.
Volume filter optional (configurable). RSI used as soft filter only.
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

# Strategy parameters — tuned to match TradingView signal frequency
EMA_FAST  = 9
EMA_SLOW  = 21
RSI_OB    = 70   # relaxed from 65 — avoids missing real breakouts
RSI_OS    = 30   # relaxed from 35
VOL_MULT  = 1.2  # reduced from 1.5 — volume filter was too strict

RECOMMENDED_PAIRS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
]

FUTURES_PAIRS = [
    "DOGE/USDT",
    "LINK/USDT",
    "AVAX/USDT",
    "ADA/USDT",
]

# Fixed USD→INR rate per Delta Exchange India
USD_TO_INR = 85.0


def usd_to_inr(usd: float) -> float:
    return round(usd * USD_TO_INR, 2)


def inr_to_usd(inr: float) -> float:
    return round(inr / USD_TO_INR, 4)


def get_indicator_snapshot(pair: str, interval: str = "15m") -> dict:
    """Live indicators for dashboard — always runs regardless of bot state."""
    df = fetch_ohlcv(pair, interval, limit=100)
    if df is None or len(df) < 30:
        return {"error": "No market data — KuCoin/OKX/Bybit unreachable"}
    try:
        df = add_indicators(df)
    except Exception as e:
        return {"error": f"Indicator error: {e}"}
    if len(df) < 2:
        return {"error": "Not enough candles"}

    curr = df.iloc[-1]
    prev = df.iloc[-2]
    cross_up   = bool(prev["ema_fast"] <= prev["ema_slow"] and curr["ema_fast"] > curr["ema_slow"])
    cross_down = bool(prev["ema_fast"] >= prev["ema_slow"] and curr["ema_fast"] < curr["ema_slow"])
    vol_spike  = bool(float(curr["volume"]) > float(curr["vol_ma"]) * VOL_MULT)
    rsi        = round(float(curr["rsi"]), 2)

    # Would this candle generate a signal?
    would_buy  = cross_up   and rsi < RSI_OB
    would_sell = cross_down and rsi > RSI_OS

    return {
        "pair":       pair,
        "interval":   interval,
        "price":      round(float(curr["close"]), 4),
        "ema_fast":   round(float(curr["ema_fast"]), 4),
        "ema_slow":   round(float(curr["ema_slow"]), 4),
        "rsi":        rsi,
        "atr":        round(float(curr["atr"]), 4),
        "vol_spike":  vol_spike,
        "trend":      "BULL" if curr["ema_fast"] > curr["ema_slow"] else "BEAR",
        "cross":      ("↑ EMA Cross UP" if cross_up else "↓ EMA Cross DOWN" if cross_down else "No cross this candle"),
        "would_signal": "BUY" if would_buy else ("SELL" if would_sell else "None"),
    }


def run_signal_engine():
    """Analyse all pairs. Generates signals matching TradingView conditions."""
    with get_session() as db:
        if get_config(db, "setup_complete") != "true":
            logger.info("Engine skipped: setup not complete")
            return
        if get_config(db, "bot_active") != "true":
            logger.info("Engine skipped: bot not active")
            return
        pairs_raw = get_config(db, "trading_pairs") or ",".join(RECOMMENDED_PAIRS)
        interval  = get_config(db, "candle_interval") or "15m"
        pairs     = [p.strip() for p in pairs_raw.split(",") if p.strip()]

    logger.info(f"Signal engine running | pairs={pairs} interval={interval}")
    generated = 0
    for pair in pairs:
        try:
            result = _analyse(pair, interval)
            if result:
                generated += 1
        except Exception as e:
            logger.error(f"Engine error {pair}: {e}", exc_info=True)
    logger.info(f"Engine complete | {generated}/{len(pairs)} signals generated")


def _analyse(pair: str, interval: str) -> bool:
    """Returns True if a signal was generated."""
    df = fetch_ohlcv(pair, interval, limit=100)
    if df is None or len(df) < 30:
        logger.warning(f"{pair}: no candle data from any source")
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

    cross_up   = bool(prev["ema_fast"] <= prev["ema_slow"] and curr["ema_fast"] > curr["ema_slow"])
    cross_down = bool(prev["ema_fast"] >= prev["ema_slow"] and curr["ema_fast"] < curr["ema_slow"])

    # Core conditions — match TradingView:
    # BUY:  EMA crossover UP + RSI not overbought
    # SELL: EMA crossover DOWN + RSI not oversold
    # Volume is logged but NOT required (TradingView default strategy doesn't require it)
    buy  = cross_up   and rsi < RSI_OB
    sell = cross_down and rsi > RSI_OS

    # Always log at INFO so visible in Streamlit Cloud logs
    logger.info(
        f"{pair} | price={price:.2f} EMA9={curr['ema_fast']:.2f} "
        f"EMA21={curr['ema_slow']:.2f} RSI={rsi:.1f} "
        f"vol_spike={vol_spike} cross_up={cross_up} cross_down={cross_down} "
        f"-> buy={buy} sell={sell}"
    )

    if not buy and not sell:
        return False

    direction     = TradeDirection.BUY if buy else TradeDirection.SELL
    direction_str = "BUY" if buy else "SELL"
    logger.info(f"*** SIGNAL {direction_str} {pair} @ {price:.4f} RSI={rsi:.1f} ***")

    # Cooldown: skip if signal for same pair in last 60s
    with get_session() as db:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)
        recent = db.execute(
            select(Signal).where(
                and_(
                    Signal.pair == pair.upper(),
                    Signal.received_at >= cutoff,
                    Signal.rejected == False,
                )
            ).limit(1)
        ).scalar_one_or_none()
        if recent:
            logger.info(f"{pair}: in cooldown, skipping")
            return False

        if buy:
            max_t  = int(get_config(db, "max_open_trades") or "3")
            open_c = len(db.execute(
                select(Trade).where(Trade.status == TradeStatus.OPEN)
            ).scalars().all())
            if open_c >= max_t:
                logger.info(f"Max {max_t} trades open — skipping")
                return False

        sig = Signal(
            source      = SignalSource.SYSTEM,
            direction   = direction,
            pair        = pair.upper(),
            price       = price,
            atr         = atr_val,
            raw_payload = f"{interval}|rsi={rsi:.1f}|vol={vol_spike}|cross_up={cross_up}",
            processed   = False,
            rejected    = False,
        )
        db.add(sig)
        db.flush()
        db.commit()
        db.refresh(sig)

    logger.info(f"Signal #{sig.id} saved — sizing position")

    # Position sizing — fund is in USD from Delta API, price is in USD
    from backend.services.position_sizer import calculate_stop_loss, calculate_quantity, calculate_tp
    from backend.services.fund_manager import get_available_fund

    with get_session() as db:
        sl_type = get_config(db, "stop_loss_type") or "fixed"
        sl_pct  = float(get_config(db, "stop_loss_fixed_pct") or "2")
        risk    = float(get_config(db, "risk_per_trade_pct") or "2")

    fund_usd = get_available_fund()   # always USD from Delta API
    sl       = calculate_stop_loss(direction_str, price, sl_type, sl_pct, atr_val)
    qty      = calculate_quantity(fund_usd, risk, price, sl)
    tp       = calculate_tp(direction_str, price, sl)

    if qty <= 0:
        logger.warning(f"Position size 0 for {pair} — fund_usd={fund_usd:.2f}")
        return False

    with get_session() as db:
        from backend.models.db_models import OrderType
        trade = Trade(
            signal_id         = sig.id,
            pair              = pair.upper(),
            direction         = direction,
            order_type        = OrderType.MARKET,
            status            = TradeStatus.PENDING,
            quantity          = Decimal(str(qty)),
            stop_loss_price   = Decimal(str(sl)),
            take_profit_price = Decimal(str(tp)),
            fund_at_entry     = Decimal(str(round(fund_usd, 2))),
            notes             = f"Engine|{interval}|rsi={rsi:.1f}|vol={vol_spike}",
        )
        db.add(trade)
        db.flush()
        db.commit()
        db.refresh(trade)

    logger.info(f"Trade #{trade.id} created — qty={qty:.6f} sl={sl:.2f} tp={tp:.2f}")

    try:
        from backend.services.trade_executor import execute_trade
        execute_trade(trade.id)
    except Exception as e:
        logger.error(f"Executor failed: {e}")

    return True
