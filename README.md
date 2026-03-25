# 🤖 Multi-Pair Crypto Trading Agent — Base DEX

Bot di trading automatico per **10 coppie ad alta liquidità** su **Uniswap v3** e **Aerodrome** (Base chain).

---

## 📊 Coppie supportate

| # | Coppia | DEX | Liquidità | Note |
|---|--------|-----|-----------|------|
| 1 | WETH/USDC | Best Price | ⭐⭐⭐⭐⭐ | Coppia più liquida su Base, >$100M/giorno |
| 2 | cbBTC/USDC | Best Price | ⭐⭐⭐⭐ | Bitcoin nativo su Base, ~$20M liquidità |
| 3 | cbETH/USDC | Best Price | ⭐⭐⭐⭐ | ETH staked Coinbase, alta liquidità |
| 4 | wstETH/USDC | Uniswap v3 | ⭐⭐⭐⭐ | Lido staked ETH, pool da $100M+ |
| 5 | AERO/USDC | Aerodrome | ⭐⭐⭐ | Token nativo Aerodrome, ~$15M liquidità |
| 6 | VIRTUAL/USDC | Best Price | ⭐⭐⭐ | AI agents su Base, alto volume |
| 7 | BRETT/USDC | Best Price | ⭐⭐⭐ | Top memecoin su Base |
| 8 | DEGEN/USDC | Aerodrome | ⭐⭐⭐ | Token Farcaster, alto volume community |
| 9 | WELL/USDC | Best Price | ⭐⭐⭐ | Token Moonwell (lending su Base) |
| 10 | cbBTC/WETH | Uniswap v3 | ⭐⭐⭐ | Cross-pair BTC/ETH per arbitraggio ratio |

---

## 📁 Struttura

```
crypto_agent/
├── agent.py              # ← entry point, loop multi-pair
├── config.py             # ← tutte le coppie e parametri
├── requirements.txt
├── core/
│   ├── wallet.py         # generazione e cifratura wallet
│   ├── data.py           # fetch OHLCV parallelo per N coppie
│   └── risk.py           # risk manager multi-posizione
├── strategies/
│   ├── indicators.py     # EMA, SMA, RSI, BB, MACD
│   └── strategy.py       # logica segnali BUY/SELL/HOLD
└── dex/
    └── client.py         # swap Uniswap v3 + Aerodrome
```

---

## ⚡ Setup

```bash
pip install -r requirements.txt
python agent.py --new-wallet        # crea wallet cifrato
python agent.py --dry-run           # simulazione tutte le coppie
python agent.py --status            # stato balances
```

### Selezionare solo alcune coppie

```bash
# Solo le 3 più liquide
python agent.py --dry-run --pairs "WETH/USDC" "cbBTC/USDC" "wstETH/USDC"

# Oppure modifica in config.py:
AGENT = {
    "enabled_pairs": ["WETH/USDC", "cbBTC/USDC", "AERO/USDC"],
    ...
}
```

---

## 🧠 Logica strategia (per ogni coppia)

Ogni coppia viene analizzata **indipendentemente** con 5 indicatori:

| Indicatore | Segnale BUY | Segnale SELL |
|---|---|---|
| EMA crossover | EMA fast > slow | EMA fast < slow |
| SMA trend | Prezzo > SMA50 | Prezzo < SMA50 |
| RSI | < 35 ipervenduto | > 65 ipercomprato |
| Bollinger Bands | Prezzo vicino BB bassa | Prezzo vicino BB alta |
| MACD | Histogram positivo | Histogram negativo |

Servono **≥ 3/5 segnali concordi** con **confidence ≥ 60%** per eseguire.

---

## 🛡️ Gestione rischio multi-pair

- **Max 4 posizioni aperte** contemporaneamente
- **Max 70% portafoglio** allocato in totale
- **Stop-loss**: -4% dal prezzo di entrata
- **Take-profit**: +8% dal prezzo di entrata
- **Cooldown**: 2 cicli di pausa dopo uno stop-loss su una coppia
- **Size per coppia**: variabile (10–25% disponibile × confidence)
- **Riserva**: 20 USDC e 0.003 ETH sempre intoccabili

---

## ⚠️ Disclaimer

Software a scopo educativo. Il trading di criptovalute è ad alto rischio.  
Non investire più di quanto sei disposto a perdere.
