"""
liquidation_monitor.py — Aave V3 Base
Fonte utenti: eventi Borrow on-chain + getUserAccountData per HF real-time
"""

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from web3 import Web3

logger = logging.getLogger(__name__)

AAVE_POOL_BASE   = "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5"
AAVE_ORACLE_BASE = "0x2Cc0Fc26eD4563A5ce5e8bdcfe1A2878676Ae156"

AAVE_POOL_ABI = [
    {
        "inputs": [{"name": "user", "type": "address"}],
        "name": "getUserAccountData",
        "outputs": [
            {"name": "totalCollateralBase",        "type": "uint256"},
            {"name": "totalDebtBase",              "type": "uint256"},
            {"name": "availableBorrowsBase",       "type": "uint256"},
            {"name": "currentLiquidationThreshold","type": "uint256"},
            {"name": "ltv",                        "type": "uint256"},
            {"name": "healthFactor",               "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {"name": "asset", "type": "address"},
            {"name": "user",  "type": "address"}
        ],
        "name": "getUserReserveData",
        "outputs": [
            {"name": "currentATokenBalance",     "type": "uint256"},
            {"name": "currentStableDebt",        "type": "uint256"},
            {"name": "currentVariableDebt",      "type": "uint256"},
            {"name": "principalStableDebt",      "type": "uint256"},
            {"name": "scaledVariableDebt",       "type": "uint256"},
            {"name": "stableBorrowRate",         "type": "uint256"},
            {"name": "liquidityRate",            "type": "uint256"},
            {"name": "stableRateLastUpdated",    "type": "uint40"},
            {"name": "usageAsCollateralEnabled", "type": "bool"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True,  "name": "reserve",         "type": "address"},
            {"indexed": False, "name": "user",            "type": "address"},
            {"indexed": True,  "name": "onBehalfOf",      "type": "address"},
            {"indexed": False, "name": "amount",          "type": "uint256"},
            {"indexed": False, "name": "interestRateMode","type": "uint8"},
            {"indexed": False, "name": "borrowRate",      "type": "uint256"},
            {"indexed": True,  "name": "referralCode",    "type": "uint16"},
        ],
        "name": "Borrow",
        "type": "event"
    }
]

WATCHLIST_FILE  = Path(__file__).parent / "liquidation_watchlist.json"
LAST_BLOCK_FILE = Path(__file__).parent / "liquidation_last_block.json"

ASSET_CONFIG = {
    "0x4200000000000000000000000000000000000006": {"symbol": "WETH",   "bonus": 0.05,  "decimals": 18},
    "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA0291": {"symbol": "USDC",   "bonus": 0.045, "decimals": 6},
    "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22": {"symbol": "cbETH",  "bonus": 0.075, "decimals": 18},
    "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf": {"symbol": "cbBTC",  "bonus": 0.05,  "decimals": 8},
    "0xc1CBa3fCea344f92D9239c08C0568f6F2F0ee452": {"symbol": "wstETH", "bonus": 0.075, "decimals": 18},
}


@dataclass
class LiquidationOpportunity:
    user:                     str
    health_factor:            float
    collateral_usd:           float
    debt_usd:                 float
    est_profit_usd:           float
    debt_asset:               str
    debt_asset_address:       str
    collateral_asset:         str
    collateral_asset_address: str
    liquidation_bonus:        float


class LiquidationMonitor:

    AAVE_FEE     = 0.0009
    GAS_COST_USD = 0.15
    MIN_PROFIT   = float(os.getenv("MIN_PROFIT_USDC", "5.0"))
    BLOCK_RANGE  = 400

    FLASH_CONTRACT = "0x65Bf0d9761895c798677f1b5E4C6f6b4e8b8504B"
    FLASH_ABI = [{
        "inputs": [
            {"name": "token",  "type": "address"},
            {"name": "amount", "type": "uint256"},
            {"name": "params", "type": "bytes"}
        ],
        "name": "initiateFlashLoan",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    }]

    USDC_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA0291"

    SWAP_FEES = {
        "0x4200000000000000000000000000000000000006": 500,
        "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf": 500,
        "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22": 500,
        "0xc1CBa3fCea344f92D9239c08C0568f6F2F0ee452": 500,
    }

    def __init__(self, w3: Web3):
        self.w3   = w3
        self.pool = w3.eth.contract(
            address=Web3.to_checksum_address(AAVE_POOL_BASE),
            abi=AAVE_POOL_ABI
        )
        self.watchlist: set[str] = self._load_watchlist()
        logger.info(f"[LIQ] Monitor avviato | watchlist={len(self.watchlist)} utenti")

    def _load_watchlist(self) -> set[str]:
        if WATCHLIST_FILE.exists():
            return set(json.loads(WATCHLIST_FILE.read_text()))
        return set()

    def _save_watchlist(self) -> None:
        WATCHLIST_FILE.write_text(json.dumps(list(self.watchlist), indent=2))

    def _load_last_block(self) -> int:
        if LAST_BLOCK_FILE.exists():
            return json.loads(LAST_BLOCK_FILE.read_text()).get("block", 0)
        return 0

    def _save_last_block(self, block: int) -> None:
        LAST_BLOCK_FILE.write_text(json.dumps({"block": block}))

    def _fetch_borrowers_from_events(self) -> None:
        try:
            current_block = self.w3.eth.block_number
            saved_block   = self._load_last_block()
            from_block    = max(saved_block + 1, current_block - self.BLOCK_RANGE)
            new_users = 0
            chunk = 400
            for start in range(from_block, current_block, chunk):
                end = min(start + chunk - 1, current_block)
                from web3 import Web3 as _W3pub
                w3_pub = _W3pub(_W3pub.HTTPProvider("https://mainnet.base.org"))
                borrow_topic = "0x" + self.w3.keccak(text="Borrow(address,address,address,uint256,uint8,uint256,uint16)").hex()
                logs = w3_pub.eth.get_logs({
                    "fromBlock": start,
                    "toBlock":   end,
                    "address":   Web3.to_checksum_address(AAVE_POOL_BASE),
                    "topics":    [borrow_topic]
                })
                for log in logs:
                    if len(log["topics"]) >= 3:
                        user = "0x" + log["topics"][2].hex()[-40:]
                        if user not in self.watchlist:
                            self.watchlist.add(user)
                            new_users += 1
            self._save_last_block(current_block)
            self._save_watchlist()
            logger.info(f"[LIQ] Borrow events: +{new_users} nuovi utenti | totale={len(self.watchlist)}")
        except Exception as e:
            logger.warning(f"[LIQ] Event listener error: {e}")

    def _get_account_data(self, user: str) -> Optional[dict]:
        try:
            data = self.pool.functions.getUserAccountData(
                Web3.to_checksum_address(user)
            ).call()
            hf = data[5] / 1e18
            if hf == 0 or data[1] == 0:
                return None
            return {
                "health_factor":  hf,
                "collateral_usd": data[0] / 1e8,
                "debt_usd":       data[1] / 1e8,
            }
        except Exception as e:
            logger.debug(f"[LIQ] getUserAccountData error {user}: {e}")
            return None

    def _get_user_positions(self, user: str) -> dict:
        """Recupera collateral e debt asset reali da getUserReserveData."""
        default = {
            "collateral_address": "0x4200000000000000000000000000000000000006",
            "collateral_symbol":  "WETH",
            "collateral_bonus":   0.05,
            "debt_address":       "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA0291",
            "debt_symbol":        "USDC",
        }
        try:
            best_collateral_addr = None
            best_collateral_val  = 0
            best_debt_addr       = None
            best_debt_val        = 0

            for addr in ASSET_CONFIG:
                try:
                    data = self.pool.functions.getUserReserveData(
                        Web3.to_checksum_address(addr),
                        Web3.to_checksum_address(user)
                    ).call()
                    atoken_bal    = data[0]
                    variable_debt = data[2]
                    is_collateral = data[8]

                    if is_collateral and atoken_bal > best_collateral_val:
                        best_collateral_val  = atoken_bal
                        best_collateral_addr = addr

                    if variable_debt > best_debt_val:
                        best_debt_val  = variable_debt
                        best_debt_addr = addr
                except:
                    continue

            if best_collateral_addr and best_debt_addr:
                return {
                    "collateral_address": best_collateral_addr,
                    "collateral_symbol":  ASSET_CONFIG[best_collateral_addr]["symbol"],
                    "collateral_bonus":   ASSET_CONFIG[best_collateral_addr]["bonus"],
                    "debt_address":       best_debt_addr,
                    "debt_symbol":        ASSET_CONFIG[best_debt_addr]["symbol"],
                }
            return default
        except Exception as e:
            logger.debug(f"[LIQ] _get_user_positions error {user}: {e}")
            return default

    def _estimate_profit(self, debt_usd: float, bonus: float = 0.05) -> float:
        repay      = debt_usd * 0.5
        collateral = repay * (1 + bonus)
        flash_fee  = repay * self.AAVE_FEE
        return collateral - repay - flash_fee - self.GAS_COST_USD

    def scan(self) -> list:
        opportunities = []
        self._fetch_borrowers_from_events()
        sample = list(self.watchlist)[:100]
        liquidatable = 0

        for user in sample:
            acc = self._get_account_data(user)
            if acc is None:
                continue
            hf = acc["health_factor"]

            if hf < 1.0:
                liquidatable += 1
                positions = self._get_user_positions(user)
                bonus  = positions["collateral_bonus"]
                profit = self._estimate_profit(acc["debt_usd"], bonus)

                logger.info(
                    f"[LIQ] LIQUIDABILE: {user[:10]}... | "
                    f"HF={hf:.4f} | debt=${acc['debt_usd']:.0f} | "
                    f"collateral={positions['collateral_symbol']} | profit=${profit:.2f}"
                )

                if profit >= self.MIN_PROFIT:
                    opp = LiquidationOpportunity(
                        user=user,
                        health_factor=hf,
                        collateral_usd=acc["collateral_usd"],
                        debt_usd=acc["debt_usd"],
                        est_profit_usd=profit,
                        debt_asset=positions["debt_symbol"],
                        debt_asset_address=positions["debt_address"],
                        collateral_asset=positions["collateral_symbol"],
                        collateral_asset_address=positions["collateral_address"],
                        liquidation_bonus=bonus,
                    )
                    opportunities.append(opp)

            elif hf < 1.05:
                logger.debug(f"[LIQ] Pre-alert: {user[:10]}... | HF={hf:.4f}")

            time.sleep(0.05)

        logger.info(f"[LIQ] Scan: {liquidatable} liquidabili | {len(opportunities)} profittevoli")
        return opportunities

    def execute_liquidation(self, opp, private_key: str, dry_run: bool = True) -> bool:
        from web3 import Web3 as _W3
        try:
            w3_pub = _W3(_W3.HTTPProvider("https://mainnet.base.org"))
            contract = w3_pub.eth.contract(
                address=_W3.to_checksum_address(self.FLASH_CONTRACT),
                abi=self.FLASH_ABI
            )
            debt_asset       = _W3.to_checksum_address(opp.debt_asset_address)
            collateral_asset = _W3.to_checksum_address(opp.collateral_asset_address)
            user             = _W3.to_checksum_address(opp.user)
            decimals         = ASSET_CONFIG.get(opp.debt_asset_address, {}).get("decimals", 6)
            debt_to_cover    = int(opp.debt_usd * 0.5 * (10 ** decimals))
            swap_fee         = self.SWAP_FEES.get(opp.collateral_asset_address.lower(), 3000)

            from eth_abi import encode
            params = encode(
                ["uint8", "address", "address", "uint256", "uint24"],
                [1, collateral_asset, user, debt_to_cover, swap_fee]
            )

            if dry_run:
                logger.info(
                    f"[LIQ] DRY-RUN | user={user[:10]}... "
                    f"debt={debt_to_cover/(10**decimals):.0f} {opp.debt_asset} | "
                    f"collateral={opp.collateral_asset} | profit=${opp.est_profit_usd:.2f}"
                )
                return True

            account = w3_pub.eth.account.from_key(private_key)
            tx = contract.functions.initiateFlashLoan(
                debt_asset, debt_to_cover, params
            ).build_transaction({
                "from":  account.address,
                "nonce": w3_pub.eth.get_transaction_count(account.address),
                "gas":   500000,
            })
            signed  = account.sign_transaction(tx)
            tx_hash = w3_pub.eth.send_raw_transaction(signed.raw_transaction)
            logger.info(f"[LIQ] TX inviata: {tx_hash.hex()}")
            return True
        except Exception as e:
            logger.error(f"[LIQ] execute_liquidation error: {e}")
            return False

    def format_telegram_message(self, opp: LiquidationOpportunity) -> str:
        return (
            "*LIQUIDAZIONE DISPONIBILE*\n"
            f"User: {opp.user[:10]}...{opp.user[-6:]}\n"
            f"Health Factor: {opp.health_factor:.4f}\n"
            f"Debt: ${opp.debt_usd:,.0f} ({opp.debt_asset})\n"
            f"Collateral: ${opp.collateral_usd:,.0f} ({opp.collateral_asset})\n"
            f"Est. Profit: ${opp.est_profit_usd:.2f}"
        )
