"""
Modulo Sentiment - Versione Disabilitata (Neutralizzata)
Tutte le funzioni restituiscono valori neutri per non influenzare la strategia tecnica.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

class SentimentZone(str, Enum):
    EXTREME_FEAR  = "EXTREME_FEAR"
    FEAR          = "FEAR"
    NEUTRAL       = "NEUTRAL"
    GREED         = "GREED"
    EXTREME_GREED = "EXTREME_GREED"

@dataclass
class SentimentData:
    fear_greed_value:  int              # 0–100
    fear_greed_zone:   SentimentZone
    market_cap_change: float            # % 24h global market cap
    btc_dominance:     float            # % BTC dominance
    token_votes_up:    Optional[float] = None
    token_votes_down:  Optional[float] = None

    @property
    def is_buy_favorable(self) -> bool:
        # SEMPRE TRUE: Ignora il filtro sentiment per i BUY
        return True

    @property
    def is_sell_favorable(self) -> bool:
        # DISABILITATO
        return False

    @property
    def confidence_multiplier(self) -> float:
        # SEMPRE 1.0: Non modifica la confidence tecnica
        return 1.0

    def summary(self) -> str:
        return "[DISABILITATO] Sentiment ignorato (Neutral Mode)"

def get_sentiment(coingecko_id: Optional[str] = None) -> SentimentData:
    """
    Versione neutralizzata di get_sentiment.
    Restituisce dati fissi per evitare ogni influenza sulla strategia.
    """
    sd = SentimentData(
        fear_greed_value  = 50,
        fear_greed_zone   = SentimentZone.NEUTRAL,
        market_cap_change = 0.0,
        btc_dominance     = 50.0,
    )
    logger.info(f"[INFO] Sentiment: {sd.summary()}")
    return sd