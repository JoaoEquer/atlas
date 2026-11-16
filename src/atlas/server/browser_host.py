"""The app browser's host, as the gateway holds it: the proof that the app and this server share the
computer's files, and the link the browser endpoint reaches the app's browser through.

The proof (docs/ARCHITECTURE.md, Browser) is a file, not an address: an SSH tunnel and a published
Docker port both arrive from the loopback, so the peer's address proves nothing. The app writes a
file with a random nonce in its own folder and names it in `browser.host`; the server reads it and
accepts only when the nonce is there. A server on another machine, in a container or in a WSL
distribution does not see the file. It is no defence against a hostile app (whoever holds the
token already runs commands through the agent): it is against the honest mistake of driving this
computer's browser from a server somewhere else.
"""

import hmac
import os
import re
import stat
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from atlas.browser import HostError, HostTab, HostTabs
from atlas.server.protocol import (
    BrowserCdpParams,
    BrowserCdpResult,
    BrowserOpenParams,
    BrowserTabRef,
    BrowserTabs,
    Empty,
)

#: The folder the app writes its proofs in, and the shape of a proof's name and nonce.
PROOF_FOLDER = "browser-proof"
_PROOF_NAME = re.compile(r"[0-9a-f]{32}")
_NONCE = re.compile(r"[0-9a-f]{64}")
#: The most a proof file may hold.
PROOF_BYTES = 128
#: What the app is told when the proof is not accepted: the same for every cause but the shape of
#: the request, so it learns nothing about this machine's files.
NOT_SHARED = "This server does not share this computer's files with the app."
NOT_A_PROOF = "That is not a browser host proof."


def check_proof(proof_file: str, nonce: str) -> str | None:
    """Why the proof is not accepted, or `None` when the file named holds the nonce. Never says
    what a file holds, and the path is not logged by whoever asks."""
    if not _NONCE.fullmatch(nonce):
        return NOT_A_PROOF
    path = Path(proof_file)
    if (
        not path.is_absolute()
        or path.parent.name != PROOF_FOLDER
        or not _PROOF_NAME.fullmatch(path.name)
    ):
        return NOT_A_PROOF
    try:
        # Not a link to something else, and a small regular file.
        status = os.lstat(path)
        if not stat.S_ISREG(status.st_mode) or status.st_size > PROOF_BYTES:
            return NOT_SHARED
        held = path.read_bytes()
    except OSError:
        return NOT_SHARED
    if not hmac.compare_digest(held.strip(), nonce.encode()):
        return NOT_SHARED
    return None


#: Asks the app a `browser.*` request and waits for the answer: its result, or `HostError`.
type Ask = Callable[[str, object], Awaitable[object]]


class HostLink:
    """The app's browser as the endpoint sees it (`AppHost`), reached through one connection."""

    def __init__(self, ask: Ask) -> None:
        self._ask = ask

    async def tabs(self) -> HostTabs:
        found = await self._ask("browser.tabs", Empty())
        if not isinstance(found, BrowserTabs):  # pragma: no cover - the gateway reads the shape
            raise HostError("The app answered browser.tabs wrongly.")
        return HostTabs(
            tuple(
                HostTab(row.id, row.url, row.title, row.owner, row.loading, row.selected)
                for row in found.tabs
            ),
            found.user_agent,
            found.product,
        )

    async def open(self, owner: str, url: str, *, background: bool) -> str:
        found = await self._ask("browser.open", BrowserOpenParams(owner, url, background))
        if not isinstance(found, BrowserTabRef):  # pragma: no cover - the gateway reads the shape
            raise HostError("The app answered browser.open wrongly.")
        return found.id

    async def close(self, tab: str) -> None:
        await self._ask("browser.close", BrowserTabRef(tab))

    async def show(self, tab: str) -> None:
        await self._ask("browser.show", BrowserTabRef(tab))

    async def attach(self, tab: str) -> None:
        await self._ask("browser.attach", BrowserTabRef(tab))

    async def detach(self, tab: str) -> None:
        await self._ask("browser.detach", BrowserTabRef(tab))

    async def cdp(self, tab: str, method: str, params: Mapping[str, Any]) -> object:
        found = await self._ask("browser.cdp", BrowserCdpParams(tab, method, dict(params)))
        if not isinstance(
            found, BrowserCdpResult
        ):  # pragma: no cover - the gateway reads the shape
            raise HostError("The app answered browser.cdp wrongly.")
        return found.result
