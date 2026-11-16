"""The app browser's host through the real server: the proof of a shared computer, the requests that
reach the host's connection alone, what it answers and what it does not, and a `browser_exec` from
the model to the engine and its browser endpoint. The app is tests/fake_app_client.py, a client of
the real gateway over a real WebSocket; the engine is tests/fake_engine.py, or a real CDP client
speaking to the endpoint the server opened."""

import asyncio
import json
import sys
import time
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from cdp_use import CDPClient

from atlas.server.browser_host import (
    NOT_A_PROOF,
    NOT_SHARED,
    PROOF_BYTES,
    PROOF_FOLDER,
    check_proof,
)
from fake_app_client import FakeAppClient
from fake_app_host import FakeAppHost
from fake_model import FakeCall, FakeModelServer, FakeReply
from server_support import AtlasServer, Serve

TESTS = Path(__file__).parent.parent
FAKE_ENGINE = (sys.executable, str(TESTS / "fake_engine.py"))
NONCE = "ab" * 32
CHAT = "/v1/chat/completions"


@pytest.fixture(autouse=True)
def _no_browser_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whatever this computer has installed, the only browser here is the one the app lends."""
    monkeypatch.setattr("atlas.assistant.service._installed_browser", lambda: None)


@pytest.fixture
def host() -> FakeAppHost:
    return FakeAppHost()


@pytest.fixture
def lent(
    serve: Serve, host: FakeAppHost, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[AtlasServer, FakeAppClient]]:
    monkeypatch.setenv("FAKE_ENGINE_LOG", str(tmp_path / "engine.jsonl"))
    server = serve(browser_engine=FAKE_ENGINE)
    with FakeAppClient(server, host, tmp_path / "app") as app:
        yield server, app


def _available(server: AtlasServer) -> bool:
    return server.gateway.assistant.browser.available


def _endpoint(server: AtlasServer, conversation_id: str) -> str:
    hosting = server.gateway.assistant.browser._hosting  # pyright: ignore[reportPrivateUsage]
    assert hosting is not None
    return hosting.endpoint.url_for(conversation_id)


class Engine:
    """The engine's CDP client on the endpoint the server opened."""

    def __init__(self, url: str) -> None:
        self.client = CDPClient(url)
        self.heard: list[tuple[str, Any, str | None]] = []

    async def __aenter__(self) -> "Engine":
        await self.client.start()
        for name in ("Page.loadEventFired", "Target.detachedFromTarget"):
            self.client._event_registry.register(  # pyright: ignore[reportPrivateUsage]
                name, lambda data, session, name=name: self.heard.append((name, data, session))
            )
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.client.stop()

    async def send(
        self, method: str, params: dict[str, Any] | None = None, session: str | None = None
    ) -> Any:
        return await self.client.send_raw(method, params, session)

    async def attach(self, tab: str) -> str:
        found = await self.send("Target.attachToTarget", {"targetId": tab, "flatten": True})
        return found["sessionId"]


def _cdp(url: str, work: Callable[[Engine], Awaitable[None]]) -> None:
    async def main() -> None:
        async with Engine(url) as engine:
            await work(engine)

    asyncio.run(main())


# -- the proof -----------------------------------------------------------------------------------


def _proof(folder: Path, content: str = NONCE, name: str = "0123456789abcdef" * 2) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(content, encoding="utf-8")
    return path


def test_a_proof_is_a_small_file_of_the_apps_with_the_nonce_in_it(tmp_path: Path) -> None:
    folder = tmp_path / PROOF_FOLDER
    good = _proof(folder)
    assert check_proof(str(good), NONCE) is None
    assert check_proof(str(good), "cd" * 32) == NOT_SHARED
    assert check_proof(str(_proof(folder, "cd" * 32, "f" * 32)), NONCE) == NOT_SHARED
    assert check_proof(str(folder / ("e" * 32)), NONCE) == NOT_SHARED  # no such file
    assert (
        check_proof(str(_proof(folder, NONCE + " " * PROOF_BYTES, "a" * 32)), NONCE) == NOT_SHARED
    )
    # What names it must have the shape the app gives it.
    assert check_proof(f"{PROOF_FOLDER}/{good.name}", NONCE) == NOT_A_PROOF
    assert check_proof(str(_proof(folder, NONCE, "notaproof")), NONCE) == NOT_A_PROOF
    assert check_proof(str(_proof(tmp_path / "elsewhere")), NONCE) == NOT_A_PROOF
    assert check_proof(str(folder), NONCE) == NOT_A_PROOF  # a folder
    assert check_proof(str(good), "short") == NOT_A_PROOF
    assert check_proof(str(good), NONCE.upper()) == NOT_A_PROOF


def test_a_link_to_a_file_is_not_a_proof(tmp_path: Path) -> None:
    secret = tmp_path / "somewhere"
    secret.write_text(NONCE, encoding="utf-8")
    folder = tmp_path / PROOF_FOLDER
    folder.mkdir()
    link = folder / ("b" * 32)
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("this computer does not make links")
    assert check_proof(str(link), NONCE) == NOT_SHARED


def test_the_app_is_accepted_with_a_proof_and_told_the_home(
    lent: tuple[AtlasServer, FakeAppClient],
) -> None:
    server, app = lent
    assert not _available(server)
    answer = app.offer()
    assert answer["result"] == {"accepted": True, "reason": "", "home": "default"}
    assert _available(server)
    # The app removes its proof once the server has answered.
    assert not list((app.folder / PROOF_FOLDER).iterdir())


def test_a_proof_the_server_cannot_read_is_refused_with_why(
    lent: tuple[AtlasServer, FakeAppClient],
) -> None:
    server, app = lent
    refused = [
        app.offer(written="cd" * 32),  # a file the nonce is not in
        app.offer(name="not-a-proof"),
        app.offer(size=PROOF_BYTES + 1),
        app.call("browser.host", {"proof_file": "relative/proof", "nonce": NONCE}),
        app.call(
            "browser.host",
            {"proof_file": str(app.folder / "browser-proof" / ("a" * 32)), "nonce": NONCE},
        ),
    ]
    for answer in refused:
        result = answer["result"]
        assert result["accepted"] is False, answer
        assert result["reason"] in (NOT_SHARED, NOT_A_PROOF)
        assert NONCE not in json.dumps(answer)
        assert str(app.folder) not in json.dumps(answer)
    assert not _available(server)


# -- what is asked of the host -------------------------------------------------------------------


def test_the_hosts_requests_reach_its_connection_and_no_other(
    lent: tuple[AtlasServer, FakeAppClient], host: FakeAppHost
) -> None:
    server, app = lent
    tab = host.add("c-1", "https://user.example", "The user's page", selected=True)
    assert app.offer()["result"]["accepted"]
    with server.client() as other:
        conversation = other.result("conversation.create")["conversation_id"]
        other.result("conversation.open", {"conversation_id": conversation})

        async def work(engine: Engine) -> None:
            listed = await engine.send("Target.getTargets")
            assert [i["targetId"] for i in listed["targetInfos"]] == [tab]
            made = await engine.send("Target.createTarget", {"url": "about:blank"})
            session = await engine.attach(tab)
            host.results["Runtime.evaluate"] = {"result": {"value": 3}}
            result = await engine.send("Runtime.evaluate", {"expression": "1+2"}, session)
            assert result == {"result": {"value": 3}}
            await engine.send("Target.activateTarget", {"targetId": tab})
            await engine.send("Target.closeTarget", {"targetId": made["targetId"]})
            await engine.send("Target.detachFromTarget", {"sessionId": session})

        _cdp(_endpoint(server, "c-1"), work)
        methods = [request["method"] for request in app.requests]
        assert methods == [
            "browser.tabs",
            "browser.open",
            "browser.tabs",
            "browser.attach",
            "browser.cdp",
            "browser.tabs",
            "browser.show",
            "browser.tabs",
            "browser.close",
            "browser.detach",
        ]
        assert all(r["id"].startswith("srq-") for r in app.requests)
        # Another client of the server, on a conversation, is asked nothing and told nothing of
        # it: no request, and no `request.cancel` for one that ended.
        with pytest.raises(TimeoutError):
            other.next_frame(
                lambda f: (
                    str(f.get("method", "")).startswith("browser.")
                    or (f.get("params") or {}).get("type") == "request.cancel"
                ),
                timeout=0.5,
            )
    assert app.other == [] or all(f.get("method") == "event" for f in app.other)
    assert not [f for f in app.other if (f.get("params") or {}).get("type") == "request.cancel"]


def test_an_answer_from_another_connection_is_not_the_hosts(
    lent: tuple[AtlasServer, FakeAppClient], host: FakeAppHost
) -> None:
    server, app = lent
    host.add("c-1", "https://real.example", "Real")
    assert app.offer()["result"]["accepted"]
    app.held.add("browser.tabs")

    async def work(engine: Engine) -> None:
        asking = asyncio.create_task(engine.send("Target.getTargets"))
        (request,) = await asyncio.to_thread(app.wait_for, "browser.tabs")
        fake = {"tabs": [], "user_agent": "x", "product": "y"}
        with server.client() as impostor:
            impostor.answer(request["id"], fake)
            time.sleep(0.3)
        assert not asking.done()  # an answer from anyone else is ignored
        real = {
            "tabs": [{"id": "t", "url": "https://real.example", "title": "Real", "owner": "c-1",
                      "loading": False, "selected": False}],
            "user_agent": "agent",
            "product": "Chrome/1",
        }  # fmt: skip
        app.answer(request["id"], real)
        found = await asyncio.wait_for(asking, 10)
        assert [i["title"] for i in found["targetInfos"]] == ["Real"]

    _cdp(_endpoint(server, "c-1"), work)


def test_what_the_host_says_is_what_the_engine_reads(
    lent: tuple[AtlasServer, FakeAppClient], host: FakeAppHost
) -> None:
    server, app = lent
    tab = host.add("c-1", "https://a.example", "A")
    assert app.offer()["result"]["accepted"]
    host.errors["DOM.getDocument"] = "Cannot find context with specified id"

    async def work(engine: Engine) -> None:
        session = await engine.attach(tab)
        with pytest.raises(RuntimeError) as raised:
            await engine.send("DOM.getDocument", {}, session)
        assert raised.value.args[0] == {
            "code": -32000,
            "message": "Cannot find context with specified id",
        }

    _cdp(_endpoint(server, "c-1"), work)


def test_a_host_that_never_answers_is_given_up_on(
    lent: tuple[AtlasServer, FakeAppClient], host: FakeAppHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, app = lent
    monkeypatch.setattr("atlas.server.service.BROWSER_HOST_SECONDS", 0.5)
    assert app.offer()["result"]["accepted"]
    app.held.add("browser.tabs")

    async def work(engine: Engine) -> None:
        with pytest.raises(RuntimeError) as raised:
            await engine.send("Target.getTargets")
        assert "did not answer browser.tabs" in raised.value.args[0]["message"]

    _cdp(_endpoint(server, "c-1"), work)
    assert _available(server)


def test_a_page_event_the_app_tells_reaches_the_engine_and_only_the_hosts_counts(
    lent: tuple[AtlasServer, FakeAppClient], host: FakeAppHost
) -> None:
    server, app = lent
    tab = host.add("c-1", "https://a.example", "A")
    assert app.offer()["result"]["accepted"]

    async def work(engine: Engine) -> None:
        session = await engine.attach(tab)
        with server.client() as impostor:
            impostor.send(
                {
                    "jsonrpc": "2.0",
                    "method": "browser.event",
                    "params": {"tab": tab, "method": "Page.loadEventFired", "params": {"n": 0}},
                }
            )
            time.sleep(0.3)
        assert engine.heard == []  # not from the host's connection
        app.notify(
            "browser.event", {"tab": tab, "method": "Page.loadEventFired", "params": {"n": 1}}
        )
        for _ in range(100):
            if engine.heard:
                break
            await asyncio.sleep(0.05)
        assert engine.heard == [("Page.loadEventFired", {"n": 1}, session)]

    _cdp(_endpoint(server, "c-1"), work)
    # A notification has no id, so nothing answers it, not even with an error.
    assert not [f for f in app.other if "id" in f]


# -- the host changes or goes --------------------------------------------------------------------


def test_a_new_host_replaces_the_old_one(
    lent: tuple[AtlasServer, FakeAppClient], host: FakeAppHost, tmp_path: Path
) -> None:
    server, first = lent
    host.add("c-1", "https://old.example", "Old")
    assert first.offer()["result"]["accepted"]
    old_url = _endpoint(server, "c-1")
    newer = FakeAppHost()
    newer.add("c-1", "https://new.example", "New")
    with FakeAppClient(server, newer, tmp_path / "newer") as second:
        assert second.offer()["result"]["accepted"]

        async def work(engine: Engine) -> None:
            found = await engine.send("Target.getTargets")
            assert [i["title"] for i in found["targetInfos"]] == ["New"]

        _cdp(_endpoint(server, "c-1"), work)
        assert [r["method"] for r in first.requests] == []
        assert [r["method"] for r in second.requests] == ["browser.tabs"]

        async def old(engine: Engine) -> None:  # pragma: no cover - the path is gone
            await engine.send("Target.getTargets")

        with pytest.raises(OSError, match=r".+"):
            _cdp(old_url, old)


def test_a_host_that_hangs_up_takes_the_browser_with_it(
    lent: tuple[AtlasServer, FakeAppClient], host: FakeAppHost
) -> None:
    server, app = lent
    host.add("c-1", "https://a.example", "A")
    assert app.offer()["result"]["accepted"]
    app.held.add("browser.tabs")
    assert _available(server)

    async def work(engine: Engine) -> None:
        asking = asyncio.create_task(engine.send("Target.getTargets"))
        await asyncio.to_thread(app.wait_for, "browser.tabs")
        await asyncio.to_thread(app.hang_up)
        with pytest.raises((RuntimeError, ConnectionError)) as raised:
            await asyncio.wait_for(asking, 10)
        assert "app is gone" in str(raised.value) or "closed" in str(raised.value)

    _cdp(_endpoint(server, "c-1"), work)
    for _ in range(100):
        if not _available(server):
            break
        time.sleep(0.05)
    assert not _available(server)


# -- from the model to the engine ----------------------------------------------------------------


def test_the_model_drives_the_app_browser_through_the_endpoint(
    lent: tuple[AtlasServer, FakeAppClient], fake: FakeModelServer, tmp_path: Path
) -> None:
    server, app = lent
    assert app.offer()["result"]["accepted"]
    code = "# Reading the page\nnew_tab('https://example.com')\nprint(page_info()['title'])"
    fake.reply(
        FakeReply(calls=[FakeCall("tool_search", json.dumps({"query": "browser web page"}))]),
        FakeReply(calls=[FakeCall("browser_exec", json.dumps({"code": code}))]),
        FakeReply(text="The page is Example."),
    )
    with server.client() as client:
        conversation = client.result("conversation.create")["conversation_id"]
        client.result("conversation.send", {"conversation_id": conversation, "text": "Open it"})
        done = client.event("message.complete", 30)
        assert done["payload"]["status"] == "answered"
        opened = client.result("conversation.open", {"conversation_id": conversation})
    results = [m for m in opened["messages"] if m["role"] == "tool"]
    assert any("Page at https://example.com" in json.dumps(m) for m in results)
    first = fake.bodies(CHAT)[0]
    search = next(t for t in first["tools"] if t["function"]["name"] == "tool_search")
    assert "browser_exec" in search["function"]["description"]
    runs = [
        json.loads(line)
        for line in (tmp_path / "engine.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    ran = next(run for run in runs if "code" in run)
    assert ran["variables"]["BU_NAME"].startswith("atlas-app-")
    assert ran["variables"]["BU_CDP_WS"].startswith("ws://127.0.0.1:")
    assert ran["code"] == code


def test_without_the_app_there_is_no_browser_exec(
    serve: Serve, fake: FakeModelServer, tmp_path: Path
) -> None:
    fake.reply(FakeReply(text="Hi."))
    server = serve(browser_engine=FAKE_ENGINE)
    with server.client() as client:
        conversation = client.result("conversation.create")["conversation_id"]
        client.result("conversation.send", {"conversation_id": conversation, "text": "Hello"})
        client.event("message.complete", 30)
    first = fake.bodies(CHAT)[0]
    search = next((t for t in first["tools"] if t["function"]["name"] == "tool_search"), None)
    assert search is None or "browser_exec" not in search["function"]["description"]
