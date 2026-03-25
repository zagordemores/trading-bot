"""
Interazione con Uniswap v3 e Aerodrome su Base.
Versione Aggiornata: Quoter V2 per Base Mainnet.
"""

import time
import logging
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

import config as cfg

logger = logging.getLogger(__name__)

# ABI Standard per i token ERC20
ERC20_ABI = [
    {"name": "balanceOf",  "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "decimals",   "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "uint8"}]},
    {"name": "approve",    "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "spender", "type": "address"},
                {"name": "amount",  "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
    {"name": "allowance",  "type": "function", "stateMutability": "view",
     "inputs": [{"name": "owner",   "type": "address"},
                {"name": "spender", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
]

UNISWAP_V3_ROUTER_ABI = [
    {"name": "exactInputSingle", "type": "function", "stateMutability": "payable",
     "inputs": [{"name": "params", "type": "tuple",
                 "components": [
                     {"name": "tokenIn",           "type": "address"},
                     {"name": "tokenOut",          "type": "address"},
                     {"name": "fee",               "type": "uint24"},
                     {"name": "recipient",         "type": "address"},
                     {"name": "amountIn",          "type": "uint256"},
                     {"name": "amountOutMinimum",  "type": "uint256"},
                     {"name": "sqrtPriceLimitX96", "type": "uint160"},
                 ]}],
     "outputs": [{"name": "amountOut", "type": "uint256"}]},
]

# ABI QuoterV2 per Base
UNISWAP_V3_QUOTER_V2_ABI = [
    {
        "inputs": [{"components": [
            {"internalType": "address", "name": "tokenIn", "type": "address"},
            {"internalType": "address", "name": "tokenOut", "type": "address"},
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "uint24", "name": "fee", "type": "uint24"},
            {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"}
        ], "internalType": "struct IQuoterV2.QuoteExactInputSingleParams", "name": "params", "type": "tuple"}],
        "name": "quoteExactInputSingle",
        "outputs": [
            {"internalType": "uint256", "name": "amountOut", "type": "uint256"},
            {"internalType": "uint160", "name": "sqrtPriceX96After", "type": "uint160"},
            {"internalType": "uint32", "name": "initializedTicksCrossed", "type": "uint32"},
            {"internalType": "uint256", "name": "gasEstimate", "type": "uint256"}
        ],
        "stateMutability": "view",
        "type": "function"
    }
]

AERODROME_ROUTER_ABI = [
    {"name": "swapExactTokensForTokens", "type": "function", "stateMutability": "nonpayable",
     "inputs": [
         {"name": "amountIn",     "type": "uint256"},
         {"name": "amountOutMin", "type": "uint256"},
         {"name": "routes", "type": "tuple[]",
          "components": [
              {"name": "from",    "type": "address"},
              {"name": "to",      "type": "address"},
              {"name": "stable",  "type": "bool"},
              {"name": "factory", "type": "address"},
          ]},
         {"name": "to",       "type": "address"},
         {"name": "deadline", "type": "uint256"},
     ],
     "outputs": [{"name": "amounts", "type": "uint256[]"}]},
    {"name": "getAmountsOut", "type": "function", "stateMutability": "view",
     "inputs": [
         {"name": "amountIn", "type": "uint256"},
         {"name": "routes", "type": "tuple[]",
          "components": [
              {"name": "from",    "type": "address"},
              {"name": "to",      "type": "address"},
              {"name": "stable",  "type": "bool"},
              {"name": "factory", "type": "address"},
          ]},
     ],
     "outputs": [{"name": "amounts", "type": "uint256[]"}]},
]

UNISWAP_QUOTER_V2 = "0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a"

class DEXClient:
    def __init__(self, rpc_url: str = None):
        rpc_urls = cfg.BASE_RPC_URLS if hasattr(cfg, "BASE_RPC_URLS") else [rpc_url or cfg.BASE_RPC_URL]
        self.w3 = None
        for url in rpc_urls:
            try:
                w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 10}))
                w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
                if w3.is_connected():
                    self.w3 = w3
                    logger.info(f"Connesso a Base via {url}")
                    break
            except Exception as e:
                logger.warning(f"RPC {url} non disponibile: {e}")
        
        if not self.w3:
            raise ConnectionError("Nessun RPC Base raggiungibile.")

        self.uni_router = self.w3.eth.contract(
            address=Web3.to_checksum_address(cfg.UNISWAP_V3_ROUTER), abi=UNISWAP_V3_ROUTER_ABI)
        self.uni_quoter = self.w3.eth.contract(
            address=Web3.to_checksum_address(UNISWAP_QUOTER_V2), abi=UNISWAP_V3_QUOTER_V2_ABI)
        self.aero_router = self.w3.eth.contract(
            address=Web3.to_checksum_address(cfg.AERODROME_ROUTER), abi=AERODROME_ROUTER_ABI)
        self._erc20_cache = {}

    def get_balances(self, address: str) -> dict:
        addr = Web3.to_checksum_address(address)
        balances = {"ETH": self.w3.eth.get_balance(addr) / 1e18}
        for symbol, token_addr in cfg.TOKENS.items():
            try:
                contract = self.w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=ERC20_ABI)
                raw = contract.functions.balanceOf(addr).call()
                decimals = cfg.TOKEN_DECIMALS.get(symbol, 18)
                balances[symbol] = raw / (10 ** decimals)
            except: balances[symbol] = 0.0
        return balances

    def quote_uniswap(self, token_in_sym: str, token_out_sym: str, amount_in: float, fee: int) -> float:
        try:
            amt_in_wei = int(amount_in * 10**cfg.TOKEN_DECIMALS[token_in_sym])
            res = self.uni_quoter.functions.quoteExactInputSingle({
                'tokenIn': Web3.to_checksum_address(cfg.TOKENS[token_in_sym]),
                'tokenOut': Web3.to_checksum_address(cfg.TOKENS[token_out_sym]),
                'amountIn': amt_in_wei,
                'fee': fee,
                'sqrtPriceLimitX96': 0
            }).call()
            out = res[0] if isinstance(res, (list, tuple)) else res
            return out / 10**cfg.TOKEN_DECIMALS[token_out_sym]
        except: return 0.0

    def quote_aerodrome(self, token_in_sym: str, token_out_sym: str, amount_in: float) -> float:
        try:
            amt_in_wei = int(amount_in * 10**cfg.TOKEN_DECIMALS[token_in_sym])
            routes = [(
                Web3.to_checksum_address(cfg.TOKENS[token_in_sym]),
                Web3.to_checksum_address(cfg.TOKENS[token_out_sym]),
                False,
                Web3.to_checksum_address(cfg.AERODROME_FACTORY),
            )]
            result = self.aero_router.functions.getAmountsOut(
                amt_in_wei, routes
            ).call()
            out = result[-1]
            return out / 10**cfg.TOKEN_DECIMALS[token_out_sym]
        except:
            return 0.0
