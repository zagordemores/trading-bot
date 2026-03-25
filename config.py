"""
Configurazione centrale — Multi-Pair Trading Agent su Base
"""

# ── Blockchain ────────────────────────────────────────────────────────────────
# RPC pubblici Base — il primo viene usato, gli altri come fallback
# Per evitare i 429 ti consiglio un RPC gratuito dedicato:
#   Alchemy : https://dashboard.alchemy.com  (crea account, scegli Base, copia URL)
#   Infura  : https://infura.io              (stesso procedimento)
# Incolla il tuo URL personale come primo elemento della lista:
BASE_RPC_URLS = [
    "https://base-mainnet.g.alchemy.com/v2/-I-sWHGGxO81wWiwOx90h",
    "https://base.llamarpc.com",
    "https://base-rpc.publicnode.com",
]
BASE_RPC_URL  = BASE_RPC_URLS[0]
BASE_CHAIN_ID = 8453

# ── Token addresses su Base ───────────────────────────────────────────────────
TOKENS = {
    "USDC":    "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",  # 6 decimali
    "WETH":    "0x4200000000000000000000000000000000000006",  # 18 decimali
    "cbBTC":   "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf",  # 8 decimali — Bitcoin su Base
    "cbETH":   "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22",  # 18 decimali — cbETH Coinbase
    "wstETH":  "0xc1CBa3fCea344f92D9239c08C0568f6F2F0ee452",  # 18 decimali — Lido staked ETH
    "AERO":    "0x940181a94A35A4569E4529A3CDfB74e38FD98631",  # 18 decimali — token nativo Aerodrome
    "VIRTUAL": "0x0b3e328455c4059EEb9e3f84b5543F74e24e7E1b",  # 18 decimali — AI agents protocol
    "BRETT":   "0x532f27101965dd16442E59d40670FaF5eBb142E4",  # 18 decimali — top memecoin Base
    "DEGEN":   "0x4ed4E862860beD51a9570b96d89aF5E1B0Efefed",  # 18 decimali — Farcaster token
    "WELL":    "0xA88594D404727625A9437C3f886C7643872296AE",  # 18 decimali — Moonwell lending
}

TOKEN_DECIMALS = {
    "USDC":    6,
    "WETH":    18,
    "cbBTC":   8,
    "cbETH":   18,
    "wstETH":  18,
    "AERO":    18,
    "VIRTUAL": 18,
    "BRETT":   18,
    "DEGEN":   18,
    "WELL":    18,
}

# ── DEX addresses su Base ─────────────────────────────────────────────────────
UNISWAP_V3_ROUTER  = "0x2626664c2603336E57B271c5C0b26F421741e481"
UNISWAP_V3_FACTORY = "0x33128a8fC17869897dcE68Ed026d694621f6FDfD"
UNISWAP_V3_QUOTER  = "0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a"
AERODROME_ROUTER   = "0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43"
AERODROME_FACTORY  = "0x420DD381b31aEf6683db6B902084cB0FFECe40Da"

# ── 10 Coppie di trading ad alta liquidità ────────────────────────────────────
# Ogni coppia specifica:
#   base_token    : token che compriamo/vendiamo
#   quote_token   : token con cui paghiamo (quasi sempre USDC)
#   coingecko_id  : ID per fetch dati OHLCV
#   uni_fee_tier  : fee tier Uniswap v3 più liquido per questa coppia
#   preferred_dex : "uniswap_v3" | "aerodrome" | "best_price"
#   max_trade_pct : override opzionale del risk % globale
#   notes         : liquidità stimata e fonti

PAIRS = {
    "WETH/USDC": {
        "base_token":   "WETH",
        "quote_token":  "USDC",
        "coingecko_id": "ethereum",
        "uni_fee_tier": 500,        # 0.05%
        "preferred_dex": "best_price",
        "max_trade_pct": 0.25,
        "notes": "Coppia più liquida su Base, >$100M volume/giorno",
    },
    "cbBTC/USDC": {
        "base_token":   "cbBTC",
        "quote_token":  "USDC",
        "coingecko_id": "coinbase-wrapped-btc",
        "uni_fee_tier": 500,
        "preferred_dex": "best_price",
        "max_trade_pct": 0.20,
        "notes": "Bitcoin nativo su Base, ~$20M liquidità, 80% volume su Aerodrome",
    },
    "cbETH/USDC": {
        "base_token":   "cbETH",
        "quote_token":  "USDC",
        "coingecko_id": "coinbase-wrapped-staked-eth",
        "uni_fee_tier": 500,
        "preferred_dex": "best_price",
        "max_trade_pct": 0.20,
        "notes": "ETH staked Coinbase, alta liquidità, fortemente correlato a ETH",
    },
    "wstETH/USDC": {
        "base_token":   "wstETH",
        "quote_token":  "USDC",
        "coingecko_id": "wrapped-steth",
        "uni_fee_tier": 500,
        "preferred_dex": "uniswap_v3",
        "max_trade_pct": 0.20,
        "notes": "Lido staked ETH, pool da $100M+ su Uniswap v3 Base",
    },
    "AERO/USDC": {
        "base_token":   "AERO",
        "quote_token":  "USDC",
        "coingecko_id": "aerodrome-finance",
        "uni_fee_tier": 3000,       # 0.3%
        "preferred_dex": "aerodrome",
        "max_trade_pct": 0.15,
        "notes": "Token nativo Aerodrome, ~$15M liquidità on-chain",
    },
    "VIRTUAL/USDC": {
        "base_token":   "VIRTUAL",
        "quote_token":  "USDC",
        "coingecko_id": "virtual-protocol",
        "uni_fee_tier": 3000,
        "preferred_dex": "best_price",
        "max_trade_pct": 0.12,
        "notes": "AI agents su Base, alta volatilità e alto volume",
    },
    "BRETT/USDC": {
        "base_token":    "BRETT",
        "quote_token":   "USDC",
        "coingecko_id":  "based-brett",  # MODIFICATO: da "brett-based" a "based-brett"
        "uni_fee_tier":  3000,
        "preferred_dex": "best_price",
        "max_trade_pct": 0.10,
        "notes": "Top memecoin su Base, buona liquidità",
    },
    "DEGEN/USDC": {
        "base_token":   "DEGEN",
        "quote_token":  "USDC",
        "coingecko_id": "degen-base",
        "uni_fee_tier": 3000,
        "preferred_dex": "aerodrome",
        "max_trade_pct": 0.10,
        "notes": "Token ecosistema Farcaster, alta comunità e volume",
    },
    "WELL/USDC": {
        "base_token":   "WELL",
        "quote_token":  "USDC",
        "coingecko_id": "moonwell",
        "uni_fee_tier": 3000,
        "preferred_dex": "best_price",
        "max_trade_pct": 0.10,
        "notes": "Token Moonwell (lending su Base), liquidità stabile",
    },
}

# ── Indicatori tecnici (parametri globali, sovrascrivibili per coppia) ─────────
STRATEGY = {
    "ema_fast":        12,
    "ema_slow":        26,
    "sma_trend":       50,
    "rsi_period":      14,
    "rsi_oversold":    35,
    "rsi_overbought":  70,
    "bb_period":       20,
    "bb_std":          2.0,
    "macd_fast":       12,
    "macd_slow":       26,
    "macd_signal":     9,
    "candle_interval": 60,      # minuti
    "lookback_candles": 200,
    "min_confidence":  0.30,    # soglia minima per agire

    # ── Druckenmiller: ADX Filter ─────────────────────────────────────────────
    "adx_period":           14,     # periodo ADX
    "adx_trend_threshold":  25,     # ADX < 20 = ranging (alza soglia conferme)

    # ── Druckenmiller: Multi-Timeframe ────────────────────────────────────────
    # Bias calcolato resampland i dati 1H a 4H e 1D
    # Se bias HTF e ribassista -> blocca BUY, e viceversa
    "min_confirmations":    4,      # conferme minime in trend (sale a 4 in ranging)

    # ── Druckenmiller: Conviction Sizing ──────────────────────────────────────
    # Il multiplier viene applicato al position size base dal RiskManager
    # sempre entro i limiti max_portfolio_pct e max_trade_pct della coppia
    "conviction_sizing": {
        "low":    {"min_conf": 3, "mult": 1.0},   # 3-4 conferme -> size normale
        "medium": {"min_conf": 5, "mult": 1.5},   # 5-6 conferme -> +50%
        "high":   {"min_conf": 7, "mult": 2.0},   # 7-8 conferme -> +100%
    },

    # ── Druckenmiller: R:R Dinamico ───────────────────────────────────────────
    "rr_base":                3.0,  # R:R base (3:1) — sostituisce il vecchio 2:1
    "rr_max":                 4.0,  # R:R massimo per alta conviction (4:1)
    "rr_high_conf_threshold": 7,    # conferme minime per usare rr_max
}

# ── Gestione rischio globale ───────────────────────────────────────────────────
RISK = {
    "max_open_positions":  4,       # max posizioni aperte contemporaneamente
    "max_portfolio_pct":   0.70,    # max 70% del portafoglio in posizioni aperte
    "stop_loss_pct":       0.04,    # stop-loss -4% (base)
    "take_profit_pct":     0.12,    # take-profit +12% (base, R:R 3:1)
    # Con R:R dinamico Druckenmiller il take_profit effettivo viene calcolato
    # come stop_loss_pct * rr_ratio dal TradeSignal (3:1 base, 4:1 alta conviction)
    "max_slippage_pct":    0.005,   # 0.5%
    "min_usdc_reserve":    2.0,     # USDC sempre in riserva
    "min_eth_gas":         0.003,   # ETH minimo per gas
    "cooldown_after_loss": 2,       # skip N cicli dopo uno stop-loss
}

# ── Agent loop ────────────────────────────────────────────────────────────────
AGENT = {
    "poll_interval_sec":     300,    # ogni 5 minuti
    "dry_run":              True,   # ⚠️ True = simulazione, False = reale
    "log_level":            "INFO",
    "enabled_pairs":        list(PAIRS.keys()),  # tutte le coppie — filtra qui per ridurle
    # Esempio per limitare a 5 coppie:
    # "enabled_pairs": ["WETH/USDC", "cbBTC/USDC", "AERO/USDC"],
}
