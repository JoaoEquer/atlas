"""The app browser as a real headless Chrome gives it: each tab of the app is a target of that
Chrome, kept in an owner's name and driven through a session of its own, as the app does with a
view and its debugger. Only the app is stand-in; the pages, the commands and the events are
Chrome's. The browser endpoint and the engine in front of it are Atlas's own."""

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

import websockets

from atlas.browser.endpoint import AppHost
from atlas.browser.errors import HostError
from atlas.browser.model import HostTab, HostTabs
from atlas.browser.store import Chrome, close_chrome, open_chrome

type Sink = Callable[[str, str, object], Awaitable[None]]


class ChromeAppHost(AppHost):
    """Tabs of a headless Chrome, the way the app lists them: oldest first."""

    def __init__(self, program: str, profile: Path) -> None:
        self._program = program
        self._profile = profile
        self._chrome: Chrome | None = None
        self._socket: websockets.ClientConnection | None = None
        self._reader: asyncio.Task[None] | None = None
        self._ids = 0
        self._waiting: dict[int, asyncio.Future[dict[str, Any]]] = {}
        #: The tabs in the order they were made: id, owner and the session that drives it.
        self._tabs: dict[str, tuple[str, str]] = {}
        self._by_session: dict[str, str] = {}
        self._watched: set[str] = set()
        self._selected: str | None = None
        self.sink: Sink | None = None
        self.user_agent = ""
        self.product = ""

    async def start(self) -> None:
        self._chrome = await open_chrome([self._program], self._profile, headless=True)
        self._socket = await websockets.connect(self._chrome.endpoint, max_size=None)
        self._reader = asyncio.create_task(self._read())
        version = await self._call("Browser.getVersion")
        self.user_agent, self.product = version["userAgent"], version["product"]
        await self._call("Target.setDiscoverTargets", {"discover": True})

    async def stop(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
        if self._socket is not None:
            await self._socket.close()
        if self._chrome is not None:
            await close_chrome(self._chrome)

    # -- Chrome ------------------------------------------------------------------------------------

    async def _call(
        self, method: str, params: Mapping[str, Any] | None = None, session: str | None = None
    ) -> dict[str, Any]:
        assert self._socket is not None
        self._ids += 1
        frame: dict[str, Any] = {"id": self._ids, "method": method, "params": dict(params or {})}
        if session is not None:
            frame["sessionId"] = session
        future = asyncio.get_running_loop().create_future()
        self._waiting[self._ids] = future
        await self._socket.send(json.dumps(frame))
        answer = await asyncio.wait_for(future, 90)
        if "error" in answer:
            raise HostError(str(answer["error"].get("message", answer["error"])))
        return answer.get("result", {})

    async def _read(self) -> None:
        assert self._socket is not None
        async for message in self._socket:
            frame = json.loads(message)
            if "id" in frame and frame["id"] in self._waiting:
                self._waiting.pop(frame["id"]).set_result(frame)
            elif frame.get("method") == "Target.targetDestroyed":
                await self._destroyed(frame["params"]["targetId"])
            else:
                tab = self._by_session.get(frame.get("sessionId", ""))
                if tab in self._watched and tab is not None and self.sink is not None:
                    await self.sink(tab, frame["method"], frame.get("params", {}))

    async def _destroyed(self, tab: str) -> None:
        known = self._tabs.pop(tab, None)
        if known is None:
            return
        self._by_session.pop(known[1], None)
        was_watched = tab in self._watched
        self._watched.discard(tab)
        if was_watched and self.sink is not None:
            await self.sink(tab, "Inspector.detached", {"reason": "target_closed"})

    # -- the app's tabs ----------------------------------------------------------------------------

    async def add(self, owner: str, url: str = "about:blank", *, selected: bool = False) -> str:
        """A tab the user opened in a conversation."""
        tab = await self.open(owner, url, background=not selected)
        if selected:
            self._selected = tab
        return tab

    async def tabs(self) -> HostTabs:
        found = {
            info["targetId"]: info
            for info in (await self._call("Target.getTargets"))["targetInfos"]
        }
        rows = tuple(
            HostTab(
                tab,
                found[tab]["url"],
                found[tab]["title"],
                owner,
                selected=tab == self._selected,
            )
            for tab, (owner, _) in self._tabs.items()
            if tab in found
        )
        return HostTabs(rows, self.user_agent, self.product)

    async def open(self, owner: str, url: str, *, background: bool) -> str:
        made = await self._call("Target.createTarget", {"url": url, "background": True})
        tab = made["targetId"]
        session = (await self._call("Target.attachToTarget", {"targetId": tab, "flatten": True}))[
            "sessionId"
        ]
        self._tabs[tab] = (owner, session)
        self._by_session[session] = tab
        return tab

    async def close(self, tab: str) -> None:
        await self._call("Target.closeTarget", {"targetId": tab})
        await self._destroyed(tab)

    async def show(self, tab: str) -> None:
        self._selected = tab
        await self._call("Target.activateTarget", {"targetId": tab})

    async def attach(self, tab: str) -> None:
        self._watched.add(tab)

    async def detach(self, tab: str) -> None:
        self._watched.discard(tab)

    async def cdp(self, tab: str, method: str, params: Mapping[str, Any]) -> object:
        known = self._tabs.get(tab)
        if known is None:
            raise HostError(f"No tab {tab}.")
        return await self._call(method, params, known[1])

    async def destroy_outside(self, tab: str) -> None:
        """The user closes a tab on the screen: Chrome tells, and the host passes it on."""
        with contextlib.suppress(HostError):
            await self._call("Target.closeTarget", {"targetId": tab})
