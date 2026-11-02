"""The detector answers at once on inputs shaped to make a regular expression backtrack."""

import time

import pytest

from atlas.trust import detect_dangerous_command, detect_hardline_command
from atlas.trust.detection import deny_command_variants


@pytest.mark.parametrize(
    "command",
    [
        "install ./.env" + ".ab" * 300 + ")",
        "cat config/.env" + ".x" * 300 + "-",
        "cp a ../.env" + ".local" * 200 + "\x1c",
    ],
    ids=["closing-paren", "trailing-dash", "control-char"],
)
def test_a_long_dotted_env_name_is_judged_without_freezing(command: str) -> None:
    # Before the fix, 22 dotted parts took two seconds and each four more multiplied it by 14.
    started = time.perf_counter()
    detect_dangerous_command(command)
    assert time.perf_counter() - started < 2


def test_a_write_to_a_long_dotted_env_name_is_still_flagged() -> None:
    flagged, _, reason = detect_dangerous_command("install ./.env" + ".ab" * 300 + ")")
    assert flagged
    assert reason == "overwrite project env/config file"


@pytest.mark.parametrize(
    "script",
    [
        "x=1\n" * 2000,
        'y="$(basename "$(pwd)")"\n' * 600,
        '"$( a ' * 400,
    ],
    ids=["assignment-lines", "nested-substitutions", "unclosed-substitutions"],
)
def test_a_long_script_is_read_in_bounded_time(script: str) -> None:
    # Before the fix: 15, 17 and 11 seconds, read on the server's event loop, so the whole server
    # waited. Now well under a second, a few with coverage measured.
    started = time.perf_counter()
    for _ in deny_command_variants(script):
        pass
    assert time.perf_counter() - started < 6


@pytest.mark.parametrize(
    "command",
    ["x=1\n" * 50 + "rm -rf /", 'x="$(pwd)"\ny="$(rm -rf /)"'],
    ids=["after-assignment-lines", "inside-a-substitution"],
)
def test_a_command_after_assignments_is_still_blocked(command: str) -> None:
    hardline, _ = detect_hardline_command(command)
    assert hardline
