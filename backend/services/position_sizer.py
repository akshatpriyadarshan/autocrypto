"""Position sizing and stop-loss calculation."""
from typing import Optional
from loguru import logger

ATR_MULT = 1.5
MIN_SL   = 0.5  # min 0.5% stop-loss

def calculate_stop_loss(direction: str, entry: float, sl_type: str,
                        sl_pct: float, atr: Optional[float] = None) -> float:
    if sl_type == "atr" and atr and atr > 0:
        dist = atr * ATR_MULT
    else:
        dist = entry * (sl_pct / 100)
    dist = max(dist, entry * (MIN_SL / 100))
    sl = (entry - dist) if direction.upper() == "BUY" else (entry + dist)
    return round(sl, 8)

def calculate_quantity(fund: float, risk_pct: float,
                       entry: float, sl: float) -> float:
    if fund <= 0 or entry <= 0: return 0.0
    price_risk = abs(entry - sl)
    if price_risk < 0.000001: return 0.0
    risk_amt = fund * (risk_pct / 100)
    qty = risk_amt / price_risk
    max_qty = (fund / entry) * 0.95
    qty = min(qty, max_qty)
    logger.info(f"Size: fund={fund:.2f} risk={risk_pct}% entry={entry:.4f} sl={sl:.4f} qty={qty:.6f}")
    return round(qty, 8)

def calculate_tp(direction: str, entry: float, sl: float, rr: float = 2.0) -> float:
    dist = abs(entry - sl) * rr
    tp = (entry + dist) if direction.upper() == "BUY" else (entry - dist)
    return round(tp, 8)
