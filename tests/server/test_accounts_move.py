"""Step 22 on a home that used other accounts before it: the update moves what Atlas kept of them
to the accounts plugin, turned on, and touches no account's folder (decided with the user on
03/11/2026: the plugin is what they used)."""

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from atlas.home import Database, Home
from atlas.jobs import AccountFailover, AccountStore, RouteTarget, SessionRoute
from atlas.plugins import Plugins
from atlas.secrets import Vault
from claude_code_support import claude_code
from server_support import WAIT, Client, Serve


def _until_listed(client: Client, wanted: int) -> list[dict[str, object]]:
    deadline = time.monotonic() + WAIT
    while True:
        listed = client.result("executor.accounts", {"executor": "claude-code"})["accounts"]
        if len(listed) >= wanted:
            return listed
        assert time.monotonic() < deadline, "the accounts never came back"
        time.sleep(0.05)


def test_an_update_moves_the_accounts_atlas_kept_to_the_plugin_turned_on(
    serve: Serve,
    home: Home,
    vault: Vault,
    tmp_path: Path,
    work: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", str(tmp_path / "auth.json"))
    store = AccountStore(Database(home.database_file))
    folder = tmp_path / ".claude-company-b"
    folder.mkdir()
    (folder / "settings.json").write_text("{}", encoding="utf-8")
    kept = store.add("claude-code", "company-b", str(folder), datetime(2026, 10, 20, tzinfo=UTC))
    store.set_failover(AccountFailover("claude-code", True, False, (kept.id, "")))
    store.set_folder_account("claude-code", str(work), kept.id)
    stop = RouteTarget("claude-code", kept.id, model="opus")
    store.set_route(SessionRoute("c-1", (stop, RouteTarget("codex"))))

    with serve(executors={"claude-code": claude_code()}).client() as client:
        listed = _until_listed(client, 2)
        accounts = client.result("executor.accounts", {"executor": "claude-code"})
        [plugin] = [p for p in client.result("plugin.list")["plugins"] if p["name"] == "accounts"]

    assert plugin["enabled"], "turned on, as the update's own doing"
    assert [(a["account_id"], a["nickname"]) for a in listed] == [
        (kept.id, "company-b"),
        ("", ""),
    ], "in the order the user set"
    assert (accounts["failover"], accounts["compact"]) == (True, False)
    data = Plugins(home, Database(home.database_file), vault).data_folder("accounts")
    book = json.loads((data / "accounts.json").read_text(encoding="utf-8"))
    assert book["executors"]["claude-code"]["folders"] == {str(work): kept.id}
    assert book["routes"]["c-1"]["stops"] == [
        {"executor": "claude-code", "account": kept.id, "model": "opus"},
        {"executor": "codex", "account": ""},
    ]
    assert (data / "from-atlas.json").exists(), "what was moved, left for the user to look at"
    assert [p.name for p in folder.iterdir()] == ["settings.json"], "its folder untouched"


def test_a_home_that_never_added_an_account_gets_no_plugin(
    serve: Serve, home: Home, vault: Vault
) -> None:
    with serve(executors={"claude-code": claude_code()}).client() as client:
        listed = client.result("executor.accounts", {"executor": "claude-code"})
        [plugin] = [p for p in client.result("plugin.list")["plugins"] if p["name"] == "accounts"]

    assert [a["account_id"] for a in listed["accounts"]] == [""]
    assert not plugin["enabled"]
    data = Plugins(home, Database(home.database_file), vault).data_folder("accounts")
    assert not (data / "from-atlas.json").exists()
