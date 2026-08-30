"""
At-rest encryption for Swiggy OAuth tokens. Fernet (symmetric, authenticated),
keyed by SWIGGY_TOKEN_ENCRYPTION_KEY in .env -- generate one with:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Deliberately a dedicated key, never derived from FLASK_SECRET_KEY or any
other existing secret -- rotating one must never force-rotate the other.
"""
from __future__ import annotations

import os
from functools import lru_cache


class EncryptionNotConfiguredError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _fernet():
    from cryptography.fernet import Fernet
    key = os.getenv("SWIGGY_TOKEN_ENCRYPTION_KEY")
    if not key:
        raise EncryptionNotConfiguredError(
            "SWIGGY_TOKEN_ENCRYPTION_KEY is not set -- required whenever "
            "SWIGGY_MCP_ENABLED=true. Generate one with:\n"
            '  python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    return Fernet(key.encode("ascii") if isinstance(key, str) else key)


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
