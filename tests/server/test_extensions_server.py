"""The Extensions screen's MCP servers and skills through the server: each server of the home's
`mcp.json` with how it stands, the one that signs in with OAuth with the command that signs in,
every client told when a server connects; and every skill the main agent loads, with where it came
from.
"""

import json
import time
from pathlib import Path

from atlas.home import Home
from atlas.server.protocol import INVALID_PARAMS
from atlas.skills import SkillLibrary, sync_bundled
from server_support import Client, Serve

SERVER = Path(__file__).parent.parent / "mcp_server.py"


def _listed(app: Client, name: str, state: str) -> dict[str, object]:
    """The server's row once it stands in `state`; the client connects in the background."""
    deadline = time.monotonic() + 20
    while True:
        rows = {row["name"]: row for row in app.result("mcp.list")["servers"]}
        if rows.get(name, {}).get("state") == state or time.monotonic() > deadline:
            return rows[name]
        app.event("mcp.changed")


def test_the_mcp_servers_say_how_they_stand_and_how_to_sign_in(home: Home, serve: Serve) -> None:
    home.mcp_file.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "notes": {"command": "python", "args": [str(SERVER)]},
                    # Never reached: without a sign-in it is left out before it connects.
                    "erp": {"type": "http", "url": "https://erp.example.com/mcp", "oauth": {}},
                }
            }
        ),
        encoding="utf-8",
    )
    server = serve()

    with server.client() as app:
        notes = _listed(app, "notes", "connected")
        erp = _listed(app, "erp", "left_out")
        listing = app.result("mcp.list")

    assert notes["transport"] == "stdio"
    assert notes["plugin"] is False
    assert isinstance(notes["tools"], int)
    assert notes["tools"] > 0
    assert notes["login"] is None
    assert erp["login"] == "atlas mcp login erp"
    assert "atlas mcp login erp" in str(erp["problem"])
    assert erp["tools"] == 0
    assert listing["problem"] is None


def test_the_sign_in_button_starts_only_for_a_server_that_signs_in(
    home: Home, serve: Serve
) -> None:
    home.mcp_file.write_text(
        json.dumps({"mcpServers": {"notes": {"command": "python", "args": [str(SERVER)]}}}),
        encoding="utf-8",
    )
    server = serve()

    with server.client() as app:
        _listed(app, "notes", "connected")
        stdio = app.call("mcp.sign_in", {"name": "notes"})
        unknown = app.call("mcp.sign_in", {"name": "crm"})

    assert stdio["error"]["code"] == INVALID_PARAMS
    assert "does not sign in" in stdio["error"]["message"]
    assert unknown["error"]["code"] == INVALID_PARAMS
    assert "No MCP server is named crm." in unknown["error"]["message"]


def test_a_broken_mcp_file_is_said(home: Home, serve: Serve) -> None:
    home.mcp_file.write_text("{", encoding="utf-8")
    server = serve()

    with server.client() as app:
        deadline = time.monotonic() + 20
        listing = app.result("mcp.list")
        while listing["problem"] is None and time.monotonic() < deadline:
            time.sleep(0.1)
            listing = app.result("mcp.list")

    assert listing["servers"] == []
    assert "is not valid JSON" in str(listing["problem"])


def test_the_skills_say_where_they_came_from(home: Home, serve: Serve) -> None:
    library = SkillLibrary(home.skills_folder)
    shipped = sync_bundled(library)
    assert "atlas" in shipped.copied
    mine = home.skills_folder / "invoices"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text(
        "---\nname: invoices\ndescription: Reads the invoices of example.com.\n---\nRead them.\n",
        encoding="utf-8",
    )
    server = serve()

    with server.client() as app:
        skills = {row["name"]: row for row in app.result("skill.list")["skills"]}

    assert skills["invoices"] == {
        "name": "invoices",
        "description": "Reads the invoices of example.com.",
        "source": "home",
        "plugin": "",
        "user_invocable": True,
    }
    assert skills["atlas"]["source"] == "atlas"
    assert list(skills) == sorted(skills)
