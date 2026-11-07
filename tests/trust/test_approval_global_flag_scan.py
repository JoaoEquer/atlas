# SPDX-FileCopyrightText: 2025 Nous Research
# SPDX-FileCopyrightText: 2026 the Atlas contributors
# SPDX-License-Identifier: MIT
"""The rules that skip a tool's global flags end on a long run of flags, and still see what follows.

A run of flags before the subcommand once made these patterns backtrack without end. Each check
runs in a fresh interpreter under a time limit: a pattern stuck in C holds the GIL, so only a
separate process can be stopped. Atlas has no gateway command, so no case is about one.
"""

from process_support import in_a_fresh_interpreter


def test_a_long_flag_run_still_reaches_the_rule_after_it() -> None:
    in_a_fresh_interpreter("""
from atlas.trust.detection import detect_dangerous_command

flags = "--opt val --flag=x -q - " * 58
expected = {
    f"docker {flags}-H ssh://prod ps": "docker with remote daemon redirect (-H/--host)",
    f"docker {flags}--context=prod ps": "docker with daemon redirect (--context: alternate daemon)",
    f"podman {flags}--url tcp://prod ps": "podman with remote daemon redirect (--url/--connection/--identity)",
    f"podman {flags}--remote ps": "podman remote mode (-r/--remote: remote daemon)",
    f"docker compose {flags}down": "docker compose restart/stop/kill/down (container lifecycle)",
    f"docker {flags}kill app": "docker restart/stop/kill (container lifecycle)",
}
for command, rule in expected.items():
    assert detect_dangerous_command(command) == (True, rule, rule), command[:40]
""")


def test_a_long_flag_run_that_matches_nothing_ends_quickly() -> None:
    in_a_fresh_interpreter("""
from atlas.trust.detection import detect_dangerous_command

tools = ("rclone lsf $HOME/.hermes --recursive --files-only", "hermes", "docker",
         "docker compose", "podman")
runs = ("--exclude " * 58, "--opt val " * 58, "--opt=val " * 58, "--opt" + " " * 100 + "val ")
for tool in tools:
    for flags in runs:
        assert detect_dangerous_command(f"{tool} {flags}ps 2>/dev/null")[0] is False, tool
""")


def test_the_space_between_a_flag_and_its_value_decides_as_it_always_did() -> None:
    # Bounding the scan moves no verdict: docker and podman take a value after exactly one
    # whitespace character, so two spaces end the flag run there.
    in_a_fresh_interpreter("""
from atlas.trust.detection import detect_dangerous_command

verdicts = {
    "docker --log-level debug stop app": True,
    "docker --log-level\\tdebug stop app": True,
    "docker --log-level  debug stop app": False,
    "docker --log-level  debug -H ssh://prod ps": False,
    "docker --log-level  debug --context prod ps": False,
    "podman --log-level  debug --remote ps": False,
    "podman --log-level  debug --url tcp://prod ps": False,
    "docker compose --project-name  demo down": False,
    "docker compose --project-name demo  down": True,
}
for command, dangerous in verdicts.items():
    assert detect_dangerous_command(command)[0] is dangerous, command
""")
