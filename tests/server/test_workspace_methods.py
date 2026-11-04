"""Workspaces through the server (step 21, A1): made from a folder of a project, its setup script
run after with every client told, archived, brought back and deleted; the session environment's
new worktree (step 18g) is a workspace in the home now, never beside the project."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from atlas.home import Home
from claude_code_support import claude_code
from server_support import Client, Serve

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _repository(folder: Path) -> Path:
    folder.mkdir(parents=True)
    identity = {
        "GIT_AUTHOR_NAME": "Ana",
        "GIT_AUTHOR_EMAIL": "ana@example.com",
        "GIT_COMMITTER_NAME": "Ana",
        "GIT_COMMITTER_EMAIL": "ana@example.com",
    }
    (folder / "README.md").write_text("Hello.\n", encoding="utf-8")
    for arguments in (
        ("init", "--quiet", "--initial-branch", "main"),
        ("add", "--all"),
        ("commit", "--quiet", "--message", "Hello"),
    ):
        subprocess.run(  # noqa: S603 - the system's git, on a test's own repository
            ["git", "-C", str(folder), *arguments],  # noqa: S607
            check=True,
            capture_output=True,
            env={**os.environ, **identity},
        )
    return folder


def _settled(client: Client, workspace_id: str) -> dict[str, object]:
    """The workspace once its setup script ended."""
    while True:
        client.event("workspaces.changed")
        [row] = [
            r for r in client.result("workspace.list")["workspaces"] if r["id"] == workspace_id
        ]
        if row["state"] != "preparing":
            return row


def test_a_workspace_is_made_prepared_archived_and_deleted_through_the_server(
    serve: Serve, home: Home, tmp_path: Path
) -> None:
    site = _repository(tmp_path / "site")
    script = f'"{sys.executable}" -c "print(\'ready\')"'
    with serve().client() as client:
        project = client.result(
            "workspace.set_project",
            {"folder": str(site), "setup_script": script, "ports_start": 5000, "ports_size": 0},
        )
        made = client.result("workspace.create", {"folder": str(site), "name": "New page"})
        prepared = _settled(client, made["id"])
        listed = client.result("workspace.list", {"folder": made["folder"]})["workspaces"]
        archived = client.result("workspace.archive", {"id": made["id"]})
        back = client.result("workspace.unarchive", {"id": made["id"]})
        (Path(back["folder"]) / "page.md").write_text("Draft.\n", encoding="utf-8")
        refused = client.call("workspace.delete", {"id": made["id"]})
        client.result("workspace.delete", {"id": made["id"], "force": True})
        left = client.result("workspace.list")["workspaces"]
        outside = client.call("workspace.create", {"folder": str(tmp_path), "name": "x"})

    assert (project["setup_script"], project["ports_size"]) == (script, 10)
    assert Path(project["root"]) == site.resolve()
    assert (made["name"], made["branch"], made["base"], made["state"]) == (
        "new-page",
        "atlas/new-page",
        "main",
        "preparing",
    )
    assert Path(made["folder"]).is_relative_to(home.worktrees_folder)
    assert (prepared["state"], str(prepared["setup_output"]).strip()) == ("ready", "ready")
    assert prepared["ports"] == "5000-5009"
    assert [row["id"] for row in listed] == [made["id"]], "found from its own folder"
    assert archived["state"] == "archived"
    assert back["state"] == "ready"
    assert "work not delivered" in refused["error"]["message"]
    assert left == []
    assert "not in a git repository" in outside["error"]["message"]


def test_the_sessions_new_worktree_is_a_workspace_in_the_home(
    serve: Serve, home: Home, tmp_path: Path
) -> None:
    site = _repository(tmp_path / "site")
    with serve().client() as client:
        made = client.result("workbench.worktree", {"folder": str(site), "name": "1101-1832"})
        [row] = client.result("workspace.list")["workspaces"]

    assert Path(made["folder"]).is_relative_to(home.worktrees_folder)
    assert (made["branch"], made["worktree"]) == ("atlas/1101-1832", True)
    assert row["folder"] == made["folder"]
    assert not (site.parent / "site-1101-1832").exists(), "never beside the project again"


def test_a_workspaces_changes_are_read_line_by_line_through_the_server(
    serve: Serve, tmp_path: Path
) -> None:
    site = _repository(tmp_path / "site")
    with serve().client() as client:
        made = client.result("workspace.create", {"folder": str(site), "name": "edit"})
        (Path(made["folder"]) / "README.md").write_text("Hello, world.\n", encoding="utf-8")
        read = client.result("workspace.diff", {"id": made["id"]})
        client.result("workspace.archive", {"id": made["id"]})
        archived = client.call("workspace.diff", {"id": made["id"]})

    [readme] = read["files"]
    assert (readme["path"], readme["added"], readme["removed"]) == ("README.md", 1, 1)
    assert [(line["kind"], line["new"], line["text"]) for line in readme["lines"]] == [
        ("minus", 0, "Hello."),
        ("plus", 1, "Hello, world."),
    ]
    assert "archived" in archived["error"]["message"]


def test_a_prompt_in_a_workspace_keeps_a_checkpoint_to_go_back_to(
    serve: Serve, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "claude-script.json"
    script.write_text(json.dumps([{"result": "Done."}]), encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(script))
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", str(tmp_path / "auth.json"))
    monkeypatch.setenv(
        "FAKE_CLAUDE_PROJECTS", str(Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects")
    )
    site = _repository(tmp_path / "site")
    with serve(executors={"claude-code": claude_code()}).client() as client:
        made = client.result("workspace.create", {"folder": str(site), "name": "cart"})
        readme = Path(made["folder"]) / "README.md"
        readme.write_text("Hello, cart.\n", encoding="utf-8")
        card = client.result(
            "workbench.start", {"executor": "claude-code", "folder": made["folder"]}
        )
        prompt = {"instance_id": card["instance_id"], "text": "Add the cart.", "attachments": []}
        client.result("workbench.prompt", prompt)
        done = client.event("workbench.update", timeout=20)
        while done["payload"]["state"] != "idle":
            done = client.event("workbench.update", timeout=20)
        [kept] = client.result("workspace.checkpoints", {"id": made["id"]})["checkpoints"]
        readme.write_text("Hello, something else.\n", encoding="utf-8")
        back = client.result("workspace.restore", {"id": made["id"], "commit": kept["commit"]})
        after = client.result("workspace.checkpoints", {"id": made["id"]})["checkpoints"]

    assert kept["label"] == "Add the cart."
    assert back["state"] == "ready"
    assert readme.read_text(encoding="utf-8") == "Hello, cart.\n"
    assert [k["label"] for k in after] == [
        "Before going back to an earlier checkpoint",
        "Add the cart.",
    ]
