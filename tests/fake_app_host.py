"""A stand-in for the app browser, for the tests: tabs in memory, behind the interface the browser
endpoint needs (`AppHost`). It lists them oldest first as the app does, records every call it gets
(`calls`), and answers a page's command from `results`, or with an error from `errors`; the test
tells the endpoint its events itself."""

import itertools
from collections.abc import Callable, Mapping
from typing import Any

from atlas.browser.endpoint import AppHost
from atlas.browser.errors import HostError
from atlas.browser.model import HostTab, HostTabs

type Answer = object | Callable[[str, Mapping[str, Any]], object]


class FakeAppHost(AppHost):
    def __init__(self) -> None:
        self.tab_rows: list[HostTab] = []
        self.calls: list[tuple[str, ...]] = []
        self.commands: list[tuple[str, str, dict[str, Any]]] = []
        self.results: dict[str, Answer] = {}
        self.errors: dict[str, str] = {}
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0) Chrome/150.0.0.0 Safari/537.36"
        self.product = "Chrome/150.0.0.0"
        self.gone = False
        self._ids = itertools.count(1)

    def add(
        self, owner: str, url: str = "about:blank", title: str = "", *, selected: bool = False
    ) -> str:
        tab = HostTab(f"tab-{next(self._ids)}", url, title, owner, selected=selected)
        self.tab_rows.append(tab)
        return tab.id

    def _check(self) -> None:
        if self.gone:
            raise HostError("The app browser closed: the app is gone.")

    async def tabs(self) -> HostTabs:
        self._check()
        self.calls.append(("tabs",))
        return HostTabs(tuple(self.tab_rows), self.user_agent, self.product)

    async def open(self, owner: str, url: str, *, background: bool) -> str:
        self._check()
        self.calls.append(("open", owner, url, str(background)))
        return self.add(owner, url, "")

    async def close(self, tab: str) -> None:
        self._check()
        self.calls.append(("close", tab))
        self.tab_rows = [row for row in self.tab_rows if row.id != tab]

    async def show(self, tab: str) -> None:
        self._check()
        self.calls.append(("show", tab))

    async def attach(self, tab: str) -> None:
        self._check()
        self.calls.append(("attach", tab))

    async def detach(self, tab: str) -> None:
        self._check()
        self.calls.append(("detach", tab))

    async def cdp(self, tab: str, method: str, params: Mapping[str, Any]) -> object:
        self._check()
        self.calls.append(("cdp", tab, method))
        self.commands.append((tab, method, dict(params)))
        if method in self.errors:
            raise HostError(self.errors[method])
        answer = self.results.get(method, {})
        return answer(tab, params) if callable(answer) else answer
