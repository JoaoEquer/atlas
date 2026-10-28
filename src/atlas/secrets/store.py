"""The vault on disk: `secrets/vault.json`, read and written under a lock, replaced atomically.

**One lock for every reader and writer**, held on `secrets/vault.lock` (`atlas.home.locked`).
Writes read, change and write the whole file, so two processes writing at once would otherwise lose
one of the changes: the server refreshing a token while `atlas secrets set` runs, for example.

**Atomic replacement** (`atlas.home.replace_file`): a crash leaves the old vault or the new one,
never half of each.

**Never empty by mistake:** a file that cannot be read as a vault raises `VaultDamagedError`, and
no write goes over it.

**Every secret read or written is registered with the `Redactor`**, so no log line shows it.

**The MCP servers' sign-ins** are a second file of the same kind beside it,
`secrets/mcp-sign-ins.json`, opened by the same key under the same lock: tokens a server gave
Atlas, renewed by Atlas, never listed among the secrets the user stored (`atlas.mcp.oauth`).
"""

import base64
import binascii
import logging
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pyrage

from atlas.home import (
    FileBusyError,
    Redactor,
    VaultSettings,
    format_utc,
    locked,
    private_folder,
    replace_file,
    utc_now,
)
from atlas.secrets.errors import (
    SecretsError,
    SecretUnreadableError,
    VaultDamagedError,
    VaultLockedError,
)
from atlas.secrets.keys import VaultKey, create_home_key, find_key, missing_key_message
from atlas.secrets.model import Entry, VaultDocument, check_name, parse_document, render_document

VAULT_FILE = "vault.json"
MCP_SIGN_IN_FILE = "mcp-sign-ins.json"
LOCK_FILE = "vault.lock"

_logger = logging.getLogger("atlas.secrets")


class Vault:
    """The secrets of one home, addressed by name. The key is found once and kept in memory."""

    def __init__(
        self, folder: Path, settings: VaultSettings, redactor: Redactor, file: str = VAULT_FILE
    ) -> None:
        """`file` is another file of the same key, `MCP_SIGN_IN_FILE` (`beside`)."""
        self.folder = folder
        self.path = folder / file
        self._settings = settings
        self._redactor = redactor
        self._key: VaultKey | None = None

    def beside(self, file: str) -> "Vault":
        """Another file of this vault's folder, opened by the same key and redacted alike."""
        return Vault(self.folder, self._settings, self._redactor, file)

    # -- reading ---------------------------------------------------------------------------------

    def names(self) -> list[str]:
        """Every secret's name, sorted. Decrypts nothing."""
        return sorted(self._read().entries)

    def updated_at(self, name: str) -> str | None:
        """When a secret was last written (UTC), without decrypting it. `None` if absent."""
        entry = self._read().entries.get(name)
        return None if entry is None else entry.updated_at

    def get(self, name: str) -> str | None:
        """The secret, or `None` if the vault has no such name.

        "Not set" and "set but unreadable" ask opposite things of the caller, so they differ: an
        absent name is `None` even when the vault cannot open, and a present one that cannot be
        read raises.
        """
        document = self._read()
        entry = document.entries.get(name)
        if entry is None:
            return None
        return self._decrypt(document, name, entry)

    def redact(self, text: str) -> str:
        """`text` without the secrets this process has read or written, as the logs show them."""
        return self._redactor.redact(text)

    def register(self, value: str) -> None:
        """Add `value` to the redactor without storing it. For a secret the user pasted on the
        app's card (step 16d): a failed write still leaves nothing in later log lines."""
        self._redactor.add(value)

    def problem(self) -> str | None:
        """Why the vault cannot open here, in one sentence, or `None`. Decrypts nothing."""
        try:
            document = self._read()
            if document.entries:
                self._opening_key(document)
        except SecretsError as error:
            return str(error)
        return None

    def verify(self) -> dict[str, str]:
        """Decrypt every entry; return the ones that fail, each with its reason."""
        problems: dict[str, str] = {}
        for name in self.names():
            try:
                self.get(name)
            except SecretsError as error:
                problems[name] = str(error)
        return problems

    # -- writing ---------------------------------------------------------------------------------

    def set(self, name: str, value: str) -> None:
        """Encrypt and store a secret, replacing any earlier value of the same name."""
        check_name(name)
        if not value:
            raise SecretsError(f"The value for {name} is empty. Delete the secret instead.")
        private_folder(self.folder)
        with self._locked():
            self._store(self._read_unlocked(), name, value)
        self._redactor.add(value)
        _logger.info("Secret %s stored.", name, extra={"secret_name": name})

    def keep(self, name: str, value: str) -> str:
        """The secret under `name`; when the vault has none, `value` is stored and returned. The
        read and the write share one lock, so two processes asking at once get the same secret."""
        check_name(name)
        if not value:
            raise SecretsError(f"The value for {name} is empty.")
        private_folder(self.folder)
        with self._locked():
            document = self._read_unlocked()
            entry = document.entries.get(name)
            if entry is not None:
                return self._decrypt(document, name, entry)
            self._store(document, name, value)
        self._redactor.add(value)
        _logger.info("Secret %s stored.", name, extra={"secret_name": name})
        return value

    def delete(self, name: str) -> bool:
        """Remove a secret. Returns whether it existed."""
        if not self.path.exists():
            return False
        with self._locked():
            document = self._read_unlocked()
            if name not in document.entries:
                return False
            self._write(document.without(name))
        _logger.info("Secret %s deleted.", name, extra={"secret_name": name})
        return True

    def _store(self, document: VaultDocument, name: str, value: str) -> None:
        """Encrypt and write one secret. The caller holds the lock."""
        key = self._writing_key(document)
        recipient = key.identity.to_public()
        ciphertext = base64.b64encode(pyrage.encrypt(value.encode("utf-8"), [recipient]))
        entry = Entry(ciphertext.decode("ascii"), format_utc(utc_now()))
        self._write(document.with_entry(name, entry, key.recipient))

    def _decrypt(self, document: VaultDocument, name: str, entry: Entry) -> str:
        key = self._opening_key(document)
        try:
            plaintext = pyrage.decrypt(
                base64.b64decode(entry.ciphertext, validate=True), [key.identity]
            )
            value = plaintext.decode("utf-8")
        except (binascii.Error, pyrage.DecryptError, UnicodeDecodeError) as error:
            raise SecretUnreadableError(
                f"The secret {name} in {self.path} cannot be decrypted: its entry is damaged. "
                "Set it again; the other secrets are not affected."
            ) from error
        self._redactor.add(value)
        return value

    # -- the key ---------------------------------------------------------------------------------

    def _found_key(self) -> VaultKey | None:
        if self._key is None:
            self._key = find_key(self._settings, self.folder)
        return self._key

    def _opening_key(self, document: VaultDocument) -> VaultKey:
        key = self._found_key()
        if key is None:
            raise VaultLockedError(missing_key_message(self.folder))
        if document.recipient is not None and document.recipient != key.recipient:
            raise VaultLockedError(
                f"The key from {key.origin} is not this vault's key: {self.path} is encrypted for "
                f"{document.recipient}, and the key opens {key.recipient}. Use the key the vault "
                "was created with."
            )
        return key

    def _writing_key(self, document: VaultDocument) -> VaultKey:
        """The key to encrypt with. A new vault gets a new home key; a vault in use never does."""
        if self._found_key() is None and document.recipient is None and not document.entries:
            self._key = create_home_key(self.folder)
            _logger.info("Created the vault key.", extra={"origin": self._key.origin})
        return self._opening_key(document)

    # -- the file --------------------------------------------------------------------------------

    def _read(self) -> VaultDocument:
        if not self.path.exists():
            return VaultDocument()
        with self._locked():
            return self._read_unlocked()

    def _read_unlocked(self) -> VaultDocument:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return VaultDocument()
        except (OSError, UnicodeDecodeError) as error:
            raise VaultDamagedError(f"The vault {self.path} cannot be read: {error}.") from error
        return parse_document(text, str(self.path))

    def _write(self, document: VaultDocument) -> None:
        replace_file(self.path, render_document(document).encode("utf-8"))

    @contextmanager
    def _locked(self) -> Generator[None]:
        try:
            with locked(self.folder / LOCK_FILE):
                yield
        except FileBusyError as error:
            raise SecretsError(f"The vault {self.path} stayed busy: {error}") from error
