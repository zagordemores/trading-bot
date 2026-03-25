"""
Deploy FlashArb.sol su Base mainnet.

Prerequisiti:
    pip install py-solc-x

Uso:
    python contracts/deploy.py
"""

import sys
import json
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

import config as cfg
from core.wallet import load_wallet, load_wallet_from_env

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("deploy")


def compile_contract() -> tuple[str, str]:
    """Compila FlashArb.sol e restituisce (abi, bytecode)."""
    try:
        from solcx import compile_source, install_solc
    except ImportError:
        logger.error("Installa py-solc-x: pip install py-solc-x")
        sys.exit(1)

    install_solc("0.8.20", show_progress=True)

    sol_path = Path(__file__).parent / "FlashArb.sol"
    source   = sol_path.read_text()

    compiled = compile_source(
        source,
        output_values=["abi", "bin"],
        solc_version="0.8.20",
        optimize=True,
        optimize_runs=200,
    )

    # La chiave è '<stdin>:FlashArb'
    contract_id = [k for k in compiled if "FlashArb" in k][0]
    contract_interface = compiled[contract_id]
    return contract_interface["abi"], contract_interface["bin"]


def deploy() -> str:
    """Deploy il contratto e restituisce l'indirizzo."""
    import os
    account = load_wallet_from_env() if os.environ.get("PRIVATE_KEY") else load_wallet()

    w3 = Web3(Web3.HTTPProvider(cfg.BASE_RPC_URL))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    logger.info("Compilazione contratto...")
    abi, bytecode = compile_contract()

    Contract = w3.eth.contract(abi=abi, bytecode=bytecode)

    logger.info("Deploy in corso...")
    tx = Contract.constructor(
        cfg.UNISWAP_V3_FACTORY,
        cfg.AERODROME_ROUTER,
        cfg.AERODROME_FACTORY,
    ).build_transaction({
        "from":  account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "gas":   2_000_000,
    })

    signed  = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    logger.info(f"TX inviata: {tx_hash.hex()}")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
    address = receipt["contractAddress"]
    logger.info(f"✅ Contratto deployato: {address}")
    logger.info(f"   BaseScan: https://basescan.org/address/{address}")

    # Salva ABI e indirizzo per uso Python
    output = {"address": address, "abi": abi}
    out_path = Path(__file__).parent / "FlashArb.json"
    out_path.write_text(json.dumps(output, indent=2))
    logger.info(f"ABI e indirizzo salvati in {out_path}")

    return address


if __name__ == "__main__":
    deploy()
