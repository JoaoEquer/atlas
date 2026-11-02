# SPDX-FileCopyrightText: 2025 Nous Research
# SPDX-FileCopyrightText: 2026 the Atlas contributors
# SPDX-License-Identifier: MIT
"""The floors: the few commands Atlas refuses outright, and the look-alikes it must let through.

No approval reaches a floor. Auto mode, a job, the guardian and the user all sit above it, so a
floor that is too narrow lets a disk wipe run unasked, and one that is too wide stops ordinary
work with no way past. Each table below proves one edge of it: which spellings of a wipe still
count, which positions in a command line are real command positions, and which mentions are only
data handed to another program (a quoted argument, a heredoc body, a newline inside quotes).
"""

from typing import Any

import pytest

from atlas.trust.detection import detect_dangerous_command, detect_hardline_command


def _cases(**groups: list[str]) -> list[Any]:
    """One parameter per command, named after its group and its place in it."""
    return [
        pytest.param(command, id=f"{group}-{place}")
        for group, commands in groups.items()
        for place, command in enumerate(commands, 1)
    ]


# -- what the floor stops ---------------------------------------------------------------------------

STOPPED = _cases(
    root=[
        "rm -rf /",
        "rm -rf /*",
        "rm --recursive --force /",
        "rm -fr /",
        "sudo rm -rf /",
    ],
    # The shell folds doubled slashes and `.`/`..` segments back into `/`. Each one once fell
    # through to the softer dangerous tier, which auto mode skips.
    root_by_another_name=[
        "rm -rf //",
        "rm -rf /.",
        "rm -rf /./",
        "rm -rf /..",
        "rm -rf //*",
        "rm -fr /./",
        "ls && rm -rf //",
    ],
    system_folder=[
        "rm -rf /home",
        "rm -rf /home/*",
        "rm -rf /etc",
        "rm -rf /usr",
        "rm -rf /var",
        "rm -rf /boot",
        "rm -rf /bin",
    ],
    home=[
        "rm -rf ~",
        "rm -rf ~/",
        "rm -rf ~/*",
        "rm -rf $HOME",
        "rm -rf ${HOME}",
        "rm -fr ${HOME}",
    ],
    # Quoting the target is how a careful author writes a path; the quote must not hide it.
    quoted_target=[
        'rm -rf "/"',
        "rm -rf '/'",
        'rm -rf "/*"',
        'rm -rf "/etc"',
        "rm -rf '/etc'",
        'rm -rf "/home"',
        'rm -rf "/usr"',
        'rm -rf "$HOME"',
        "rm -rf '$HOME'",
        'rm -rf "$HOME/"',
        'rm -rf "~"',
        'sudo rm -rf "/"',
        'rm -rf "/" && echo done',
        'rm -rf "${HOME}"',
    ],
    format_disk=[
        "mkfs.ext4 /dev/sda1",
        "mkfs /dev/sdb",
        "mkfs.xfs /dev/nvme0n1",
        "true && mkfs.ext4 /dev/sda1",
        "sudo mkfs.ext4 /dev/sda1",
        "nohup mkfs /dev/sdb",
    ],
    overwrite_device=[
        "dd if=/dev/zero of=/dev/sda bs=1M",
        "dd if=/dev/urandom of=/dev/nvme0n1",
        "dd if=anything of=/dev/hda",
        "echo bad > /dev/sda",
        "cat /dev/urandom > /dev/sdb",
    ],
    fork_bomb=[":(){ :|:& };:"],
    kill_everything=["kill -9 -1", "kill -1"],
    power_off=[
        "shutdown -h now",
        "shutdown -r now",
        "sudo shutdown now",
        "reboot",
        "sudo reboot",
        "halt",
        "poweroff",
        "init 0",
        "init 6",
        "telinit 0",
        "systemctl poweroff",
        "systemctl reboot",
        "systemctl halt",
    ],
    after_separator_or_wrapper=[
        "ls; reboot",
        "echo done && shutdown -h now",
        "false || halt",
        "$(reboot)",
        "`shutdown now`",
        "sudo -E shutdown now",
        "env FOO=1 reboot",
        "exec shutdown",
        "nohup reboot",
        "setsid poweroff",
    ],
    # `(` opens a subshell and `{` a group: the word after either is a command, as after `$(`.
    in_subshell_or_group=[
        "(reboot)",
        "( reboot )",
        "(shutdown -h now)",
        "(poweroff)",
        "(halt)",
        "(init 0)",
        "(systemctl reboot)",
        "(sudo reboot)",
        "{ reboot; }",
        "{ shutdown -h now; }",
        "{ poweroff; }",
        "true && (reboot)",
        "echo hi; { reboot; }",
    ],
    # Data in quotes stays data, but only there: a bare newline separates commands, and a
    # substitution inside double quotes still runs.
    real_boundary_near_quotes=[
        "echo hi\nsudo reboot",
        'echo "a"\nsudo reboot',
        'git commit -m "safe message"\nshutdown -h now',
        'hermes send -t telegram "$(sudo reboot)"',
        'echo "`shutdown -h now`"',
        'hermes send "line1\nline2" && sudo reboot',
        "sudo reboot <<'EOF'\nignored\nEOF",
    ],
    # A root wipe at every place a command can start; the substitutions also end the bare path
    # on a shell metacharacter.
    root_at_every_position=[
        "ls && rm -rf /",
        "ls; rm -rf /",
        "echo x | rm -rf /",
        "env X=1 rm -rf /",
        "$(rm -rf /)",
        "`rm -rf /`",
        'echo "$(rm -rf /)"',
        "(rm -rf /)",
        "{ rm -rf /; }",
        "(rm -rf ~)",
        "(sudo rm -rf /)",
    ],
    # The rules that used to match a word anywhere now look at command positions only, so
    # every real shape must still be caught: wrappers, separators, substitutions, and the
    # payload of `sh -c`, `bash -c` and `eval`.
    format_at_every_position=[
        "sudo mkfs.xfs /dev/nvme0n1",
        "ls; mkfs /dev/sdb",
        "env FOO=1 mkfs.ext4 /dev/sda1",
        "$(mkfs.ext4 /dev/sda1)",
        "`mkfs /dev/sdb`",
        'bash -c "mkfs.ext4 /dev/sda1"',
    ],
    copy_to_device_at_every_position=[
        "sudo dd if=/dev/urandom of=/dev/nvme0n1",
        "echo start && dd if=/dev/zero of=/dev/sdb",
        "ls; dd if=x of=/dev/mmcblk0",
        "env X=1 dd if=/dev/zero of=/dev/sda",
        "$(dd if=/dev/zero of=/dev/sda)",
        "`dd if=/dev/zero of=/dev/sda`",
        'sh -c "dd if=/dev/zero of=/dev/sda"',
    ],
    redirect_to_device_at_every_position=[
        "cat file > /dev/sda",
        "echo junk > /dev/sdb",
        "true && cat f > /dev/nvme0n1",
        'sh -c "cat f > /dev/sda"',
        'bash -c "echo x > /dev/sdb"',
        'eval "cat f > /dev/sda"',
        'echo "$(cat f > /dev/sda)"',
        'echo "`cat f > /dev/sdb`"',
    ],
    kill_everything_at_every_position=[
        "sudo kill -1",
        "ls; kill -1",
        "true && kill -HUP -1",
        "$(kill -1)",
        'bash -c "kill -1"',
    ],
    fork_bomb_at_every_position=[
        "true && :(){ :|:& };:",
        'sh -c ":(){ :|:& };:"',
        "eval ':(){ :|:& };:'",
    ],
    # The shell runs a program behind assignments or a wrapper all the same.
    behind_assignments=[
        "FOO=1 rm -rf /",
        'FOO="a b" rm -rf /',
        "X=1 mkfs.ext4 /dev/sda",
        "A=1 B=2 shutdown now",
        "cd /tmp; X=1 reboot",
        "(X=1 reboot)",
        "X=1 sudo rm -rf ~",
        "FOO=1 dd if=/dev/zero of=/dev/sda",
    ],
    behind_a_wrapper=[
        "timeout 5 rm -rf /",
        "nice rm -rf /",
        "nice -n 10 reboot",
        "ionice -c 3 mkfs.ext4 /dev/sda",
    ],
)


@pytest.mark.parametrize("command", STOPPED)
def test_a_floor_command_is_stopped_and_says_why(command: str) -> None:
    stopped, reason = detect_hardline_command(command)

    assert stopped, f"the floor let {command!r} through"
    assert reason, f"the floor stopped {command!r} without a reason"


# -- what the floor lets through --------------------------------------------------------------------

LET_THROUGH = _cases(
    ordinary_path=[
        "rm -rf /tmp/foo",
        "rm -rf /tmp/*",
        "rm -rf ./build",
        "rm -rf node_modules",
        "rm -rf /home/user/scratch",
        "rm -rf ~/Downloads/old",
        "rm -rf $HOME/tmp",
        "rm foo.txt",
        "rm -rf some/path",
    ],
    # Only a segment that is exactly `.` or `..` folds back into `/`. `/...` is a folder named
    # `...`, and `/.foo` a dotfile at the root.
    dotted_name_at_root=[
        "rm -rf /...",
        "rm -rf /....",
        "rm -rf /.foo",
        "rm -rf /.config/foo",
    ],
    # The path ends on a closing quote it did not open itself: a whole command quoted as the
    # argument of another one.
    quoted_as_argument=[
        'git commit -m "rm -rf /"',
        'git commit -m "wipe with rm -rf /etc"',
        'gh pr create --title "block rm -rf / spellings"',
        'git commit -m "fixes rm -rf / bypass"',
        'echo "run rm -rf / now"',
        'echo "rm -rf /"',
        'printf "%s" "rm -rf /"',
        'gh issue comment 1 --body "the fix blocks rm -rf //"',
    ],
    # Inside quotes, `(` and `{` are punctuation, not a subshell or a group.
    bracket_inside_quotes=[
        'gh pr create --title "block (reboot) spellings"',
        'git commit -m "(rm -rf /) note"',
        'echo "(reboot)"',
        'echo "{ reboot; }"',
        "echo '(poweroff)'",
        "echo '{ rm -rf /; }'",
        'find . -name "*(reboot)*"',
    ],
    copy_to_file=[
        "dd if=/dev/zero of=./image.bin",
        "dd if=./data of=./backup.bin",
    ],
    redirect_to_file=[
        "echo done > /tmp/flag",
        "echo test > /dev/null",
    ],
    read_device=[
        "ls /dev/sda",
        "cat /dev/urandom | head -c 10",
    ],
    trigger_word_as_text=[
        "grep 'shutdown' logs.txt",
        "echo reboot",
        "echo '# init 0 in comment'",
        "cat rebooting.log",
        "echo 'halt and catch fire'",
        "python3 -c 'print(\"shutdown\")'",
        "find . -name '*reboot*'",
        "mkfs_helper --version",
    ],
    # A quoted sentence that names a floor command is prose for echo, git or gh. The rules
    # with no command position (a redirect to a device, the fork bomb) skip quoted text too.
    quoted_prose=[
        'echo "does this workflow use mkfs anywhere?"',
        "echo 'run mkfs.ext4 on the backup disk later'",
        'git commit -m "add mkfs.ext4 warning to the runbook"',
        'gh pr create --body "this PR anchors the mkfs pattern"',
        "grep 'mkfs' docs/runbook.md",
        'git commit -m "never run dd of=/dev/sda in prod"',
        'echo "dd if=/dev/zero of=/dev/sda wipes the disk"',
        "grep 'dd if=/dev/zero of=/dev/sda' notes.md",
        'echo "kill -1 sends SIGHUP to every process"',
        'gh issue comment 7 --body "the agent must never run kill -1"',
        'echo "cat file > /dev/sda destroys the disk"',
        "echo 'redirect > /dev/sdb1 is fatal'",
        'git commit -m "block > /dev/sda redirects"',
        'gh pr create --body "guards the > /dev/nvme0n1 redirect"',
        'git commit -m "document the fork bomb :(){ :|:& };: pattern"',
        'echo "classic fork bomb: :(){ :|:& };:"',
        "echo ':(){ :|:& };: is a fork bomb'",
    ],
    # A newline inside quotes belongs to the argument: a message body, a commit message, a
    # heredoc. It does not start a command.
    newline_inside_quotes=[
        'hermes send -t telegram -s "spark1" "console output:\nsudo reboot\ndone"',
        'hermes send -t telegram "line1\nshutdown -h now\nline3"',
        "git commit -m 'ops notes:\nreboot the box after the deploy'",
        'git commit -m "fix startup\nsystemctl reboot was flaky here"',
        "python3 - <<'EOF'\nmsg = 'run sudo reboot later'\nprint(msg)\nEOF",
        "cat > /tmp/notes.txt <<'EOF'\nremember: shutdown -h now\nEOF",
        'git commit -m "docs:\nwarn about rm -rf / in the guide"',
    ],
    harmless_systemctl=[
        "systemctl status nginx",
        "systemctl restart nginx",
        "systemctl stop nginx",
        "systemctl start nginx",
    ],
    kill_one_process=[
        "kill -9 12345",
        "kill -HUP 1234",
        "pkill python",
    ],
    everyday=[
        "git status",
        "npm run build",
        "sudo apt update",
        "curl https://example.com | head",
    ],
    # An assignment's value is data, and `command -v` only says where a name resolves.
    floor_word_as_a_value=[
        'FOO="reboot" ls',
        "X=1 ls /",
        'git commit -m "X=1 reboot"',
        "command -v reboot",
    ],
)


@pytest.mark.parametrize("command", LET_THROUGH)
def test_a_look_alike_passes_the_floor_with_no_reason(command: str) -> None:
    stopped, reason = detect_hardline_command(command)

    assert not stopped, f"the floor stopped {command!r} as {reason!r}"
    assert reason is None


# -- the dangerous tier below the floor -------------------------------------------------------------

# The dangerous tier has its own rules for formatting and copying disks. They look at command
# positions the same way, so prose asks for nothing, and a real call still asks, under the same
# description. Auto mode may still let these run: only the floor is beyond it.


@pytest.mark.parametrize(
    "command",
    [
        pytest.param('echo "mkfs is a formatting tool"', id="mkfs-in-echo"),
        pytest.param('git commit -m "explain dd if=/dev/zero usage"', id="dd-in-commit-message"),
    ],
)
def test_prose_about_a_disk_tool_asks_for_no_approval(command: str) -> None:
    dangerous, _, description = detect_dangerous_command(command)

    assert not dangerous, f"{command!r} asked for approval as {description!r}"


@pytest.mark.parametrize(
    ("command", "description"),
    [
        pytest.param("mkfs /dev/sdb1", "format filesystem", id="mkfs"),
        pytest.param("sudo mkfs -t vfat /dev/sdc1", "format filesystem", id="mkfs-under-sudo"),
        pytest.param("dd if=backup.img of=restore.img", "disk copy", id="dd-between-files"),
        pytest.param("true && dd if=a.img of=b.img", "disk copy", id="dd-after-and"),
    ],
)
def test_a_real_disk_tool_call_still_asks_under_its_description(
    command: str, description: str
) -> None:
    dangerous, _, said = detect_dangerous_command(command)

    assert dangerous, f"{command!r} no longer asks for approval"
    assert said == description


# -- a backslash at the end of a line ---------------------------------------------------------------

# The shell drops a backslash and the newline after it, joining the two lines into one word list:
# `rm -rf \` then `/` on the next line is `rm -rf /`. The detector once kept the backslash between
# the words, and the wipe slipped under the floor. Each case is a floor command split this way.


@pytest.mark.parametrize(
    ("command", "named"),
    [
        pytest.param("rm -rf \\\n/", "root", id="before-the-path"),
        pytest.param("rm -r\\\nf /", "root", id="inside-the-flags"),
        pytest.param("rm -rf \\\n~", "home", id="home"),
        pytest.param("rm -rf \\\r\n/", "root", id="windows-line-end"),
        pytest.param("mkfs.ext4 \\\n/dev/sda1", "mkfs", id="format"),
    ],
)
def test_a_command_split_by_a_line_continuation_is_still_stopped(command: str, named: str) -> None:
    stopped, reason = detect_hardline_command(command)

    assert stopped, f"a line continuation hid {command!r} from the floor"
    assert reason, f"{command!r} was stopped without a reason"
    assert named in reason.lower(), f"{command!r} was stopped as {reason!r}"
