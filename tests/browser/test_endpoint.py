"""The browser endpoint over a real WebSocket, spoken to by the engine's own CDP client, with the
app browser replaced by tests/fake_app_host.py: what a conversation sees and may touch, how a
page's command and events travel, and who is let in."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
import websockets
from cdp_use import CDPClient

from atlas.browser.endpoint import BrowserEndpoint
from fake_app_host import FakeAppHost

type Check = Callable[[BrowserEndpoint, FakeAppHost], Awaitable[None]]


async def _nothing(_url: str) -> str | None:
    return None


def _run(check: Check, refuse: Callable[[str], Awaitable[str | None]] = _nothing) -> FakeAppHost:
    host = FakeAppHost()

    async def main() -> None:
        endpoint = BrowserEndpoint(host, refuse)
        await endpoint.start()
        try:
            await check(endpoint, host)
        finally:
            await endpoint.close()

    asyncio.run(main())
    return host


class Engine:
    """The engine's client, with the events it heard."""

    def __init__(self, url: str) -> None:
        self.client = CDPClient(url)
        self.events: list[tuple[str, Any, str | None]] = []

    async def __aenter__(self) -> "Engine":
        await self.client.start()
        for name in (
            "Target.detachedFromTarget",
            "Target.targetDestroyed",
            "Page.loadEventFired",
            "Network.requestWillBeSent",
            "Inspector.detached",
        ):
            self.client._event_registry.register(  # pyright: ignore[reportPrivateUsage]
                name, lambda data, session, name=name: self.events.append((name, data, session))
            )
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.client.stop()

    async def send(
        self, method: str, params: dict[str, Any] | None = None, *, session: str | None = None
    ) -> Any:
        return await self.client.send_raw(method, params, session)

    async def error(
        self, method: str, params: dict[str, Any] | None = None, *, session: str | None = None
    ) -> dict[str, Any]:
        with pytest.raises(RuntimeError) as raised:
            await self.send(method, params, session=session)
        (payload,) = raised.value.args
        assert isinstance(payload, dict)
        return payload  # pyright: ignore[reportUnknownVariableType]

    async def heard(self, name: str, count: int = 1) -> list[tuple[Any, str | None]]:
        for _ in range(100):
            found = [(data, session) for n, data, session in self.events if n == name]
            if len(found) >= count:
                return found
            await asyncio.sleep(0.02)
        raise AssertionError(f"{name} was not heard: {self.events}")


def test_a_conversation_sees_its_own_tabs_the_selected_one_first() -> None:
    async def check(endpoint: BrowserEndpoint, host: FakeAppHost) -> None:
        host.add("c-1", "https://old.example", "Old")
        host.add("c-2", "https://other.example", "Other conversation")
        host.add("", "https://draft.example", "The draft's")
        host.add("c-1", "https://user.example", "Opened by the user", selected=True)
        host.add("c-1", "", "Blank")
        async with Engine(endpoint.url_for("c-1")) as engine:
            found = await engine.send("Target.getTargets")
            infos = found["targetInfos"]
            assert [i["title"] for i in infos] == ["Opened by the user", "Blank", "Old"]
            assert infos[1]["url"] == "about:blank"
            assert infos[0]["type"] == "page"
            assert infos[0]["attached"] is False
            # Another conversation's tab, and the draft's, do not exist for it.
            for method in ("Target.attachToTarget", "Target.closeTarget", "Target.activateTarget",
                           "Target.getTargetInfo"):  # fmt: skip
                params: dict[str, Any] = {"targetId": "tab-2"}
                if method == "Target.attachToTarget":
                    params["flatten"] = True
                refused = await engine.error(method, params)
                assert refused["message"] == "No target with given id found"
            refused = await engine.error(
                "Target.attachToTarget", {"targetId": "tab-3", "flatten": True}
            )
            assert refused["message"] == "No target with given id found"
        async with Engine(endpoint.url_for("c-2")) as other:
            assert [i["title"] for i in (await other.send("Target.getTargets"))["targetInfos"]] == [
                "Other conversation"
            ]
        async with Engine(endpoint.url_for("c-3")) as nobody:
            assert (await nobody.send("Target.getTargets"))["targetInfos"] == []

    host = _run(check)
    assert not [call for call in host.calls if call[0] in ("attach", "close", "show", "cdp")]


def test_a_tab_is_made_for_the_conversation_and_driven_in_a_session() -> None:
    async def check(endpoint: BrowserEndpoint, host: FakeAppHost) -> None:
        host.results["Runtime.evaluate"] = {"result": {"type": "number", "value": 2}}
        async with Engine(endpoint.url_for("c-1")) as engine:
            made = await engine.send(
                "Target.createTarget", {"url": "about:blank", "background": True}
            )
            tab = made["targetId"]
            # The tab is the conversation's, whoever made it: it is listed at once.
            assert [
                i["targetId"] for i in (await engine.send("Target.getTargets"))["targetInfos"]
            ] == [tab]
            assert (await engine.error("Target.attachToTarget", {"targetId": tab}))[
                "message"
            ].startswith("Not supported")
            attached = await engine.send(
                "Target.attachToTarget", {"targetId": tab, "flatten": True}
            )
            session = attached["sessionId"]
            assert len(session) == 32
            listed = (await engine.send("Target.getTargets"))["targetInfos"]
            assert listed[0]["attached"] is True
            result = await engine.send("Runtime.evaluate", {"expression": "1+1"}, session=session)
            assert result == {"result": {"type": "number", "value": 2}}
            # Emulating a desktop layout is a page's own business.
            await engine.send(
                "Emulation.setDeviceMetricsOverride",
                {"width": 1280, "height": 800, "deviceScaleFactor": 1, "mobile": False},
                session=session,
            )
            # A second session on the tab does not attach the host again.
            again = await engine.send("Target.attachToTarget", {"targetId": tab, "flatten": True})
            await engine.send("Target.detachFromTarget", {"sessionId": again["sessionId"]})
            await engine.send("Target.detachFromTarget", {"sessionId": session})
            assert (await engine.error("Target.detachFromTarget", {"sessionId": session}))[
                "message"
            ] == "No session with given id"
            await engine.send("Target.activateTarget", {"targetId": tab})
            info = await engine.send("Target.getTargetInfo", {"targetId": tab})
            assert info["targetInfo"]["targetId"] == tab
            assert (await engine.send("Target.getTargetInfo"))["targetInfo"]["type"] == "browser"
            assert await engine.send("Target.setDiscoverTargets", {"discover": True}) == {}
            assert await engine.send("Target.setAutoAttach", {"autoAttach": True}) == {}

    host = _run(check)
    assert host.calls[:2] == [("open", "c-1", "about:blank", "True"), ("tabs",)]
    assert host.calls.count(("attach", "tab-1")) == 1
    assert host.calls.count(("detach", "tab-1")) == 1
    assert ("show", "tab-1") in host.calls
    assert [method for _, method, _ in host.commands] == [
        "Runtime.evaluate",
        "Emulation.setDeviceMetricsOverride",
    ]
    assert host.commands[0] == ("tab-1", "Runtime.evaluate", {"expression": "1+1"})


def test_a_page_events_reach_every_session_of_the_tab_and_no_other() -> None:
    async def check(endpoint: BrowserEndpoint, host: FakeAppHost) -> None:
        first, second, third = host.add("c-1"), host.add("c-1"), host.add("c-2")
        async with (
            Engine(endpoint.url_for("c-1")) as engine,
            Engine(endpoint.url_for("c-2")) as other,
        ):
            one = (
                await engine.send("Target.attachToTarget", {"targetId": first, "flatten": True})
            )["sessionId"]
            two = (
                await engine.send("Target.attachToTarget", {"targetId": first, "flatten": True})
            )["sessionId"]
            await engine.send("Target.attachToTarget", {"targetId": second, "flatten": True})
            await other.send("Target.attachToTarget", {"targetId": third, "flatten": True})
            await endpoint.host_event(first, "Page.loadEventFired", {"timestamp": 1})
            await endpoint.host_event(third, "Network.requestWillBeSent", {"requestId": "r"})
            heard = await engine.heard("Page.loadEventFired", 2)
            assert sorted(session or "" for _, session in heard) == sorted([one, two])
            assert heard[0][0] == {"timestamp": 1}
            (seen,) = await other.heard("Network.requestWillBeSent")
            assert seen[0] == {"requestId": "r"}
            await asyncio.sleep(0.1)
            assert not [e for e in engine.events if e[0] == "Network.requestWillBeSent"]
            assert not [e for e in other.events if e[0] == "Page.loadEventFired"]

    _run(check)


def test_a_session_that_does_not_exist_gets_the_sentence_the_engine_looks_for() -> None:
    async def check(endpoint: BrowserEndpoint, host: FakeAppHost) -> None:
        tab = host.add("c-1")
        async with (
            Engine(endpoint.url_for("c-1")) as engine,
            Engine(endpoint.url_for("c-1")) as other,
        ):
            refused = await engine.error("Runtime.evaluate", {"expression": "1"}, session="NOPE")
            assert refused["message"] == "Session with given id not found."
            assert refused["code"] == -32001
            # A session of another engine's connection is as unknown.
            session = (
                await other.send("Target.attachToTarget", {"targetId": tab, "flatten": True})
            )["sessionId"]
            refused = await engine.error("Runtime.evaluate", {"expression": "1"}, session=session)
            assert refused["message"] == "Session with given id not found."
            refused = await engine.error("Target.detachFromTarget", {"sessionId": session})
            assert refused["message"] == "No session with given id"

    host = _run(check)
    assert not host.commands


def test_closing_a_tab_ends_its_sessions_and_a_released_debugger_does_too() -> None:
    async def check(endpoint: BrowserEndpoint, host: FakeAppHost) -> None:
        closing, letting_go = host.add("c-1"), host.add("c-1")
        async with Engine(endpoint.url_for("c-1")) as engine:
            one = (
                await engine.send("Target.attachToTarget", {"targetId": closing, "flatten": True})
            )["sessionId"]
            two = (
                await engine.send(
                    "Target.attachToTarget", {"targetId": letting_go, "flatten": True}
                )
            )["sessionId"]
            assert await engine.send("Target.closeTarget", {"targetId": closing}) == {
                "success": True
            }
            ((detached, _),) = await engine.heard("Target.detachedFromTarget")
            assert detached == {"sessionId": one, "targetId": closing}
            ((destroyed, _),) = await engine.heard("Target.targetDestroyed")
            assert destroyed == {"targetId": closing}
            assert (await engine.error("Runtime.evaluate", {"expression": "1"}, session=one))[
                "message"
            ] == ("Session with given id not found.")
            # The app says the page's debugger let go: the engine hears it, then the end.
            await endpoint.host_event(letting_go, "Inspector.detached", {"reason": "target_closed"})
            ((said, session),) = await engine.heard("Inspector.detached")
            assert said == {"reason": "target_closed"}
            assert session == two
            assert len(await engine.heard("Target.detachedFromTarget", 2)) == 2
            refused = await engine.error("Runtime.evaluate", {"expression": "1"}, session=two)
            assert refused["message"] == "Session with given id not found."

    host = _run(check)
    assert ("close", "tab-1") in host.calls


def test_the_browser_says_what_it_is() -> None:
    async def check(endpoint: BrowserEndpoint, host: FakeAppHost) -> None:
        async with Engine(endpoint.url_for("c-1")) as engine:
            version = await engine.send("Browser.getVersion")
            assert version["userAgent"] == host.user_agent
            assert version["product"] == "Chrome/150.0.0.0"
            assert version["protocolVersion"] == "1.3"
            # The engine also asks it on a page's session.
            tab = await engine.send("Target.createTarget", {"url": "about:blank"})
            session = (
                await engine.send(
                    "Target.attachToTarget", {"targetId": tab["targetId"], "flatten": True}
                )
            )["sessionId"]
            assert await engine.send("Browser.getVersion", session=session) == version

    _run(check)


def test_what_a_page_session_may_not_do_is_refused_before_the_app() -> None:
    async def check(endpoint: BrowserEndpoint, host: FakeAppHost) -> None:
        tab = host.add("c-1")
        async with Engine(endpoint.url_for("c-1")) as engine:
            session = (
                await engine.send("Target.attachToTarget", {"targetId": tab, "flatten": True})
            )["sessionId"]
            for method in (
                "Browser.close",
                "Target.getTargets",
                "Page.setDownloadBehavior",
                "Page.close",
                "IO.read",
                "Storage.getCookies",
            ):
                refused = await engine.error(method, {}, session=session)
                assert refused["message"] == f"{method} is not available in the app's browser."
            # A command of the browser's own that Atlas does not answer is Chrome's own words.
            for method in ("Target.exposeDevToolsProtocol", "Browser.close", "SystemInfo.getInfo"):
                refused = await engine.error(method, {})
                assert refused["message"] == f"'{method}' wasn't found"
                assert refused["code"] == -32601

    host = _run(check)
    assert not host.commands


def test_an_address_is_checked_on_the_wire_and_the_app_never_hears_of_a_refused_one() -> None:
    async def refuse(url: str) -> str | None:
        return "Blocked: a secret" if "key=" in url else None

    async def check(endpoint: BrowserEndpoint, host: FakeAppHost) -> None:
        tab = host.add("c-1")
        async with Engine(endpoint.url_for("c-1")) as engine:
            session = (
                await engine.send("Target.attachToTarget", {"targetId": tab, "flatten": True})
            )["sessionId"]
            for url in (
                "http://169.254.169.254/latest",
                "file:///etc/passwd",
                "https://a.example/?key=1",
            ):
                refused = await engine.error("Page.navigate", {"url": url}, session=session)
                assert refused["message"].startswith("Blocked")
                refused = await engine.error("Target.createTarget", {"url": url})
                assert refused["message"].startswith("Blocked")
            await engine.send("Page.navigate", {"url": "https://example.com/"}, session=session)
            await engine.send("Target.createTarget", {"url": "http://127.0.0.1:8000/"})

    host = _run(check, refuse)
    assert host.commands == [("tab-1", "Page.navigate", {"url": "https://example.com/"})]
    assert [call for call in host.calls if call[0] == "open"] == [
        ("open", "c-1", "http://127.0.0.1:8000/", "True")
    ]


def test_a_command_the_app_refuses_comes_back_with_its_own_words() -> None:
    async def check(endpoint: BrowserEndpoint, host: FakeAppHost) -> None:
        tab = host.add("c-1")
        host.errors["DOM.getDocument"] = "Cannot find context with specified id"
        async with Engine(endpoint.url_for("c-1")) as engine:
            session = (
                await engine.send("Target.attachToTarget", {"targetId": tab, "flatten": True})
            )["sessionId"]
            refused = await engine.error("DOM.getDocument", {}, session=session)
            assert refused == {"code": -32000, "message": "Cannot find context with specified id"}

    _run(check)


def test_only_an_engine_with_a_conversations_path_gets_in() -> None:
    async def check(endpoint: BrowserEndpoint, host: FakeAppHost) -> None:
        url = endpoint.url_for("c-1")
        port = url.split(":")[2].split("/")[0]
        assert url == endpoint.url_for("c-1")
        assert url != endpoint.url_for("c-2")

        async def status(target: str, **headers: str) -> int:
            with pytest.raises(websockets.InvalidStatus) as raised:
                async with websockets.connect(target, additional_headers=headers or None):
                    pass
            return raised.value.response.status_code

        # No such path; a page of the user's browser (an Origin); a name that resolves here.
        assert await status(f"ws://127.0.0.1:{port}/devtools/browser/guess") == 404
        assert await status(f"ws://127.0.0.1:{port}/") == 404
        assert await status(url, Origin="https://evil.example") == 403
        # The client names the host itself, so a second one is a request of someone else's.
        assert await status(url, Host="evil.example:80") == 403
        async with websockets.connect(f"ws://localhost:{port}/{url.split('/', 3)[3]}") as ok:
            await ok.send('{"id": 1, "method": "Target.getTargets"}')
            assert '"targetInfos"' in str(await ok.recv())

    _run(check)


def test_a_command_that_is_not_one_is_ignored_or_refused() -> None:
    async def check(endpoint: BrowserEndpoint, host: FakeAppHost) -> None:
        async with websockets.connect(endpoint.url_for("c-1")) as socket:
            await socket.send("not json")
            await socket.send("[1]")
            await socket.send('{"id": 3, "method": 5}')
            assert '"id": 3' in str(await socket.recv())
            await socket.send('{"id": 4, "method": "Target.createTarget", "params": {"url": 7}}')
            assert "url must be a string" in str(await socket.recv())

    _run(check)


def test_a_lost_app_ends_the_engines_and_a_gone_engine_releases_its_pages() -> None:
    async def check(endpoint: BrowserEndpoint, host: FakeAppHost) -> None:
        tab = host.add("c-1")
        async with Engine(endpoint.url_for("c-1")) as engine:
            await engine.send("Target.attachToTarget", {"targetId": tab, "flatten": True})
        # The engine went away with a session on the tab: the page stops sending.
        for _ in range(100):
            if ("detach", tab) in host.calls:
                break
            await asyncio.sleep(0.02)
        assert ("detach", tab) in host.calls
        async with Engine(endpoint.url_for("c-1")) as engine:
            host.gone = True
            error = await engine.error("Target.getTargets")
            assert error["message"] == "The app browser closed: the app is gone."
            await endpoint.lost()
            await asyncio.wait_for(_closed(engine), 5)

    _run(check)


async def _closed(engine: Engine) -> None:
    assert engine.client.ws is not None
    await engine.client.ws.wait_closed()
    assert engine.client.ws.close_code == 1001
