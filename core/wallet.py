"""
Gestione wallet: creazione sicura, caricamento da file cifrato.
"""

import os
import json
import getpass
import logging
from pathlib import Path

from eth_account import Account
from eth_account.signers.local import LocalAccount
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
import base64

logger = logging.getLogger(__name__)
WALLET_FILE = Path("wallet.enc")


def _derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=salt, iterations=480_000)
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))


def create_wallet(save_path: Path = WALLET_FILE) -> LocalAccount:
    Account.enable_unaudited_hdwallet_features()
    account, mnemonic = Account.create_with_mnemonic()
    print("\n" + "═" * 60)
    print("  ⚠️  NUOVO WALLET CREATO — SALVA QUESTE INFO IN LUOGO SICURO")
    print("═" * 60)
    print(f"  Indirizzo   : {account.address}")
    print(f"  Chiave priv : {account.key.hex()}")
    print(f"\n  Seed phrase :\n  {mnemonic}")
    print("═" * 60 + "\n")
    password  = getpass.getpass("Password per cifrare il wallet: ")
    password2 = getpass.getpass("Conferma password: ")
    if password != password2:
        raise ValueError("Le password non coincidono.")
    salt  = os.urandom(16)
    key   = _derive_key(password, salt)
    token = Fernet(key).encrypt(account.key.hex().encode())
    save_path.write_text(json.dumps({"salt": salt.hex(), "token": token.decode()}))
    logger.info(f"Wallet salvato in {save_path}")
    return account


def load_wallet(save_path: Path = WALLET_FILE) -> LocalAccount:
    if not save_path.exists():
        raise FileNotFoundError(f"'{save_path}' non trovato. Esegui --new-wallet.")
    data     = json.loads(save_path.read_text())
    salt     = bytes.fromhex(data["salt"])
    password = os.environ.get("WALLET_PASSWORD") or getpass.getpass("Password wallet: ")
    key      = _derive_key(password, salt)
    try:
        pk = Fernet(key).decrypt(data["token"].encode()).decode()
    except Exception:
        raise ValueError("Password errata o file corrotto.")
    account = Account.from_key(pk)
    logger.info(f"Wallet caricato: {account.address}")
    return account


def load_wallet_from_env() -> LocalAccount:
    pk = os.environ.get("PRIVATE_KEY")
    if not pk:
        raise EnvironmentError("Variabile PRIVATE_KEY non impostata.")
    return Account.from_key(pk)
