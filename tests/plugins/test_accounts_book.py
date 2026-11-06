"""The accounts plugin's file (step 22): one JSON file in its data folder, read by two loads of
the plugin at once (its screen's calls run in one, what Atlas asks in another)."""

import json
from pathlib import Path

import pytest

from accounts_support import accounts_module


def test_a_change_one_load_writes_is_read_by_the_other(tmp_path: Path) -> None:
    book = accounts_module("book")
    path = tmp_path / "accounts.json"
    calls, atlas = book.Book(path), book.Book(path)
    assert atlas.accounts("claude-code") == []

    added = calls.add("claude-code", "company-b", str(tmp_path / ".claude-company-b"))
    calls.set_folder("claude-code", str(tmp_path / "Site"), added.id)

    assert [a.nickname for a in atlas.accounts("claude-code")] == ["company-b"]
    assert atlas.folder_account("claude-code", tmp_path / "site") in ("", added.id)
    assert atlas.folder_account("claude-code", str(tmp_path / "Site")) == added.id
    with pytest.raises(book.BookError, match="named 'Company-B' already"):
        atlas.add("claude-code", "Company-B", str(tmp_path / ".claude-x"))
    with pytest.raises(book.BookError, match="nickname of letters"):
        atlas.add("claude-code", "a b", str(tmp_path / ".claude-y"))
    assert atlas.remove("claude-code", added.id)
    assert calls.folders("claude-code") == {}, "the folders that started on it start on the main"


def test_a_file_that_cannot_be_read_is_never_written_over(tmp_path: Path) -> None:
    book = accounts_module("book")
    path = tmp_path / "accounts.json"
    path.write_text("{ half written", encoding="utf-8")

    with pytest.raises(book.BookError, match="cannot be read"):
        book.Book(path).add("claude-code", "work", str(tmp_path / ".claude-work"))

    assert path.read_text(encoding="utf-8") == "{ half written"


def test_what_atlas_kept_is_adopted_once_and_left_where_it_was(tmp_path: Path) -> None:
    book = accounts_module("book")
    source = tmp_path / "from-atlas.json"
    kept = {"version": 1, "executors": {"codex": {"accounts": [{"id": "ab12", "nickname": "w"}]}}}
    source.write_text(json.dumps(kept), encoding="utf-8")
    first = book.Book(tmp_path / "accounts.json")

    assert first.adopt(source)
    first.remove("codex", "ab12")
    assert not book.Book(tmp_path / "accounts.json").adopt(source), "once: its own file is there"
    assert book.Book(tmp_path / "accounts.json").accounts("codex") == []
    assert source.exists()
