"""The cipher of a connection through the relay (step 20): only the app and the home read what
crosses it; the relay sees bytes it cannot read.

- **The key** is the connection code's: 32 random bytes the home made when it was paired with a
  relay, which the relay never sees. The code is the secret, as its token already is.
- **Each connection agrees its own keys:** the app sends a fresh nonce, the home answers with its
  own, and HKDF-SHA256 over the code's key, salted with both, gives one key for each direction. A
  connection recorded and played again finds other keys, and fails.
- **Each frame is sealed** with ChaCha20-Poly1305, its nonce the frame's number in its direction:
  the relay can neither read, change, drop, reorder nor replay a frame without the other end
  stopping at once (`TunnelError`).
- **The home proves it holds the key first** (its answer carries a sealed `ready`), and the app
  proves it with its first frame, which the home opens before it forwards anything.

Pure: bytes in, bytes out.
"""

import base64
import secrets
from dataclasses import dataclass
from typing import Literal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from atlas.instances.errors import TunnelError

KEY_BYTES = 32
NONCE_BYTES = 16
#: What starts each side's opening frame: the tunnel's version.
HELLO = b"ATR1"
#: What the home's opening frame seals, by which the app knows it holds the key.
READY = b"ready"

type Side = Literal["app", "home"]


def new_key() -> str:
    """A new key for a home's relay, as the connection code carries it (base64url, unpadded)."""
    return _text(secrets.token_bytes(KEY_BYTES))


def read_key(text: str) -> bytes:
    """A key from its text; raises `TunnelError` when it is not one."""
    try:
        key = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except ValueError as error:
        raise TunnelError("The relay's key is damaged.") from error
    if len(key) != KEY_BYTES:
        raise TunnelError("The relay's key is damaged.")
    return key


def hello(nonce: bytes) -> bytes:
    """A side's opening frame: the version and its nonce."""
    return HELLO + nonce


def read_hello(frame: bytes) -> tuple[bytes, bytes]:
    """The nonce of the other side's opening frame, and what follows it; raises `TunnelError`
    when it is not one."""
    if not frame.startswith(HELLO) or len(frame) < len(HELLO) + NONCE_BYTES:
        raise TunnelError("The other end does not speak this tunnel: is it an Atlas?")
    rest = frame[len(HELLO) :]
    return rest[:NONCE_BYTES], rest[NONCE_BYTES:]


@dataclass
class _Direction:
    cipher: ChaCha20Poly1305
    count: int = 0

    def nonce(self) -> bytes:
        nonce = self.count.to_bytes(12, "big")
        self.count += 1
        return nonce


class Tunnel:
    """One connection's two keys, from one side: it seals what it sends and opens what it
    receives, each frame numbered."""

    def __init__(self, key: bytes, app_nonce: bytes, home_nonce: bytes, side: Side) -> None:
        sending = _derive(key, app_nonce + home_nonce, f"atlas relay {side}")
        other: Side = "home" if side == "app" else "app"
        receiving = _derive(key, app_nonce + home_nonce, f"atlas relay {other}")
        self._out = _Direction(ChaCha20Poly1305(sending))
        self._in = _Direction(ChaCha20Poly1305(receiving))

    def seal(self, data: bytes) -> bytes:
        return self._out.cipher.encrypt(self._out.nonce(), data, None)

    def open(self, data: bytes) -> bytes:
        """A frame opened; raises `TunnelError` when it was not sealed with this connection's key
        in its order."""
        try:
            return self._in.cipher.decrypt(self._in.nonce(), data, None)
        except InvalidTag as error:
            raise TunnelError(
                "A frame through the relay did not open: the code's key differs, or the relay "
                "changed what it carried."
            ) from error


def _derive(key: bytes, salt: bytes, info: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=KEY_BYTES, salt=salt, info=info.encode()).derive(
        key
    )


def _text(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")
