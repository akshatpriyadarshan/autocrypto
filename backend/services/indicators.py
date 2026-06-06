"""
Pure pandas/numpy technical indicators.
No C extensions, no pandas-ta, works on Python 3.11-3.14.
"""
import pandas as pd
import numpy as np


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_g = gain.ewm(com=period - 1, adjust=False).mean()
    avg_l = loss.ewm(com=period - 1, adjust=False).mean()
    rs    = avg_g / avg_l.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()


def volume_ma(volume: pd.Series, period: int = 20) -> pd.Series:
    return volume.rolling(period).mean()


def add_indicators(df: pd.DataFrame,
                   ema_fast: int = 9, ema_slow: int = 21,
                   rsi_p: int = 14, atr_p: int = 14,
                   vol_p: int = 20) -> pd.DataFrame:
    """Add all indicators to OHLCV DataFrame. Returns copy with new columns."""
    df = df.copy()
    df["ema_fast"] = ema(df["close"], ema_fast)
    df["ema_slow"] = ema(df["close"], ema_slow)
    df["rsi"]      = rsi(df["close"], rsi_p)
    df["atr"]      = atr(df["high"], df["low"], df["close"], atr_p)
    df["vol_ma"]   = volume_ma(df["volume"], vol_p)
    return df.dropna().reset_index(drop=True)
