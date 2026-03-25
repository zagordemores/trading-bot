"""
Calcolo indicatori tecnici su DataFrame OHLCV.
Druckenmiller upgrade:
  - ADX per filtrare mercati in ranging
  - Resample 4H/1D per multi-timeframe bias
"""

import pandas as pd
import numpy as np


def add_all_indicators(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    df = df.copy()
    df = _add_ema(df, cfg["ema_fast"],  "ema_fast")
    df = _add_ema(df, cfg["ema_slow"],  "ema_slow")
    df = _add_sma(df, cfg["sma_trend"], "sma_trend")
    df = _add_rsi(df, cfg["rsi_period"])
    df = _add_bollinger(df, cfg["bb_period"], cfg["bb_std"])
    df = _add_macd(df, cfg["macd_fast"], cfg["macd_slow"], cfg["macd_signal"])
    df = _add_adx(df, cfg.get("adx_period", 14))
    return df.dropna()


def get_htf_bias(df: pd.DataFrame) -> dict:
    """
    Calcola il bias direzionale sui timeframe superiori (4H e 1D)
    resamplandoli dai dati 1H gia disponibili.

    Ritorna:
        {
            "4H": "bull" | "bear" | "neutral",
            "1D": "bull" | "bear" | "neutral",
            "bias": "bull" | "bear" | "neutral"   # consenso finale
        }
    """
    if df is None or len(df) < 50:
        return {"4H": "neutral", "1D": "neutral", "bias": "neutral"}

    results = {}
    for tf, bars in [("4H", 4), ("1D", 24)]:
        try:
            df_tf = df["close"].resample(f"{bars}h").ohlc()
            if len(df_tf) < 10:
                results[tf] = "neutral"
                continue

            ema20 = df_tf["close"].ewm(span=20, adjust=False).mean()
            ema50 = df_tf["close"].ewm(span=50, adjust=False).mean()
            last_close = df_tf["close"].iloc[-1]
            last_e20   = ema20.iloc[-1]
            last_e50   = ema50.iloc[-1]

            if last_e20 > last_e50 and last_close > last_e20:
                results[tf] = "bull"
            elif last_e20 < last_e50 and last_close < last_e20:
                results[tf] = "bear"
            else:
                results[tf] = "neutral"
        except Exception:
            results[tf] = "neutral"

    # Consenso: entrambi bull -> bull, entrambi bear -> bear, altrimenti neutral
    if results.get("4H") == "bull" and results.get("1D") == "bull":
        results["bias"] = "bull"
    elif results.get("4H") == "bear" and results.get("1D") == "bear":
        results["bias"] = "bear"
    else:
        results["bias"] = "neutral"

    return results


def _add_ema(df, period, name):
    df[name] = df["close"].ewm(span=period, adjust=False).mean()
    return df


def _add_sma(df, period, name):
    df[name] = df["close"].rolling(window=period).mean()
    return df


def _add_rsi(df, period=14):
    delta    = df["close"].diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    return df


def _add_bollinger(df, period=20, std=2.0):
    mid = df["close"].rolling(window=period).mean()
    sig = df["close"].rolling(window=period).std()
    df["bb_mid"] = mid
    df["bb_up"]  = mid + std * sig
    df["bb_low"] = mid - std * sig
    df["bb_pct"] = (df["close"] - df["bb_low"]) / (df["bb_up"] - df["bb_low"])
    return df


def _add_macd(df, fast=12, slow=26, signal=9):
    ema_f = df["close"].ewm(span=fast,   adjust=False).mean()
    ema_s = df["close"].ewm(span=slow,   adjust=False).mean()
    df["macd"]      = ema_f - ema_s
    df["macd_sig"]  = df["macd"].ewm(span=signal, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_sig"]
    return df


def _add_adx(df, period=14):
    """
    Average Directional Index (ADX).
    ADX > 25  -> mercato in trend   (segnali affidabili)
    ADX < 20  -> mercato in ranging (alzare soglia conferme)
    """
    high  = df["high"]  if "high"  in df.columns else df["close"]
    low   = df["low"]   if "low"   in df.columns else df["close"]
    close = df["close"]

    # True Range
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low  - close.shift()).abs()
    tr  = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Directional Movement
    up_move   = high - high.shift()
    down_move = low.shift() - low

    idx      = df.index
    plus_dm  = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=idx)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=idx)

    atr      = tr.ewm(com=period - 1, min_periods=period).mean()
    plus_di  = 100 * plus_dm.ewm(com=period - 1, min_periods=period).mean() / atr
    minus_di = 100 * minus_dm.ewm(com=period - 1, min_periods=period).mean() / atr

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df["adx"]      = dx.ewm(com=period - 1, min_periods=period).mean()
    df["plus_di"]  = plus_di
    df["minus_di"] = minus_di

    return df
