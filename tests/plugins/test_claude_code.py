"""The Claude Code plugin as Atlas drives it, through the SDK's executor: the real pipes and the
real control protocol, against tests/fake_claude.py, which speaks it as `claude` does with a script
in place of the model. The host here plays Atlas: it keeps what it hears and answers what is
asked."""

import asyncio
import base64
import json
import logging
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from pathlib import Path
from typing import Any

import pytest

from atlas.plugins import (
    Answers,
    Called,
    CommandsChanged,
    Compacted,
    Connector,
    ExecutorError,
    ExecutorEvent,
    ExecutorFile,
    ExecutorInstance,
    ExecutorSpec,
    Finished,
    LimitReached,
    Permission,
    PlanUsage,
    Question,
    Returned,
    Said,
    SlashCommand,
    Started,
    Thought,
    UsageChanged,
    UsageWindow,
    Verdict,
)
from claude_code_support import FAKE, FOLDER, claude_code, plugin_module


@pytest.fixture
def script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> "Script":
    return Script(tmp_path, monkeypatch)


class Script:
    def __init__(self, folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.file = folder / "script.json"
        self.log = folder / "log.jsonl"
        monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(self.file))
        monkeypatch.setenv("FAKE_CLAUDE_LOG", str(self.log))
        monkeypatch.setenv("FAKE_CLAUDE_AUTH", str(folder / "auth.json"))

    def write(self, *steps: dict[str, Any]) -> None:
        self.file.write_text(json.dumps(list(steps)), encoding="utf-8")

    def received(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]


class Host:
    """Atlas, as the instance sees it: what it heard, what it was asked, and its answers."""

    def __init__(
        self,
        approve: Callable[[Permission], Awaitable[Verdict]] | None = None,
        ask: Callable[[Sequence[Question]], Awaitable[Answers]] | None = None,
    ) -> None:
        self.events: list[ExecutorEvent] = []
        self.permissions: list[Permission] = []
        self.questions: list[Sequence[Question]] = []
        self.failures: list[str] = []
        self._approve = approve
        self._ask = ask
        self.during: Callable[[ExecutorEvent], Coroutine[Any, Any, None]] | None = None

    def event(self, event: ExecutorEvent) -> None:
        self.events.append(event)
        if self.during is not None:
            asyncio.get_running_loop().create_task(self.during(event))

    async def ask(self, questions: Sequence[Question]) -> Answers:
        self.questions.append(questions)
        return None if self._ask is None else await self._ask(questions)

    async def approve(self, permission: Permission) -> Verdict:
        self.permissions.append(permission)
        return Verdict(True) if self._approve is None else await self._approve(permission)

    def failed(self, reason: str) -> None:
        self.failures.append(reason)


#: The fake `claude`'s slash commands, as its `initialize` describes them.
COMMANDS = (
    SlashCommand(
        "compact",
        "Clear the conversation history but keep a summary",
        "<optional custom summarization instructions>",
        builtin=True,
    ),
    SlashCommand("review", "Review a pull request (user)", "[pr]"),
)


def _run(folder: Path, host: Host, *prompts: str, spec: ExecutorSpec | None = None) -> Host:
    async def scenario() -> None:
        instance = await claude_code().start(spec or ExecutorSpec(folder), host)
        try:
            for prompt in prompts:
                await instance.prompt(prompt)
        finally:
            await instance.stop()

    asyncio.run(scenario())
    return host


def test_a_task_runs_its_tools_with_permission_and_ends_with_a_result(
    script: Script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-atlas")
    script.write(
        {"think": "The test imports the wrong module."},
        {"say": "I will run the tests."},
        {"tool": "Bash", "input": {"command": "pytest -x"}},
        {"result": "Fixed: the import was wrong."},
    )

    host = _run(tmp_path, Host(), "Fix the failing test.")

    events = host.events
    # Its slash commands come with its `initialize`, before its first turn says `init` (16f).
    assert events[0] == Started("", commands=("compact", "review"))
    assert events[1] == CommandsChanged(COMMANDS)
    assert events[2] == Started("fake-session-0001", "fake-opus", "none", ("compact", "review"))
    assert events[3:6] == [
        Thought("The test imports the wrong module."),
        Said("I will run the tests."),
        Called("toolu_call_1", "Bash", '{"command": "pytest -x"}'),
    ]
    assert events[6] == Returned("toolu_call_1", "done", False)
    finished = events[-1]
    assert isinstance(finished, Finished)
    assert (finished.text, finished.failed, finished.reason, finished.turns) == (
        "Fixed: the import was wrong.",
        False,
        "completed",
        1,
    )
    assert [(p.tool, p.summary, p.title) for p in host.permissions] == [
        ("Bash", "pytest -x", "Claude wants to use Bash")
    ]
    received = script.received()
    started = received[0]
    assert started["credentials"] == [], "an API key in Atlas never bills in place of the login"
    assert "--permission-prompt-tool" in started["argv"]
    assert started["argv"][started["argv"].index("--permission-mode") + 1] == "default"
    assert "--settings" not in started["argv"], "Atlas adds no rules to the user's own"
    assert "--setting-sources=user,project,local" in started["argv"]
    assert Path(started["cwd"]) == tmp_path
    assert received[1]["request"]["subtype"] == "initialize"
    assert received[2]["message"]["content"] == "Fix the failing test."
    assert received[3]["response"]["response"] == {
        "behavior": "allow",
        "updatedInput": {"command": "pytest -x"},
    }


def test_a_denial_reaches_claude_code_and_the_task_goes_on(script: Script, tmp_path: Path) -> None:
    script.write(
        {"tool": "Bash", "input": {"command": "rm -rf build"}},
        {"say": "I will leave build alone."},
        {"result": "Left it."},
    )

    async def deny(permission: Permission) -> Verdict:
        return Verdict(False, "Keep the build folder.")

    events = _run(tmp_path, Host(approve=deny), "Clean up.").events

    assert Returned("toolu_call_1", "Keep the build folder.", True) in events
    assert Said("I will leave build alone.") in events
    assert isinstance(events[-1], Finished)
    assert not events[-1].failed


def test_a_question_is_answered_with_the_users_choices(script: Script, tmp_path: Path) -> None:
    questions = [
        {
            "question": "Which runner?",
            "header": "Runner",
            "options": [{"label": "Jest", "description": "the old one"}, {"label": "Vitest"}],
            "multiSelect": False,
        }
    ]
    script.write({"questions": questions}, {"result": "Using Vitest."})

    async def choose(asked: Sequence[Question]) -> Answers:
        return {"Which runner?": "Vitest"}

    host = _run(tmp_path, Host(ask=choose), "Pick a runner.")

    [asked] = host.questions
    assert [(q.text, q.header, [o.label for o in q.options]) for q in asked] == [
        ("Which runner?", "Runner", ["Jest", "Vitest"])
    ]
    assert asked[0].options[0].description == "the old one"
    assert host.permissions == [], "a question is not a permission"
    assert Returned("toolu_call_1", '{"Which runner?": "Vitest"}', False) in host.events


def test_questions_left_unanswered_are_refused_and_the_task_goes_on(
    script: Script, tmp_path: Path
) -> None:
    script.write({"questions": [{"question": "Which?", "options": []}]}, {"result": "Went on."})

    events = _run(tmp_path, Host(), "Go.").events

    assert Returned("toolu_call_1", "The questions went unanswered. Go on without them, or stop.",
                    True) in events  # fmt: skip
    assert isinstance(events[-1], Finished)


def test_accents_reach_claude_code_intact(script: Script, tmp_path: Path) -> None:
    """Found by the rehearsal on 12/10/2026: written raw, an accented question reached a reader in
    the system's encoding garbled, and its answer no longer matched it."""
    question = "Qual o nome da função?"
    options = [{"label": "ação"}, {"label": "vez"}]
    script.write(
        {"questions": [{"question": question, "header": "Nome", "options": options}]},
        {"result": "Ok."},
    )

    async def choose(asked: Sequence[Question]) -> Answers:
        return {question: "ação"}

    events = _run(tmp_path, Host(ask=choose), "Nomeie.").events

    assert Returned("toolu_call_1", json.dumps({question: "ação"}), False) in events


def test_a_request_waits_as_long_as_the_answer_takes(script: Script, tmp_path: Path) -> None:
    script.write({"tool": "Edit", "input": {"file_path": "a.py"}}, {"result": "Edited."})

    async def slowly(permission: Permission) -> Verdict:
        await asyncio.sleep(1.5)
        return Verdict(True)

    events = _run(tmp_path, Host(approve=slowly), "Edit it.").events

    assert Called("toolu_call_1", "Edit", '{"file_path": "a.py"}', ("a.py",)) in events
    assert Returned("toolu_call_1", "done", False) in events


def test_an_interrupt_ends_the_turn_as_claude_code_ends_it(script: Script, tmp_path: Path) -> None:
    script.write({"say": "Reading every file."}, {"sleep": 30}, {"result": "never"})
    host = Host()
    started: list[ExecutorInstance] = []

    async def stop_on_text(event: ExecutorEvent) -> None:
        if isinstance(event, Said):
            await started[0].process.interrupt()  # type: ignore[attr-defined]

    host.during = stop_on_text

    async def scenario() -> None:
        instance = await claude_code().start(ExecutorSpec(tmp_path), host)
        started.append(instance)
        try:
            await instance.prompt("Read everything.")
        finally:
            await instance.stop()

    asyncio.run(scenario())

    finished = host.events[-1]
    assert isinstance(finished, Finished)
    assert (finished.failed, finished.reason) == (True, "aborted_streaming")


# -- the workbench's instance (step 16c) ----------------------------------------------------------


def test_one_instance_takes_many_prompts_a_steer_and_its_auto_mode(
    script: Script, tmp_path: Path
) -> None:
    script.write({"say": "Working."}, {"sleep": 0.8}, {"result": "Done."})
    host = Host()

    async def scenario() -> None:
        instance = await claude_code().start(ExecutorSpec(tmp_path, auto=True), host)
        try:
            await instance.prompt("First.")
            turn = asyncio.create_task(instance.prompt("Second."))
            await asyncio.sleep(0.3)
            await instance.steer("Use the other folder.")
            await instance.set_auto(False)
            await turn
        finally:
            await instance.stop()

    asyncio.run(scenario())

    finished = [e for e in host.events if isinstance(e, Finished)]
    assert len(finished) == 2, "the same process ran both turns"
    assert Said("Steered: Use the other folder.") in host.events
    received = script.received()
    argv = received[0]["argv"]
    assert argv[argv.index("--permission-mode") + 1] == "auto"
    assert "bypassPermissions" not in argv
    assert "--dangerously-skip-permissions" not in argv
    assert "--settings" not in argv, "auto keeps its own rules and the user's (17/10/2026)"
    assert "--setting-sources=user,project,local" in argv
    requests = [r["request"] for r in received if "request" in r]
    modes = [r for r in requests if r.get("subtype") == "set_permission_mode"]
    assert modes == [{"subtype": "set_permission_mode", "mode": "default"}]
    assert len([r for r in received if "argv" in r]) == 1


def test_a_prompt_while_a_turn_runs_is_refused(script: Script, tmp_path: Path) -> None:
    script.write({"sleep": 0.6}, {"result": "Done."})
    host = Host()

    async def scenario() -> str:
        instance = await claude_code().start(ExecutorSpec(tmp_path), host)
        try:
            turn = asyncio.create_task(instance.prompt("First."))
            await asyncio.sleep(0.2)
            with pytest.raises(ExecutorError) as refused:
                await instance.prompt("Second.")
            await turn
            return str(refused.value)
        finally:
            await instance.stop()

    assert "steer it instead" in asyncio.run(scenario())


#: A `claude` that answers `initialize`, reads the prompt, and dies without a result.
_DIES = (
    "import json, sys; asked = json.loads(sys.stdin.readline()); "
    "print(json.dumps({'type': 'control_response', 'response': {'subtype': 'success', "
    "'request_id': asked['request_id'], 'response': {}}}), flush=True); "
    "sys.stdin.readline(); sys.stderr.write('it broke\\n'); sys.stderr.flush()"
)


def test_a_process_that_dies_without_a_result_says_so(tmp_path: Path) -> None:
    host = Host()

    async def scenario() -> str:
        instance = await claude_code([sys.executable, "-c", _DIES]).start(
            ExecutorSpec(tmp_path), host
        )
        try:
            with pytest.raises(ExecutorError) as ended:
                await instance.prompt("Go.")
            return str(ended.value)
        finally:
            await instance.stop()

    said = asyncio.run(scenario())

    assert said.startswith("It ended without a result.")
    assert host.failures == [], "a prompt waited on it: the prompt says so, not the host"


def test_a_process_that_does_not_start_says_so(tmp_path: Path) -> None:
    async def scenario() -> None:
        await claude_code([str(tmp_path / "no-such-claude")]).start(ExecutorSpec(tmp_path), Host())

    with pytest.raises(ExecutorError, match="Claude Code could not start"):
        asyncio.run(scenario())


def test_detection_says_version_and_account_and_refuses_an_old_claude(
    script: Script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = asyncio.run(claude_code().detect())
    assert (found.installed, found.version, found.supported, found.minimum) == (
        True,
        "2.1.140",
        True,
        "2.1.140",
    )
    assert found.account is not None
    assert (found.account.login, found.account.plan) == ("someone@example.com", "max")

    monkeypatch.setenv("FAKE_CLAUDE_VERSION", "2.1.99")
    old = asyncio.run(claude_code().detect())
    assert (old.installed, old.supported) == (True, False)
    assert old.problem == "Claude Code 2.1.99 is older than 2.1.140. Update it: claude update"

    missing = asyncio.run(claude_code(None).detect())
    assert (missing.installed, missing.minimum) == (False, "2.1.140")
    assert "Install it" in missing.problem
    assert claude_code().capabilities().steer


def test_detection_reads_a_login_that_is_not_there_as_claude_code_says_it(
    script: Script, tmp_path: Path
) -> None:
    """Claude Code 2.1.296 says nobody is logged in with exit code 1 and the status still written
    (found on 18/10/2026): it is installed and driven, and the screen asks for a login."""
    (tmp_path / "auth.json").write_text(json.dumps({"loggedIn": False}), encoding="utf-8")

    found = asyncio.run(claude_code().detect())

    assert (found.installed, found.supported) == (True, True)
    assert found.account is not None
    assert (found.account.logged_in, found.account.login) == (False, "")


def test_a_version_is_read_past_a_notice_and_compared_by_its_numbers() -> None:
    protocol = plugin_module("protocol")

    assert protocol.version_of("2.1.296 (Claude Code)\n") == "2.1.296"
    noticed = "Node.js 18.0.0 is no longer supported\n2.1.300-beta.1 (Claude Code)\n"
    assert protocol.version_of(noticed) == "2.1.300-beta.1"
    assert protocol.version_of("no version here") == ""
    assert protocol.at_least("2.1.300-beta.1", "2.1.140")
    assert protocol.at_least("2.1.140", "2.1.140")
    assert not protocol.at_least("2.1.99", "2.1.140")
    assert not protocol.at_least("2.1", "2.1.140")
    assert not protocol.at_least("", "2.1.140")
    said = 'A notice first\n{\n  "loggedIn": false,\n  "authMethod": "none"\n}\n'
    assert protocol.status_of(said) == {"loggedIn": False, "authMethod": "none"}
    assert protocol.status_of("not json") is None


def _homes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, path: list[Path]) -> Path:
    """A user folder, a local app data folder and a PATH of the test's own: the machine's real
    Claude Code is never found."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("PATH", os.pathsep.join(str(folder) for folder in path))
    return home


def _file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def test_claude_is_found_where_its_installers_put_it_when_the_path_does_not_name_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Agent SDK's places (its `_find_cli`): an app opened before Claude Code was installed
    has a PATH without it, and one opened from a Mac's Dock the system's PATH only."""
    executor = plugin_module("executor")
    home = _homes(monkeypatch, tmp_path, [tmp_path / "empty"])
    # Only a machine-wide place (`/usr/local/bin`, outside the test's home) may hold one already.
    machine = [p for p in executor.known_places() if not p.is_relative_to(tmp_path) and p.is_file()]
    assert executor.claude_program() == (machine[0] if machine else None)

    # The first of the SDK's places, so it is found before any machine-wide one.
    first = home / (
        ".local/bin/claude.exe" if sys.platform == "win32" else ".npm-global/bin/claude"
    )
    installed = _file(first)
    assert executor.known_places()[0] == installed
    assert executor.claude_program() == installed
    if sys.platform == "win32":
        installed.unlink()
        winget = tmp_path / "local" / "Microsoft" / "WinGet"
        package = _file(winget / "Packages" / "Anthropic.ClaudeCode_Source_8w" / "claude.exe")
        assert executor.claude_program() == package
        assert executor.too_old("2.1.99", str(package)).endswith(
            "Update it: winget upgrade Anthropic.ClaudeCode"
        )


@pytest.mark.skipif(sys.platform != "win32", reason="npm's claude.cmd is Windows'")
def test_on_windows_npms_claude_cmd_is_never_run_but_the_program_its_package_carries_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Agent SDK refuses the `.cmd` npm leaves (`CreateProcess` runs no batch file as a
    program), and Atlas never runs it either: it runs the native `claude.exe` npm's package
    carries, which the `.cmd` only starts (a tester's npm install, 19/10/2026), and an npm install
    without one says so instead of "not on the PATH". A `.cmd` early on the PATH does not hide a
    native `claude.exe` later on it."""
    executor = plugin_module("executor")
    npm, native = tmp_path / "npm", tmp_path / "native"
    _homes(monkeypatch, tmp_path, [npm, native])
    _file(npm / "claude.cmd")

    assert executor.claude_program() is None
    said = executor.not_found()
    assert "installed through npm (claude.cmd)" in said
    assert said.endswith("irm https://claude.ai/install.ps1 | iex")

    carried = _file(npm / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe")
    assert executor.claude_program() == carried
    assert executor.too_old("2.1.99", str(carried)).endswith("Update it: claude update")

    exe = _file(native / "claude.exe")
    assert executor.claude_program() == exe


@pytest.mark.skipif(sys.platform != "win32", reason="npm's global folder is Windows'")
def test_on_windows_npms_global_folder_is_looked_in_when_the_path_does_not_name_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """npm's own global folder, `%APPDATA%\\npm`, as the installers' places are: an app opened
    before Claude Code was installed has a PATH without it."""
    executor = plugin_module("executor")
    _homes(monkeypatch, tmp_path, [tmp_path / "empty"])
    package = tmp_path / "roaming" / "npm" / "node_modules" / "@anthropic-ai" / "claude-code"
    carried = _file(package / "bin" / "claude.exe")

    assert executor.claude_program() == carried


def test_claude_codes_environment_keeps_the_users_own_folder_and_drops_other_credentials() -> None:
    """`CLAUDE_CONFIG_DIR` is where the user keeps their Claude Code: its login, and the sessions
    the executors tab lists from it, so the program Atlas runs is given it too."""
    protocol = plugin_module("protocol")
    given = {
        "CLAUDE_CONFIG_DIR": "/somewhere/claude",
        "ANTHROPIC_API_KEY": "sk-ant-x",
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "CLAUDECODE": "1",
        "PATH": "/bin",
    }

    kept = protocol.environment(given)

    assert kept == {
        "CLAUDE_CONFIG_DIR": "/somewhere/claude",
        "PATH": "/bin",
        "CLAUDE_CODE_ENTRYPOINT": "sdk-py",
    }


def test_another_account_runs_on_its_own_folder_and_no_inherited_token_ever_does() -> None:
    """Step 16h: an account is Claude Code's own folder for it, handed to its process as its
    documentation says; the user's own folder is passed on as it is; a token inherited from Atlas
    never reaches Claude Code."""
    protocol = plugin_module("protocol")
    given = {
        "CLAUDE_CONFIG_DIR": "/somewhere/claude",
        "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-inherited",
        "PATH": "/bin",
    }

    assert "CLAUDE_CODE_OAUTH_TOKEN" not in protocol.environment(given)
    assert protocol.environment(given, "/home/me/.claude-work") == {
        "CLAUDE_CONFIG_DIR": "/home/me/.claude-work",
        "PATH": "/bin",
        "CLAUDE_CODE_ENTRYPOINT": "sdk-py",
    }


def test_an_executor_on_another_account_keeps_everything_in_its_folder(
    script: Script, tmp_path: Path
) -> None:
    script.write({"account": True}, {"result": "Done."})
    main = claude_code()
    folder = main.new_account_folder("work")
    on = main.for_account(folder)

    host = asyncio.run(_on(on, tmp_path))

    said = [event.text for event in host.events if isinstance(event, Said)]
    assert said == ["Running on .claude-work."]
    assert folder == Path.home() / ".claude-work", "beside the user's own"
    assert on.account_folder() == folder
    assert script.received()[0]["account"] == str(folder)
    capabilities = main.capabilities()
    assert capabilities.account_folders
    assert capabilities.delete_sessions


def test_deleting_a_session_removes_what_claude_purge_would_of_it_and_nothing_else(
    tmp_path: Path,
) -> None:
    """Asked for by the user on 19/10/2026: one session's files, as `claude purge` lists them for a
    project; the project's other sessions and its memory stay."""
    transcript = plugin_module("transcript")
    root = tmp_path / ".claude-work"
    project = root / "projects" / "C--work-site"
    kept = project / "s-other.jsonl"
    gone = project / "s-gone.jsonl"
    for file in (
        kept,
        gone,
        project / "s-gone" / "subagents" / "a.jsonl",
        project / "memory" / "MEMORY.md",
        root / "file-history" / "s-gone" / "edit.txt",
        root / "tasks" / "s-gone" / "1.json",
        root / "debug" / "s-gone.txt",
        root / "debug" / "s-other.txt",
    ):
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("{}", encoding="utf-8")

    transcript.delete_session_files(root, gone)

    left = sorted(str(f.relative_to(root)) for f in root.rglob("*") if f.is_file())
    assert left == sorted(
        str(Path(name))
        for name in (
            "debug/s-other.txt",
            "projects/C--work-site/memory/MEMORY.md",
            "projects/C--work-site/s-other.jsonl",
        )
    )
    executor = claude_code().for_account(root)
    with pytest.raises(ExecutorError, match="no longer keeps this session"):
        asyncio.run(executor.delete_session("s-gone", tmp_path))


def test_a_copy_of_a_session_the_other_account_no_longer_keeps_is_refused(tmp_path: Path) -> None:
    executor = claude_code().for_account(tmp_path / ".claude-work")
    spec = ExecutorSpec(tmp_path, resume="s-none", resume_from=tmp_path / ".claude")

    with pytest.raises(ExecutorError, match="other account no longer keeps"):
        asyncio.run(executor.start(spec, Host()))


async def _on(executor: Any, folder: Path) -> Host:
    """One turn of `executor` in `folder`, its host's events."""
    host = Host()
    instance = await executor.start(ExecutorSpec(folder), host)
    await instance.prompt("Go.")
    await instance.stop()
    return host


def test_the_program_setting_is_a_json_list_or_the_claude_on_the_path() -> None:
    executor = plugin_module("executor")  # the package is imported with it
    package = sys.modules["atlas_plugin_claude_code"]

    assert package.program('["/opt/claude", "--x"]') == ["/opt/claude", "--x"]
    with pytest.raises(ExecutorError, match="not a JSON list"):
        package.program("/opt/claude")
    with pytest.raises(ExecutorError, match="a JSON list of the program"):
        package.program("[]")
    assert package.program("") == executor.installed_cli()


def test_the_plugin_folder_is_its_name_and_ships_with_atlas() -> None:
    assert FOLDER.name == "claude-code"
    manifest = json.loads((FOLDER / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["atlas"]["executors"] == ["claude-code"]
    # Version 3 lists sessions, which this plugin does; it stays at 2, the version the user's
    # consent was given for (the fingerprint holds it), since Atlas ships it with itself.
    assert manifest["atlas"]["api"] == 2


# -- its sessions, wherever they began (step 16f) ------------------------------------------------

#: A session as Claude Code 2.1.140 keeps it, its words neutral: the keys of each kind of line are
#: those of a real session's, with a rewound reply, a meta line and a side chain.
SESSION_FILE = Path(__file__).parent / "data" / "claude-session.jsonl"
SESSION_ID = "6f1c2a8e-4b7d-4e2a-9c3f-1a2b3c4d5e6f"


def _keep(root: Path, folder: str, name: str, text: str, mtime: float) -> Path:
    """A session file under Claude Code's `projects/`, last changed at `mtime`."""
    kept = root / "projects" / folder / f"{name}.jsonl"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text(text, encoding="utf-8")
    os.utime(kept, (mtime, mtime))
    return kept


def _line(**fields: object) -> str:
    return json.dumps(fields, separators=(",", ":")) + "\n"


def test_sessions_are_listed_from_every_project_the_most_recent_first(tmp_path: Path) -> None:
    root = tmp_path / "claude"
    _keep(root, "C--work-shop", SESSION_ID, SESSION_FILE.read_text(encoding="utf-8"), 1_000)
    renamed = "0a1b2c3d-0000-4000-8000-00000000abcd"
    _keep(
        root,
        "C--work-api",
        renamed,
        _line(type="user", cwd="C:\\work\\api", timestamp="2026-10-15T09:00:00.000Z",
              message={"role": "user", "content": "Check the API."}, uuid="u1", parentUuid=None)
        + _line(type="ai-title", aiTitle="API check", sessionId=renamed)
        + _line(type="custom-title", customTitle="My API session", sessionId=renamed),
        2_000,
    )  # fmt: skip
    _keep(
        root,
        "C--work-api",
        "1a1b2c3d-0000-4000-8000-00000000abcd",
        _line(type="user", isSidechain=True, cwd="C:\\work\\api", message={"content": "x"}),
        3_000,
    )
    _keep(root, "C--work-api", "2a1b2c3d-0000-4000-8000-00000000abcd", _line(type="x"), 4_000)
    _keep(root, "C--work-api", "notes", _line(type="user", cwd="C:\\work"), 5_000)

    found = asyncio.run(claude_code(sessions=root).sessions())

    # A side chain alone, a file of metadata only and a name that is no session id are left out.
    assert [(s.id, s.title, s.folder) for s in found] == [
        (renamed, "My API session", Path("C:\\work\\api")),
        (SESSION_ID, "Show cents in the cart total", Path("C:\\work\\shop")),
    ]
    assert found[0].updated_at == "1970-01-01T00:33:20.000Z"
    assert found[0].started_at == "2026-10-15T09:00:00.000Z"
    assert claude_code().capabilities().sessions


def test_its_folders_are_listed_apart_and_a_folders_sessions_read_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The executors tab lists the folders first and reads a folder's sessions once it is opened:
    the folders read one session file each, the newest the list would show, and a folder's
    sessions only its own project's."""
    root = tmp_path / "claude"
    shop = "6b6b6b6b-0000-4000-8000-00000000000"
    for number in range(3):
        _keep(
            root, "C--work-shop", f"{shop}{number}",
            _line(type="user", cwd="C:\\work\\shop", message={"content": f"Shop task {number}."}),
            1_000 + number,
        )  # fmt: skip
    # Its newest file is a side chain: the folder's time is its newest session's.
    _keep(
        root, "C--work-shop", "1b1b1b1b-0000-4000-8000-000000000000",
        _line(type="user", isSidechain=True, cwd="C:\\work\\shop", message={"content": "x"}),
        9_000,
    )  # fmt: skip
    _keep(
        root, "C--work-api", "2b2b2b2b-0000-4000-8000-000000000000",
        _line(type="user", cwd="C:\\work\\api", message={"content": "Check the API."}),
        5_000,
    )  # fmt: skip
    # A project of metadata only names no folder.
    _keep(root, "C--work-empty", "3b3b3b3b-0000-4000-8000-000000000000", _line(type="x"), 8_000)
    sessions = plugin_module("sessions")
    read = sessions._read_lite
    files: list[str] = []

    def counted(file: Path) -> tuple[float, str, str] | None:
        files.append(file.stem)
        return read(file)

    monkeypatch.setattr(sessions, "_read_lite", counted)
    executor = claude_code(sessions=root)

    folders = asyncio.run(executor.folders())

    assert [(f.folder, f.updated_at) for f in folders] == [
        (Path("C:\\work\\api"), "1970-01-01T01:23:20.000Z"),
        (Path("C:\\work\\shop"), "1970-01-01T00:16:42.000Z"),
    ]
    assert sorted(files) == sorted(
        ["2b2b2b2b-0000-4000-8000-000000000000", "1b1b1b1b-0000-4000-8000-000000000000",
         f"{shop}2", "3b3b3b3b-0000-4000-8000-000000000000"]
    ), "one file a folder, and the side chain passed over"  # fmt: skip

    files.clear()
    found = asyncio.run(executor.sessions(folder=Path("C:\\work\\shop")))

    assert [s.id for s in found] == [f"{shop}2", f"{shop}1", f"{shop}0"]
    assert {s.folder for s in found} == {Path("C:\\work\\shop")}
    assert all(not stem.startswith("2b2b") for stem in files), "another folder is not read"
    assert asyncio.run(executor.sessions(folder=Path("C:\\work\\none"))) == []
    assert claude_code().capabilities().folders


def test_a_long_folders_sessions_are_found_under_claude_codes_own_hash(tmp_path: Path) -> None:
    """Claude Code under Bun hashes a long path another way than the SDK does (the SDK's
    `_find_project_dir`): its project is found by the first 200 characters; a session that
    recorded no `cwd` is that folder's."""
    root = tmp_path / "claude"
    sessions = plugin_module("sessions")
    long = "C:\\" + "deep\\" * 50 + "app"
    named = sessions.project_folder(long)[:200] + "-bunhash"
    _keep(
        root, named, "4b4b4b4b-0000-4000-8000-000000000000",
        _line(type="user", message={"role": "user", "content": "Deep work."}), 1_000,
    )  # fmt: skip

    [found] = asyncio.run(claude_code(sessions=root).sessions(folder=Path(long)))

    assert (found.title, found.folder) == ("Deep work.", Path(long))


def test_a_title_is_the_first_prompt_when_claude_code_kept_none(tmp_path: Path) -> None:
    root = tmp_path / "claude"
    prompt = "Explain the cart. " * 20
    _keep(
        root,
        "C--work",
        SESSION_ID,
        _line(type="user", cwd="C:\\work", message={"role": "user", "content": [
            {"type": "text", "text": "<command-name>/clear</command-name>"}]})
        + _line(type="user", cwd="C:\\work", message={"role": "user", "content": prompt}),
        1_000,
    )  # fmt: skip

    [found] = asyncio.run(claude_code(sessions=root).sessions())

    assert found.title == prompt.strip()[:200].rstrip() + "…"


def test_a_session_reads_as_its_chain_from_the_latest_leaf(tmp_path: Path) -> None:
    root = tmp_path / "claude"
    _keep(root, "C--work-shop", SESSION_ID, SESSION_FILE.read_text(encoding="utf-8"), 1_000)
    executor = claude_code(sessions=root)

    read = asyncio.run(executor.transcript(SESSION_ID, Path("C:\\work\\shop")))
    expired = asyncio.run(executor.transcript("0a1b2c3d-0000-4000-8000-000000000000", tmp_path))
    sneaky = asyncio.run(executor.transcript(f"../{SESSION_ID}", tmp_path))

    # The rewound reply, the meta line, the side chain and the thinking are not the conversation.
    assert [(e.kind, e.text, e.tool, e.by, e.call, e.failed) for e in read.entries] == [
        ("text", "Make the cart total show cents.", "", "user", "", False),
        ("tool", '{"file_path": "C:\\\\work\\\\shop\\\\cart.js"}', "Read", "harness", "toolu_01",
         False),
        ("result", "1\tconst total = sum(items)", "", "harness", "toolu_01", False),
        ("text", "Fixed: the total is shown with two decimals.", "", "harness", "", False),
        ("text", "Now run the tests.", "", "user", "", False),
        ("tool", '{"command": "npm test", "description": "Run the tests"}', "Bash", "harness",
         "toolu_02", False),
        ("result", "1 failing: total rounds 0.1 + 0.2", "", "harness", "toolu_02", True),
        ("question", "Fix the test or the code?", "AskUserQuestion", "harness", "toolu_03", False),
        ("answer", 'User has answered your questions: "Fix the test or the code?"="The code".',
         "", "harness", "toolu_03", False),
        ("text", "The code rounds now; the test passes.", "", "harness", "", False),
    ]  # fmt: skip
    assert read.entries[0].at == "2026-10-14T10:00:02.000Z"
    assert not read.expired
    assert expired.expired
    assert expired.entries == ()
    assert sneaky.expired


def test_a_session_begun_in_a_terminal_is_listed_and_continued_with_resume(
    script: Script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "claude"
    work = tmp_path / "work"
    work.mkdir()
    session = "7d3e5f60-1111-4222-8333-944455556666"
    monkeypatch.setenv("FAKE_CLAUDE_PROJECTS", str(root / "projects"))
    monkeypatch.setenv("FAKE_CLAUDE_SESSION", session)
    script.write({"say": "Added it."}, {"result": "Added a README."})
    # The user's own terminal: `claude` run by hand, outside Atlas.
    terminal = subprocess.run(  # noqa: S603 - the fake claude, run as a terminal runs it
        [*FAKE, "--output-format", "stream-json", "--input-format", "stream-json",
         "--permission-mode", "default"],
        input=json.dumps({"type": "user", "message": {"role": "user", "content": "Add a README."}}),
        capture_output=True, text=True, cwd=work, timeout=60, check=True,
    )  # fmt: skip
    assert '"result": "Added a README."' in terminal.stdout
    executor = claude_code(sessions=root)

    [listed] = asyncio.run(executor.sessions())
    assert (listed.id, listed.folder, listed.title) == (session, work, "Add a README.")

    script.write({"say": "Went on."}, {"result": "Added a licence too."})
    host = _run(work, Host(), "Add a licence.", spec=ExecutorSpec(work, resume=session))

    started = next(r for r in reversed(script.received()) if "argv" in r)
    assert f"--resume={session}" in started["argv"]
    assert Started(session, "fake-opus", "none", ("compact", "review")) in host.events
    read = asyncio.run(executor.transcript(session, work))
    said = [(e.by, e.text) for e in read.entries if e.kind == "text"]
    assert said == [
        ("user", "Add a README."),
        ("harness", "Added it."),
        ("user", "Add a licence."),
        ("harness", "Went on."),
    ]
    assert len(list((root / "projects").glob("*/*.jsonl"))) == 1, "the same session, one file"


def test_a_compaction_is_heard_live_and_read_back_as_its_boundary(
    script: Script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "claude"
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("FAKE_CLAUDE_PROJECTS", str(root / "projects"))
    script.write(
        {"say": "Reading it all."}, {"compact": [967_713, 13_603]}, {"say": "Went on."},
        {"result": "Done."},
    )  # fmt: skip

    host = _run(work, Host(), "Port the whole module.")

    assert Compacted(967_713, 13_603) in host.events
    assert Compacted(967_713, 13_603).text == (
        "The conversation was compacted from about 967,713 to 13,603 tokens."
    )
    read = asyncio.run(claude_code(sessions=root).transcript("fake-session-0001", work))
    # Its summary is the model's, not the user's: the boundary stands for it and what came before.
    assert [(e.kind, e.by, e.text) for e in read.entries] == [
        ("compacted", "harness", Compacted(967_713, 13_603).text),
        ("text", "harness", "Went on."),
    ]


def test_auto_and_manual_differ_only_in_the_mode_and_atlas_adds_no_rule() -> None:
    protocol = plugin_module("protocol")

    manual = protocol.command(["claude"])
    auto = protocol.command(["claude"], auto=True)

    assert manual[manual.index("--permission-mode") + 1] == "default"
    assert auto[auto.index("--permission-mode") + 1] == "auto"
    differ = [(m, a) for m, a in zip(manual, auto, strict=True) if m != a]
    assert differ == [("default", "auto")]
    assert "--settings" not in auto


def test_a_compaction_says_what_its_harness_said() -> None:
    protocol = plugin_module("protocol")

    manual = {"trigger": "manual", "preTokens": 50_000}
    assert protocol.compacted_of(manual) == Compacted(50_000, None, automatic=False)
    assert protocol.compacted_of(None) == Compacted(0)
    assert Compacted(50_000).text == "The conversation was compacted from about 50,000 tokens."


def test_the_project_folder_is_named_as_claude_code_names_it() -> None:
    sessions = plugin_module("sessions")

    assert sessions.project_folder("C:\\work\\shop") == "C--work-shop"
    assert sessions.project_folder("/home/x/Ação") == "-home-x-A--o", "only ASCII is kept"
    long = "C:\\" + "Ação\\" * 60 + "fim"
    # Cut at 200, then the hash JavaScript's own code gives for this path (Node 24 running the
    # SDK's `simpleHash`).
    assert sessions.project_folder(long) == sessions.project_folder(long)[:200] + "-e0ypdn"


def test_its_models_and_efforts_are_listed_without_a_prompt(script: Script, tmp_path: Path) -> None:
    script.write({"result": "Never run."})

    models = asyncio.run(claude_code().models())

    assert [(m.id, m.name, m.efforts, m.resolved) for m in models] == [
        (
            "default",
            "Default (recommended)",
            ("low", "medium", "high", "xhigh", "max"),
            "fake-opus",
        ),
        ("sonnet", "Fake Sonnet", ("low", "medium", "high"), "fake-sonnet"),
        ("haiku", "Fake Haiku", (), "fake-haiku"),
    ]
    received = script.received()
    assert not [r for r in received if r.get("type") == "user"], "no prompt, no model call"
    assert received[0]["cwd"] == str(Path.home())


def test_the_model_and_effort_start_with_it_and_switch_mid_session(
    script: Script, tmp_path: Path
) -> None:
    script.write({"say": "Working."}, {"result": "Done."})
    host = Host()
    refused: list[str] = []

    async def scenario() -> None:
        spec = ExecutorSpec(tmp_path, model="sonnet", effort="high")
        instance = await claude_code().start(spec, host)
        try:
            await instance.prompt("First.")
            await instance.set_model("haiku", None)
            await instance.prompt("Second.")
            try:
                await instance.set_model("haiku", "high")
            except ExecutorError as error:
                refused.append(str(error))
        finally:
            await instance.stop()

    asyncio.run(scenario())

    ran = [event.model for event in host.events if isinstance(event, Started) and event.model]
    assert ran == ["fake-sonnet", "fake-haiku"]
    received = script.received()
    argv = received[0]["argv"]
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--effort") + 1] == "high"
    requests = [r["request"] for r in received if "request" in r]
    assert {"subtype": "set_model", "model": "haiku"} in requests
    assert {"subtype": "apply_flag_settings", "settings": {"effortLevel": None}} in requests
    # Claude Code would take the effort and drop it quietly: Atlas refuses it first.
    assert refused == ["Fake Haiku does not take the effort 'high' (it takes none)."]
    assert len([r for r in requests if r.get("subtype") == "set_model"]) == 1


def test_an_effort_the_model_does_not_take_is_refused_before_it_starts(
    script: Script, tmp_path: Path
) -> None:
    script.write({"result": "Done."})
    executor = claude_code()
    asyncio.run(executor.models())

    with pytest.raises(ExecutorError, match="Fake Sonnet does not take the effort 'max'"):
        asyncio.run(executor.start(ExecutorSpec(tmp_path, model="sonnet", effort="max"), Host()))
    assert len([r for r in script.received() if "argv" in r]) == 1, "only the list was asked"


def test_slash_commands_come_with_initialize_and_init() -> None:
    protocol = plugin_module("protocol")

    assert protocol.commands_of(["compact", "/review", "", 3]) == ("compact", "review")
    assert protocol.commands_of([{"name": "/init", "description": "x"}, {"name": "init"}]) == (
        "init",
    )
    assert protocol.commands_of(None) == ()


def test_its_connectors_are_listed_once_the_accounts_have_come(
    script: Script, tmp_path: Path
) -> None:
    script.write({"result": "Never run."})

    found = asyncio.run(claude_code().connectors())

    assert found == [
        Connector("files", "connected", "user", tools=2),
        Connector("slack", "needs-auth", "plugin", "marketing"),
        Connector("gmail", "failed", "plugin", "design", error="No URL configured for this server"),
        Connector("Google Drive", "connected", "account", tools=1),
        Connector("Gmail", "needs-auth", "account"),
    ]
    received = script.received()
    asked = [r["request"]["subtype"] for r in received if r.get("type") == "control_request"]
    assert asked == ["initialize", "mcp_status", "mcp_status", "mcp_status"], (
        "asked again while the account's were missing, then while one was pending"
    )
    assert not [r for r in received if r.get("type") == "user"], "no prompt, no model call"
    assert received[0]["cwd"] == str(Path.home())


def test_a_login_with_an_api_key_has_no_account_connectors_to_wait_for(
    script: Script, monkeypatch: pytest.MonkeyPatch
) -> None:
    script.write({"result": "Never run."})
    monkeypatch.setenv("FAKE_CLAUDE_SUBSCRIPTION", "")

    found = asyncio.run(claude_code().connectors())

    assert [c.name for c in found] == ["files", "slack", "gmail"]
    asked = [r for r in script.received() if r.get("type") == "control_request"]
    assert len(asked) == 2, "one mcp_status: nothing more to come"


def test_connectors_still_pending_at_the_deadline_are_given_as_pending(
    script: Script, monkeypatch: pytest.MonkeyPatch
) -> None:
    script.write({"result": "Never run."})
    executor = plugin_module("executor")
    # The second answer, with the account's and one pending, is the last before the deadline.
    monkeypatch.setattr(executor, "CONNECTORS_SECONDS", 0.9)

    found = asyncio.run(claude_code().connectors())

    assert ("Google Drive", "pending") in [(c.name, c.status) for c in found]


def test_connectors_are_read_as_claude_codes_mcp_lists_them() -> None:
    protocol = plugin_module("protocol")

    # Shapes Claude Code 2.1.296 answered on 16/10/2026, and what it might answer next.
    assert protocol.connectors_of(
        [
            {"name": "plugin:marketing:google calendar", "status": "failed", "scope": "dynamic",
             "source": "plugin", "error": "No URL configured for this server"},
            {"name": "claude.ai Canva", "status": "connected", "scope": "claudeai",
             "source": "claudeai", "tools": [{"name": "a"}, {"name": "b"}, {"name": "c"}]},
            {"name": "db", "status": "disabled", "scope": "project"},
            {"name": "corp", "status": "connected", "scope": "enterprise"},
            {"name": "odd", "status": "restarting", "scope": "local"},
            {"name": "", "status": "connected"},
            "not a server",
        ]
    ) == (
        Connector("google calendar", "failed", "plugin", "marketing",
                  error="No URL configured for this server"),
        Connector("Canva", "connected", "account", tools=3),
        Connector("db", "disabled", "project"),
        Connector("corp", "connected", "managed"),
        Connector("odd", "failed", "local", error="restarting"),
    )  # fmt: skip
    assert protocol.connectors_of(None) == ()
    assert protocol.hosts_connectors({"subscriptionType": "Claude Team"})
    assert not protocol.hosts_connectors({"apiProvider": "firstParty"})
    assert not protocol.hosts_connectors(None)


# -- the plan's limits (version 9) ----------------------------------------------------------------


def test_its_plans_usage_is_read_as_its_usage_screen_shows_it(script: Script) -> None:
    script.write({"result": "Never run."})

    found = asyncio.run(claude_code().usage())

    assert found == PlanUsage(
        (
            UsageWindow("five_hour", "Current session", 45.0, "2026-10-18T19:10:00.378Z", 300, ""),
            UsageWindow(
                "seven_day", "Current week (all models)", 25.0, "2026-10-23T08:00:00.378Z", 10_080
            ),
            UsageWindow(
                "model:Fable",
                "Current week (Fable)",
                60.0,
                "2026-10-23T08:00:00.000Z",
                10_080,
                "Fable",
            ),
        ),
        "max",
        extra_usage=False,
    ), "the code-named windows it does not show are left out, as its /usage leaves them"
    received = script.received()
    asked = [r["request"] for r in received if r.get("type") == "control_request"]
    assert asked[1] == {"subtype": "get_usage", "skip_behaviors": True}, (
        "no scan of the week's transcripts"
    )
    assert not [r for r in received if r.get("type") == "user"], "no prompt, no model call"


def test_a_login_with_an_api_key_has_no_plan_limits(
    script: Script, monkeypatch: pytest.MonkeyPatch
) -> None:
    script.write({"result": "Never run."})
    monkeypatch.setenv("FAKE_CLAUDE_SUBSCRIPTION", "")

    found = asyncio.run(claude_code().usage())

    assert found.windows == ()
    assert "API key" in found.unavailable


def test_a_claude_code_that_refuses_the_request_says_where_the_usage_shows(
    script: Script, monkeypatch: pytest.MonkeyPatch
) -> None:
    script.write({"result": "Never run."})
    monkeypatch.setenv("FAKE_CLAUDE_USAGE", "refuse")

    with pytest.raises(ExecutorError, match="once a session's turn runs"):
        asyncio.run(claude_code().usage())


def test_a_turn_tells_the_plans_usage_after_each_request(script: Script, tmp_path: Path) -> None:
    script.write({"usage": [0.46, 0.255]}, {"say": "Done."}, {"result": "Done."})

    host = _run(tmp_path, Host(), "Go.")

    told = [event for event in host.events if isinstance(event, UsageChanged)]
    assert told == [
        UsageChanged(
            (
                UsageWindow("five_hour", "Current session", 46.0, "2026-10-18T19:10:00.000Z", 300),
                UsageWindow(
                    "seven_day",
                    "Current week (all models)",
                    25.5,
                    "2026-10-23T08:00:00.000Z",
                    10_080,
                ),
            )
        )
    ]
    assert not [event for event in host.events if isinstance(event, LimitReached)]


def test_a_rate_limit_event_is_read_in_either_shape() -> None:
    protocol = plugin_module("protocol")

    older = protocol.events_of(
        {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "rejected",
                "resetsAt": 1792350600,
                "rateLimitType": "five_hour",
                "utilization": 1.0,
            },
        }
    )
    assert older == [
        UsageChanged(
            (UsageWindow("five_hour", "Current session", 100.0, "2026-10-18T19:10:00.000Z", 300),)
        ),
        LimitReached(1792350600),
    ]
    assert (
        protocol.events_of({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}})
        == []
    )
    assert protocol.windows_of_event({"unifiedWindows": {"tangelo": {"utilization": 0.5}}}) == ()
    assert protocol.usage_of({"rate_limits": {"five_hour": {"utilization": "x"}}}) == PlanUsage()
    on = {"rate_limits": {"extra_usage": {"is_enabled": True}}}
    assert protocol.usage_of(on).extra_usage is True, "past the limits it bills"
    unsaid = {"rate_limits": {"extra_usage": None}}
    assert protocol.usage_of(unsaid).extra_usage is None, "not known, not assumed off"


# -- slash commands (version 10) ------------------------------------------------------------------


def test_its_commands_in_a_folder_are_read_with_no_session(script: Script, tmp_path: Path) -> None:
    """Before a session runs there, a Claude Code started in the folder says them: its own, the
    user's, and the project's skills."""
    script.write({"result": "Never run."})
    (tmp_path / ".claude" / "skills" / "deploy").mkdir(parents=True)

    found = asyncio.run(claude_code().commands(folder=tmp_path))

    assert found == [*COMMANDS, SlashCommand("deploy", "The deploy skill (project)")]
    received = script.received()
    assert received[0]["cwd"] == str(tmp_path)
    assert not [r for r in received if r.get("type") == "user"], "no prompt, no model call"


def test_its_commands_are_said_again_when_they_change(script: Script, tmp_path: Path) -> None:
    script.write({"commands_changed": ["lint"]}, {"result": "Done."})

    host = _run(tmp_path, Host(), "Go.")

    changed = [event for event in host.events if isinstance(event, CommandsChanged)]
    assert changed == [
        CommandsChanged(COMMANDS),
        CommandsChanged((*COMMANDS, SlashCommand("lint", "The lint skill"))),
    ]


def test_a_command_it_runs_itself_answers_with_its_words_and_reads_back_as_typed(
    script: Script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/context` is Claude Code's own: its output, no model; read back, the session says the
    command as the user typed it and the output after it, its last line included."""
    projects = tmp_path / "projects"
    monkeypatch.setenv("FAKE_CLAUDE_PROJECTS", str(projects))
    script.write({"say": "Hello."}, {"result": "Hello."})
    folder = tmp_path / "work"
    folder.mkdir()

    host = _run(folder, Host(), "Hi.", "/context")

    said = [event.text for event in host.events if isinstance(event, Said)]
    assert said == ["Hello.", "## Context Usage\n\n**Tokens:** 20.1k / 1m (2%)"]
    finished = [event for event in host.events if isinstance(event, Finished)]
    assert finished[-1].turns == 0
    transcript = asyncio.run(
        plugin_module("executor")
        .ClaudeCode(None, logger=logging.getLogger("test"), sessions=tmp_path)
        .transcript("fake-session-0001", folder)
    )
    assert [(e.kind, e.by, e.text) for e in transcript.entries][-2:] == [
        ("text", "user", "/context"),
        ("text", "harness", "## Context Usage\n\n**Tokens:** 20.1k / 1m (2%)"),
    ]


def test_slash_commands_are_read_as_claude_code_writes_them() -> None:
    protocol = plugin_module("protocol")

    assert protocol.slash_commands_of(
        [
            {"name": "/review", "description": "A skill of the user's"},
            {"name": "review", "description": "Its own", "builtin": True},
            {"name": "review", "description": "Another"},
            "init",
            {"name": ""},
            3,
        ]
    ) == (SlashCommand("review", "Its own", builtin=True), SlashCommand("init"))
    assert protocol.slash_commands_of(None) == ()
    assert protocol.events_of(
        {"type": "system", "subtype": "local_command_output", "content": "On."}
    ) == [Said("On.")]
    assert (
        protocol.events_of({"type": "system", "subtype": "local_command_output", "content": " "})
        == []
    )


def test_a_command_and_its_output_read_back_from_claude_codes_own_lines(tmp_path: Path) -> None:
    """As 2.1.296 keeps them: the command's tags with its arguments, a skill's own text hidden,
    and the output of a command run by Claude Code itself on a `system` line below."""
    transcript = plugin_module("transcript")
    folder = tmp_path / "projects" / "p"
    folder.mkdir(parents=True)
    lines = [
        {"type": "user", "uuid": "a", "parentUuid": None, "message": {"role": "user", "content": (
            "<command-message>roadmap</command-message>\n<command-name>/roadmap</command-name>\n"
            "<command-args>executar C-02</command-args>")}},
        {"type": "user", "uuid": "b", "parentUuid": "a", "isMeta": True,
         "message": {"role": "user", "content": [{"type": "text", "text": "The skill's body."}]}},
        {"type": "assistant", "uuid": "c", "parentUuid": "b",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]}},
        {"type": "user", "uuid": "d", "parentUuid": "c", "isMeta": True,
         "message": {"role": "user", "content": "<local-command-caveat>C</local-command-caveat>"}},
        {"type": "user", "uuid": "e", "parentUuid": "d", "message": {"role": "user", "content": (
            "<command-name>/mcp</command-name>\n<command-message>mcp</command-message>\n"
            "<command-args></command-args>")}},
        {"type": "system", "subtype": "local_command", "uuid": "f", "parentUuid": "e",
         "content": "<local-command-stdout>3 MCP servers.</local-command-stdout>"},
        {"type": "system", "subtype": "local_command", "uuid": "g", "parentUuid": "f",
         "content": "<local-command-stdout></local-command-stdout>"},
    ]  # fmt: skip
    file = folder / "s1.jsonl"
    file.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")

    read = transcript.read_transcript(tmp_path, "s1", Path("p"))

    assert [(e.by, e.text) for e in read.entries] == [
        ("user", "/roadmap executar C-02"),
        ("harness", "Done."),
        ("user", "/mcp"),
        ("harness", "3 MCP servers."),
    ]


def test_a_message_sent_while_claude_code_works_reads_back_where_it_entered(
    tmp_path: Path,
) -> None:
    """As Claude Code keeps a prompt sent mid-turn: an `attachment` line of type `queued_command`
    in the chain, not a user line. Read again (a tab switched back to), it must stay where it
    entered, or Claude Code's answer to it reads as an answer to nothing."""
    transcript = plugin_module("transcript")
    folder = tmp_path / "projects" / "p"
    folder.mkdir(parents=True)
    lines = [
        {"type": "user", "uuid": "a", "parentUuid": None,
         "message": {"role": "user", "content": "build it"}},
        {"type": "assistant", "uuid": "b", "parentUuid": "a",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "On it."}]}},
        {"type": "attachment", "uuid": "c", "parentUuid": "b",
         "attachment": {"type": "queued_command", "prompt": "stuck?", "commandMode": "prompt"}},
        {"type": "queue-operation", "operation": "remove", "content": "stuck?"},
        {"type": "attachment", "uuid": "d", "parentUuid": "c",
         "attachment": {"type": "queued_command", "prompt": "/x", "commandMode": "bash"}},
        {"type": "assistant", "uuid": "e", "parentUuid": "d",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Not stuck."}]}},
    ]  # fmt: skip
    file = folder / "s1.jsonl"
    file.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")

    read = transcript.read_transcript(tmp_path, "s1", Path("p"))

    assert [(e.kind, e.by, e.text) for e in read.entries] == [
        ("text", "user", "build it"),
        ("text", "harness", "On it."),
        ("text", "user", "stuck?"),
        ("text", "harness", "Not stuck."),
    ]


def test_a_compact_the_user_typed_reads_back_as_its_compaction_where_they_typed_it(
    tmp_path: Path,
) -> None:
    """As 2.1.302 keeps a `/compact`: the boundary roots the chain, above the summary, and the
    command's output is a bare "Compacted". Asked for by the user on 22/10/2026: how many tokens
    became how many reads below the command, in that output's place."""
    transcript = plugin_module("transcript")
    folder = tmp_path / "projects" / "p"
    folder.mkdir(parents=True)
    meta = {"trigger": "manual", "preTokens": 128_584, "postTokens": 4_718}
    lines = [
        {"type": "system", "subtype": "compact_boundary", "uuid": "a", "parentUuid": None,
         "content": "Conversation compacted", "compactMetadata": meta},
        {"type": "user", "uuid": "b", "parentUuid": "a", "isCompactSummary": True,
         "message": {"role": "user", "content": "This session is being continued..."}},
        {"type": "user", "uuid": "c", "parentUuid": "b", "message": {"role": "user", "content": (
            "<command-name>/compact</command-name>\n<command-message>compact</command-message>\n"
            "<command-args></command-args>")}},
        {"type": "user", "uuid": "d", "parentUuid": "c", "message": {
            "role": "user", "content": "<local-command-stdout>Compacted </local-command-stdout>"}},
    ]  # fmt: skip
    file = folder / "s1.jsonl"
    file.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")

    read = transcript.read_transcript(tmp_path, "s1", Path("p"))

    said = "The conversation was compacted from about 128,584 to 4,718 tokens."
    assert [(e.kind, e.by, e.text) for e in read.entries] == [
        ("text", "user", "/compact"),
        ("compacted", "harness", said),
    ]
    assert read.context == 4_718, "what the next message reads again"


def test_a_sessions_context_is_its_last_replys_usage(tmp_path: Path) -> None:
    transcript = plugin_module("transcript")
    folder = tmp_path / "projects" / "p"
    folder.mkdir(parents=True)
    usage = {
        "input_tokens": 3,
        "cache_creation_input_tokens": 500,
        "cache_read_input_tokens": 90_000,
        "output_tokens": 200,
    }
    lines = [
        {"type": "user", "uuid": "a", "parentUuid": None,
         "message": {"role": "user", "content": "Hi."}},
        {"type": "assistant", "uuid": "b", "parentUuid": "a", "message": {
            "role": "assistant", "content": [{"type": "text", "text": "Hello."}], "usage": usage}},
        {"type": "user", "uuid": "c", "parentUuid": "b",
         "message": {"role": "user", "content": "And?"}},
    ]  # fmt: skip
    file = folder / "s1.jsonl"
    file.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")

    assert transcript.read_transcript(tmp_path, "s1", Path("p")).context == 90_703
    (folder / "s2.jsonl").write_text(json.dumps(lines[0]), encoding="utf-8")
    assert transcript.read_transcript(tmp_path, "s2", Path("p")).context is None


#: The smallest PNG there is: one transparent pixel.
PIXEL = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


def test_attached_files_go_as_the_sdk_sends_them_and_read_back_as_files(
    script: Script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Step 16i: an image as an `image` block, as the SDK's streaming input sends one; every file
    by its path in the plugin's note, for Claude Code's Read; the home's attachments read as its
    own; and the session read back with its files, the note not the user's words."""
    root = tmp_path / "claude"
    work = tmp_path / "work"
    work.mkdir()
    kept = tmp_path / "home" / "attachments"
    (kept / "a1").mkdir(parents=True)
    (kept / "b2").mkdir(parents=True)
    shot = kept / "a1" / "shot.png"
    shot.write_bytes(PIXEL)
    report = kept / "b2" / "report (final).pdf"
    report.write_bytes(b"%PDF-1.7\n")
    monkeypatch.setenv("FAKE_CLAUDE_PROJECTS", str(root / "projects"))
    script.write({"result": "Seen."})
    files = [
        ExecutorFile("shot.png", "image/png", shot),
        ExecutorFile("report (final).pdf", "application/pdf", report),
    ]

    async def scenario() -> None:
        instance = await claude_code().start(ExecutorSpec(work, directories=(kept,)), Host())
        try:
            await instance.prompt("What is wrong here?", files=files)
            await instance.prompt("", files=files[1:])
        finally:
            await instance.stop()

    asyncio.run(scenario())

    received = script.received()
    argv = received[0]["argv"]
    assert argv[argv.index("--add-dir") + 1] == str(kept)
    first, second = [r["message"]["content"] for r in received if r.get("type") == "user"]
    assert first[0] == {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(PIXEL).decode("ascii"),
        },
    }
    note = first[1]["text"]
    assert note.startswith("<atlas-attachments>")
    assert "Read tool" in note
    assert f"- {shot} (image/png, shown above)" in note
    assert f"- {report} (application/pdf)" in note
    assert first[2] == {"type": "text", "text": "What is wrong here?"}
    assert [block["type"] for block in second] == ["text"], "no words, no image: the note alone"

    read = asyncio.run(claude_code(sessions=root).transcript("fake-session-0001", work))
    users = [(e.text, e.files) for e in read.entries if e.by == "user"]
    assert users == [
        ("What is wrong here?", (str(shot), str(report))),
        ("", (str(report),)),
    ]


def test_a_prompt_with_no_files_is_its_words_alone_and_no_folder_is_added() -> None:
    protocol = plugin_module("protocol")

    assert protocol.user_message("Hi.")["message"]["content"] == "Hi."
    assert "--add-dir" not in protocol.command(["claude"])
    assert protocol.attached_paths("<atlas-attachments> by hand") is None
