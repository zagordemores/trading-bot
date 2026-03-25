#!/usr/bin/env python3
"""
Telegram notifier per il trading bot.
Invia alert su BUY/SELL/stop_loss e report giornaliero.
"""
import requests
import logging

logger = logging.getLogger(__name__)

TELEGRAM_TOKEN   = "8534718892:AAEYk9SSPdHLXZAoHp_L3xhndRSrQ3IB-LA"
TELEGRAM_CHAT_ID = "269160215"

def send_message(text: str) -> bool:
    try:
        url = "https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage"
        resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        logger.warning("Telegram error: " + str(e))
        return False

def notify_open(pair: str, usdc: float, price: float, sl: float, tp: float, rr: float):
    msg = "🟢 <b>BUY " + pair + "</b>\n"
    msg += "💵 " + str(round(usdc,2)) + " USDC @ " + str(round(price,4)) + "\n"
    msg += "🛑 SL: " + str(round(sl,4)) + "\n"
    msg += "🎯 TP: " + str(round(tp,4)) + "\n"
    msg += "📊 R:R " + str(int(rr)) + ":1"
    send_message(msg)

def notify_close(pair: str, price: float, pnl_pct: float, pnl_usdc: float, reason: str):
    icon = "✅" if pnl_usdc >= 0 else "❌"
    msg = icon + " <b>CLOSE " + pair + "</b>\n"
    msg += "💵 @ " + str(round(price,4)) + "\n"
    msg += "📈 PnL: " + str(round(pnl_pct*100,1)) + "% (" + str(round(pnl_usdc,2)) + " USDC)\n"
    msg += "📋 " + reason
    send_message(msg)

def notify_stop_loss(pair: str, price: float, pnl_usdc: float):
    msg = "🚨 <b>STOP LOSS " + pair + "</b>\n"
    msg += "💵 @ " + str(round(price,4)) + "\n"
    msg += "📉 Perso: " + str(round(pnl_usdc,2)) + " USDC"
    send_message(msg)

def send_daily_report(n_pos: int, allocated: float, pnl_unrealized: float,
                      n_trades: int, win_rate: float, pnl_total: float):
    icon = "📈" if pnl_unrealized >= 0 else "📉"
    msg = "🤖 <b>REPORT GIORNALIERO</b>\n\n"
    msg += icon + " PnL unrealizzato: " + str(round(pnl_unrealized,2)) + " USDC\n"
    msg += "📊 Posizioni aperte: " + str(n_pos) + " (" + str(round(allocated,2)) + " USDC)\n"
    msg += "🔢 Trade totali: " + str(n_trades) + "\n"
    msg += "🏆 Win rate: " + str(round(win_rate)) + "%\n"
    msg += "💰 PnL totale: " + str(round(pnl_total,2)) + " USDC"
    send_message(msg)

def send_status(positions: dict, usdc: float, eth: float):
    msg = "📊 <b>STATO BOT</b>\n\n"
    msg += "💵 USDC: " + str(round(usdc,2)) + "\n"
    msg += "⛽ ETH: " + str(round(eth,5)) + "\n\n"
    if positions:
        msg += "📂 <b>Posizioni aperte:</b>\n"
        for pair, pos in positions.items():
            msg += "• " + pair + " @ " + str(round(pos.entry_price,4)) + "\n"
    else:
        msg += "📂 Nessuna posizione aperta"
    send_message(msg)
