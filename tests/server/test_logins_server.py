"""The first run's Connections over the real server (step 18d): `login.list` reads `gh` and
`gws`, `login.github` gives `gh`'s code and `logins.changed` tells when the browser said yes;
only the owner's app may ask. The programs are stand-ins (`tests/fake_logins.py`)."""

import asyncio
from pathlib import Path

import pytest

from atlas.logins import ProgramLogins
from atlas.server import Connection, Gateway, ProtocolError
from atlas.server.protocol import FORBIDDEN
from fake_logins import CODE, programs
from server_support import Serve


def test_the_logins_are_read_and_githubs_is_started_from_the_app(
    serve: Serve, tmp_path: Path
) -> None:
    state = tmp_path / "state"
    logins = ProgramLogins(environment=programs(tmp_path / "bin", state))
    (state / "gws-login").write_text("ana@example.com", "utf-8")
    server = serve(logins=logins)
    with server.client() as client:
        assert client.result("login.list")["logins"] == [
            {
                "service": "github",
                "program": "gh",
                "installed": True,
                "logged_in": False,
                "account": "",
            },
            {
                "service": "google",
                "program": "gws",
                "installed": True,
                "logged_in": True,
                "account": "ana@example.com",
            },
        ]
        assert client.result("login.github") == {
            "code": CODE,
            "url": "https://github.com/login/device",
        }
        (state / "gh-approve").write_text("example-user", "utf-8")
        client.event("logins.changed")
        github = client.result("login.list")["logins"][0]
        assert (github["logged_in"], github["account"]) == (True, "example-user")


def test_a_login_that_cannot_start_says_why(serve: Serve, tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    server = serve(logins=ProgramLogins(environment={"PATH": str(tmp_path / "empty")}))
    with server.client() as client:
        refused = client.call("login.github")
        assert "gh, is not on this machine" in refused["error"]["message"]
        rows = client.result("login.list")["logins"]
        assert [row["installed"] for row in rows] == [False, False]


def test_only_the_owners_app_reads_or_starts_a_login(serve: Serve) -> None:
    gateway: Gateway = serve().gateway

    class NotTheOwner(Connection):
        pass

    async def call(method: str) -> object:
        return await gateway._call(  # pyright: ignore[reportPrivateUsage]
            NotTheOwner(peer=None),  # pyright: ignore[reportArgumentType]
            {"method": method, "params": {}},
        )

    for method in ("login.list", "login.github"):
        with pytest.raises(ProtocolError) as refused:
            asyncio.run(call(method))
        assert refused.value.code == FORBIDDEN
