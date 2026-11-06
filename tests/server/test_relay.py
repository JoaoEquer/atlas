"""Reaching a home through a relay, and what the home says of itself to the host that sleeps it
(step 20): the tunnel's cipher, the connection code that carries it, and a real server reached
from an app through a relay over real WebSockets, the relay never
reading what crosses it.

The relay is tests/fake_relay.py, which does what Atlas Cloud's does; the server is a real one
(tests/server_support.py), its model the fake model server.
"""

import asyncio
import dataclasses
import json
import os
import secrets
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest

from atlas.home import BackupSettings, format_utc
from atlas.instances import (
    CodeError,
    ConnectionCode,
    RelayAddress,
    RelayRoom,
    Transport,
    TunnelError,
    decode_code,
    encode_code,
    new_key,
    relay_client,
)
from atlas.instances.tunnel import Tunnel, read_key
from fake_model import FakeModelServer
from fake_relay import FakeRelay
from server_support import TOKEN, Serve

# -- the tunnel ------------------------------------------------------------------------------------


def _pair(key: str, other: str | None = None) -> tuple[Tunnel, Tunnel]:
    app_nonce, home_nonce = secrets.token_bytes(16), secrets.token_bytes(16)
    app = Tunnel(read_key(key), app_nonce, home_nonce, "app")
    home = Tunnel(read_key(other or key), app_nonce, home_nonce, "home")
    return app, home


def test_what_one_end_seals_only_the_other_opens_in_order() -> None:
    app, home = _pair(new_key())
    first, second = app.seal(b'{"method": "routine.list"}'), app.seal(b"two")

    assert b"routine.list" not in first
    assert home.open(first) == b'{"method": "routine.list"}'
    assert home.open(second) == b"two"
    assert app.open(home.seal(b"back")) == b"back"
    with pytest.raises(TunnelError, match="did not open"):
        home.open(second)  # played again


def test_a_frame_sealed_with_another_key_or_changed_on_the_way_does_not_open() -> None:
    app, stranger = _pair(new_key(), new_key())
    with pytest.raises(TunnelError):
        stranger.open(app.seal(b"hello"))
    app, home = _pair(new_key())
    sealed = bytearray(app.seal(b"hello"))
    sealed[0] ^= 1
    with pytest.raises(TunnelError):
        home.open(bytes(sealed))


def test_a_connection_code_carries_its_relay_and_refuses_a_damaged_one() -> None:
    relay = RelayAddress("wss://relay.example.com", "room-1", new_key())
    code = ConnectionCode("work", 18791, "t" * 32, relay=relay)

    read = decode_code(encode_code(code))

    assert read.relay == relay
    assert read.transports == (Transport.SSH, Transport.RELAY)
    short = RelayAddress(relay.url, relay.room, "too-short")
    damaged = encode_code(ConnectionCode("work", 18791, "t" * 32, relay=short))
    with pytest.raises(CodeError, match="damaged"):
        decode_code(damaged)


# -- through a relay ------------------------------------------------------------------------------


@pytest.fixture
def relay() -> Iterator[FakeRelay]:
    with FakeRelay() as running:
        yield running


def _request(method: str, request_id: int = 1) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": {}})


async def _answer(client: Any, request_id: int) -> dict[str, Any]:
    while True:
        frame = json.loads(await client.recv())
        if frame.get("id") == request_id:
            return frame


async def _waiting(relay: FakeRelay, room: str) -> None:
    async with asyncio.timeout(20):
        while relay.waiting(room) == 0:
            await asyncio.sleep(0.02)


def test_an_app_reaches_the_home_through_the_relay_which_reads_nothing(
    serve: Serve, fake: FakeModelServer, relay: FakeRelay
) -> None:
    room = RelayRoom(relay.url, "room-1", new_key())
    server = serve(relay=room)
    server.gateway.assistant.routines.create("news", "every day at 9am", prompt="Read the news.")

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        await _waiting(relay, room.room)
        async with relay_client(room) as first:
            await first.send(_request("routine.list", 1))
            listed = await _answer(first, 1)
            await _waiting(relay, room.room)
            async with relay_client(room) as second:
                await second.send(_request("routine.list", 7))
                again = await _answer(second, 7)
        return listed, again

    listed, again = asyncio.run(scenario())

    assert [r["name"] for r in listed["result"]["routines"]] == ["news"]
    assert again["result"]["routines"] == listed["result"]["routines"], "a second app at once"
    carried = b"".join(relay.carried)
    assert relay.carried
    assert b"routine.list" not in carried
    assert b"news" not in carried
    assert b"Read the news" not in carried


def test_an_app_with_another_key_is_refused_and_learns_nothing(
    serve: Serve, fake: FakeModelServer, relay: FakeRelay
) -> None:
    room = RelayRoom(relay.url, "room-2", new_key())
    serve(relay=room)
    wrong = RelayRoom(relay.url, room.room, new_key())

    async def scenario() -> None:
        await _waiting(relay, room.room)
        async with relay_client(wrong):
            pass

    with pytest.raises(TunnelError, match="did not open"):
        asyncio.run(scenario())


# -- the home's activity, for a host that sleeps it (step 20) --------------------------------------


def test_the_home_says_whether_it_works_since_when_it_is_idle_and_when_to_wake_it(
    serve: Serve, fake: FakeModelServer
) -> None:
    server = serve()
    made = server.gateway.assistant.routines.create("news", "every day at 9am", prompt="News.")
    url = f"http://127.0.0.1:{server.port}/activity"

    refused = httpx2.get(url)
    wrong = httpx2.get(url, headers={"Authorization": "Bearer nope"})
    idle = httpx2.get(url, headers={"Authorization": f"Bearer {TOKEN}"}).json()
    with server.client() as client:
        connected = client.result("server.activity")

    assert (refused.status_code, wrong.status_code) == (401, 401)
    assert (idle["working"], idle["clients"]) == (False, 0)
    assert idle["idle_since"] is not None
    assert made.next_run_at is not None
    assert idle["next_wake"] == format_utc(made.next_run_at)
    assert (connected["clients"], connected["idle_since"]) == (1, None)
    assert server.gateway.back_up_when_due() is None, "no backup folder set, no backup (step 20)"


def test_the_home_backs_itself_up_when_due_into_the_folder_set(
    serve: Serve, fake: FakeModelServer, tmp_path: Path
) -> None:
    """Step 20: a home on a machine that may be lost keeps its last backups on its own."""
    folder = tmp_path / "backups"
    old = folder / "atlas-backup-default-20260101T000000Z.zip"
    folder.mkdir()
    old.write_bytes(b"an old backup")
    os.utime(old, (0, 0))
    chosen = BackupSettings(folder=folder, every_hours=24, keep=1)
    gateway = serve(adjust=lambda settings: dataclasses.replace(settings, backup=chosen)).gateway

    written = gateway.back_up_when_due()
    again = gateway.back_up_when_due()

    assert written is not None
    assert written.parent == folder
    assert again is None, "the next is due in a day"
    assert [found.name for found in folder.iterdir()] == [written.name], "only the newest kept"
