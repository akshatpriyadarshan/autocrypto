"""
Market data — multiple free public APIs with geo-aware fallback.
Priority: KuCoin → OKX → Bybit → CoinGecko
Binance removed — geo-blocked on Streamlit Cloud (HTTP 451).
All sync httpx calls.
"""
from typing import Optional
import pandas as pd
import httpx
from loguru import logger

KUCOIN  = "https://api.kucoin.com/api/v1"
OKX     = "https://www.okx.com/api/v5/market"
BYBIT   = "https://api.bybit.com/v5/market"

KC_TF  = {"1m":"1min","5m":"5min","15m":"15min","1h":"1hour","4h":"4hour","1d":"1day"}
OKX_TF = {"1m":"1m","5m":"5m","15m":"15m","1h":"1H","4h":"4H","1d":"1D"}
BB_TF  = {"1m":"1","5m":"5","15m":"15","1h":"60","4h":"240","1d":"D"}

TIMEOUT = 10.0


def fetch_ohlcv(pair: str, interval: str = "15m", limit: int = 100) -> Optional[pd.DataFrame]:
    """Try KuCoin → OKX → Bybit in order."""
    for source_fn in [_kucoin, _okx, _bybit]:
        df = source_fn(pair, interval, limit)
        if df is not None and len(df) >= 10:
            return df
    logger.error(f"All data sources failed for {pair}")
    return None


def fetch_price(pair: str) -> Optional[float]:
    """Quick single price check."""
    # Try KuCoin ticker first
    symbol = pair.replace("/", "-").upper()
    try:
        r = httpx.get(f"{KUCOIN}/market/orderbook/level1",
                      params={"symbol": symbol}, timeout=5.0)
        r.raise_for_status()
        data = r.json().get("data", {})
        price = data.get("price")
        if price:
            return float(price)
    except Exception:
        pass

    # OKX fallback
    inst = pair.replace("/", "-").upper()
    try:
        r = httpx.get(f"{OKX}/ticker", params={"instId": inst}, timeout=5.0)
        r.raise_for_status()
        data = r.json().get("data", [{}])
        price = data[0].get("last") if data else None
        if price:
            return float(price)
    except Exception:
        pass

    return None


def _kucoin(pair: str, interval: str, limit: int) -> Optional[pd.DataFrame]:
    symbol = pair.replace("/", "-").upper()
    tf     = KC_TF.get(interval, "15min")
    try:
        r = httpx.get(f"{KUCOIN}/market/candles",
                      params={"symbol": symbol, "type": tf},
                      timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            return None
        df = pd.DataFrame(data, columns=["timestamp","open","close","high","low","volume","amount"])
        df = df[["timestamp","open","high","low","close","volume"]].copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="s", utc=True)
        for c in ["open","high","low","close","volume"]:
            df[c] = df[c].astype(float)
        df = df.sort_values("timestamp").reset_index(drop=True)
        df = df.tail(limit).reset_index(drop=True)
        logger.debug(f"KuCoin {symbol}: {len(df)} candles")
        return df
    except Exception as e:
        logger.warning(f"KuCoin {pair}: {e}")
        return None


def _okx(pair: str, interval: str, limit: int) -> Optional[pd.DataFrame]:
    inst = pair.replace("/", "-").upper()
    tf   = OKX_TF.get(interval, "15m")
    try:
        r = httpx.get(f"{OKX}/candles",
                      params={"instId": inst, "bar": tf, "limit": str(limit)},
                      timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            return None
        # OKX: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
        df = pd.DataFrame(data, columns=["timestamp","open","high","low","close",
                                          "volume","volccy","volccy2","confirm"])
        df = df[["timestamp","open","high","low","close","volume"]].copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms", utc=True)
        for c in ["open","high","low","close","volume"]:
            df[c] = df[c].astype(float)
        df = df.sort_values("timestamp").reset_index(drop=True)
        logger.debug(f"OKX {inst}: {len(df)} candles")
        return df
    except Exception as e:
        logger.warning(f"OKX {pair}: {e}")
        return None


def _bybit(pair: str, interval: str, limit: int) -> Optional[pd.DataFrame]:
    symbol = pair.replace("/", "").upper()
    tf     = BB_TF.get(interval, "15")
    try:
        r = httpx.get(f"{BYBIT}/kline",
                      params={"symbol": symbol, "interval": tf, "limit": str(limit)},
                      timeout=TIMEOUT)
        r.raise_for_status()
        result = r.json().get("result", {})
        data   = result.get("list", [])
        if not data:
            return None
        # Bybit: [startTime, openPrice, highPrice, lowPrice, closePrice, volume, turnover]
        df = pd.DataFrame(data, columns=["timestamp","open","high","low","close","volume","turnover"])
        df = df[["timestamp","open","high","low","close","volume"]].copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms", utc=True)
        for c in ["open","high","low","close","volume"]:
            df[c] = df[c].astype(float)
        df = df.sort_values("timestamp").reset_index(drop=True)
        logger.debug(f"Bybit {symbol}: {len(df)} candles")
        return df
    except Exception as e:
        logger.warning(f"Bybit {pair}: {e}")
        return None
