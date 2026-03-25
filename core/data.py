import time, logging, requests, json, os
import pandas as pd

logger = logging.getLogger(__name__)

GT_BASE    = "https://api.geckoterminal.com/api/v2"
DS_BASE    = "https://api.dexscreener.com/latest/dex/tokens"
GT_HEADERS = {"Accept": "application/json;version=20230302"}
POOL_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pool_cache.json")

TOKEN_ADDRESSES = {
    "ethereum":                    "0x4200000000000000000000000000000000000006",
    "coinbase-wrapped-btc":        "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf",
    "coinbase-wrapped-staked-eth": "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22",
    "wrapped-steth":               "0xc1CBa3fCea344f92D9239c08C0568f6F2F0ee452",
    "aerodrome-finance":           "0x940181a94A35A4569E4529A3CDfB74e38FD98631",
    "virtual-protocol":            "0x0b3e328455c4059EEb9e3f84b5543F74E24e7E1b",
    "based-brett":                 "0x532f27101965dd16442E59d40670FaF5eBB142E4",
    "degen-base":                  "0x4ed4E862860beD51a9570b96d89aF5E1B0Efefed",
    "moonwell":                    "0xA88594D404727625A9437C3f886C7643872296AE",
    "usd-coin":                    "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
}
_GT_TIMEFRAME = {15:("minute",15),30:("minute",15),60:("hour",1),240:("hour",4)}

def _load_pool_cache():
    try:
        if os.path.exists(POOL_CACHE_FILE):
            with open(POOL_CACHE_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def _save_pool_cache(cache):
    try:
        with open(POOL_CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except Exception:
        pass

_pool_cache = _load_pool_cache()

def _get_top_pool(token_address):
    if token_address in _pool_cache:
        return _pool_cache[token_address]
    try:
        time.sleep(2)
        r = requests.get(GT_BASE + "/networks/base/tokens/" + token_address + "/pools",
            headers=GT_HEADERS, timeout=10, params={"page":1})
        r.raise_for_status()
        pools = r.json().get("data",[])
        if pools:
            pool_addr = pools[0]["attributes"]["address"]
            _pool_cache[token_address] = pool_addr
            _save_pool_cache(_pool_cache)
            return pool_addr
    except Exception as e:
        logger.warning("GT pool non trovato per " + token_address + ": " + str(e))
    return None

def _gt_fetch_ohlcv(token_address, interval_minutes, lookback):
    pool_addr = _get_top_pool(token_address)
    if not pool_addr:
        raise ValueError("Nessun pool per " + token_address)
    timeframe, aggregate = _GT_TIMEFRAME.get(interval_minutes, ("hour",1))
    time.sleep(2)
    r = requests.get(
        GT_BASE + "/networks/base/pools/" + pool_addr + "/ohlcv/" + timeframe,
        headers=GT_HEADERS, timeout=15,
        params={"aggregate":aggregate,"limit":min(lookback,1000),"currency":"usd"})
    r.raise_for_status()
    raw = r.json()["data"]["attributes"]["ohlcv_list"]
    df = pd.DataFrame(raw, columns=["timestamp","open","high","low","close","volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"],unit="s",utc=True)
    df.set_index("timestamp",inplace=True)
    df = df.astype(float)
    df.sort_index(inplace=True)
    return df

def fetch_ohlcv(coingecko_id, interval_minutes=60, lookback=200, retries=3, quote_coingecko_id=None):
    token_addr = TOKEN_ADDRESSES.get(coingecko_id)
    if not token_addr:
        raise ValueError("Indirizzo non configurato per " + coingecko_id)
    for attempt in range(retries):
        try:
            df = _gt_fetch_ohlcv(token_addr, interval_minutes, lookback)
            if quote_coingecko_id:
                quote_addr = TOKEN_ADDRESSES.get(quote_coingecko_id)
                if quote_addr:
                    dq = _gt_fetch_ohlcv(quote_addr, interval_minutes, lookback)
                    df,dq = df.align(dq,join="inner")
                    for c in ["open","high","low","close"]:
                        df[c] = df[c] / dq[c]
            return df.tail(lookback).copy()
        except Exception as e:
            logger.warning("GT " + coingecko_id + " tentativo " + str(attempt+1) + ": " + str(e))
            time.sleep(3 * (attempt+1))
    raise RuntimeError("Fetch OHLCV fallito per " + coingecko_id)

def fetch_current_prices_batch(coingecko_ids):
    prices = {}
    addrs  = [TOKEN_ADDRESSES[cg] for cg in coingecko_ids if cg in TOKEN_ADDRESSES]
    id_map = {TOKEN_ADDRESSES[cg]:cg for cg in coingecko_ids if cg in TOKEN_ADDRESSES}
    for i in range(0, len(addrs), 30):
        chunk = addrs[i:i+30]
        try:
            r = requests.get(DS_BASE + "/" + ",".join(chunk), timeout=10)
            r.raise_for_status()
            pairs = r.json().get("pairs") or []
            seen = set()
            for pair in pairs:
                base_addr = pair.get("baseToken",{}).get("address","").lower()
                for addr in chunk:
                    if addr.lower()==base_addr and addr not in seen:
                        price_str = pair.get("priceUsd")
                        if price_str:
                            cg_id = id_map.get(addr)
                            if cg_id:
                                prices[cg_id] = float(price_str)
                                seen.add(addr)
        except Exception as e:
            logger.warning("DS batch fallito: " + str(e))
        time.sleep(0.5)
    return prices

def fetch_current_price(coingecko_id):
    return fetch_current_prices_batch([coingecko_id]).get(coingecko_id, 0.0)

def fetch_all_pairs(pairs_cfg, interval_minutes=60, lookback=200):
    results, errors = {}, []
    for name, pcfg in pairs_cfg.items():
        try:
            results[name] = fetch_ohlcv(pcfg["coingecko_id"], interval_minutes, lookback,
                quote_coingecko_id=pcfg.get("quote_coingecko_id"))
        except Exception as e:
            logger.error("fetch fallito " + name + ": " + str(e))
            errors.append(name)
        time.sleep(6)
    if errors:
        logger.warning("Errori: " + str(errors))
    logger.info("Dati caricati per " + str(len(results)) + "/" + str(len(pairs_cfg)) + " coppie")
    return results
