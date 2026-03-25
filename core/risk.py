"""
Gestione rischio multi-coppia.
Tiene traccia di tutte le posizioni aperte e applica limiti globali.
Le posizioni vengono salvate su file JSON e sopravvivono ai riavvii.
"""

import logging
import time
import json
import os
try:
    from core.telegram_notify import notify_open, notify_close, notify_stop_loss
    TELEGRAM_OK = True
except Exception:
    TELEGRAM_OK = False
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

POSITIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "positions.json")


@dataclass
class Position:
    pair:         str
    base_token:   str
    quote_token:  str
    entry_price:  float
    entry_usdc:   float
    stop_loss:    float
    take_profit:  float
    opened_at:    float = field(default_factory=time.time)

    @property
    def age_minutes(self) -> float:
        return (time.time() - self.opened_at) / 60

    def pnl_pct(self, current_price: float) -> float:
        if self.entry_price == 0:
            return 0.0
        return (current_price - self.entry_price) / self.entry_price

    def to_dict(self) -> dict:
        return {
            "pair": self.pair, "base_token": self.base_token,
            "quote_token": self.quote_token, "entry_price": self.entry_price,
            "entry_usdc": self.entry_usdc, "stop_loss": self.stop_loss,
            "take_profit": self.take_profit, "opened_at": self.opened_at,
        }

    @staticmethod
    def from_dict(d: dict):
        return Position(
            pair=d["pair"], base_token=d["base_token"], quote_token=d["quote_token"],
            entry_price=d["entry_price"], entry_usdc=d["entry_usdc"],
            stop_loss=d["stop_loss"], take_profit=d["take_profit"],
            opened_at=d.get("opened_at", time.time()),
        )


class RiskManager:
    def __init__(self, risk_cfg: dict):
        self.cfg = risk_cfg
        self.positions: dict[str, Position] = {}
        self._loss_cooldowns: dict[str, int] = {}
        self._load_positions()

    # ── Persistenza ───────────────────────────────────────────────────────────

    def _load_positions(self) -> None:
        try:
            if os.path.exists(POSITIONS_FILE):
                with open(POSITIONS_FILE) as f:
                    data = json.load(f)
                for pair, d in data.items():
                    self.positions[pair] = Position.from_dict(d)
                if self.positions:
                    logger.info("[RISK] Posizioni ripristinate dal file: " + str(list(self.positions.keys())))
        except Exception as e:
            logger.warning("[RISK] Errore caricamento posizioni: " + str(e))

    def _save_positions(self) -> None:
        try:
            with open(POSITIONS_FILE, "w") as f:
                json.dump({k: v.to_dict() for k, v in self.positions.items()}, f, indent=2)
        except Exception as e:
            logger.warning("[RISK] Errore salvataggio posizioni: " + str(e))

    # ── Checks globali ────────────────────────────────────────────────────────

    def can_open_new_position(self, pair: str, usdc_balance: float) -> tuple:
        if self._loss_cooldowns.get(pair, 0) > 0:
            return False, "Cooldown attivo dopo loss (" + str(self._loss_cooldowns[pair]) + " cicli)"
        if len(self.positions) >= self.cfg["max_open_positions"]:
            return False, "Limite posizioni raggiunto (" + str(self.cfg["max_open_positions"]) + ")"
        allocated = sum(p.entry_usdc for p in self.positions.values())
        if usdc_balance > 0:
            pct_allocated = allocated / (allocated + usdc_balance)
            if pct_allocated >= self.cfg["max_portfolio_pct"]:
                return False, "Portafoglio troppo allocato (" + str(round(pct_allocated*100)) + "%)"
        if pair in self.positions:
            return False, "Posizione gia aperta su questa coppia"
        return True, "OK"

    def calc_trade_size(self, pair: str, pair_cfg: dict, usdc_balance: float,
                        confidence: float, size_mult: float = 1.0) -> float:
        available = usdc_balance - self.cfg["min_usdc_reserve"]
        if available <= 0:
            return 0.0
        pair_max_pct = pair_cfg.get("max_trade_pct", 0.15)
        scale = max(0.5, confidence)
        size  = available * pair_max_pct * scale * size_mult
        max_single = available * 0.30
        return round(min(size, max_single, available), 2)

    def has_enough_gas(self, eth_balance: float) -> bool:
        ok = eth_balance >= self.cfg["min_eth_gas"]
        if not ok:
            logger.warning("[WARN] ETH gas insufficiente: " + str(eth_balance) + " ETH")
        return ok

    # ── Apri / chiudi posizione ───────────────────────────────────────────────

    def open_position(self, pair: str, base_token: str, quote_token: str,
                      entry_price: float, usdc_spent: float, rr_ratio: float = 3.0) -> Position:
        sl = entry_price * (1 - self.cfg["stop_loss_pct"])
        tp_pct = self.cfg["stop_loss_pct"] * rr_ratio
        tp = entry_price * (1 + tp_pct)
        pos = Position(pair=pair, base_token=base_token, quote_token=quote_token,
                       entry_price=entry_price, entry_usdc=usdc_spent,
                       stop_loss=sl, take_profit=tp)
        self.positions[pair] = pos
        self._save_positions()
        if TELEGRAM_OK:
            try: notify_open(pair, usdc_spent, entry_price, sl, tp, rr_ratio)
            except Exception: pass
        logger.info("[OPEN] [" + pair + "] Aperta @ " + str(round(entry_price,4)) +
                    " | USDC=" + str(usdc_spent) + " | SL=" + str(round(sl,4)) +
                    " | TP=" + str(round(tp,4)) + " | R:R=" + str(int(rr_ratio)) + ":1")
        return pos

    def close_position(self, pair: str, current_price: float, reason: str = "") -> float:
        pos = self.positions.pop(pair, None)
        if not pos:
            return 0.0
        self._save_positions()
        pnl_pct  = pos.pnl_pct(current_price)
        pnl_usdc = pos.entry_usdc * pnl_pct
        logger.info("[CLOSE] [" + pair + "] Chiusa @ " + str(round(current_price,4)) +
                    " | PnL=" + str(round(pnl_pct*100,1)) + "% (" +
                    str(round(pnl_usdc,2)) + " USDC) | " + reason)
        if TELEGRAM_OK:
            try:
                if reason == "stop_loss": notify_stop_loss(pair, current_price, pnl_usdc)
                else: notify_close(pair, current_price, pnl_pct, pnl_usdc, reason)
            except Exception: pass
        if reason == "stop_loss":
            self._loss_cooldowns[pair] = self.cfg["cooldown_after_loss"]
            logger.info("[WAIT] [" + pair + "] Cooldown attivato per " +
                        str(self.cfg["cooldown_after_loss"]) + " cicli")
        return pnl_usdc

    # ── Tick cooldowns ────────────────────────────────────────────────────────

    def tick_cooldowns(self) -> None:
        for pair in list(self._loss_cooldowns.keys()):
            if self._loss_cooldowns[pair] > 0:
                self._loss_cooldowns[pair] -= 1

    # ── Check SL/TP ───────────────────────────────────────────────────────────

    def check_exit(self, pair: str, current_price: float) -> tuple:
        pos = self.positions.get(pair)
        if not pos:
            return False, ""
        if current_price <= pos.stop_loss:
            return True, "stop_loss"
        if current_price >= pos.take_profit:
            return True, "take_profit"
        return False, ""

    # ── Summary ───────────────────────────────────────────────────────────────

    def portfolio_summary(self, current_prices: dict) -> dict:
        total_entry = sum(p.entry_usdc for p in self.positions.values())
        total_pnl   = sum(p.entry_usdc * p.pnl_pct(current_prices.get(name, p.entry_price))
                          for name, p in self.positions.items())
        return {"open_positions": len(self.positions), "total_allocated": total_entry,
                "unrealized_pnl": total_pnl, "pairs": list(self.positions.keys())}
