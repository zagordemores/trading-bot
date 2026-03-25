import os
import requests
from dotenv import load_dotenv

load_dotenv()

token = os.getenv("TELEGRAM_TOKEN")
chat_id = os.getenv("TELEGRAM_CHAT_ID")

message = "🚀 Test Bot: Connessione stabilita! Pronto per monitorare le liquidazioni su Aave."
url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={message}"

response = requests.get(url)
if response.status_code == 200:
    print("✅ Messaggio inviato con successo su Telegram!")
else:
    print(f"❌ Errore: {response.text}")
