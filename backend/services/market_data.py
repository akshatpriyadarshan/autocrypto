"""
Market data — sync httpx calls to Binance/KuCoin public APIs.
No async, no aiosqlite, works on Python 3.14.
"""
from typing import Optional
import pandas as pd
import httpx
from loguru import logger

BINANCE = "https://api.binance.com/api/v3"
KUCOIN  = "https://api.kucoin.com/api/v1"
TF_MAP  = {"1m":"1m","5m":"5m","15m":"15m","1h":"1h","4h":"4h","1d":"1d"}
KC_MAP  = {"1m":"1min","5m":"5min","15m":"15min","1h":"1hour","4h":"4hour","1d":"1day"}


def fetch_ohlcv(pair: str, interval: str = "15m", limit: int = 100) -> Optional[pd.DataFrame]:
    """Fetch OHLCV candles. Returns DataFrame or None."""
    symbol = pair.replace("/", "").upper()
    tf     = TF_MAP.get(interval, "15m")
    df     = _binance(symbol, tf, limit)
    if df is None:
        logger.warning(f"Binance failed for {pair} — trying KuCoin")
        df = _kucoin(pair, interval, limit)
    return df


def fetch_price(pair: str) -> Optional[float]:
    """Quick single price — no candle overhead."""
    symbol = pair.replace("/", "").upper()
    try:
        r = httpx.get(f"{BINANCE}/ticker/price",
                      params={"symbol": symbol}, timeout=5.0)
        r.raise_for_status()
        return float(r.json()["price"])
    except Exception as e:
        logger.warning(f"fetch_price {pair}: {e}")
        return None


def _binance(symbol: str, tf: str, limit: int) -> Optional[pd.DataFrame]:
    try:
        r = httpx.get(f"{BINANCE}/klines",
                      params={"symbol": symbol, "interval": tf, "limit": limit},
                      timeout=10.0)
        r.raise_for_status()
        cols = ["timestamp","open","high","low","close","volume",
                "ct","qv","n","tbb","tbq","ig"]
        df = pd.DataFrame(r.json(), columns=cols)[
            ["timestamp","open","high","low","close","volume"]].copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        for c in ["open","high","low","close","volume"]:
            df[c] = df[c].astype(float)
        df = df.sort_values("timestamp").reset_index(drop=True)
        logger.debug(f"Binance {symbol} {tf}: {len(df)} candles")
        return df
    except Exception as e:
        logger.warning(f"Binance {symbol}: {e}")
        return None


def _kucoin(pair: str, interval: str, limit: int) -> Optional[pd.DataFrame]:
    symbol = pair.replace("/", "-").upper()
    tf     = KC_MAP.get(interval, "15min")
    try:
        r = httpx.get(f"{KUCOIN}/market/candles",
                      params={"symbol": symbol, "type": tf},
                      timeout=10.0)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            return None
        df = pd.DataFrame(data,
                          columns=["timestamp","open","close","high","low","volume","amount"])
        df = df[["timestamp","open","high","low","close","volume"]].copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="s", utc=True)
        for c in ["open","high","low","close","volume"]:
            df[c] = df[c].astype(float)
        df = df.sort_values("timestamp").reset_index(drop=True)
        df = df.tail(limit).reset_index(drop=True)
        logger.debug(f"KuCoin {symbol} {tf}: {len(df)} candles")
        return df
    except Exception as e:
        logger.warning(f"KuCoin {pair}: {e}")
        return None
