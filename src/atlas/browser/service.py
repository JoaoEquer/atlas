"""The browser: one Chrome per mode, opened when a conversation first needs it, and the code of
each call run through the engine in the conversation's own session.

Decided with the user on 12/10/2026 (docs/ARCHITECTURE.md, Browser). A call, in order:

1. The addresses written in the code are checked: a cloud metadata address, by name, by number or
   by what the name resolves to, is refused, and so is an address carrying a secret Atlas knows.
   A private address is allowed: the browser is local.
2. The code runs, the conversation's tab pinned first; at its time limit its whole process tree
   is stopped.
3. The output is redacted, the error output cut to its last 4,000 characters, and the screenshot
   it printed, if any, goes with it.

In `profile` mode the call runs in the app's browser when the app is open on this computer and
lent it (`host`): the engine connects to the browser endpoint, one path per conversation, instead
of a Chrome's DevTools port. Otherwise, and always in `clean` and `headless`, it runs in Chrome.
"""

import asyncio
import hashlib
import json
import logging
import os
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from atlas.agent import Tool, ToolOutput
from atlas.browser.endpoint import AppHost, BrowserEndpoint
from atlas.browser.errors import BrowserError
from atlas.browser.model import (
    APP_CLOSED,
    APP_CLOSED_NO_CHROME,
    CHROME_ONLY,
    METADATA_REFUSED,
    NO_BROWSER,
    OWN_TAB_PREAMBLE,
    PARAMETERS,
    STDERR_CHARS,
    TO_APP,
    TO_CHROME,
    TOOL_NAME,
    VAULT_URL_REFUSED,
    BrowserMode,
    app_session_name,
    description,
    engine_environment,
    host_of,
    metadata_address,
    metadata_host,
    printed_images,
    session_name,
    timed_out,
    timeout_of,
    urls_in,
)
from atlas.browser.store import (
    ENGINE,
    Chrome,
    EngineRun,
    close_chrome,
    listening,
    open_chrome,
    read_image,
    remove_profile,
    run_engine,
    stop_engine,
)
from atlas.models import Image
from atlas.trust import untrusted

#: How long a name may take to resolve before the call goes on without it.
RESOLVE_SECONDS = 2.0
#: The largest screenshot handed to the model, as read_file's largest image.
IMAGE_BYTES = 5 * 1024 * 1024
_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp"}  # fmt: skip

_logger = logging.getLogger("atlas.browser")


@dataclass(frozen=True, slots=True)
class BrowserResult:
    """A call's result for the model, and the screenshot it printed."""

    text: str
    images: tuple[Image, ...] = ()


#: Resolves a host name to its addresses.
type Resolve = Callable[[str], Awaitable[list[str]]]


def _failure(error: str, note: str | None) -> BrowserResult:
    result: dict[str, object] = {"success": False, "error": error}
    if note:
        result["note"] = note
    return BrowserResult(json.dumps(result))


async def _resolve(host: str) -> list[str]:
    try:
        async with asyncio.timeout(RESOLVE_SECONDS):
            found = await asyncio.get_running_loop().getaddrinfo(host, None)
    except (OSError, TimeoutError, UnicodeError):
        return []
    return [str(entry[4][0]) for entry in found]


@dataclass(eq=False)
class _AppCall:
    """A call running in the app's browser, and whether the app went away under it."""

    endpoint: BrowserEndpoint
    lost: bool = False


@dataclass
class _Hosting:
    """What the app's browser holds for this server: the endpoint, the conversations whose engine
    daemon was started on it, and the calls running now."""

    endpoint: BrowserEndpoint
    sessions: set[str] = field(default_factory=set[str])
    calls: set[_AppCall] = field(default_factory=set[_AppCall])


class Browser:
    """The browsers of one Atlas, and the conversations that drive them."""

    def __init__(
        self,
        folder: Path,
        chrome: Sequence[str] | None,
        *,
        redact: Callable[[str], str],
        engine: Sequence[str] = ENGINE,
        resolve: Resolve = _resolve,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        """`folder` holds the profile, the engine's files and the workspaces; `chrome` is the
        browser's program and the arguments before its own, `None` where none is installed (the
        app's browser may still be lent); `redact` takes Atlas's secrets out of a text."""
        self._folder = folder
        self._chrome = None if chrome is None else list(chrome)
        self._redact = redact
        self._engine = list(engine)
        self._resolve = resolve
        self._environ = dict(os.environ if environ is None else environ)
        self._open: dict[BrowserMode, Chrome] = {}
        self._starting: dict[BrowserMode, asyncio.Lock] = {}
        self._sessions: dict[BrowserMode, set[str]] = {mode: set() for mode in BrowserMode}
        self._hosting: _Hosting | None = None
        #: Where each conversation's last call ran, so the first call in another browser says so.
        self._last: dict[str, str] = {}

    @property
    def available(self) -> bool:
        """Whether a call can run now: a Chrome is installed, or the app lends its browser."""
        return self._chrome is not None or self._hosting is not None

    def tool(self, conversation_id: str, *, sees_images: bool) -> Tool:
        """`browser_exec` for a conversation, deferred behind tool search."""

        async def run(arguments: dict[str, Any]) -> str | ToolOutput:
            code = arguments.get("code")
            if not isinstance(code, str) or not code.strip():
                return (
                    "No code provided. Pass Python that uses the pre-imported helpers, e.g. "
                    'new_tab("https://example.com") then print(page_info()).'
                )
            try:
                mode = BrowserMode(arguments.get("mode") or BrowserMode.PROFILE)
            except ValueError:
                return f"Unknown mode {arguments.get('mode')!r}: use profile, clean or headless."
            result = await self.run(
                conversation_id, code, mode, timeout_of(arguments.get("timeout_s"))
            )
            return ToolOutput(result.text, images=result.images) if result.images else result.text

        return Tool(
            TOOL_NAME,
            description(sees_images),
            PARAMETERS,
            run,
            failure_tolerant=True,
            deferred=True,
        )

    # -- a call ------------------------------------------------------------------------------------

    async def run(
        self, conversation_id: str, code: str, mode: BrowserMode, seconds: int
    ) -> BrowserResult:
        """Run a call's code in the conversation's session of the `mode`'s browser."""
        refused = await self._refused(code)
        if refused is not None:
            return BrowserResult(json.dumps({"success": False, "error": refused}))
        hosting = self._hosting
        if mode is BrowserMode.PROFILE and hosting is not None:
            return await self._run_in_app(hosting, conversation_id, code, seconds)
        if self._chrome is None:
            error = NO_BROWSER if mode is BrowserMode.PROFILE else CHROME_ONLY
            return BrowserResult(json.dumps({"success": False, "error": error}))
        return await self._run_in_chrome(self._chrome, conversation_id, code, mode, seconds)

    async def _run_in_chrome(
        self, program: list[str], conversation_id: str, code: str, mode: BrowserMode, seconds: int
    ) -> BrowserResult:
        note = self._switched(conversation_id, "chrome")
        session = session_name(conversation_id, mode)
        started = time.time()
        try:
            chrome = await self._chrome_for(mode, program)
            self._sessions[mode].add(conversation_id)
            environment = self._environment(chrome.endpoint, session, session)
            ran = await run_engine(self._engine, OWN_TAB_PREAMBLE + code, environment, seconds)
        except TimeoutError:
            return _failure(timed_out(seconds), note)
        except BrowserError as error:
            return _failure(str(error), note)
        return self._outcome(ran, started, note)

    async def _run_in_app(
        self, hosting: _Hosting, conversation_id: str, code: str, seconds: int
    ) -> BrowserResult:
        """A call in the app's browser: its own engine session, in the profile mode's workspace
        so the agent's files stay across a change of browser, with no tab of its own made first
        (the engine's daemon already makes one for the session). The first call of a conversation
        on this endpoint stops the daemon of an earlier life, which holds a path that is gone."""
        note = self._switched(conversation_id, "app")
        call = _AppCall(hosting.endpoint)
        hosting.calls.add(call)
        started = time.time()
        try:
            environment = self._environment(
                hosting.endpoint.url_for(conversation_id),
                app_session_name(conversation_id),
                session_name(conversation_id, BrowserMode.PROFILE),
            )
            if conversation_id not in hosting.sessions:
                hosting.sessions.add(conversation_id)
                await stop_engine(self._engine, environment)
            ran = await run_engine(self._engine, code, environment, seconds)
        except TimeoutError:
            return _failure(timed_out(seconds), note)
        except BrowserError as error:
            return _failure(str(error), note)
        finally:
            hosting.calls.discard(call)
        if call.lost:
            closed = APP_CLOSED if self._chrome is not None else APP_CLOSED_NO_CHROME
            return self._outcome(ran, started, closed, failed=True)
        return self._outcome(ran, started, note)

    def _switched(self, conversation_id: str, where: str) -> str | None:
        """A note when this call runs in another browser than the conversation's last one."""
        before = self._last.get(conversation_id)
        self._last[conversation_id] = where
        if before is None or before == where:
            return None
        return TO_APP if where == "app" else TO_CHROME

    def _outcome(
        self, ran: EngineRun, started: float, note: str | None, *, failed: bool = False
    ) -> BrowserResult:
        result: dict[str, object] = {
            "success": ran.exit_code == 0 and not failed,
            "exit_code": ran.exit_code,
            "output": self._redact(ran.output),
        }
        errors = self._redact(ran.errors.strip())
        if errors:
            result["stderr"] = (
                errors if len(errors) <= STDERR_CHARS else "… " + errors[-STDERR_CHARS:]
            )
        if note:
            result["note"] = note
        text = untrusted(TOOL_NAME, json.dumps(result, ensure_ascii=False))
        return BrowserResult(text, self._screenshot(ran.output, started))

    async def _refused(self, code: str) -> str | None:
        """Why the code's addresses may not be reached, or `None`."""
        for url in urls_in(code):
            refused = await self.refused_url(url)
            if refused is not None:
                return refused
        return None

    async def refused_url(self, url: str) -> str | None:
        """Why one address may not be reached, or `None`: a secret of the vault in it, or a cloud
        metadata address, by name, by number or by what the name resolves to."""
        if self._redact(url) != url:
            return VAULT_URL_REFUSED
        host = host_of(url)
        if not host:
            return None
        if metadata_host(host):
            return METADATA_REFUSED
        if any(metadata_address(address) for address in await self._resolve(host)):
            return METADATA_REFUSED
        return None

    def _screenshot(self, output: str, started: float) -> tuple[Image, ...]:
        for printed in printed_images(output):
            path = Path(printed)
            data = read_image(path, started, IMAGE_BYTES)
            if data is not None:
                return (Image(_MEDIA_TYPES.get(path.suffix.lower(), "image/png"), data),)
        return ()

    def _environment(self, endpoint: str, session: str, workspace: str) -> dict[str, str]:
        folder = self._folder / "workspaces" / workspace
        folder.mkdir(parents=True, exist_ok=True)
        return engine_environment(
            self._environ,
            home=str(self._folder / "engine"),
            workspace=str(folder),
            session=session,
            endpoint=endpoint,
            runtime=_socket_folder(self._folder, self._environ),
        )

    async def _chrome_for(self, mode: BrowserMode, program: list[str]) -> Chrome:
        """The mode's Chrome, opened again when the user closed it, with the engine's sessions on
        it stopped: each holds the endpoint it started with."""
        lock = self._starting.setdefault(mode, asyncio.Lock())
        async with lock:
            found = self._open.get(mode)
            if found is not None and await listening(found.endpoint):
                return found
            if found is not None:
                _logger.info("The browser was closed; it opens again.", extra={"mode": mode.value})
                await self._stop_sessions(mode, found)
                if not mode.kept:
                    remove_profile(found.profile)
            profile = self._profile(mode)
            if not mode.kept:
                remove_profile(profile)
            chrome = await open_chrome(program, profile, headless=not mode.windowed)
            self._open[mode] = chrome
            return chrome

    def _profile(self, mode: BrowserMode) -> Path:
        if mode.kept:
            return self._folder / "profile"
        return self._folder / "temporary" / mode.value

    async def _stop_sessions(self, mode: BrowserMode, chrome: Chrome) -> None:
        for conversation_id in self._sessions[mode]:
            session = session_name(conversation_id, mode)
            environment = self._environment(chrome.endpoint, session, session)
            await stop_engine(self._engine, environment)
        self._sessions[mode].clear()

    # -- the app's browser -------------------------------------------------------------------------

    async def host(self, host: AppHost) -> None:
        """Take the app's browser: from now `profile` calls run in it. A host taken before is
        let go of first, as if the app had closed."""
        await self.unhost()
        endpoint = BrowserEndpoint(host, self.refused_url)
        await endpoint.start()
        self._hosting = _Hosting(endpoint)

    async def unhost(self) -> None:
        """The app's browser is gone: the calls running in it end, the engines' daemons on it
        stop, and the next call runs in Chrome."""
        hosting, self._hosting = self._hosting, None
        if hosting is None:
            return
        for call in hosting.calls:
            call.lost = True
        sessions = {
            conversation_id: self._environment(
                hosting.endpoint.url_for(conversation_id),
                app_session_name(conversation_id),
                session_name(conversation_id, BrowserMode.PROFILE),
            )
            for conversation_id in hosting.sessions
        }
        await hosting.endpoint.close()
        for environment in sessions.values():
            await stop_engine(self._engine, environment)

    async def host_event(self, tab: str, method: str, params: object) -> None:
        """An event of one of the app browser's pages, for the engines driving it."""
        if self._hosting is not None:
            await self._hosting.endpoint.host_event(tab, method, params)

    # -- the end -----------------------------------------------------------------------------------

    async def close(self) -> None:
        """Stop every session and close every browser; the temporary profiles go with them."""
        await self.unhost()
        for mode, chrome in list(self._open.items()):
            await self._stop_sessions(mode, chrome)
            await close_chrome(chrome)
            if not mode.kept:
                remove_profile(chrome.profile)
        self._open.clear()


def _socket_folder(folder: Path, environ: Mapping[str, str]) -> str | None:
    """Where the engine's daemon puts its Unix socket: a short private folder, since a socket's
    address may hold at most 108 bytes on Linux (104 on macOS), and the home's own `runtime/` under
    a long home or a named one passed that (found by CI on 13/10/2026: "AF_UNIX path too long").
    Atlas's own choice over the engine's default. `None` on Windows, which uses a port."""
    if sys.platform == "win32":
        return None
    base = environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    mark = hashlib.sha256(str(folder.resolve()).encode("utf-8")).hexdigest()[:12]
    return str(Path(base) / f"atlas-{os.getuid()}" / mark)
