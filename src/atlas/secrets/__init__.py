"""The vault: secrets encrypted with age in the home, and the key that opens them."""

from atlas.secrets.errors import (
    InvalidSecretNameError,
    SecretsError,
    SecretUnreadableError,
    VaultDamagedError,
    VaultLockedError,
)
from atlas.secrets.keys import (
    KEY_FILE_VARIABLE,
    KEY_VARIABLE,
    PLAIN_KEY_FILE,
    PROTECTED_KEY_FILE,
)
from atlas.secrets.model import check_name
from atlas.secrets.store import LOCK_FILE, MCP_SIGN_IN_FILE, VAULT_FILE, Vault

__all__ = [
    "KEY_FILE_VARIABLE",
    "KEY_VARIABLE",
    "LOCK_FILE",
    "MCP_SIGN_IN_FILE",
    "PLAIN_KEY_FILE",
    "PROTECTED_KEY_FILE",
    "VAULT_FILE",
    "InvalidSecretNameError",
    "SecretUnreadableError",
    "SecretsError",
    "Vault",
    "VaultDamagedError",
    "VaultLockedError",
    "check_name",
]
