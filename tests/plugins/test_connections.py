"""The GitHub and Google Workspace plugins Atlas makes (step 26; the core's `atlas.logins` since
step 18d until then): what `gh`'s and `gws`'s `auth status` say, read, and `gh`'s login in the
browser started, its code given and its end seen, on stand-ins for both programs
(`tests/fake_logins.py`) found on the PATH as the real ones are."""

import asyncio
import json
from pathlib import Path

import pytest

from fake_logins import CODE, programs
from official_support import official_module

github = official_module("github")
github_programs = official_module("github", "programs")
google = official_module("google")


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

    said = asyncio.run(github.Github().status({}))

    assert (said["installed"], said["logged_in"], said["account"]) == (True, True, "example-user")
    assert asyncio.run(google.status({}))["account"] == "ana@example.com"


def test_a_program_not_on_the_path_is_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "empty").mkdir()
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))

    assert asyncio.run(github.Github().status({}))["installed"] is False
    assert asyncio.run(google.status({}))["installed"] is False
    with pytest.raises(github_programs.LoginError, match="gh, is not on this machine"):
        asyncio.run(github.Github().log_in({}))


def test_githubs_login_gives_its_code_and_its_end_is_read_once_the_browser_said_yes(
    state: Path,
) -> None:
    async def scenario() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        plugin = github.Github()
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
        plugin = github.Github()
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
    plugin = github.Github()
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

    listed = asyncio.run(github.Github().issues({"folder": str(tmp_path)}))

    assert listed == [
        {"number": 12, "title": "The cart rounds wrong", "body": "1.005 shows 1.00.", "url": "u"}
    ]
