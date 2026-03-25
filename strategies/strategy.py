"""
Strategia multi-indicatore con upgrade Druckenmiller:
  1. Multi-Timeframe Analysis  — bias 4H/1D blocca segnali contro-trend
  2. ADX Filter                — mercati in ranging alzano la soglia conferme
  3. Conviction Sizing         — position size scala con qualita segnale
  4. R:R Dinamico              — da 3:1 base fino a 4:1 ad alta conviction
"""

from dataclasses import dataclass, field
from enum import Enum
import logging
import pandas as pd

from strategies.indicators import get_htf_bias

logger = logging.getLogger(__name__)


class Signal(str, Enum):
    BUY  = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class TradeSignal:
    signal:       Signal
    confidence:   float
    reasons:      list
    price:        float
    rsi:          float
    macd_hist:    float
    pair:         str   = ""
    # --- Druckenmiller additions ---
    adx:          float = 0.0
    htf_bias:     str   = "neutral"   # "bull" | "bear" | "neutral"
    size_mult:    float = 1.0         # conviction size multiplier
    rr_ratio:     float = 3.0         # dynamic Risk:Reward ratio
    confirmations: int  = 0           # numero conferme concordi


def evaluate(df: pd.DataFrame, cfg: dict, pair: str = "") -> TradeSignal:
    if len(df) < 2:
        return TradeSignal(Signal.HOLD, 0.0, ["Dati insufficienti"], 0, 0, 0, pair)

    last  = df.iloc[-1]
    prev  = df.iloc[-2]
    price = float(last["close"])

    # ── ADX ──────────────────────────────────────────────────────────────────
    adx_val    = float(last.get("adx", 0))
    adx_thresh = cfg.get("adx_trend_threshold", 20)
    is_ranging = adx_val < adx_thresh and adx_val > 0

    # In ranging: richiedi piu conferme per agire
    base_min_conf = cfg.get("min_confirmations", 3)       # default: 3/5
    min_conf      = base_min_conf + 1 if is_ranging else base_min_conf

    # ── Multi-Timeframe Bias ──────────────────────────────────────────────────
    htf = get_htf_bias(df)
    htf_bias = htf["bias"]   # "bull" | "bear" | "neutral"

    # ── Indicatori (invariati) ────────────────────────────────────────────────
    buy_reasons  = []
    sell_reasons = []

    # 1. EMA crossover
    cross_up   = prev["ema_fast"] <= prev["ema_slow"] and last["ema_fast"] > last["ema_slow"]
    cross_down = prev["ema_fast"] >= prev["ema_slow"] and last["ema_fast"] < last["ema_slow"]

    if cross_up:
        buy_reasons.append("EMA crossover rialzista")
    elif last["ema_fast"] > last["ema_slow"]:
        buy_reasons.append("EMA fast > slow (trend up)")

    if cross_down:
        sell_reasons.append("EMA crossover ribassista")
    elif last["ema_fast"] < last["ema_slow"]:
        sell_reasons.append("EMA fast < slow (trend down)")

    # 2. SMA trend filter
    if price > last["sma_trend"]:
        buy_reasons.append(f"Prezzo > SMA{cfg['sma_trend']}")
    else:
        sell_reasons.append(f"Prezzo < SMA{cfg['sma_trend']}")

    # 3. RSI
    rsi = float(last["rsi"])
    if rsi < cfg["rsi_oversold"]:
        buy_reasons.append(f"RSI ipervenduto ({rsi:.1f})")
    elif rsi > cfg["rsi_overbought"]:
        sell_reasons.append(f"RSI ipercomprato ({rsi:.1f})")
    elif rsi >= 50:
        buy_reasons.append(f"RSI sopra 50 ({rsi:.1f})")
    else:
        sell_reasons.append(f"RSI sotto 50 ({rsi:.1f})")

    # 4. Bollinger Bands
    bb_pct = float(last["bb_pct"])
    if bb_pct < 0.2:
        buy_reasons.append(f"Prezzo vicino BB inferiore ({bb_pct:.2f})")
    elif bb_pct > 0.8:
        sell_reasons.append(f"Prezzo vicino BB superiore ({bb_pct:.2f})")

    # 5. MACD
    macd_hist = float(last["macd_hist"])
    prev_hist = float(prev["macd_hist"])
    if macd_hist > 0 and prev_hist <= 0:
        buy_reasons.append("MACD histogram cross zero ^")
    elif macd_hist > 0:
        buy_reasons.append(f"MACD hist positivo ({macd_hist:.5f})")
    if macd_hist < 0 and prev_hist >= 0:
        sell_reasons.append("MACD histogram cross zero v")
    elif macd_hist < 0:
        sell_reasons.append(f"MACD hist negativo ({macd_hist:.5f})")

    n_buy  = len(buy_reasons)
    n_sell = len(sell_reasons)
    total  = n_buy + n_sell

    # ── Multi-Timeframe Filter ────────────────────────────────────────────────
    # Blocca segnali contro il bias HTF (solo se bias non e neutro)
    htf_blocked = False
    if htf_bias == "bear" and n_buy >= min_conf and n_buy > n_sell:
        htf_blocked = True
        logger.info(f"[MTF] [{pair}] BUY bloccato: bias 4H/1D ribassista")
    elif htf_bias == "bull" and n_sell >= min_conf and n_sell > n_buy:
        htf_blocked = True
        logger.info(f"[MTF] [{pair}] SELL bloccato: bias 4H/1D rialzista")

    # ── Segnale finale ────────────────────────────────────────────────────────
    if not htf_blocked and n_buy >= min_conf and n_buy > n_sell:
        confirmations = n_buy
        confidence    = n_buy / total if total else 0
        signal, reasons = Signal.BUY, buy_reasons
    elif not htf_blocked and n_sell >= min_conf and n_sell > n_buy:
        confirmations = n_sell
        confidence    = n_sell / total if total else 0
        signal, reasons = Signal.SELL, sell_reasons
    else:
        confirmations = 0
        confidence    = 0.0
        signal, reasons = Signal.HOLD, buy_reasons + sell_reasons

    # ── Conviction Sizing (Druckenmiller) ─────────────────────────────────────
    # 3-4 conferme -> 1.0x | 5-6 -> 1.5x | 7-8 -> 2.0x
    size_mult = _conviction_size(confirmations, cfg)

    # ── R:R Dinamico (Druckenmiller) ──────────────────────────────────────────
    # Base 3:1, fino a 4:1 con alta conviction
    rr_ratio = _dynamic_rr(confirmations, cfg)

    result = TradeSignal(
        signal        = signal,
        confidence    = round(confidence, 3),
        reasons       = reasons,
        price         = price,
        rsi           = rsi,
        macd_hist     = macd_hist,
        pair          = pair,
        adx           = round(adx_val, 1),
        htf_bias      = htf_bias,
        size_mult     = size_mult,
        rr_ratio      = rr_ratio,
        confirmations = confirmations,
    )

    icon = {"BUY": "[BUY]", "SELL": "[SELL]", "HOLD": "[HOLD]"}[signal]
    adx_tag  = f"ADX={adx_val:.0f}({'ranging' if is_ranging else 'trend'})"
    bias_tag = f"HTF={htf_bias}"
    size_tag = f"size={size_mult:.1f}x" if signal != Signal.HOLD else ""
    rr_tag   = f"R:R={rr_ratio:.0f}:1"  if signal != Signal.HOLD else ""

    logger.info(
        f"{icon} [{pair}] {signal} | prezzo={price:.4f} | conf={confidence:.0%} "
        f"| RSI={rsi:.1f} | {adx_tag} | {bias_tag}"
        + (f" | {size_tag} | {rr_tag}" if signal != Signal.HOLD else "")
    )
    return result


# ── Helpers Druckenmiller ─────────────────────────────────────────────────────

def _conviction_size(confirmations: int, cfg: dict) -> float:
    """
    Scala il position size in base al numero di conferme concordi.
    Thresholds configurabili in STRATEGY["conviction_sizing"].
    """
    tiers = cfg.get("conviction_sizing", {
        "low":    {"min_conf": 3, "mult": 1.0},
        "medium": {"min_conf": 5, "mult": 1.5},
        "high":   {"min_conf": 7, "mult": 2.0},
    })
    mult = 1.0
    for tier in sorted(tiers.values(), key=lambda x: x["min_conf"]):
        if confirmations >= tier["min_conf"]:
            mult = tier["mult"]
    return mult


def _dynamic_rr(confirmations: int, cfg: dict) -> float:
    """
    R:R base 3:1, incrementa fino a 4:1 per segnali ad alta conviction.
    """
    rr_base = cfg.get("rr_base", 3.0)
    rr_max  = cfg.get("rr_max",  4.0)
    high_conf_threshold = cfg.get("rr_high_conf_threshold", 7)

    if confirmations >= high_conf_threshold:
        return rr_max
    return rr_base
