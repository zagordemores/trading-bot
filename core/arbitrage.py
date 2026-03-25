"""
Modulo Arbitrage - rileva opportunità di arbitraggio tra Uniswap V3 e Aerodrome
e le esegue tramite flash-swap (zero capitale richiesto).

Flusso:
  1. scan_opportunities()  - confronta prezzi off-chain per tutte le coppie
  2. Per ogni opportunità profittevole -> esegue il contratto FlashArb on-chain
  3. Se il profitto è insufficiente dopo slippage+gas -> skip (nessun costo)
"""

import json
import logging
import time
try:
    from core.telegram_notify import send_message
    TELEGRAM_OK = True
except Exception:
    TELEGRAM_OK = False
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from web3 import Web3
from eth_abi import encode

import config as cfg
from dex.client import DEXClient

logger = logging.getLogger(__name__)

FLASH_ARB_JSON = Path(__file__).parent.parent / "contracts" / "FlashArb.json"


# ── Opportunità ───────────────────────────────────────────────────────────────

@dataclass
class ArbOpportunity:
    pair:              str
    token_borrow:      str   # token che prendiamo in prestito (nome simbolo)
    token_repay:       str   # token con cui ripagheremo
    amount_borrow:     float # importo da borroware
    uni_price:         float # prezzo su Uniswap V3 (tokenRepay per 1 tokenBorrow)
    aero_price:        float # prezzo su Aerodrome
    spread_pct:        float # differenza percentuale
    estimated_profit:  float # profitto stimato in tokenRepay
    direction:         str   # "uni_buy_aero_sell" | "aero_buy_uni_sell"

    @property
    def is_profitable(self) -> bool:
        return self.estimated_profit > 0


# ── Scanner ───────────────────────────────────────────────────────────────────

class ArbScanner:
    def __init__(self, dex_client: DEXClient):
        self.dex = dex_client

        # Config soglie
        self.min_spread_pct   = 0.05
        self.allowed_pairs    = ["WETH/USDC", "AERO/USDC"]   # spread minimo 0.30% per tentare (copre gas+fee)
        self.gas_cost_usdc    = 0.50   # stima costo gas in USDC su Base
        self.uni_fee_pct      = 0.05   # Uniswap V3 fee 0.05%
        self.aero_fee_pct     = 0.20   # Aerodrome fee ~0.20% volatile pool

    def scan_opportunities(
        self,
        pairs_cfg: dict,
        borrow_amounts: Optional[dict] = None,
    ) -> list[ArbOpportunity]:
        """
        Scansiona tutte le coppie e restituisce le opportunità profittevoli ordinate.
        borrow_amounts: {pair_name: usdc_amount} - quanto borroware per coppia.
        """
        opportunities = []

        for pair_name, pair_cfg in pairs_cfg.items():
            if pair_name not in self.allowed_pairs:
                continue
            base  = pair_cfg["base_token"]
            quote = pair_cfg["quote_token"]
            fee   = pair_cfg["uni_fee_tier"]

            # Importo da usare per la stima (1 unità del token base di default)
            amount = 1000.0  # Flash loan fisso 1000 USDC

            try:
                opp = self._check_pair(pair_name, base, quote, amount, fee, pair_cfg)
                if opp and opp.spread_pct >= self.min_spread_pct:
                    opportunities.append(opp)
                    logger.info(
                        f"[ARB] [{pair_name}] Arb trovato! spread={opp.spread_pct:.3f}% "
                        f"profit≈{opp.estimated_profit:.4f} {quote} | dir={opp.direction}"
                    )
            except Exception as e:
                logger.debug(f"[{pair_name}] Scan arb fallito: {e}")

            time.sleep(0.1)  # evita rate limit

        # Ordina per profitto stimato decrescente
        opportunities.sort(key=lambda o: o.estimated_profit, reverse=True)
        return opportunities

    def _check_pair(
        self, pair_name: str, base: str, quote: str,
        amount_quote: float, fee: int, pair_cfg: dict,
    ) -> Optional[ArbOpportunity]:
        """
        Controlla una singola coppia per opportunità di arb.
        amount_quote = quanti USDC (o token quote) stiamo usando per la stima.
        """
        # Converti amount_quote in amount_base usando un prezzo approssimativo
        # Per semplicità usiamo amount_quote direttamente (es. 1000 USDC)
        amount_in = amount_quote

        # Quote da Uniswap: quanto base otteniamo per amount_in quote
        uni_out  = self.dex.quote_uniswap(quote, base, amount_in, fee)
        # Quote da Aerodrome: quanto base otteniamo per amount_in quote
        aero_out = self.dex.quote_aerodrome(quote, base, amount_in)

        if uni_out == 0 or aero_out == 0:
            return None

        aave_fee = amount_in * 0.0009
        uni_buy_return = self.dex.quote_aerodrome(base, quote, uni_out)
        profit_uni_buy = uni_buy_return - amount_in - aave_fee - self.gas_cost_usdc
        aero_buy_return = self.dex.quote_uniswap(base, quote, aero_out, fee)
        profit_aero_buy = aero_buy_return - amount_in - aave_fee - self.gas_cost_usdc
        if profit_uni_buy >= profit_aero_buy:
            direction = 'uni_buy_aero_sell'
            spread = abs(uni_out - aero_out) / aero_out if aero_out else 0
            profit = profit_uni_buy
        else:
            direction = 'aero_buy_uni_sell'
            spread = abs(aero_out - uni_out) / uni_out if uni_out else 0
            profit = profit_aero_buy

        return ArbOpportunity(
            pair=pair_name,
            token_borrow=quote,
            token_repay=base,
            amount_borrow=amount_in,
            uni_price=uni_out / amount_in if amount_in else 0,
            aero_price=aero_out / amount_in if amount_in else 0,
            spread_pct=spread * 100,
            estimated_profit=profit,
            direction=direction,
        )


# ── Executor ──────────────────────────────────────────────────────────────────

class ArbExecutor:
    def __init__(self, w3: Web3, account):
        self.w3      = w3
        self.account = account
        self.contract = None
        self._load_contract()

    def _load_contract(self) -> None:
        if not FLASH_ARB_JSON.exists():
            logger.warning(
                f"FlashArb.json non trovato in {FLASH_ARB_JSON}. "
                "Esegui prima: python contracts/deploy.py"
            )
            return
        data     = json.loads(FLASH_ARB_JSON.read_text())
        address  = Web3.to_checksum_address(data["address"])
        abi      = data["abi"]
        self.contract = self.w3.eth.contract(address=address, abi=abi)
        logger.info(f"FlashArb caricato: {address}")

    def is_ready(self) -> bool:
        return self.contract is not None

    def execute_arb(
        self,
        opp: ArbOpportunity,
        pairs_cfg: dict,
        dry_run: bool = True,
    ) -> Optional[str]:
        if not self.is_ready():
            logger.error("Contratto FlashArb non deployato.")
            return None

        pair_cfg     = pairs_cfg[opp.pair]
        token_borrow = cfg.TOKENS[opp.token_borrow]
        token_repay  = cfg.TOKENS[opp.token_repay]
        fee_tier     = pair_cfg["uni_fee_tier"]
        aero_stable  = pair_cfg.get("aero_stable", False)
        decimals_in  = cfg.TOKEN_DECIMALS[opp.token_borrow]
        amount_wei   = int(opp.amount_borrow * 10 ** decimals_in)
        decimals_out = cfg.TOKEN_DECIMALS[opp.token_repay]
        min_profit   = int(max(0.01, opp.estimated_profit * 0.8) * 10 ** decimals_out)

        logger.info(
            f"[ARB] [{opp.pair}] Flash arb | borrow={opp.amount_borrow:.2f} {opp.token_borrow} | "
            f"spread={opp.spread_pct:.3f}% | est.profit={opp.estimated_profit:.4f} {opp.token_borrow} (USDC)"
        )

        if dry_run:
            logger.info(f"[DRY-RUN] Flash arb simulato - nessuna TX.")
            if TELEGRAM_OK:
                if opp.estimated_profit > 0: send_message("🔵 <b>ARB DRY-RUN</b> " + opp.pair + "\nSpread: " + str(round(opp.spread_pct,2)) + "% | Profit stimato: " + str(round(opp.estimated_profit,4)) + " USDC")
            return "dry_run"

        try:
            # Encode ArbParams per FlashLoanArbitrage v2
            direction = 0 if opp.direction == "uni_buy_aero_sell" else 1
            steps = [(
                Web3.to_checksum_address(token_borrow),
                Web3.to_checksum_address(token_repay),
                direction,
                fee_tier,
                aero_stable,
            )]
            arb_params = encode(
                ["(uint8,uint256,(address,address,uint8,uint24,bool)[])"],
                [(2, min_profit, steps)]
            )
            tx = self.contract.functions.initiateFlashLoan(
                Web3.to_checksum_address(token_borrow),
                amount_wei,
                arb_params,
            ).build_transaction({
                "from":  self.account.address,
                "nonce": self.w3.eth.get_transaction_count(self.account.address),
                "gas":   800_000,
            })
            signed  = self.account.sign_transaction(tx)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            logger.info(f"Flash arb TX: {tx_hash.hex()}")
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            if receipt["status"] == 1:
                logger.info(f"[OK] Arb confermato! https://basescan.org/tx/{tx_hash.hex()}")
                if TELEGRAM_OK:
                    send_message("💰 <b>ARB ESEGUITO!</b> " + opp.pair + "\nProfit: " + str(round(opp.estimated_profit,4)) + " USDC\n🔗 https://basescan.org/tx/" + tx_hash.hex())
                return tx_hash.hex()
            else:
                logger.error(f"[ERR] Arb revertito (non profittevole in quel blocco): {tx_hash.hex()}")
                return None
        except Exception as e:
            logger.error(f"Flash arb fallito: {e}")
            return None
