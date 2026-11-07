"""The homes from the app (step 10c, docs/ARCHITECTURE.md, Homes in the app): a home made, a
backup written where the save dialog said, and a backup checked and imported as a new home, over
the real server, a real socket and real files, as the desktop asks for them."""

import base64
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any

from atlas import __version__
from atlas.__main__ import SCHEMAS
from atlas.home import Home, Redactor, VaultSettings, check_backup, utc_now
from atlas.secrets import Vault
from fake_model import FakeModelServer, FakeReply
from server_support import WAIT, Client, Serve

#: JSON-RPC's invalid params: what the server answers a request it refuses.
INVALID_PARAMS = -32602


def _names(listed: dict[str, Any]) -> list[str]:
    return [row["name"] for row in listed["homes"]]


def _keys(home: Path) -> list[str]:
    return Vault(Home(home).secrets_folder, VaultSettings(), Redactor()).names()


def _refused(client: Client, method: str, params: dict[str, Any]) -> str:
    response = client.call(method, params)
    assert response["error"]["code"] == INVALID_PARAMS, response
    return response["error"]["message"]


def _threads(database: Path) -> int:
    connection = sqlite3.connect(database)
    try:
        (count,) = connection.execute("SELECT COUNT(*) FROM conversations_threads").fetchone()
        return int(count)
    finally:
        connection.close()


def _talk(client: Client, fake: FakeModelServer) -> None:
    fake.reply(FakeReply(text="Noted."))
    conversation = client.result("conversation.create")["conversation_id"]
    client.result("conversation.send", {"conversation_id": conversation, "text": "Remember this"})
    client.events_until("message.complete")


def test_a_home_is_made_empty_or_a_copy_of_another(serve: Serve, home: Home) -> None:
    (home.root / "memory").mkdir(exist_ok=True)
    (home.root / "memory" / "USER.md").write_text("Ana, designer.", encoding="utf-8")
    server = serve()
    with server.client() as client:
        made = client.result("home.create", {"name": "work", "clone_from": "default"})
        empty = client.result("home.create", {"name": "blank"})

        assert made["current"] == "default"
        assert _names(made) == ["default", "work"]
        assert _names(empty) == ["default", "blank", "work"]
        # A copy carries the keys, the profile and the settings (`--clone`); an empty one nothing.
        work = home.root / "homes" / "work"
        assert _keys(work) == _keys(home.root) != []
        assert (work / "memory" / "USER.md").read_text(encoding="utf-8") == "Ana, designer."
        assert (work / "models.toml").is_file()
        assert _keys(home.root / "homes" / "blank") == []
        assert not (home.root / "homes" / "blank" / "models.toml").exists()

        assert "already a home" in _refused(client, "home.create", {"name": "work"})
        assert "not a home name" in _refused(client, "home.create", {"name": "My Work"})
        assert "no home" in _refused(
            client, "home.create", {"name": "other", "clone_from": "nowhere"}
        )


def test_a_backup_goes_where_the_save_dialog_said(
    serve: Serve, fake: FakeModelServer, home: Home, tmp_path: Path
) -> None:
    server = serve()
    with server.client() as client:
        _talk(client, fake)
        target = tmp_path / "chosen" / "my backup.zip"

        written = client.result("home.backup", {"path": str(target)})

        assert written["path"] == str(target)
        assert written["size"] == target.stat().st_size
        manifest = check_backup(target, SCHEMAS)
        assert manifest.home == "default"
        assert written["files"] == manifest.files
        assert manifest.tables["conversations_threads"] == 1
        assert _keys(home.root) != []
        assert b"fake-key" not in target.read_bytes()

        # The dialog asked before it gave a file that exists; without that, nothing is replaced.
        assert "never writes over" in _refused(client, "home.backup", {"path": str(target)})
        again = client.result("home.backup", {"path": str(target), "replace": True})
        assert again["path"] == str(target)
        assert "not an absolute path" in _refused(client, "home.backup", {"path": "backup.zip"})


def test_a_backup_is_checked_then_imported_as_a_new_home(
    serve: Serve, fake: FakeModelServer, home: Home, tmp_path: Path
) -> None:
    server = serve()
    with server.client() as client:
        _talk(client, fake)
        backup = tmp_path / "backup.zip"
        client.result("home.backup", {"path": str(backup)})

        info = client.result("home.inspect", {"path": str(backup)})

        today = utc_now().strftime("%Y-%m-%d")
        assert info == {
            "home": "default",
            "created_at": info["created_at"],
            "atlas": __version__,
            "conversations": 1,
            # The default home always exists, so its backup is offered a dated name.
            "suggested_name": f"default-{today}",
        }
        assert info["created_at"].startswith(today)

        listed = client.result("home.import", {"path": str(backup), "name": info["suggested_name"]})

        assert listed["current"] == "default"
        assert _names(listed) == ["default", f"default-{today}"]
        made = home.root / "homes" / f"default-{today}"
        assert _threads(made / "atlas.db") == 1
        # Keys never travel in a backup: the new home opens on the key screen.
        assert _keys(made) == []
        assert _keys(home.root) != []
        assert "already a home" in _refused(
            client, "home.import", {"path": str(backup), "name": f"default-{today}"}
        )


def test_what_is_not_a_backup_is_refused_before_anything_is_made(
    serve: Serve, home: Home, tmp_path: Path
) -> None:
    other = tmp_path / "photo.zip"
    other.write_bytes(b"not a zip at all")
    server = serve()
    with server.client() as client:
        assert "not a backup" in _refused(client, "home.inspect", {"path": str(other)})
        assert "not a backup" in _refused(
            client, "home.import", {"path": str(other), "name": "restored"}
        )
        missing = str(tmp_path / "gone.zip")
        assert "not a backup" in _refused(client, "home.inspect", {"path": missing})

        assert _names(client.result("home.list")) == ["default"]
        assert not (home.root / "homes" / "restored.creating").exists()


def test_a_backup_comes_down_the_connection_in_parts(
    serve: Serve, fake: FakeModelServer, home: Home, tmp_path: Path, monkeypatch: Any
) -> None:
    """Step 15: a remote machine's home is backed up where this machine's save dialog chose, the
    zip coming down the connection in parts as attachments go up (small parts here, to see many)."""
    monkeypatch.setattr("atlas.server.service.ATTACHMENT_PART_BYTES", 4096)
    server = serve()
    with server.client() as client, server.client() as other:
        _talk(client, fake)

        made = client.result("home.backup_download")
        taken = b""
        parts = 0
        while True:
            part = client.result("home.backup_part", {"id": made["id"], "offset": len(taken)})
            taken += base64.b64decode(part["data"])
            parts += 1
            if part["done"]:
                break
            refused = other.call("home.backup_part", {"id": made["id"], "offset": 0})
            assert "gone" in refused["error"]["message"], "another connection's backup is not"

        assert parts > 1
        assert len(taken) == made["size"]
        target = tmp_path / "down.zip"
        target.write_bytes(taken)
        manifest = check_backup(target, SCHEMAS)
        assert (manifest.home, manifest.files) == ("default", made["files"])
        assert manifest.tables["conversations_threads"] == 1
        # Taken whole, it is let go.
        gone = client.call("home.backup_part", {"id": made["id"], "offset": 0})
        assert "gone" in gone["error"]["message"]

        # One left half taken goes with its connection.
        left = other.result("home.backup_download")
        other.result("home.backup_part", {"id": left["id"], "offset": 0})
    folders = list(Path(tempfile.gettempdir()).glob("atlas-backup-*"))
    deadline = time.monotonic() + WAIT
    while any(folder.exists() for folder in folders) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not any(folder.exists() for folder in folders)
