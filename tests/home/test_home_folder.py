"""The home folder: created private, and every entry the code writes has a declared class."""

import subprocess
import sys
from pathlib import Path

import pytest

from atlas.home import (
    LAYOUT,
    EntryClass,
    HomeFolderError,
    classify,
    is_private,
    open_home,
    private_folder,
    unclassified,
)


def test_a_missing_home_is_created_with_its_parents_and_only_the_owner_can_open_it(
    tmp_path: Path,
) -> None:
    home = open_home(tmp_path / "nested" / "home")

    assert home.root.is_dir()
    assert is_private(home.root), _icacls(home.root)


def test_an_existing_home_is_opened_as_it_is(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("", encoding="utf-8")

    home = open_home(tmp_path)

    assert home.config_file.read_text(encoding="utf-8") == ""


def test_a_file_where_the_home_should_be_is_refused(tmp_path: Path) -> None:
    (tmp_path / "home").write_text("not a folder", encoding="utf-8")

    with pytest.raises(HomeFolderError, match="must be a folder"):
        open_home(tmp_path / "home")


def test_a_home_under_a_file_is_refused_with_the_reason(tmp_path: Path) -> None:
    (tmp_path / "taken").write_text("a file, not a folder", encoding="utf-8")

    with pytest.raises(HomeFolderError, match="Could not create the home folder"):
        open_home(tmp_path / "taken" / "home")


def test_a_file_where_a_private_folder_should_be_is_refused(tmp_path: Path) -> None:
    (tmp_path / "secrets").write_text("a file", encoding="utf-8")

    with pytest.raises(HomeFolderError, match="must be a folder"):
        private_folder(tmp_path / "secrets")


def test_a_private_folder_under_a_file_is_refused(tmp_path: Path) -> None:
    (tmp_path / "taken").write_text("a file", encoding="utf-8")

    with pytest.raises(HomeFolderError, match="Could not create"):
        private_folder(tmp_path / "taken" / "secrets")


def test_a_private_folder_is_closed_before_anything_is_written_in_it(tmp_path: Path) -> None:
    folder = private_folder(tmp_path / "secrets")
    (folder / "key.txt").write_text("x", encoding="utf-8")

    assert is_private(folder)
    if sys.platform == "win32":
        # The file inherits the folder's single entry.
        assert is_private(folder / "key.txt")
    else:
        # The file gets the umask's mode, but nobody else can enter the folder to reach it.
        assert folder.stat().st_mode & 0o777 == 0o700


def test_an_existing_private_folder_is_accepted_again(tmp_path: Path) -> None:
    folder = private_folder(tmp_path / "secrets")
    (folder / "vault.json").write_text("{}", encoding="utf-8")

    assert private_folder(folder) == folder


def test_an_existing_folder_others_can_open_is_refused(tmp_path: Path) -> None:
    open_folder = tmp_path / "secrets"
    open_folder.mkdir()
    if sys.platform != "win32":
        open_folder.chmod(0o755)
    # On Windows a temporary folder inherits SYSTEM and Administrators besides the owner.

    with pytest.raises(HomeFolderError, match="can be opened by other accounts"):
        private_folder(open_folder)


def _icacls(path: Path) -> str:
    """What Windows says of a path's access list, for a failure's message."""
    if sys.platform != "win32":
        return oct(path.stat().st_mode)
    return subprocess.run(  # noqa: S603 - a fixed system tool
        ["icacls", str(path)],  # noqa: S607
        capture_output=True,
        text=True,
        encoding="oem",
        check=False,
    ).stdout


# -- the boundary ---------------------------------------------------------------------------------


def test_the_vault_never_travels_and_the_database_does() -> None:
    assert classify("secrets", is_folder=True) is not None
    assert classify("atlas.db", is_folder=False) is not None
    assert {entry.name: entry.entry_class for entry in LAYOUT} == {
        "config.toml": EntryClass.USER,
        "models.toml": EntryClass.USER,
        "atlas.db": EntryClass.USER,
        "atlas.db-wal": EntryClass.USER,
        "atlas.db-shm": EntryClass.USER,
        "secrets": EntryClass.MACHINE,
        "logs": EntryClass.DIAGNOSTIC,
        "skills": EntryClass.USER,
        "memory": EntryClass.USER,
        "SOUL.md": EntryClass.USER,
        "picture.png": EntryClass.USER,
        "plugins": EntryClass.USER,
        "plugin-data": EntryClass.USER,
        "marketplace": EntryClass.USER,
        "bots": EntryClass.USER,
        "attachments": EntryClass.USER,
        "documents": EntryClass.MACHINE,
        "mcp.json": EntryClass.USER,
        "spill": EntryClass.MACHINE,
        "commands": EntryClass.MACHINE,
        "home": EntryClass.USER,
        "homes": EntryClass.HOMES,
        "active_home": EntryClass.MACHINE,
        "backups": EntryClass.MACHINE,
        "server.lock": EntryClass.MACHINE,
        "always_on.json": EntryClass.MACHINE,
        "browser": EntryClass.MACHINE,
        "speech": EntryClass.MACHINE,
        "dictations": EntryClass.USER,
        "worktrees": EntryClass.MACHINE,
    }


def test_a_name_with_the_wrong_kind_is_not_declared() -> None:
    assert classify("secrets", is_folder=False) is None
    assert classify("atlas.db", is_folder=True) is None


def test_undeclared_entries_are_listed_folders_with_a_slash() -> None:
    found = unclassified({"atlas.db": False, "scratch": True, "notes.txt": False})

    assert found == ["notes.txt", "scratch/"]


def test_every_entry_the_code_writes_at_the_top_of_the_home_is_declared(tmp_path: Path) -> None:
    home = open_home(tmp_path / "home")
    private_folder(home.secrets_folder)
    home.config_file.write_text("", encoding="utf-8")
    home.models_file.write_text("", encoding="utf-8")

    written = {entry.name: entry.is_dir() for entry in home.root.iterdir()}

    assert unclassified(written) == []
