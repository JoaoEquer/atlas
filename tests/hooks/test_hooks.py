"""Hooks (step 16k): Claude Code's `hooks.json` read, matched and answered as Claude Code does, run
as real commands on the event's JSON, inside real turns, and brought by real plugins.

The command hooks are Python scripts run by this interpreter, in exec form, so they run the same on
every system; one test runs a shell-form hook through bash where bash is installed.
"""

import asyncio
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from atlas.__main__ import SCHEMAS
from atlas.agent import AgentLoop, Tool, TurnOutcome
from atlas.home import Database, Home, migrate
from atlas.hooks import (
    Answer,
    CommandHook,
    ConversationHooks,
    HookConfigError,
    Hooks,
    PluginCallback,
    PluginCommand,
    parse_hooks,
)
from atlas.hooks.model import combined, matches, read_answer, read_object, rule_matches
from atlas.hooks.service import _git_bash  # pyright: ignore[reportPrivateUsage]
from atlas.models import Message, SystemMessage, ToolResult, UserMessage
from atlas.plugins import Plugins
from atlas.secrets import Vault
from fake_model import FakeCall, FakeModelServer, FakeReply
from model_support import Connect, Run

CHAT = "/v1/chat/completions"

GUARD = """
import json, os, sys

event = json.load(sys.stdin)
name = event["hook_event_name"]
with open(os.path.join(os.environ["CLAUDE_PLUGIN_DATA"], "seen.txt"), "a") as seen:
    seen.write(name + " " + event.get("permission_mode", "") + "\\n")


def say(**fields):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": name, **fields}}))


if name == "PreToolUse":
    command = event["tool_input"].get("command", "")
    if command.startswith("rm "):
        print("rm is not allowed here", file=sys.stderr)
        sys.exit(2)
    if command.startswith("deploy"):
        say(permissionDecision="ask")
    if command == "echo old":
        say(updatedInput={"command": "echo new"})
elif name == "PostToolUse":
    say(additionalContext="checked by the guard")
elif name == "UserPromptSubmit":
    if "secret" in event["prompt"]:
        print(json.dumps({"decision": "block", "reason": "no secrets in this chat"}))
    else:
        print("The user likes short answers.")
elif name == "Stop":
    if not event["stop_hook_active"]:
        print(json.dumps({"decision": "block", "reason": "run the tests first"}))
elif name == "SessionStart":
    print("Today is a holiday.")
"""


def _guard(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / "guard.py"
    script.write_text(GUARD, encoding="utf-8")
    return script


def _hooks_json(matcher: str = "") -> dict[str, Any]:
    hook = {
        "type": "command",
        "command": sys.executable,
        "args": ["${CLAUDE_PLUGIN_ROOT}/guard.py"],
    }
    return {
        event: [{"matcher": matcher, "hooks": [hook]}]
        for event in ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop")
    }


# -- reading hooks.json --------------------------------------------------------------------------


def test_hooks_json_is_read_as_claude_code_writes_it() -> None:
    hooks = parse_hooks(
        {
            "PreToolUse": [
                {
                    "matcher": "Bash|Write",
                    "hooks": [
                        {"type": "command", "command": "check.sh", "timeout": 5},
                        {"command": "node", "args": ["a.js"], "onFailure": "block"},
                    ],
                }
            ],
            "Stop": [{"matcher": "ignored", "hooks": [{"type": "command", "command": "x"}]}],
            "SubagentStop": [{"hooks": [{"type": "command", "command": "later"}]}],
        },
        "hooks.json",
    )
    assert hooks[0] == CommandHook("PreToolUse", "Bash|Write", "check.sh", None, 5.0)
    assert (hooks[1].args, hooks[1].fail_closed, hooks[1].seconds()) == (("a.js",), True, 600.0)
    assert hooks[2].matcher == "", "Stop takes no matcher"
    assert hooks[3].summary() == "`later` on SubagentStop, which Atlas does not fire yet"
    assert not hooks[3].runs


@pytest.mark.parametrize(
    ("document", "problem"),
    [
        ([], "object of events"),
        ({"OnSave": []}, "does not know: 'OnSave'"),
        ({"Stop": {}}, "list of matcher groups"),
        ({"Stop": [{"hooks": [{"type": "prompt", "prompt": "x"}]}]}, "type 'prompt'"),
        ({"Stop": [{"hooks": [{"type": "command"}]}]}, "needs a command"),
        ({"Stop": [{"hooks": [{"command": "x", "args": "y"}]}]}, "list of texts"),
        ({"Stop": [{"hooks": [{"command": "x", "timeout": 0}]}]}, "above 0"),
        ({"Stop": [{"hooks": [{"command": "x", "shell": "zsh"}]}]}, "bash or powershell"),
        ({"Stop": [{"hooks": [{"command": "x", "if": "Bash(git"}]}]}, "permission rule"),
        ({"PreToolUse": [{"matcher": "(", "hooks": []}]}, "no pattern"),
    ],
)
def test_a_hooks_json_atlas_cannot_run_says_what_to_fix(document: object, problem: str) -> None:
    with pytest.raises(HookConfigError, match=problem):
        parse_hooks(document, "hooks.json")


@pytest.mark.parametrize(
    ("matcher", "value", "matched"),
    [
        ("", "anything", True),
        ("*", "anything", True),
        ("run_command", "run_command", True),
        ("Bash", "run_command", True),  # Claude Code's name for Atlas's tool
        ("Edit|Write", "write_file", True),
        ("Edit, Read", "read_file", True),
        ("Read", "run_command", False),
        ("mcp__erp__.*", "mcp__erp__orders", True),
        ("^mcp__erp", "mcp__other__x", False),
        ("Notebook", "NotebookEdit", False),  # a plain name is matched whole, as in Claude Code
    ],
)
def test_the_matcher_is_claude_codes(matcher: str, value: str, matched: bool) -> None:
    assert matches(matcher, value) is matched


@pytest.mark.parametrize(
    ("rule", "tool", "arguments", "matched"),
    [
        ("", "run_command", {}, True),
        ("Bash", "run_command", {"command": "ls"}, True),
        ("Bash(git *)", "run_command", {"command": "git push"}, True),
        ("Bash(git *)", "run_command", {"command": "rm -rf /"}, False),
        ("Bash(npm run test:*)", "run_command", {"command": "npm run test -- -x"}, True),
        (
            "Bash(py* *security*scripts/*.py *)",
            "run_command",
            {"command": "python3 /p/security/scripts/a.py x"},
            True,
        ),
        ("Edit(*.py)", "edit_file", {"file_path": "src/a.py"}, True),
        ("Edit(*.py)", "edit_file", {"file_path": "README.md"}, False),
        ("Edit(*.py)", "run_command", {"command": "a.py"}, False),
        ("run_command(ls*)", "run_command", {"command": "ls -la"}, True),
    ],
)
def test_a_hook_s_if_is_claude_code_s_permission_rule(
    rule: str, tool: str, arguments: dict[str, Any], matched: bool
) -> None:
    assert rule_matches(rule, tool, arguments) is matched


def test_an_answer_is_read_as_claude_code_reads_it() -> None:
    assert read_answer("PreToolUse", 2, "", "no rm\n") == Answer(blocked="no rm")
    assert read_answer("PostToolUse", 2, "", "too long") == Answer(context="too long")
    assert read_answer("SessionStart", 2, "", "x") == Answer(error="x")
    assert read_answer("PreToolUse", 1, "", "boom\nmore") == Answer(error="It exited with 1: boom")
    assert read_answer("UserPromptSubmit", 0, "Be brief.", "") == Answer(context="Be brief.")
    assert read_answer("PreToolUse", 0, "plain text", "") == Answer(), "only some events read it"
    deny = {"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "No."}}
    assert read_object("PreToolUse", deny).blocked == "No."
    allow = {"hookSpecificOutput": {"permissionDecision": "allow"}}
    assert read_object("PreToolUse", allow) == Answer(), "allow lowers nothing"
    assert read_object("Stop", {"decision": "block", "reason": "Go on."}).blocked == "Go on."
    assert (
        read_object("PostToolUse", {"decision": "block", "reason": "Fix it."}).context == "Fix it."
    )
    assert read_object("UserPromptSubmit", {"continue": False}).blocked == "A hook stopped it."
    assert read_object("Stop", {"continue": False}).blocked is None
    replaced = {"hookSpecificOutput": {"updatedToolOutput": {"ok": True}}}
    assert read_object("PostToolUse", replaced).output == '{"ok": true}'
    assert read_object(
        "PostToolUse", {"hookSpecificOutput": {"additionalContext": "x" * 20_000}}
    ).context.endswith("…")
    together = combined([Answer(blocked="a"), Answer(ask=True, context="c"), Answer(blocked="b")])
    assert (together.blocked, together.ask, together.context) == ("a; b", True, "c")


# -- running commands ----------------------------------------------------------------------------


def _command(tmp_path: Path, event: str, **fields: Any) -> PluginCommand:
    root = tmp_path / "plugin"
    _guard(root)
    hook = CommandHook(event, "", sys.executable, ("${CLAUDE_PLUGIN_ROOT}/guard.py",), **fields)
    return PluginCommand("guard", hook, root, tmp_path / "data")


def test_a_command_hook_gets_the_event_on_stdin_and_its_answer_is_read(tmp_path: Path) -> None:
    hooks = Hooks([_command(tmp_path, "PreToolUse")])
    payload = {"hook_event_name": "PreToolUse", "permission_mode": "default"}

    async def scenario() -> list[Answer]:
        return [
            await hooks.run(
                "PreToolUse", {**payload, "tool_input": {"command": c}}, "run_command", cwd=tmp_path
            )
            for c in ("rm -rf /", "deploy prod", "echo old", "ls")
        ]

    denied, asked, rewritten, fine = asyncio.run(scenario())
    assert denied.blocked == "rm is not allowed here"
    assert asked.ask
    assert asked.blocked is None
    assert rewritten.arguments == {"command": "echo new"}
    assert fine == Answer()
    seen = (tmp_path / "data" / "seen.txt").read_text().splitlines()
    assert seen == ["PreToolUse default"] * 4, "CLAUDE_PLUGIN_DATA reached it"


def test_a_hook_that_takes_too_long_lets_it_go_unless_it_blocks_on_failure(tmp_path: Path) -> None:
    slow = tmp_path / "slow.py"
    slow.write_text("import time; time.sleep(5)", encoding="utf-8")

    def hook(**fields: Any) -> PluginCommand:
        command = CommandHook("PreToolUse", "", sys.executable, (str(slow),), 0.5, **fields)
        return PluginCommand("slow", command, tmp_path, tmp_path / "data")

    async def scenario(entry: PluginCommand) -> Answer:
        return await Hooks([entry]).run("PreToolUse", {}, "x", cwd=tmp_path)

    open_ = asyncio.run(scenario(hook()))
    assert open_.blocked is None
    assert "took more than 0.5 s" in str(open_.error)
    closed = asyncio.run(scenario(hook(fail_closed=True)))
    assert "took more than 0.5 s" in str(closed.blocked)
    missing = PluginCommand(
        "gone", CommandHook("PreToolUse", "", str(tmp_path / "nowhere.exe"), ()), tmp_path, tmp_path
    )
    assert "could not start" in str(asyncio.run(scenario(missing)).error)


def test_a_hook_gets_its_plugin_s_options_as_claude_code_gives_them(tmp_path: Path) -> None:
    """`CLAUDE_PLUGIN_OPTION_<KEY>` in its environment, for every option, and
    `${user_config.KEY}` in an exec-form hook's arguments."""
    script = tmp_path / "options.py"
    script.write_text(
        "import os, sys\nprint(os.environ['CLAUDE_PLUGIN_OPTION_API_TOKEN'] + ' ' + sys.argv[1])\n",
        encoding="utf-8",
    )
    hook = CommandHook(
        "UserPromptSubmit", "", sys.executable, (str(script), "--region=${user_config.region}")
    )
    entry = PluginCommand(
        "erp", hook, tmp_path, tmp_path / "data", {"api_token": "t0k", "region": "south"}
    )
    said = asyncio.run(Hooks([entry]).run("UserPromptSubmit", {"prompt": "x"}, cwd=tmp_path))
    assert said.context == "t0k --region=south"


def test_a_background_hook_never_holds_the_action_and_may_wake_the_model(tmp_path: Path) -> None:
    """Claude Code's `async`: the action goes on at once and the hook's answer is not read;
    with `asyncRewake`, a hook that exits 2 brings its stderr to the model as a notice. A hook
    whose `if` the call does not match does not run."""
    script = tmp_path / "slow_block.py"
    script.write_text(
        "import sys, time\ntime.sleep(0.5)\nprint('found a leak', file=sys.stderr)\nsys.exit(2)\n",
        encoding="utf-8",
    )

    def entry(**fields: Any) -> PluginCommand:
        hook = CommandHook("PostToolUse", "Bash", sys.executable, (str(script),), **fields)
        return PluginCommand("audit", hook, tmp_path, tmp_path / "data")

    woken: list[str] = []
    hooks = Hooks(
        [
            entry(background=True, rewake=True, rule="Bash(git *)"),
            entry(background=True, rule="Bash(git *)"),
            entry(background=True, rewake=True, rule="Bash(npm *)"),
        ]
    )

    async def scenario() -> tuple[Answer, float]:
        loop = asyncio.get_running_loop()
        started = loop.time()
        said = await hooks.run(
            "PostToolUse",
            {"tool_input": {"command": "git push"}},
            "run_command",
            cwd=tmp_path,
            wake=woken.append,
        )
        took = loop.time() - started
        for _ in range(100):
            if woken:
                break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.6)
        return said, took

    said, took = asyncio.run(scenario())
    assert said == Answer()
    assert took < 0.4, "the action did not wait for it"
    assert woken == ["[Hook of audit]\nfound a leak"], (
        "once: the other does not rewake; npm's never ran"
    )


@pytest.mark.skipif(
    (_git_bash() if sys.platform == "win32" else shutil.which("bash")) is None,
    reason="a shell-form hook needs bash (Git Bash on Windows)",
)
def test_a_shell_form_hook_runs_through_bash(tmp_path: Path) -> None:
    hook = CommandHook("UserPromptSubmit", "", 'read -r line; echo "from bash" ; exit 0')
    entry = PluginCommand("sh", hook, tmp_path, tmp_path / "data")
    said = asyncio.run(Hooks([entry]).run("UserPromptSubmit", {"prompt": "x"}, cwd=tmp_path))
    assert said.context == "from bash"


def test_a_python_hook_answers_as_a_command_does(tmp_path: Path) -> None:
    async def later(event: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(0)
        return {
            "hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "no"}
        }

    def broken(event: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("boom")

    hooks = Hooks(
        callbacks=[
            PluginCallback("p", "PreToolUse", "Bash", later),
            PluginCallback("p", "PreToolUse", "", broken),
            PluginCallback("p", "PreToolUse", "Read", lambda event: {"decision": "block"}),
        ]
    )
    said = asyncio.run(hooks.run("PreToolUse", {}, "run_command", cwd=tmp_path))
    assert said.blocked == "no", "the Read hook does not match run_command"
    assert "RuntimeError: boom" in str(said.error)


# -- in a turn -----------------------------------------------------------------------------------


def test_the_hooks_run_at_a_turns_events(
    fake: FakeModelServer, connect: Connect, run: Run, tmp_path: Path
) -> None:
    ran: list[str] = []

    async def command(arguments: dict[str, Any]) -> str:
        ran.append(arguments["command"])
        return f"ran {arguments['command']}"

    tool = Tool("run_command", "Runs a command.", {"type": "object"}, command)
    asked: list[bool] = []

    async def gate(called: Tool, arguments: dict[str, Any]) -> str | None:
        asked.append(called.needs_approval)
        return None

    entries = [
        PluginCommand("guard", hook, tmp_path / "plugin", tmp_path / "data")
        for hook in parse_hooks(_hooks_json(), "hooks.json")
    ]
    _guard(tmp_path / "plugin")
    calls = [
        FakeCall("run_command", json.dumps({"command": c}), id=f"c{n}")
        for n, c in enumerate(("rm -rf /", "deploy prod", "echo old"))
    ]
    fake.reply(FakeReply(calls=calls), FakeReply(text="Done."), FakeReply(text="Tests pass, done."))
    models = connect(fake, "openrouter")
    history: list[Message] = [SystemMessage("You are Atlas.")]
    hooks = Hooks(entries)
    conversation = ConversationHooks(lambda: hooks, session="c-1", cwd=tmp_path, auto=lambda: True)

    async def scenario() -> tuple[Any, Any]:
        loop = AgentLoop(models.client("openrouter"), [tool], gate=gate, hooks=conversation)
        first = await loop.reply(history, "Clean up, please")
        second = await loop.reply(history, "Here is my secret: 1234")
        return first, second

    first, second = run(models, scenario)

    assert first.outcome is TurnOutcome.ANSWERED
    assert first.text == "Tests pass, done.", "the Stop hook made it go on once"
    prompt = history[1]
    assert isinstance(prompt, UserMessage)
    assert prompt.text.startswith("Clean up, please\n\n")
    assert "[Atlas: a plugin's hook adds: Today is a holiday.]" in prompt.text
    assert "[Atlas: a plugin's hook adds: The user likes short answers.]" in prompt.text
    assert ran == ["deploy prod", "echo new"], "rm was refused, echo old rewritten"
    assert asked == [True, False], "deploy went through the user's approval"
    results = [m for m in history if isinstance(m, ToolResult)]
    assert results[0].text == "[Atlas: a plugin's hook refused this call: rm is not allowed here]"
    assert results[2].text == "ran echo new\n\n[Atlas: a plugin's hook adds: checked by the guard]"
    went_on = [m.text for m in history if isinstance(m, UserMessage)][1]
    assert (
        went_on
        == "[Atlas: a plugin's hook asks you to go on before you finish: run the tests first]"
    )

    assert (second.outcome, second.error) == (TurnOutcome.FAILED, "HookBlocked")
    assert second.text == "A plugin's hook stopped this message: no secrets in this chat"
    assert "1234" not in json.dumps([getattr(m, "text", "") for m in history])
    assert len(fake.bodies(CHAT)) == 3, "the blocked message never reached the model"
    seen = (tmp_path / "data" / "seen.txt").read_text().splitlines()
    assert seen[:2] == ["SessionStart auto", "UserPromptSubmit auto"]


def test_a_stop_hook_makes_a_turn_go_on_three_times_at_most(
    fake: FakeModelServer, connect: Connect, run: Run, tmp_path: Path
) -> None:
    always = Hooks(callbacks=[PluginCallback("p", "Stop", "", lambda e: {"decision": "block"})])
    fake.reply(*(FakeReply(text=f"Answer {n}") for n in range(5)))
    models = connect(fake, "openrouter")

    async def scenario() -> Any:
        hooks = ConversationHooks(lambda: always, session="c", cwd=tmp_path)
        loop = AgentLoop(models.client("openrouter"), hooks=hooks)
        return await loop.reply([], "Hi")

    ended = run(models, scenario)
    assert (ended.text, ended.model_calls) == ("Answer 3", 4)


# -- in a plugin ---------------------------------------------------------------------------------


@pytest.fixture
def plugins(home: Home, vault: Vault, tmp_path: Path) -> Plugins:
    database = Database(home.database_file)
    migrate(database, SCHEMAS)
    return Plugins(home, database, vault, official_folder=tmp_path / "bundled")


def _plugin(home: Home, manifest: dict[str, Any], code: str | None = None) -> Path:
    folder = home.plugins_folder / manifest["name"]
    (folder / ".claude-plugin").mkdir(parents=True)
    (folder / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), "utf-8")
    if code is not None:
        (folder / "__init__.py").write_text(code, encoding="utf-8")
    return folder


def _on(plugins: Plugins, name: str) -> None:
    manifest = plugins.view(name).manifest
    assert manifest is not None, plugins.view(name).problem
    plugins.enable(name, manifest.fingerprint())


def test_a_plugin_s_hooks_json_is_consented_to_and_runs(
    plugins: Plugins, home: Home, tmp_path: Path
) -> None:
    folder = _plugin(home, {"name": "guard"})
    _guard(folder)
    (folder / "hooks").mkdir()
    (folder / "hooks" / "hooks.json").write_text(
        json.dumps({"hooks": _hooks_json("Bash")}), encoding="utf-8"
    )
    view = plugins.view("guard")
    assert view.manifest is not None
    shown = f"Runs `{sys.executable} ${{CLAUDE_PLUGIN_ROOT}}/guard.py` on PreToolUse (Bash): it"
    assert any(line.startswith(shown) for line in view.manifest.declaration())
    assert plugins.command_hooks() == [], "off: no hook runs"
    _on(plugins, "guard")
    commands = plugins.command_hooks()
    assert {c.hook.event for c in commands} == {
        "SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"
    }  # fmt: skip
    said = asyncio.run(
        Hooks(commands).run(
            "PreToolUse",
            {"hook_event_name": "PreToolUse", "tool_input": {"command": "rm x"}},
            "run_command",
            cwd=tmp_path,
        )
    )
    assert said.blocked == "rm is not allowed here"
    assert (home.plugin_data_folder / "guard" / "seen.txt").is_file()

    # A changed command asks again.
    before = view.manifest.fingerprint()
    (folder / "hooks" / "hooks.json").write_text(
        json.dumps({"hooks": _hooks_json("Write")}), encoding="utf-8"
    )
    assert plugins.view("guard").manifest.fingerprint() != before  # type: ignore[union-attr]
    assert not plugins.view("guard").running


@pytest.mark.parametrize(
    ("hooks", "problem"),
    [
        ("./missing.json", "is not there"),
        ("../outside.json", "must start with ./"),
        ({"PreToolUse": [{"hooks": [{"type": "http", "url": "x"}]}]}, "type 'http'"),
    ],
)
def test_a_plugin_whose_hooks_atlas_cannot_run_says_why(
    plugins: Plugins, home: Home, hooks: object, problem: str
) -> None:
    _plugin(home, {"name": "bad", "hooks": hooks})
    assert problem in str(plugins.view("bad").problem)


PYTHON_HOOK = """
def register(ctx):
    def guard(event):
        if "rm" in event["tool_input"].get("command", ""):
            return {"decision": "block", "reason": "not rm"}
        return None

    ctx.register_hook("PreToolUse", guard, matcher="Bash")
"""


def test_a_plugin_s_python_hooks_are_declared_and_registered(
    plugins: Plugins, home: Home, tmp_path: Path
) -> None:
    _plugin(home, {"name": "py", "atlas": {"api": 15, "hooks": ["PreToolUse"]}}, PYTHON_HOOK)
    manifest = plugins.view("py").manifest
    assert manifest is not None
    assert "Its Python hooks into PreToolUse" in " ".join(manifest.declaration())
    _on(plugins, "py")
    parts = asyncio.run(plugins.parts())
    [hook] = parts.hooks
    assert (hook.plugin, hook.event, hook.matcher) == ("py", "PreToolUse", "Bash")
    said = asyncio.run(
        Hooks(callbacks=parts.hooks).run(
            "PreToolUse", {"tool_input": {"command": "rm x"}}, "run_command", cwd=tmp_path
        )
    )
    assert said.blocked == "not rm"


@pytest.mark.parametrize(
    ("declared", "problem"),
    [
        (["Stop"], "a hook on PreToolUse, which it does not declare"),
        (["PreToolUse", "SubagentStart"], None),
    ],
)
def test_a_python_hook_it_does_not_declare_marks_the_plugin(
    plugins: Plugins, home: Home, declared: list[str], problem: str | None
) -> None:
    _plugin(home, {"name": "py", "atlas": {"api": 15, "hooks": declared}}, PYTHON_HOOK)
    _on(plugins, "py")
    asyncio.run(plugins.parts())
    if problem is None:
        assert plugins.view("py").problem is None
    else:
        assert problem in str(plugins.view("py").problem)


def test_a_python_hook_on_an_event_atlas_does_not_fire_marks_the_plugin(
    plugins: Plugins, home: Home
) -> None:
    code = "def register(ctx):\n    ctx.register_hook('SubagentStart', lambda e: None)\n"
    _plugin(home, {"name": "py", "atlas": {"api": 15, "hooks": ["SubagentStart"]}}, code)
    _on(plugins, "py")
    asyncio.run(plugins.parts())
    assert "which Atlas does not fire" in str(plugins.view("py").problem)
