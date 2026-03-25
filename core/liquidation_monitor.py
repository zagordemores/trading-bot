"""
liquidation_monitor.py — Aave V3 Base
Fonte utenti: eventi Borrow on-chain (Alchemy) + getUserAccountData per HF real-time
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

# ── Aave V3 Base ──────────────────────────────────────────────
AAVE_POOL_BASE    = "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5"
AAVE_ORACLE_BASE  = "0x2Cc0Fc26eD4563A5ce5e8bdcfe1A2878676Ae156"

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

WATCHLIST_FILE = Path(__file__).parent / "liquidation_watchlist.json"
LAST_BLOCK_FILE = Path(__file__).parent / "liquidation_last_block.json"

# Asset principali su Base con liquidation bonus
ASSET_CONFIG = {
    "0x4200000000000000000000000000000000000006": {"symbol": "WETH",   "bonus": 0.05,  "decimals": 18},
    "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA0291": {"symbol": "USDC",   "bonus": 0.045, "decimals": 6},
    "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22": {"symbol": "cbETH",  "bonus": 0.075, "decimals": 18},
    "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf": {"symbol": "cbBTC",  "bonus": 0.05,  "decimals": 8},
    "0xc1CBa3fCea344f92D9239c08C0568f6F2F0ee452": {"symbol": "wstETH", "bonus": 0.075, "decimals": 18},
}


@dataclass
class LiquidationOpportunity:
    user:              str
    health_factor:     float
    collateral_usd:    float
    debt_usd:          float
    est_profit_usd:    float
    debt_asset:        str
    collateral_asset:  str
    liquidation_bonus: float


class LiquidationMonitor:

    AAVE_FEE     = 0.0009   # 0.09% flash loan fee
    GAS_COST_USD = 0.15     # stima conservativa gas Base
    MIN_PROFIT   = float(os.getenv("MIN_PROFIT_USDC", "5.0"))
    BLOCK_RANGE  = 1800     # ~1 ora di blocchi su Base (2s/block)

    def __init__(self, w3: Web3):
        self.w3   = w3
        self.pool = w3.eth.contract(
            address=Web3.to_checksum_address(AAVE_POOL_BASE),
            abi=AAVE_POOL_ABI
        )
        self.watchlist: set[str] = self._load_watchlist()
        logger.info(f"[LIQ] Monitor avviato | watchlist={len(self.watchlist)} utenti")

    # ── Persistenza ───────────────────────────────────────────

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

    # ── Event listener Borrow ────────────────────────────────

    def _fetch_borrowers_from_events(self) -> None:
        """
        Scansiona eventi Borrow Aave nelle ultime BLOCK_RANGE blocchi.
        Aggiunge onBehalfOf alla watchlist.
        Max 2000 blocchi per call (limite Alchemy).
        """
        try:
            current_block = self.w3.eth.block_number
            saved_block   = self._load_last_block()
            from_block    = max(saved_block + 1, current_block - self.BLOCK_RANGE)

            new_users = 0
            # Spezza in chunk da 2000 (limite Alchemy)
            chunk = 2000
            for start in range(from_block, current_block, chunk):
                end = min(start + chunk - 1, current_block)
                from web3 import Web3 as _W3pub
                w3_pub = _W3pub(_W3pub.HTTPProvider("https://mainnet.base.org"))
                borrow_topic = "0x" + self.w3.keccak(text="Borrow(address,address,address,uint256,uint8,uint256,uint16)").hex()
                logs = w3_pub.eth.get_logs({"fromBlock": start, "toBlock": end, "address": Web3.to_checksum_address(AAVE_POOL_BASE), "topics": [borrow_topic]})
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

    # ── Health Factor on-chain ────────────────────────────────

    def _get_account_data(self, user: str) -> Optional[dict]:
        try:
            data = self.pool.functions.getUserAccountData(
                Web3.to_checksum_address(user)
            ).call()
            hf = data[5] / 1e18
            if hf == 0 or data[1] == 0:  # nessun debito
                return None
            return {
                "health_factor":  hf,
                "collateral_usd": data[0] / 1e8,  # Aave oracle usa 8 decimali
                "debt_usd":       data[1] / 1e8,
            }
        except Exception as e:
            logger.debug(f"[LIQ] getUserAccountData error {user}: {e}")
            return None

    # ── Calcolo profitto ──────────────────────────────────────

    def _estimate_profit(self, debt_usd: float, bonus: float = 0.05) -> float:
        """Aave liquida max 50% del debito."""
        repay      = debt_usd * 0.5
        collateral = repay * (1 + bonus)
        flash_fee  = repay * self.AAVE_FEE
        return collateral - repay - flash_fee - self.GAS_COST_USD

    # ── Scan principale ───────────────────────────────────────

    def scan(self) -> list:
        opportunities = []

        # 1. Aggiorna watchlist da eventi Borrow
        self._fetch_borrowers_from_events()

        # 2. Verifica HF on-chain (max 100 per ciclo, priorità watchlist)
        sample = list(self.watchlist)[:100]
        liquidatable = 0

        for user in sample:
            acc = self._get_account_data(user)
            if acc is None:
                continue

            hf = acc["health_factor"]

            if hf < 1.0:
                liquidatable += 1
                bonus  = 0.05  # default, WETH collateral
                profit = self._estimate_profit(acc["debt_usd"], bonus)

                logger.info(
                    f"[LIQ] 🚨 LIQUIDABILE: {user[:10]}... | "
                    f"HF={hf:.4f} | debt=${acc['debt_usd']:.0f} | profit=${profit:.2f}"
                )

                if profit >= self.MIN_PROFIT:
                    opp = LiquidationOpportunity(
                        user=user,
                        health_factor=hf,
                        collateral_usd=acc["collateral_usd"],
                        debt_usd=acc["debt_usd"],
                        est_profit_usd=profit,
                        debt_asset="USDC",
                        collateral_asset="WETH",
                        liquidation_bonus=bonus,
                    )
                    opportunities.append(opp)

            elif hf < 1.05:
                logger.debug(f"[LIQ] ⚠️ Pre-alert: {user[:10]}... | HF={hf:.4f}")

            time.sleep(0.05)  # rate limit RPC

        logger.info(f"[LIQ] Scan: {liquidatable} liquidabili | {len(opportunities)} profittevoli")
        return opportunities

    # ── Formato Telegram ─────────────────────────────────────


    # ── Executor liquidazione ─────────────────────────────────

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

    # Asset → swap fee UniV3 su Base
    SWAP_FEES = {
        "0x4200000000000000000000000000000000000006": 500,   # WETH
        "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf": 500,   # cbBTC
        "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22": 500,   # cbETH
        "0xc1CBa3fCea344f92D9239c08C0568f6F2F0ee452": 500,   # wstETH
    }

    def execute_liquidation(self, opp, private_key: str, dry_run: bool = True) -> bool:
        """
        Chiama initiateFlashLoan sul contratto per eseguire la liquidazione.
        dry_run=True: simula solo, non invia TX.
        """
        import os
        from web3 import Web3 as _W3
        try:
            w3_pub = _W3(_W3.HTTPProvider("https://mainnet.base.org"))
            contract = w3_pub.eth.contract(
                address=_W3.to_checksum_address(self.FLASH_CONTRACT),
                abi=self.FLASH_ABI
            )

            debt_asset       = _W3.to_checksum_address(self.USDC_ADDRESS)
            collateral_asset = _W3.to_checksum_address(opp.collateral_asset_address)
            user             = _W3.to_checksum_address(opp.user)
            debt_to_cover    = int(opp.debt_usd * 0.5 * 1e6)  # USDC 6 decimali, 50%
            swap_fee         = self.SWAP_FEES.get(opp.collateral_asset_address.lower(), 3000)

            # Encoding params per OP_LIQ (uint8=1)
            from eth_abi import encode
            params = encode(
                ["uint8", "address", "address", "uint256", "uint24"],
                [1, collateral_asset, user, debt_to_cover, swap_fee]
            )

            if dry_run:
                logger.info(
                    f"[LIQ] DRY-RUN liquidazione | user={user[:10]}... "
                    f"debt={debt_to_cover/1e6:.0f} USDC | profit=${opp.est_profit_usd:.2f}"
                )
                return True

            account = w3_pub.eth.account.from_key(private_key)
            tx = contract.functions.initiateFlashLoan(
                debt_asset,
                debt_to_cover,
                params
            ).build_transaction({
                "from":  account.address,
                "nonce": w3_pub.eth.get_transaction_count(account.address),
                "gas":   500000,
            })
            signed = account.sign_transaction(tx)
            tx_hash = w3_pub.eth.send_raw_transaction(signed.raw_transaction)
            logger.info(f"[LIQ] TX inviata: {tx_hash.hex()}")
            return True

        except Exception as e:
            logger.error(f"[LIQ] execute_liquidation error: {e}")
            return False

    def format_telegram_message(self, opp: LiquidationOpportunity) -> str:
        return (
            f"🚨 *LIQUIDAZIONE DISPONIBILE*\n"
            f"👤 User: `{opp.user[:10]}...{opp.user[-6:]}`\n"
            f"❤️ Health Factor: `{opp.health_factor:.4f}`\n"
            f"💸 Debt: `${opp.debt_usd:,.0f}`\n"
            f"💎 Collateral: `${opp.collateral_usd:,.0f}`\n"
            f"💰 Est. Profit: `${opp.est_profit_usd:.2f}`\n"
            f"📋 Debt Asset: `{opp.debt_asset}`\n"
            f"🔗 [Aave Dashboard](https://app.aave.com/)"
        )
