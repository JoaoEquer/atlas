"""The logins of `gh` and `gws` (step 18d): what their `auth status` says read, and `gh`'s login
in the browser started, its code given, and its end told, on stand-ins for both programs
(`tests/fake_logins.py`) found on the PATH as the real ones are."""

import asyncio
import json
from pathlib import Path

import pytest

from atlas.logins import (
    DeviceCode,
    LoginsError,
    ProgramLogin,
    ProgramLogins,
    parse_device_code,
    parse_gh_status,
    parse_gws_status,
)
from fake_logins import CODE, programs


def test_gh_is_logged_in_when_its_active_account_on_github_com_succeeded() -> None:
    def hosts(*accounts: dict[str, object]) -> str:
        return json.dumps({"hosts": {"github.com": list(accounts)}})

    good = {"state": "success", "active": True, "login": "example-user"}
    assert parse_gh_status(hosts(good)) == "example-user"
    assert parse_gh_status(hosts({**good, "active": False}, {**good, "login": "b"})) == "b"
    assert parse_gh_status(hosts({**good, "state": "error"})) is None
    assert parse_gh_status(json.dumps({"hosts": {}})) is None
    assert parse_gh_status("not json") is None
    assert parse_gh_status("[]") is None


def test_gws_is_logged_in_only_with_a_token_google_takes() -> None:
    said = 'Using keyring backend: keyring\n{"token_valid": true, "user": "ana@example.com"}'
    assert parse_gws_status(said) == (True, "ana@example.com")
    assert parse_gws_status('{"token_valid": true}') == (True, "")
    revoked = '{"token_valid": false, "token_error": "Token has been expired or revoked."}'
    assert parse_gws_status(revoked) == (False, "")
    assert parse_gws_status("error: no credentials") == (False, "")


def test_the_code_and_its_page_are_read_from_what_gh_printed() -> None:
    printed = (
        "\n! One-time code (B01A-C2FA) copied to clipboard\n"
        "Open this URL to continue in your web browser: https://github.com/login/device\n"
    )
    assert parse_device_code(printed) == DeviceCode("B01A-C2FA", "https://github.com/login/device")
    assert parse_device_code(printed[:40]) is None


@pytest.fixture
def state(tmp_path: Path) -> Path:
    return tmp_path / "state"


@pytest.fixture
def logins(tmp_path: Path, state: Path) -> ProgramLogins:
    return ProgramLogins(environment=programs(tmp_path / "bin", state))


def test_the_logins_are_read_from_each_programs_status(logins: ProgramLogins, state: Path) -> None:
    assert asyncio.run(logins.read()) == [
        ProgramLogin("github", "gh", installed=True, logged_in=False),
        ProgramLogin("google", "gws", installed=True, logged_in=False),
    ]
    (state / "gh-login").write_text("example-user", "utf-8")
    (state / "gws-login").write_text("ana@example.com", "utf-8")
    assert asyncio.run(logins.read()) == [
        ProgramLogin("github", "gh", True, True, "example-user"),
        ProgramLogin("google", "gws", True, True, "ana@example.com"),
    ]


def test_a_program_not_on_the_path_is_not_installed(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    logins = ProgramLogins(environment={"PATH": str(empty)})
    assert [row.installed for row in asyncio.run(logins.read())] == [False, False]
    with pytest.raises(LoginsError, match="gh, is not on this machine"):
        asyncio.run(logins.log_in_github())


def test_githubs_login_gives_its_code_and_tells_when_the_browser_said_yes(
    logins: ProgramLogins, state: Path
) -> None:
    told: list[bool] = []
    logins.changed = lambda: told.append(True)

    async def scenario() -> list[ProgramLogin]:
        code = await logins.log_in_github()
        assert code == DeviceCode(CODE, "https://github.com/login/device")
        assert await logins.log_in_github() == code, "one login at a time, its code again"
        (state / "gh-approve").write_text("example-user", "utf-8")
        for _ in range(200):
            if told:
                break
            await asyncio.sleep(0.05)
        return await logins.read()

    read = asyncio.run(scenario())
    assert told == [True]
    assert read[0] == ProgramLogin("github", "gh", True, True, "example-user")


def test_a_login_refused_in_the_browser_is_told_too_and_a_new_one_starts(
    logins: ProgramLogins, state: Path
) -> None:
    told: list[bool] = []
    logins.changed = lambda: told.append(True)

    async def scenario() -> None:
        await logins.log_in_github()
        (state / "gh-refuse").write_text("", "utf-8")
        for _ in range(200):
            if told:
                break
            await asyncio.sleep(0.05)
        (state / "gh-refuse").unlink()
        assert await logins.log_in_github() == DeviceCode(CODE, "https://github.com/login/device")
        await logins.close()

    asyncio.run(scenario())
    assert told == [True]
    assert not (state / "gh-login").exists()
