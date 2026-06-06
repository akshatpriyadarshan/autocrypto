"""Free market data — Binance public API, no key needed."""
from typing import Optional
import pandas as pd
import httpx
from loguru import logger

BINANCE = "https://api.binance.com/api/v3"
KUCOIN  = "https://api.kucoin.com/api/v1"
TF_MAP  = {"1m":"1m","5m":"5m","15m":"15m","1h":"1h","4h":"4h","1d":"1d"}
KC_MAP  = {"1m":"1min","5m":"5min","15m":"15min","1h":"1hour","4h":"4hour","1d":"1day"}

async def fetch_ohlcv(pair: str, interval: str = "15m", limit: int = 100) -> Optional[pd.DataFrame]:
    symbol = pair.replace("/","").upper()
    tf     = TF_MAP.get(interval, "15m")
    df     = await _binance(symbol, tf, limit)
    if df is None:
        df = await _kucoin(pair, interval, limit)
    return df

async def fetch_price(pair: str) -> Optional[float]:
    symbol = pair.replace("/","").upper()
    try:
        async with httpx.AsyncClient(timeout=5.0) as c:
            r = await c.get(f"{BINANCE}/ticker/price", params={"symbol": symbol})
            r.raise_for_status()
            return float(r.json()["price"])
    except Exception as e:
        logger.warning(f"price fetch {pair}: {e}")
        return None

async def _binance(symbol: str, tf: str, limit: int) -> Optional[pd.DataFrame]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{BINANCE}/klines", params={"symbol":symbol,"interval":tf,"limit":limit})
            r.raise_for_status()
        cols = ["timestamp","open","high","low","close","volume",
                "ct","qv","n","tbb","tbq","ig"]
        df = pd.DataFrame(r.json(), columns=cols)[["timestamp","open","high","low","close","volume"]].copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        for col in ["open","high","low","close","volume"]:
            df[col] = df[col].astype(float)
        return df.sort_values("timestamp").reset_index(drop=True)
    except Exception as e:
        logger.warning(f"Binance {symbol}: {e}")
        return None

async def _kucoin(pair: str, interval: str, limit: int) -> Optional[pd.DataFrame]:
    symbol = pair.replace("/","-").upper()
    tf     = KC_MAP.get(interval, "15min")
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{KUCOIN}/market/candles", params={"symbol":symbol,"type":tf})
            r.raise_for_status()
        data = r.json().get("data",[])
        if not data: return None
        df = pd.DataFrame(data, columns=["timestamp","open","close","high","low","volume","amount"])
        df = df[["timestamp","open","high","low","close","volume"]].copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="s", utc=True)
        for col in ["open","high","low","close","volume"]:
            df[col] = df[col].astype(float)
        return df.sort_values("timestamp").reset_index(drop=True).tail(limit).reset_index(drop=True)
    except Exception as e:
        logger.warning(f"KuCoin {pair}: {e}")
        return None
