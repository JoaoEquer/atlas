"""The GitHub and Google Workspace plugins Atlas makes (step 26; the core's `atlas.logins` since
step 18d until then): what `gh`'s and `gws`'s `auth status` say, read, and `gh`'s login in the
browser started, its code given and its end seen, on stand-ins for both programs
(`tests/fake_logins.py`) found on the PATH as the real ones are."""

import asyncio
import hashlib
import json
import os
import tarfile
import zipfile
from pathlib import Path

import pytest

from fake_logins import CODE, programs
from official_support import official_module

github = official_module("github")
github_programs = official_module("github", "programs")
google = official_module("google")
github_fetch = official_module("github", "fetch")

#: A data folder where the plugin never fetched gh: the PATH's is used.
NO_FETCHED = Path("no-such-folder")


@pytest.fixture
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The stand-ins on the PATH, their state in the folder returned."""
    folder = tmp_path / "state"
    for name, value in programs(tmp_path / "bin", folder).items():
        monkeypatch.setenv(name, value)
    return folder


def test_gh_is_logged_in_when_its_active_account_on_github_com_succeeded() -> None:
    parse = github_programs.parse_status

    def hosts(*accounts: dict[str, object]) -> str:
        return json.dumps({"hosts": {"github.com": list(accounts)}})

    good = {"state": "success", "active": True, "login": "example-user"}
    assert parse(hosts(good)) == "example-user"
    assert parse(hosts({**good, "active": False}, {**good, "login": "b"})) == "b"
    assert parse(hosts({**good, "state": "error"})) is None
    assert parse(json.dumps({"hosts": {}})) is None
    assert parse("not json") is None
    assert parse("[]") is None


def test_gws_is_logged_in_only_with_a_token_google_takes() -> None:
    parse = google.parse_status
    said = 'Using keyring backend: keyring\n{"token_valid": true, "user": "ana@example.com"}'
    assert parse(said) == (True, "ana@example.com")
    assert parse('{"token_valid": true}') == (True, "")
    revoked = '{"token_valid": false, "token_error": "Token has been expired or revoked."}'
    assert parse(revoked) == (False, "")
    assert parse("error: no credentials") == (False, "")


def test_the_code_and_its_page_are_read_from_what_gh_printed() -> None:
    printed = (
        "\n! One-time code (B01A-C2FA) copied to clipboard\n"
        "Open this URL to continue in your web browser: https://github.com/login/device\n"
    )
    found = github_programs.parse_device_code(printed)
    assert (found.code, found.url) == ("B01A-C2FA", "https://github.com/login/device")
    assert github_programs.parse_device_code(printed[:40]) is None


def test_each_status_is_read_from_its_program(state: Path) -> None:
    assert asyncio.run(google.status({})) == {"installed": True, "logged_in": False, "account": ""}
    (state / "gh-login").write_text("example-user", "utf-8")
    (state / "gws-login").write_text("ana@example.com", "utf-8")

    said = asyncio.run(github.Github(NO_FETCHED).status({}))

    assert (said["installed"], said["logged_in"], said["account"]) == (True, True, "example-user")
    assert asyncio.run(google.status({}))["account"] == "ana@example.com"


def test_a_program_not_on_the_path_is_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "empty").mkdir()
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))

    assert asyncio.run(github.Github(NO_FETCHED).status({}))["installed"] is False
    assert asyncio.run(google.status({}))["installed"] is False

    def offline(folder: Path) -> Path:
        raise github_fetch.FetchError("gh could not be downloaded from github.com: offline")

    monkeypatch.setattr(github, "fetch", offline)
    with pytest.raises(github_programs.LoginError, match="could not be downloaded"):
        asyncio.run(github.Github(NO_FETCHED).log_in({}))


def test_githubs_login_gives_its_code_and_its_end_is_read_once_the_browser_said_yes(
    state: Path,
) -> None:
    async def scenario() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        plugin = github.Github(NO_FETCHED)
        code = await plugin.log_in({})
        again = await plugin.log_in({})
        waiting = await plugin.status({})
        (state / "gh-approve").write_text("example-user", "utf-8")
        read: dict[str, object] = {}
        for _ in range(200):
            read = await plugin.status({})
            if read["logged_in"]:
                break
            await asyncio.sleep(0.05)
        await plugin.close()
        return code, again, {**waiting, "after": read}

    code, again, seen = asyncio.run(scenario())

    assert code == {"code": CODE, "url": "https://github.com/login/device"}
    assert again == code, "one login at a time, its code again"
    assert seen["waiting"] is True
    after = seen["after"]
    assert isinstance(after, dict)
    assert (after["logged_in"], after["account"]) == (True, "example-user")


def test_a_login_refused_in_the_browser_ends_and_a_new_one_starts(state: Path) -> None:
    async def scenario() -> dict[str, str]:
        plugin = github.Github(NO_FETCHED)
        await plugin.log_in({})
        (state / "gh-refuse").write_text("", "utf-8")
        for _ in range(200):
            if not (await plugin.status({}))["waiting"]:
                break
            await asyncio.sleep(0.05)
        (state / "gh-refuse").unlink()
        started = await plugin.log_in({})
        await plugin.close()
        return started

    assert asyncio.run(scenario())["code"] == CODE
    assert not (state / "gh-login").exists()


def test_a_workspaces_pull_request_is_opened_and_read_with_its_checks(
    state: Path, tmp_path: Path
) -> None:
    """Step 21, A4: the pull request of a workspace's branch, none at first, then opened into the
    branch it came from with the user's title, its checks read as they stand."""
    (state / "gh-login").write_text("example-user", "utf-8")
    checks = [
        {"name": "test", "conclusion": "SUCCESS", "status": "COMPLETED"},
        {"name": "lint", "conclusion": "", "status": "IN_PROGRESS"},
    ]
    (state / "checks.json").write_text(json.dumps(checks), "utf-8")
    plugin = github.Github(NO_FETCHED)
    where = {"folder": str(tmp_path), "branch": "atlas/cart"}

    none = asyncio.run(plugin.pull_request(where))
    made = asyncio.run(
        plugin.open_pull_request({**where, "base": "main", "title": "Add the cart", "draft": True})
    )

    assert none == {"exists": False}
    assert (made["number"], made["state"], made["title"], made["draft"]) == (
        7,
        "open",
        "Add the cart",
        True,
    )
    assert made["checks"] == [
        {"name": "test", "state": "success"},
        {"name": "lint", "state": "in_progress"},
    ]
    created = json.loads((state / "pr-created.json").read_text("utf-8"))
    assert created[created.index("--base") + 1] == "main"
    with pytest.raises(github_programs.GhError, match="needs branch"):
        asyncio.run(plugin.pull_request({"folder": str(tmp_path)}))


def test_a_projects_open_issues_are_listed(state: Path, tmp_path: Path) -> None:
    """Step 21, A5: each issue that may become a workspace."""
    issues = [
        {"number": 12, "title": "The cart rounds wrong", "body": "1.005 shows 1.00.", "url": "u"},
        "not an issue",
    ]
    (state / "issues.json").write_text(json.dumps(issues), "utf-8")

    listed = asyncio.run(github.Github(NO_FETCHED).issues({"folder": str(tmp_path)}))

    assert listed == [
        {"number": 12, "title": "The cart rounds wrong", "body": "1.005 shows 1.00.", "url": "u"}
    ]


# -- gh fetched when the computer has none (05/11/2026) --------------------------------------------


def _build(tmp_path: Path, archive: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """This computer's build of gh, a local archive of a stand-in, pinned by its digest."""
    inside = "gh_test/bin/gh"
    source = tmp_path / "gh-source"
    source.write_text("#!/bin/sh\necho gh\n", "utf-8")
    packed = tmp_path / f"gh.{archive}"
    if archive == "zip":
        with zipfile.ZipFile(packed, "w") as opened:
            opened.write(source, inside)
            opened.writestr("../escape", "outside")
    else:
        with tarfile.open(packed, "w:gz") as opened:
            opened.add(source, inside)
    digest = hashlib.sha256(packed.read_bytes()).hexdigest()
    build = github_fetch.Build(packed.as_uri(), digest, inside)
    monkeypatch.setattr(github_fetch, "build", lambda: build)


@pytest.mark.parametrize("archive", ["zip", "tar.gz"])
def test_gh_is_fetched_checked_and_only_its_program_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, archive: str
) -> None:
    _build(tmp_path, archive, monkeypatch)
    data = tmp_path / "data"

    program = github_fetch.fetch(data)

    assert program == data / f"gh-{github_fetch.VERSION}" / "gh"
    assert program.read_text("utf-8").startswith("#!/bin/sh")
    assert github_fetch.fetched(data) == program
    assert sorted(p.name for p in data.rglob("*")) == ["gh", f"gh-{github_fetch.VERSION}"]
    assert github_fetch.fetch(data) == program, "fetched once"


def test_a_download_whose_digest_differs_is_refused_and_nothing_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _build(tmp_path, "zip", monkeypatch)
    pinned = github_fetch.build()
    assert pinned is not None
    wrong = github_fetch.Build(pinned.url, "0" * 64, pinned.inside)
    monkeypatch.setattr(github_fetch, "build", lambda: wrong)

    with pytest.raises(github_fetch.FetchError, match="digest differs"):
        github_fetch.fetch(tmp_path / "data")
    assert not (tmp_path / "data").exists()


def test_connect_fetches_gh_and_the_plugin_uses_it_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: Path
) -> None:
    """With no gh on the computer, Connect fetches it, and then runs the one it fetched."""
    data = tmp_path / "data"
    found_on_path = github_programs.program("gh", {"PATH": os.environ["PATH"]})
    assert found_on_path is not None
    fetched_to: list[Path] = []

    def fake_fetch(folder: Path) -> Path:
        fetched_to.append(folder)
        return Path(found_on_path[-1])

    monkeypatch.setattr(github, "fetch", fake_fetch)
    monkeypatch.setattr(github.Github, "_program", lambda self: None)

    async def scenario() -> dict[str, str]:
        plugin = github.Github(data)
        started = await plugin.log_in({})
        await plugin.close()
        return started

    assert asyncio.run(scenario())["code"] == CODE
    assert fetched_to == [data]
