import os
import requests
import logging

logger = logging.getLogger(__name__)

TELEGRAM_TOKEN   = os.getenv('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning('[TG] Token o chat_id mancanti')
        return False
    try:
        url = 'https://api.telegram.org/bot' + TELEGRAM_TOKEN + '/sendMessage'
        resp = requests.post(url, json={
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message,
            'parse_mode': 'Markdown'
        }, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        logger.warning('[TG] Errore invio: ' + str(e))
        return False
