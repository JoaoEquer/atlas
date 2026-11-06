"""The detector answers at once on inputs shaped to make a regular expression backtrack."""

import time

import pytest

from atlas.trust import detect_dangerous_command, detect_hardline_command
from process_support import in_a_fresh_interpreter


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
    # Python expressions, built in the fresh interpreter: the texts are too long for a command line.
    [
        "'x=1\\n' * 8000",
        """'y="$(basename "$(pwd)")"\\n' * 2400""",
        """'"$( a ' * 800""",
    ],
    ids=["assignment-lines", "nested-substitutions", "unclosed-substitutions"],
)
def test_a_long_script_is_read_in_bounded_time(script: str) -> None:
    # Read on the server's event loop, a quarter of these took 15, 17 and 11 seconds before the
    # fix, and the reading grew with the square of the length: minutes at this size. Now under two
    # seconds each, timed in a fresh interpreter, away from coverage and the other tests.
    in_a_fresh_interpreter(f"""
from atlas.trust.detection import deny_command_variants

for _ in deny_command_variants({script}):
    pass
""")


@pytest.mark.parametrize(
    "command",
    ["x=1\n" * 50 + "rm -rf /", 'x="$(pwd)"\ny="$(rm -rf /)"'],
    ids=["after-assignment-lines", "inside-a-substitution"],
)
def test_a_command_after_assignments_is_still_blocked(command: str) -> None:
    hardline, _ = detect_hardline_command(command)
    assert hardline
