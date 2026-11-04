"""The workbench for real (step 16c): the user's executor instances, started and prompted from
their cards, with tests/fake_claude.py standing in for `claude` over real pipes and the Claude Code
plugin Atlas ships driving it; Atlas's model is the fake model server. The desk plays the user's
screen. The database is real, in a temporary home."""

import asyncio
import dataclasses
import json
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from accounts_support import AccountsPlugin, Stop
from atlas.__main__ import SCHEMAS
from atlas.assistant import (
    HANDOFF_MARK,
    Assistant,
    ExecutorRun,
    ExecutorUsage,
    InstanceActivity,
    InstanceChanged,
    WorkbenchEvent,
)
from atlas.assistant import workbench as workbench_module
from atlas.assistant.model import UsageWindowView
from atlas.assistant.workbench import not_compacted
from atlas.conversations import WORKBENCH
from atlas.home import (
    Database,
    Home,
    ModelSettings,
    Settings,
    WorkspaceSettings,
    format_utc,
    migrate,
)
from atlas.jobs import InstanceRecord
from atlas.models import CallLedger, UserMessage
from atlas.plugins import (
    Answers,
    Capabilities,
    ExecutorError,
    Permission,
    Question,
    SessionInfo,
    Verdict,
)
from atlas.secrets import Vault
from claude_code_support import claude_code, plugin_module
from codex_support import codex
from fake_model import FakeModelServer, FakeReply
from model_support import Connect

CHAT = "/v1/chat/completions"
GUARDIAN = "You are a security reviewer"
ANSWERING = "Claude Code is working on a task you delegated"
#: The smallest PNG there is: one transparent pixel.
PIXEL = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


class Screen:
    """The workbench's cards, as the desk sees them: what was asked, and the answers to give."""

    def __init__(self) -> None:
        self.asked: list[tuple[InstanceChanged, Permission | tuple[Question, ...]]] = []
        self.answers: list[Verdict | Answers] = []

    async def ask(self, asker: InstanceChanged, questions: Sequence[Question]) -> Answers:
        self.asked.append((asker, tuple(questions)))
        answer = self.answers.pop(0)
        assert not isinstance(answer, Verdict)
        return answer

    async def approve(self, asker: InstanceChanged, permission: Permission) -> Verdict:
        self.asked.append((asker, permission))
        answer = self.answers.pop(0)
        assert isinstance(answer, Verdict)
        return answer


class Bench:
    """A test's assistant, what its workbench told, and the fake `claude`'s scripts and log."""

    def __init__(self, assistant: Assistant, heard: list[WorkbenchEvent], root: Path) -> None:
        self.assistant = assistant
        self.workbench = assistant.workbench
        self.heard = heard
        self.root = root
        self.scripts = root / "scripts"
        #: The accounts plugin Atlas ships, on, as a home that uses other accounts has it.
        self.accounts = AccountsPlugin(assistant, root / "accounts-plugin")

    def script(self, folder: str, *steps: dict[str, Any], resumed: bool = False) -> Path:
        """What the fake `claude` does for each prompt in `folder` (a folder of the workspace)."""
        project = self.root / "work" / folder
        project.mkdir(parents=True, exist_ok=True)
        name = f"{folder}.resume.json" if resumed else f"{folder}.json"
        (self.scripts / name).write_text(json.dumps(list(steps)), encoding="utf-8")
        return project

    def log(self) -> list[dict[str, Any]]:
        lines = (self.root / "claude-log.jsonl").read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]

    def activity(self, instance_id: str) -> list[tuple[str, str]]:
        return [
            (e.kind, e.text)
            for e in self.heard
            if isinstance(e, InstanceActivity) and e.instance_id == instance_id
        ]

    def last(self, instance_id: str) -> InstanceChanged:
        return [
            e for e in self.heard if isinstance(e, InstanceChanged) and e.instance_id == instance_id
        ][-1]


@pytest.fixture
def bench(
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[Screen | None], Bench]:
    migrate(Database(home.database_file), SCHEMAS)
    (tmp_path / "scripts").mkdir()
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(tmp_path / "scripts"))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "claude-log.jsonl"))
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", str(tmp_path / "claude-auth.json"))

    def make(desk: Screen | None = None) -> Bench:
        chosen = ModelSettings(provider="openrouter")
        models = connect(
            fake, "openrouter", settings=chosen, ledger=CallLedger(Database(home.database_file))
        )
        work = tmp_path / "work"
        work.mkdir(exist_ok=True)
        settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(work))
        heard: list[WorkbenchEvent] = []
        assistant = Assistant(
            home,
            settings,
            models,
            vault,
            listener=lambda _conversation, _event: None,
            desk=desk,
            executors={"claude-code": claude_code()},
            on_workbench=heard.append,
        )
        return Bench(assistant, heard, tmp_path)

    return make


def _run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())  # pyright: ignore[reportArgumentType]


def _said(run: ExecutorRun) -> str:
    """How its last turn ended, in its harness's words."""
    assert run.finished is not None
    return run.finished.text


async def _idle(bench: Bench, instance_id: str) -> None:
    for _ in range(400):
        if not bench.workbench.get(instance_id).working:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("The instance kept working.")


async def _saying(bench: Bench, instance_id: str, text: str) -> None:
    """Wait until the harness says *text*: a steer sent then lands mid-turn. A fixed wait guessed
    how long the harness takes to start, and on a machine busy with the other test processes the
    guess landed before it."""
    for _ in range(1500):
        if ("said", text) in bench.activity(instance_id):
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"The harness never said {text!r}.")


def test_an_instance_of_the_users_takes_prompts_steers_and_asks_on_its_card(
    bench: Callable[[Screen | None], Bench],
) -> None:
    screen = Screen()
    screen.answers.append(Verdict(True))
    b = bench(screen)
    site = b.script(
        "site",
        {"say": "Looking at the header."},
        {"compact": [180_000, 12_000]},
        {"sleep": 0.8},
        {"tool": "Edit", "input": {"file_path": "index.html"}},
        {"result": "The header is fixed."},
    )

    async def scenario() -> tuple[str, bool, bool]:
        run = await b.workbench.start("claude-code", site)
        first = await b.workbench.prompt(run.id, "Fix the header.")
        await _saying(b, run.id, "Looking at the header.")
        steered = await b.workbench.prompt(run.id, "Keep the logo.")
        await _idle(b, run.id)
        await b.assistant.close()
        return run.id, first, steered

    instance_id, first, steered = _run(scenario)

    assert (first, steered) == (False, True), "a new turn, then a steer into it"
    activity = b.activity(instance_id)
    assert activity[0] == ("prompted", "Fix the header.")
    assert ("steered", "Keep the logo.") in activity
    assert ("said", "Steered: Keep the logo.") in activity, "the harness took it mid-turn"
    compacted = "The conversation was compacted from about 180,000 to 12,000 tokens."
    assert ("compacted", compacted) in activity, "its own compaction, on its session's screen"
    [(asker, permission)] = screen.asked
    assert isinstance(permission, Permission)
    assert (asker.owner, asker.parent_id, asker.executor) == ("user", "", "claude-code")
    assert permission.summary == "index.html"
    card = b.last(instance_id)
    assert (card.account, card.folder, card.state) == ("someone@example.com", str(site), "ended")
    assert (card.last_tool, card.tools, card.steer, card.can_auto) == ("Edit", 1, True, True)
    stored = b.assistant.conversations.info(card.transcript_id)
    assert stored.source == WORKBENCH
    assert stored.title == "Claude Code · site"
    kept = b.assistant.conversations.open(card.transcript_id).history
    assert [m.text for m in kept if isinstance(m, UserMessage)] == [
        "Fix the header.",
        "Keep the logo.",
    ]
    listed = [row.id for row in b.assistant.conversations.recent(50)]
    assert card.transcript_id not in listed, "hidden from the user's conversations"


def test_a_prompt_and_a_steer_take_the_files_the_user_attached(
    bench: Callable[[Screen | None], Bench],
) -> None:
    """Step 16i: the files kept in the home reach Claude Code (an image as itself, every file by
    its path, the home's attachments one of its folders), its session's screen hears them, and
    its stored conversation keeps them; one that does not take files is refused them."""

    class Blind:
        """An executor from a plugin written before version 13: it takes no files."""

        def __init__(self) -> None:
            self.inner = claude_code()
            self.title = "Blind"

        def capabilities(self) -> Capabilities:
            return dataclasses.replace(self.inner.capabilities(), files=False)

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

    b = bench(Screen())
    # The stand-in passes for an executor as a plugin's does: by what Atlas calls on it.
    executors: dict[str, Any] = {"claude-code": claude_code(), "blind": Blind()}
    b.assistant.set_executors(executors)
    site = b.script("site", {"say": "Looking."}, {"sleep": 0.8}, {"result": "Seen."})
    kept = b.assistant.attachments
    shot = kept.add("", "shot.png", len(PIXEL), 0, PIXEL).attachment
    notes = kept.add("", "notes.txt", 5, 0, b"notes").attachment
    assert shot is not None
    assert notes is not None

    async def scenario() -> tuple[str, str]:
        run = await b.workbench.start("claude-code", site)
        await b.workbench.prompt(run.id, "What is wrong here?", (shot,))
        await _saying(b, run.id, "Looking.")
        await b.workbench.prompt(run.id, "", (notes,))
        await _idle(b, run.id)
        blind = await b.workbench.start("blind", site)
        with pytest.raises(ExecutorError, match="takes no files"):
            await b.workbench.prompt(blind.id, "Look.", (shot,))
        with pytest.raises(ExecutorError, match="empty"):
            await b.workbench.prompt(run.id, " ")
        await b.assistant.close()
        return run.id, run.transcript.id

    instance_id, transcript_id = _run(scenario)

    log = b.log()
    # Each instance on Atlas's effort; the probes that read its models start on none.
    argv, blind_argv = [line["argv"] for line in log if "--effort" in line.get("argv", [])]
    assert argv[argv.index("--add-dir") + 1] == str(kept.folder)
    assert "--add-dir" not in blind_argv, "no folder for an executor that takes no files"
    first, steer = [line["message"]["content"] for line in log if line.get("type") == "user"][:2]
    assert [block["type"] for block in first] == ["image", "text", "text"]
    assert str(kept.path(shot.id)) in first[1]["text"]
    assert [block["type"] for block in steer] == ["text"]
    assert str(kept.path(notes.id)) in steer[0]["text"]
    heard = [
        (e.kind, e.text, e.attachments)
        for e in b.heard
        if isinstance(e, InstanceActivity)
        and e.kind in ("prompted", "steered")
        and e.instance_id == instance_id
    ]
    assert heard == [("prompted", "What is wrong here?", (shot,)), ("steered", "", (notes,))]
    history = b.assistant.conversations.open(transcript_id).history
    said = [(m.text, m.attachments) for m in history if isinstance(m, UserMessage)]
    assert said == [("What is wrong here?", (shot,)), ("", (notes,))]


def test_atlas_answers_an_instance_of_the_users_from_its_own_prompts(
    bench: Callable[[Screen | None], Bench], fake: FakeModelServer
) -> None:
    questions = [{"question": "Which color?", "options": [{"label": "Blue"}, {"label": "Red"}]}]
    screen = Screen()
    b = bench(screen)
    site = b.script(
        "site",
        {"tool": "Bash", "input": {"command": "npm run build"}},
        {"questions": questions},
        {"result": "Built, in blue."},
    )
    fake.reply(
        FakeReply(text="APPROVE", when=GUARDIAN),
        FakeReply(text='{"Which color?": "Blue"}', when=ANSWERING),
    )

    async def scenario() -> str:
        run = await b.workbench.start("claude-code", site)
        b.workbench.set_atlas_answers(run.id, True)
        await b.workbench.prompt(run.id, "Build the site; I like blue.")
        await _idle(b, run.id)
        await b.assistant.close()
        return run.id

    instance_id = _run(scenario)

    assert screen.asked == [], "nothing reached the user"
    answered = [text for kind, text in b.activity(instance_id) if kind == "answered"]
    assert answered == ["allowed Bash", '{"Which color?": "Blue"}']
    answering = [body for body in fake.bodies(CHAT) if ANSWERING in str(body["messages"][0])]
    assert "I like blue." in answering[0]["messages"][1]["content"]
    assert b.last(instance_id).atlas_answers


def test_in_auto_its_own_rules_decide_and_atlas_adds_none(
    bench: Callable[[Screen | None], Bench], fake: FakeModelServer
) -> None:
    screen = Screen()
    b = bench(screen)
    site = b.script(
        "site",
        {"tool": "Bash", "input": {"command": "pytest -x"}},
        {"tool": "Bash", "input": {"command": "git push origin main"}},
        {"result": "Tested and pushed."},
    )

    async def scenario() -> None:
        run = await b.workbench.start("claude-code", site, auto=True)
        b.workbench.set_atlas_answers(run.id, True)
        await b.workbench.prompt(run.id, "Test it and push.")
        await _idle(b, run.id)
        await b.assistant.close()

    _run(scenario)

    assert screen.asked == [], "Claude Code's auto pushes to the project's own repository"
    guardian = [body for body in fake.bodies(CHAT) if GUARDIAN in str(body["messages"][0])]
    assert guardian == []
    [argv] = [entry["argv"] for entry in b.log() if "argv" in entry and Path(entry["cwd"]) == site]
    assert argv[argv.index("--permission-mode") + 1] == "auto"
    assert "--settings" not in argv, "an outside program keeps its own rules (17/10/2026)"


def test_in_auto_what_the_users_own_rules_keep_asking_is_the_users_never_atlas(
    bench: Callable[[Screen | None], Bench],
    fake: FakeModelServer,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    own = tmp_path / "user-settings.json"
    own.write_text(json.dumps({"permissions": {"ask": ["Bash(git push *)"]}}), encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_USER_SETTINGS", str(own))
    screen = Screen()
    screen.answers.append(Verdict(False, "Not yet."))
    b = bench(screen)
    site = b.script(
        "site",
        {"tool": "Bash", "input": {"command": "pytest -x"}},
        {"tool": "Bash", "input": {"command": "git push origin main"}},
        {"result": "Tested; the push waits."},
    )

    async def scenario() -> None:
        run = await b.workbench.start("claude-code", site, auto=True)
        b.workbench.set_atlas_answers(run.id, True)
        await b.workbench.prompt(run.id, "Test it and push.")
        await _idle(b, run.id)
        await b.assistant.close()

    _run(scenario)

    [(asker, permission)] = screen.asked
    assert isinstance(permission, Permission)
    assert permission.summary == "git push origin main", "the tests ran without asking"
    assert asker.auto
    assert asker.atlas_answers
    guardian = [body for body in fake.bodies(CHAT) if GUARDIAN in str(body["messages"][0])]
    assert guardian == [], "what the user's own rules keep for a person never reaches the guardian"


def test_an_executor_with_no_automatic_mode_starts_asking_whatever_the_switch_says(
    bench: Callable[[Screen | None], Bench], monkeypatch: pytest.MonkeyPatch
) -> None:
    b = bench(Screen())
    executor = b.assistant.executor("claude-code")
    assert executor is not None
    own = executor.capabilities()
    monkeypatch.setattr(executor, "capabilities", lambda: dataclasses.replace(own, auto=False))
    site = b.script("site", {"result": "Ok."})

    async def scenario() -> ExecutorRun:
        run = await b.workbench.start("claude-code", site, auto=True)
        await b.workbench.prompt(run.id, "Go.")
        await _idle(b, run.id)
        await b.assistant.close()
        return run

    run = _run(scenario)

    assert not run.auto
    [argv] = [entry["argv"] for entry in b.log() if "argv" in entry and Path(entry["cwd"]) == site]
    assert argv[argv.index("--permission-mode") + 1] == "default"
    assert "--settings" not in argv


def test_the_harnesss_own_auto_mode_switches_mid_session(
    bench: Callable[[Screen | None], Bench],
) -> None:
    b = bench(Screen())
    site = b.script("beef", {"result": "Ok."})

    async def scenario() -> None:
        run = await b.workbench.start("claude-code", site)
        await b.workbench.set_auto(run.id, True)
        await b.workbench.set_auto(run.id, False)
        await b.assistant.close()

    _run(scenario)

    requests = [entry["request"] for entry in b.log() if "request" in entry]
    modes = [r["mode"] for r in requests if r.get("subtype") == "set_permission_mode"]
    assert modes == ["auto", "default"]


def test_the_harnesss_permission_modes_switch_mid_session(
    bench: Callable[[Screen | None], Bench],
) -> None:
    b = bench(Screen())
    site = b.script("modes", {"result": "Ok."})

    async def scenario() -> tuple[list[str], str, str, bool, str]:
        run = await b.workbench.start("claude-code", site)
        listed = list(run.view().modes)
        first = run.view().mode
        await b.workbench.set_mode(run.id, "plan")
        await b.workbench.set_mode(run.id, "auto")
        auto = run.auto
        await b.workbench.set_auto(run.id, False)
        after = run.view().mode
        with pytest.raises(ExecutorError):
            await b.workbench.set_mode(run.id, "bypassPermissions")
        await b.assistant.close()
        return listed, first, after, auto, run.view().mode

    listed, first, after, auto, last = _run(scenario)

    # Its default first, and never the mode that checks nothing.
    assert listed == ["default", "acceptEdits", "plan", "auto"]
    assert first == "default"
    # `auto` is the automatic mode the box's switch turns on and off.
    assert auto
    assert after == last == "default"
    requests = [entry["request"] for entry in b.log() if "request" in entry]
    modes = [r["mode"] for r in requests if r.get("subtype") == "set_permission_mode"]
    assert modes == ["plan", "auto", "default"]


def test_a_sessions_context_is_its_tokens_against_its_models_window(
    bench: Callable[[Screen | None], Bench],
) -> None:
    b = bench(Screen())
    site = b.script("context", {"result": "Ok."})

    async def scenario() -> tuple[tuple[int | None, int | None], tuple[int | None, int | None]]:
        run = await b.workbench.start("claude-code", site, model="opus[1m]")
        await b.workbench.prompt(run.id, "Go.")
        await _idle(b, run.id)
        on_million = await b.workbench.context(run.id)
        run.model = "a-model-nobody-knows"
        run.chosen_model = None
        unknown = await b.workbench.context(run.id)
        await b.assistant.close()
        return on_million, unknown

    on_million, unknown = _run(scenario)

    assert on_million[1] == 1_000_000
    assert unknown[1] is None


def test_an_instance_is_duplicated_moved_and_stopped(
    bench: Callable[[Screen | None], Bench],
) -> None:
    b = bench(Screen())
    site = b.script("site", {"result": "Site done."})
    api = b.script("api", {"result": "API done."})
    docs = b.script("docs", {"result": "Docs done."})

    async def scenario() -> tuple[str, str, list[str], str]:
        workbench = b.workbench
        run = await workbench.start("claude-code", site)
        twin = await workbench.duplicate(run.id, api)
        moved = await workbench.move(run.id, docs)
        await workbench.prompt(moved.id, "Write the docs.")
        await _idle(b, moved.id)
        done = _said(moved)
        listed = [found.id for found in workbench.runs()]
        await workbench.stop(twin.id)
        after = [found.id for found in workbench.runs()]
        await b.assistant.close()
        return run.id, twin.id, [*listed, "|", *after], done

    original, twin, listed, done = _run(scenario)

    assert listed == [original, twin, "|", original], "the moved card keeps its place and its id"
    assert b.last(original).folder.endswith("docs")
    assert b.last(twin).state == "ended"
    runs = [entry for entry in b.log() if "argv" in entry and "auth" not in entry["argv"] and
            "--version" not in entry["argv"]]  # fmt: skip
    # Its models, read in the home folder with no prompt for the default effort: once, then kept.
    probes = [entry for entry in runs if Path(entry["cwd"]) == Path.home()]
    assert len(probes) == 1
    assert [Path(entry["cwd"]).name for entry in runs if entry not in probes] == [
        "site",
        "api",
        "docs",
    ]
    assert done == "Docs done.", "the moved card works in its new folder"
    assert b.workbench.runs() == []


def test_another_account_goes_on_with_the_same_session_and_never_mid_task(
    bench: Callable[[Screen | None], Bench],
) -> None:
    b = bench(Screen())
    site = b.script("site", {"sleep": 0.6}, {"result": "Half done."})
    b.script("site", {"result": "All done, on the other account."}, resumed=True)

    async def scenario() -> tuple[str, str, str]:
        workbench = b.workbench
        run = await workbench.start("claude-code", site)
        await workbench.prompt(run.id, "Do the work.")
        with pytest.raises(ExecutorError, match="is working"):
            await workbench.switch_account(run.id, "other@example.com")
        await _idle(b, run.id)
        switched = await workbench.switch_account(run.id, "other@example.com")
        await workbench.prompt(switched.id, "Go on.")
        await _idle(b, switched.id)
        done = _said(switched)
        await b.assistant.close()
        return run.id, switched.account, done

    instance_id, account, done = _run(scenario)

    assert account == "other@example.com"
    log = b.log()
    assert ["auth", "login", "--claudeai", "--email", "other@example.com"] in [
        entry["argv"] for entry in log if "argv" in entry
    ]
    runs = [e["argv"] for e in log if "argv" in e and not {"auth", "--version"} & set(e["argv"])]
    assert "--resume=fake-session-0001" in runs[-1], "the same session goes on"
    assert done == "All done, on the other account."
    kept = b.assistant.conversations.open(b.last(instance_id).transcript_id).history
    assert [m.text for m in kept if isinstance(m, UserMessage)] == ["Do the work.", "Go on."]


def test_the_executor_switches_account_from_the_sidebar_and_its_sessions_go_on_with_it(
    bench: Callable[[Screen | None], Bench],
) -> None:
    """Asked for by the user on 18/10/2026: the switch is the executor's, not a session's; each of
    the user's instances of it goes on with its session on the new account."""
    b = bench(Screen())
    site = b.script("site", {"sleep": 0.6}, {"result": "Half done."})
    b.script("site", {"result": "All done, on the other account."}, resumed=True)

    async def scenario() -> tuple[str, list[str], str]:
        workbench = b.workbench
        run = await workbench.start("claude-code", site)
        await workbench.prompt(run.id, "Do the work.")
        with pytest.raises(ExecutorError, match="is working"):
            await workbench.switch_executor_account("claude-code", "other@example.com")
        await _idle(b, run.id)
        switched = await workbench.switch_executor_account("claude-code", "other@example.com")
        (again,) = switched
        await workbench.prompt(again.id, "Go on.")
        await _idle(b, again.id)
        done = _said(again)
        await b.assistant.close()
        return run.id, [found.account for found in switched], done

    instance_id, accounts, done = _run(scenario)

    assert accounts == ["other@example.com"]
    log = b.log()
    assert ["auth", "login", "--claudeai", "--email", "other@example.com"] in [
        entry["argv"] for entry in log if "argv" in entry
    ]
    runs = [e["argv"] for e in log if "argv" in e and not {"auth", "--version"} & set(e["argv"])]
    assert "--resume=fake-session-0001" in runs[-1], "the same session goes on"
    assert done == "All done, on the other account."
    assert b.last(instance_id).account == "other@example.com"


def test_the_model_and_effort_are_chosen_switched_and_kept_on_another_account(
    bench: Callable[[Screen | None], Bench],
) -> None:
    b = bench(Screen())
    site = b.script("site", {"result": "Done."})
    b.script("site", {"result": "Done again."}, resumed=True)

    async def scenario() -> tuple[list[str], str, str, str]:
        workbench = b.workbench
        offered = [m.id for m in await workbench.models("claude-code")]
        run = await workbench.start("claude-code", site, model="sonnet", effort="low")
        await workbench.prompt(run.id, "First.")
        await _idle(b, run.id)
        ran_first = run.view().model
        await workbench.set_model(run.id, "haiku", None)
        with pytest.raises(ExecutorError, match="does not take the effort"):
            await workbench.set_model(run.id, "haiku", "high")
        await workbench.prompt(run.id, "Second.")
        await _idle(b, run.id)
        ran_then = run.view().model
        switched = await workbench.switch_account(run.id, "other@example.com")
        chosen = switched.view().chosen_model
        await b.assistant.close()
        return offered, ran_first, ran_then, chosen

    offered, ran_first, ran_then, chosen = _run(scenario)

    assert offered == ["default", "sonnet", "haiku"]
    assert (ran_first, ran_then) == ("fake-sonnet", "fake-haiku")
    assert chosen == "haiku", "another account goes on with the model chosen"
    runs = [e["argv"] for e in b.log() if "argv" in e and "--resume=fake-session-0001" in e["argv"]]
    assert runs[-1][runs[-1].index("--model") + 1] == "haiku"
    assert "--effort" not in runs[-1]


def test_an_instance_runs_on_atlas_default_effort_unless_one_is_chosen(
    bench: Callable[[Screen | None], Bench],
) -> None:
    """Decided by the user on 17/10/2026: an executor Atlas starts runs on medium, as Atlas's own
    conversations do, rather than whatever the harness's settings say."""
    b = bench(Screen())
    site = b.script("site", {"result": "Done."})

    async def scenario() -> str:
        workbench = b.workbench
        run = await workbench.start("claude-code", site)
        await workbench.prompt(run.id, "First.")
        await _idle(b, run.id)
        effort = run.view().effort
        await b.assistant.close()
        return effort

    assert _run(scenario) == "medium"
    started = [e["argv"] for e in b.log() if "argv" in e and "--effort" in e["argv"]]
    assert started, "the effort goes on its command line"
    assert started[-1][started[-1].index("--effort") + 1] == "medium"


def test_what_cannot_start_says_why(
    bench: Callable[[Screen | None], Bench], tmp_path: Path
) -> None:
    b = bench(Screen())
    broken = claude_code([str(tmp_path / "no-such-claude")])
    b.assistant.set_executors({"claude-code": claude_code(), "broken": broken})

    async def scenario() -> str:
        workbench = b.workbench
        with pytest.raises(ExecutorError, match="does not exist"):
            await workbench.start("claude-code", tmp_path / "nowhere")
        with pytest.raises(ExecutorError, match="not installed"):
            await workbench.start("codex", tmp_path)
        failed = await workbench.start("broken", tmp_path)
        with pytest.raises(ExecutorError, match="not running"):
            await workbench.prompt(failed.id, "Go.")
        with pytest.raises(ExecutorError, match="empty"):
            await workbench.prompt(failed.id, "  ")
        with pytest.raises(ExecutorError, match="no instance"):
            workbench.get("in-none")
        await b.assistant.close()
        return failed.id

    failed_id = _run(scenario)

    card = b.last(failed_id)
    assert card.state == "failed"
    assert "Claude Code could not start" in card.problem


# -- the executors tab (step 16f) ----------------------------------------------------------------


def test_the_sessions_of_an_executor_cross_its_instances_and_atlas_s_delegations(
    bench: Callable[[Screen | None], Bench], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every session Claude Code keeps, with the instance working on each here; one process per
    session in Atlas; a copy of one is a session of its own; and the delegated ones are marked."""
    claude = tmp_path / "claude"
    monkeypatch.setenv("FAKE_CLAUDE_PROJECTS", str(claude / "projects"))
    monkeypatch.setenv("FAKE_CLAUDE_SESSION", "5e5e5e5e-0000-4000-8000-{folder:0>12}")
    b = bench(Screen())
    b.assistant.set_executors({"claude-code": claude_code(sessions=claude)})
    # The folder names the session the fake keeps: a session's id is hexadecimal.
    site = b.script("beef", {"say": "Looking."}, {"result": "Done."})
    session = "5e5e5e5e-0000-4000-8000-00000000beef"

    async def scenario() -> tuple[list[Any], list[Any], list[Any], str]:
        workbench = b.workbench
        run = await workbench.start("claude-code", site)
        # Not kept yet: listed first, under its folder and with no title.
        fresh = await workbench.sessions("claude-code")
        await workbench.prompt(run.id, "Fix the header.")
        await _idle(b, run.id)
        listed = await workbench.sessions("claude-code")
        again = await workbench.start("claude-code", site, resume=session)
        assert again is run, "one process per session in Atlas"
        await workbench.stop(run.id)
        copy = await workbench.start("claude-code", site, resume=session, fork=True)
        await workbench.prompt(copy.id, "Go on in a copy.")
        await _idle(b, copy.id)
        copied = copy.session_id or ""
        b.assistant.instance_store.save(
            InstanceRecord(
                "in-atlas", "claude-code", "Claude Code", "atlas", str(site), "", "ended",
                datetime.now(UTC), datetime.now(UTC), session_id=copied,
            )
        )  # fmt: skip
        after = await workbench.sessions("claude-code")
        await b.assistant.close()
        return fresh, listed, after, copied

    fresh, listed, after, copied = _run(scenario)

    assert [(v.session_id, v.folder, v.title, v.state) for v in fresh] == [
        ("", str(site), "", "idle")
    ]
    assert [(v.session_id, v.title, v.state, v.by_atlas) for v in listed] == [
        (session, "Fix the header.", "idle", False)
    ]
    assert listed[0].instance_id
    assert copied not in ("", session), "a copy's id is its own"
    assert {(v.session_id, v.state, v.by_atlas) for v in after} == {
        (session, "", False),
        (copied, "idle", True),
    }
    started = [line["argv"] for line in b.log() if "argv" in line]
    assert {f"--resume={session}", "--fork-session"} <= set(started[-1])


def test_the_folders_come_apart_and_each_folder_lists_only_its_sessions(
    bench: Callable[[Screen | None], Bench], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The executors tab lists the folders before any session: an instance's folder first while
    its harness has not kept its session; a folder's sessions are its own and its instances'. A
    plugin that does not list folders apart has its sessions' taken; one that fails says so."""
    claude = tmp_path / "claude"
    monkeypatch.setenv("FAKE_CLAUDE_PROJECTS", str(claude / "projects"))
    monkeypatch.setenv("FAKE_CLAUDE_SESSION", "7e7e7e7e-0000-4000-8000-{folder:0>12}")
    other = tmp_path / "other"
    kept = claude / "projects" / plugin_module("sessions").project_folder(str(other))
    kept.mkdir(parents=True)
    old = "7e7e7e7e-0000-4000-8000-000000000001"
    (kept / f"{old}.jsonl").write_text(
        json.dumps({"type": "user", "cwd": str(other), "message": {"content": "Old one."}}) + "\n",
        encoding="utf-8",
    )
    os.utime(kept / f"{old}.jsonl", (1_000, 1_000))

    class Plain:
        """Claude Code from a plugin written before version 8: it lists sessions, not folders."""

        def __init__(self, title: str) -> None:
            self.inner = claude_code(sessions=claude)
            self.title = title

        def capabilities(self) -> Capabilities:
            return dataclasses.replace(self.inner.capabilities(), folders=False)

        async def sessions(self) -> Sequence[SessionInfo]:
            return await self.inner.sessions()

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

    class Broken(Plain):
        async def sessions(self) -> Sequence[SessionInfo]:
            raise OSError("projects is unreadable")

    b = bench(Screen())
    # The two stand-ins pass for executors as a plugin's do: by what Atlas calls on them.
    executors: dict[str, Any] = {
        "claude-code": claude_code(sessions=claude),
        "plain": Plain("Plain"),
        "broken": Broken("Broken"),
    }
    b.assistant.set_executors(executors)
    site = b.script("beef", {"say": "Looking."}, {"result": "Done."})
    session = "7e7e7e7e-0000-4000-8000-00000000beef"

    async def scenario() -> tuple[list[Any], ...]:
        workbench = b.workbench
        run = await workbench.start("claude-code", site)
        fresh = await workbench.folders("claude-code")
        await workbench.prompt(run.id, "Fix the header.")
        await _idle(b, run.id)
        folders = await workbench.folders("claude-code")
        mine = await workbench.sessions("claude-code", str(site))
        theirs = await workbench.sessions("claude-code", str(other))
        plain = await workbench.folders("plain")
        plain_theirs = await workbench.sessions("plain", str(other))
        with pytest.raises(ExecutorError, match="Broken could not list its sessions: OSError"):
            await workbench.folders("broken")
        with pytest.raises(ExecutorError, match="not installed"):
            await workbench.folders("codex")
        await b.assistant.close()
        return fresh, folders, mine, theirs, plain, plain_theirs

    fresh, folders, mine, theirs, plain, plain_theirs = _run(scenario)

    assert [v.folder for v in fresh] == [str(site), str(other)]
    assert [v.folder for v in folders] == [str(site), str(other)]
    assert folders[1].updated_at == "1970-01-01T00:16:40.000Z"
    assert [(v.session_id, v.state) for v in mine] == [(session, "idle")]
    assert [(v.session_id, v.title, v.state) for v in theirs] == [(old, "Old one.", "")]
    assert [(v.executor, v.folder) for v in plain] == [("plain", str(site)), ("plain", str(other))]
    assert [v.session_id for v in plain_theirs] == [old]


def test_a_session_open_in_another_program_says_where(
    bench: Callable[[Screen | None], Bench], tmp_path: Path
) -> None:
    """Claude Code's own registry of its running processes says a session is open elsewhere, as
    its VS Code extension reads it; a process that is gone, or another one under the same id, does
    not count."""
    claude = tmp_path / "claude"
    (claude / "projects" / "C--work").mkdir(parents=True)
    ids = [f"6a6a6a6a-0000-4000-8000-00000000000{n}" for n in range(3)]
    for session in ids:
        (claude / "projects" / "C--work" / f"{session}.jsonl").write_text(
            json.dumps({"type": "user", "cwd": "C:\\work", "message": {"content": "Hi."}}) + "\n",
            encoding="utf-8",
        )
    registry = claude / "sessions"
    registry.mkdir()
    live = plugin_module("live")
    me = os.getpid()
    started = str(live._win32_created(me)) if sys.platform == "win32" else "x"
    gone = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                          capture_output=True, text=True, check=True)  # fmt: skip
    records = [
        {"pid": me, "sessionId": ids[0], "procStart": started, "entrypoint": "claude-vscode"},
        {"pid": int(gone.stdout), "sessionId": ids[1], "procStart": "1", "entrypoint": "cli"},
        {"pid": me, "sessionId": ids[2], "procStart": "0" if sys.platform == "win32" else "x",
         "entrypoint": "cli"},
    ]  # fmt: skip
    for number, record in enumerate(records):
        (registry / f"{number}.json").write_text(json.dumps(record), encoding="utf-8")
    b = bench(Screen())
    b.assistant.set_executors({"claude-code": claude_code(sessions=claude)})

    views = _run(lambda: b.workbench.sessions("claude-code"))

    elsewhere = {view.session_id: view.elsewhere for view in views}
    assert elsewhere[ids[0]] == "claude-vscode"
    assert elsewhere[ids[1]] == "", "its process is gone"
    if sys.platform == "win32":
        assert elsewhere[ids[2]] == "", "another process under the same id"


# -- the plan's limits (asked for by the user on 18/10/2026) --------------------------------------


def test_the_plans_usage_is_read_then_told_as_instances_work_and_forgotten_on_another_account(
    bench: Callable[[Screen | None], Bench],
) -> None:
    b = bench(Screen())
    site = b.script("site", {"usage": [0.5, 0.3]}, {"result": "Done."})

    async def scenario() -> tuple[ExecutorUsage | None, list[ExecutorUsage]]:
        workbench = b.workbench
        read = await workbench.usage("claude-code")
        run = await workbench.start("claude-code", site)
        await workbench.prompt(run.id, "Go.")
        await _idle(b, run.id)
        (again,) = await workbench.switch_executor_account("claude-code", "other@example.com")
        await workbench.prompt(again.id, "Go on.")
        await _idle(b, again.id)
        await b.assistant.close()
        return read, [e for e in b.heard if isinstance(e, ExecutorUsage)]

    read, told = _run(scenario)

    assert read is not None
    assert (read.plan, [(w.id, w.used) for w in read.windows]) == (
        "max",
        [("five_hour", 45.0), ("seven_day", 25.0), ("model:Fable", 60.0)],
    )
    assert told[0] == read, "a read is told to every screen"
    merged = told[1]
    assert (merged.plan, [(w.id, w.used) for w in merged.windows]) == (
        "max",
        [("five_hour", 50.0), ("seven_day", 30.0), ("model:Fable", 60.0)],
    ), "the windows the turn named replace theirs; the others stay as read"
    assert merged.read_at >= read.read_at
    after = told[-1]
    assert [w.id for w in after.windows] == ["five_hour", "seven_day"], (
        "another account's plan does not keep the last one's windows"
    )


def test_an_executor_that_does_not_read_its_usage_says_nothing(
    bench: Callable[[Screen | None], Bench],
) -> None:
    b = bench(Screen())
    executor = b.assistant.executor("claude-code")
    assert executor is not None

    class Quiet:
        title = "Quiet"

        def capabilities(self) -> Capabilities:
            return dataclasses.replace(executor.capabilities(), usage=False)

    b.assistant.set_executors({"claude-code": executor, "quiet": Quiet()})  # pyright: ignore[reportArgumentType]

    assert _run(lambda: b.workbench.usage("quiet")) is None


# -- slash commands (asked for by the user on 18/10/2026) -----------------------------------------


def test_the_boxs_commands_come_from_the_instance_or_a_harness_started_in_the_folder(
    bench: Callable[[Screen | None], Bench],
) -> None:
    b = bench(Screen())
    site = b.script("site", {"commands_changed": ["lint"]}, {"result": "Done."})
    (site / ".claude" / "skills" / "deploy").mkdir(parents=True)

    async def scenario() -> tuple[list[tuple[str, str]], list[str], list[str]]:
        workbench = b.workbench
        before = await workbench.commands("claude-code", site)
        run = await workbench.start("claude-code", site)
        await workbench.prompt(run.id, "Go.")
        await _idle(b, run.id)
        during = await workbench.commands("claude-code", site, run.id)
        await b.assistant.close()
        return [(c.name, c.group) for c in before], [c.name for c in during], list(run.commands)

    before, during, names = _run(scenario)

    assert before == [("compact", "command"), ("review", "skill"), ("deploy", "skill")], (
        "Claude Code's own among the commands, the rest among the skills"
    )
    assert during == ["compact", "review", "lint"], "what the instance said last"
    assert names == during, "the card's names change, so the box reads them again"
    prompts = [entry for entry in b.log() if entry.get("type") == "user"]
    assert len(prompts) == 1, "listing before the session ran no prompt"


# -- other accounts (step 16h, redone with the user on 19/10/2026) ---------------------------------


def _main_sessions(monkeypatch: pytest.MonkeyPatch) -> Path:
    """The user's own Claude Code folder, where the fake keeps the main account's sessions as
    Claude Code does."""
    root = Path(os.environ["CLAUDE_CONFIG_DIR"])
    monkeypatch.setenv("FAKE_CLAUDE_PROJECTS", str(root / "projects"))
    return root


#: A session's id as Claude Code makes them, which it lists by: the fake's for its first one.
ORIGINAL = "5e55a0e0-0000-4000-8000-000000000001"


async def _settled(bench: Bench) -> None:
    """Until the deletions of the sessions copies replaced, started when a turn ended, are
    done."""
    await asyncio.sleep(0.05)
    while bench.workbench._handing:  # pyright: ignore[reportPrivateUsage]
        await asyncio.sleep(0.02)


def _started(bench: Bench, folder: str) -> list[dict[str, Any]]:
    """Each Claude Code started in `folder` to work, in order, as the fake logged it."""
    return [
        entry
        for entry in bench.log()
        if "argv" in entry
        and Path(entry["cwd"]).name == folder
        and not {"auth", "--version"} & set(entry["argv"])
    ]


def test_two_accounts_work_at_once_each_in_its_own_folder(
    bench: Callable[[Screen | None], Bench], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user's asks: an executor on two accounts at once, each a Claude Code folder of its own
    with its own login, and each project on its account."""
    _main_sessions(monkeypatch)
    b = bench(Screen())
    site = b.script("site", {"account": True}, {"sleep": 0.5}, {"result": "Site done."})
    api = b.script("api", {"account": True}, {"sleep": 0.5}, {"result": "API done."})

    async def scenario() -> tuple[Any, ...]:
        workbench = b.workbench
        added = await b.accounts.add_account("claude-code", "company-b")
        await workbench.switch_executor_account(added.name, "b@example.com")
        await b.accounts.set_folder_account("claude-code", str(api), added.account_id)
        mine = await workbench.start("claude-code", site)
        theirs = await workbench.start("claude-code", api)
        await workbench.prompt(mine.id, "Fix the site.")
        await workbench.prompt(theirs.id, "Fix the API.")
        await asyncio.sleep(0.2)
        both = mine.working and theirs.working
        await _idle(b, mine.id)
        await _idle(b, theirs.id)
        listed = await workbench.accounts("claude-code")
        await b.assistant.close()
        return added, both, mine, theirs, listed

    added, both, mine, theirs, listed = _run(scenario)

    folder = Path.home() / ".claude-company-b"
    assert Path(added.folder) == folder, "beside the user's own, named by its nickname"
    assert folder.is_dir()
    assert both, "two accounts work at once"
    assert (mine.name, mine.account) == ("claude-code", "someone@example.com")
    assert (theirs.name, theirs.account) == (added.name, "b@example.com")
    assert theirs.account_id == added.account_id, "the folder's account"
    assert [e["account"] for e in _started(b, "api")] == [str(folder)]
    assert [e["account"] for e in _started(b, "site")] == [""], "the user's own folder, untouched"
    kept = b.assistant.conversations.open(b.last(theirs.id).transcript_id).history
    said = [m.text for m in kept if not isinstance(m, UserMessage) and m.text]
    assert said[0] == "Running on .claude-company-b."
    assert [(a.nickname, a.login, a.logged_in) for a in listed.accounts] == [
        ("", "someone@example.com", True),
        ("company-b", "b@example.com", True),
    ]
    assert listed.can_add


def test_a_switch_goes_on_in_a_copy_on_the_other_account_and_leaves_the_original_as_it_was(
    bench: Callable[[Screen | None], Bench], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decided with the user on 19/10/2026: each account keeps its own history, so the other
    account's Claude Code reads this one's session and keeps a copy of its own. Decided with the
    user on 03/11/2026: the original stays as it was, Atlas deleting no session (it was deleted
    once the copy was kept, from 22/10/2026)."""
    root = _main_sessions(monkeypatch)
    monkeypatch.setenv("FAKE_CLAUDE_SESSION", ORIGINAL)
    b = bench(Screen())
    site = b.script("site", {"say": "Started."}, {"result": "The site is half done."})
    b.script("site", {"account": True}, {"result": "The site is done."}, resumed=True)

    async def scenario() -> tuple[Any, ...]:
        workbench = b.workbench
        added = await b.accounts.add_account("claude-code", "company-b")
        await workbench.switch_executor_account(added.name, "")
        run = await workbench.start("claude-code", site)
        await workbench.prompt(run.id, "Build the site.")
        await _idle(b, run.id)
        original = next((root / "projects").glob(f"*/{ORIGINAL}.jsonl"))
        check = await workbench.account_switch(run.id, added.account_id)
        moved = await workbench.use_account(run.id, added.account_id)
        await workbench.prompt(moved.id, "Go on.")
        await _idle(b, moved.id)
        await _settled(b)
        await b.assistant.close()
        return added, run, moved, original, check

    added, run, moved, original, check = _run(scenario)

    assert original.exists(), "the original stays as it was"
    assert moved.id == run.id, "the card stays"
    assert (moved.name, moved.account) == (added.name, "other@example.com")
    assert moved.session_id, "a copy, with an id of its own"
    assert moved.session_id != ORIGINAL
    assert _said(moved) == "The site is done."
    copy = next((Path(added.folder) / "projects").glob(f"*/{moved.session_id}.jsonl"))
    assert "Build the site." in copy.read_text(encoding="utf-8"), "the copy has the conversation"
    argv = _started(b, "site")[-1]["argv"]
    assert f"--resume={original}" in argv
    assert "--fork-session" in argv
    assert (check.login, check.compared) == ("other@example.com", True)
    assert check.missing == ("files", "gmail", "slack"), "what this account has and the other lacks"
    assert check.tokens == 31_234, "what its first message there reads again"


def test_a_session_not_running_started_on_another_account_goes_on_there_in_a_copy(
    bench: Callable[[Screen | None], Bench], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found on 19/10/2026: a session of the main account, not running, switched in the box and
    then prompted, was looked for in the other account's folder. It goes on there in a copy; and
    the folder's account, which is its new sessions', leaves it on its own when nobody asks."""
    root = _main_sessions(monkeypatch)
    monkeypatch.setenv("FAKE_CLAUDE_SESSION", ORIGINAL)
    b = bench(Screen())
    site = b.script("site", {"result": "Started."})
    b.script("site", {"account": True}, {"result": "Again."}, resumed=True)

    async def scenario() -> tuple[Any, ...]:
        workbench = b.workbench
        added = await b.accounts.add_account("claude-code", "company-b")
        await workbench.switch_executor_account(added.name, "")
        first = await workbench.start("claude-code", site)
        await workbench.prompt(first.id, "Build it.")
        await _idle(b, first.id)
        await workbench.stop(first.id)
        original = next((root / "projects").glob(f"*/{ORIGINAL}.jsonl"))
        before = original.read_bytes()
        await b.accounts.set_folder_account("claude-code", str(site), added.account_id)
        stays = await workbench.start("claude-code", site, resume=ORIGINAL)
        await workbench.stop(stays.id)
        moved = await workbench.start(
            "claude-code", site, resume=ORIGINAL, account=added.account_id
        )
        await workbench.prompt(moved.id, "Go on.")
        await _idle(b, moved.id)
        await _settled(b)
        await b.assistant.close()
        return added, stays, moved, original, before

    added, stays, moved, original, before = _run(scenario)

    assert stays.name == "claude-code", "an existing session stays on its own account"
    assert before, "it was kept before"
    assert moved.name == added.name
    assert _said(moved) == "Again."
    assert original.read_bytes() == before, "the original stays as it was"
    assert moved.session_id
    assert moved.session_id != ORIGINAL
    assert list((Path(added.folder) / "projects").glob(f"*/{moved.session_id}.jsonl"))


def test_an_account_is_nicknamed_checked_and_removed_its_folder_left_as_it_is(
    bench: Callable[[Screen | None], Bench],
) -> None:
    b = bench(Screen())
    site = b.script("site", {"sleep": 0.4}, {"result": "Done."})

    async def scenario() -> tuple[Any, ...]:
        workbench = b.workbench
        for nickname, why in (
            ("", "nickname of letters"),
            ("company b", "nickname of letters"),
            ("../away", "nickname of letters"),
            ("w" * 41, "at most 40"),
        ):
            with pytest.raises(ExecutorError, match=why):
                await b.accounts.add_account("claude-code", nickname)
        side = await b.accounts.add_account("claude-code", " side ")
        with pytest.raises(ExecutorError, match="another account's already"):
            await b.accounts.add_account("claude-code", "side")
        with pytest.raises(ExecutorError, match=r"named 'Side' already|another account's"):
            await b.accounts.add_account("claude-code", "Side")
        await b.accounts.set_folder_account("claude-code", str(site), side.account_id)
        run = await workbench.start("claude-code", site)
        await workbench.prompt(run.id, "Go.")
        with pytest.raises(ExecutorError, match="runs on side"):
            await b.accounts.remove_account("claude-code", side.account_id)
        await _idle(b, run.id)
        await workbench.stop(run.id)
        await b.accounts.remove_account("claude-code", side.account_id)
        after = await workbench.accounts("claude-code")
        gone = b.assistant.executor(side.name)
        await b.assistant.close()
        return side, workbench.folder_account("claude-code", site), after, gone

    side, folder_account, after, gone = _run(scenario)

    assert (side.nickname, side.logged_in) == ("side", False), "not logged in until it logs in"
    assert folder_account == "", "the folder starts on the main account again"
    assert [a.account_id for a in after.accounts] == [""]
    assert gone is None
    assert Path(side.folder).is_dir(), "its folder, its login and its sessions stay"


def test_a_session_is_deleted_on_its_account_alone_and_never_while_it_works(
    bench: Callable[[Screen | None], Bench], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asked for by the user on 19/10/2026: a session may be deleted on its account alone."""
    root = _main_sessions(monkeypatch)
    monkeypatch.setenv("FAKE_CLAUDE_SESSION", ORIGINAL)
    b = bench(Screen())
    site = b.script("site", {"sleep": 0.5}, {"result": "Half done."})
    b.script("site", {"result": "Done."}, resumed=True)

    async def scenario() -> tuple[Any, ...]:
        workbench = b.workbench
        added = await b.accounts.add_account("claude-code", "company-b")
        await workbench.switch_executor_account(added.name, "")
        run = await workbench.start("claude-code", site)
        await workbench.prompt(run.id, "Build it.")
        await _idle(b, run.id)
        moved = await workbench.use_account(run.id, added.account_id)
        await workbench.prompt(moved.id, "Go on.")
        await _idle(b, moved.id)
        copy = moved.session_id or ""
        history = Path(added.folder) / "file-history" / copy
        history.mkdir(parents=True)
        await workbench.delete_session(added.name, copy, str(site))
        listed = await workbench.sessions(added.name)
        await b.assistant.close()
        return added, copy, history, listed, moved.state

    added, copy, history, listed, state = _run(scenario)

    assert state.value == "ended", "its idle card closed first"
    assert not list((Path(added.folder) / "projects").glob(f"*/{copy}.jsonl"))
    assert not history.exists(), "what Claude Code keeps of it alone, as `claude purge` does"
    assert [s.session_id for s in listed] == []
    assert list((root / "projects").glob(f"*/{ORIGINAL}.jsonl")), "the original, on its account"


# -- an account's plan limit hands over to the next (asked for by the user on 21/10/2026) ---------

#: A reset in the future, so the account at its limit stays passed over.
LATER = 4102444800


async def _handed_over(bench: Bench, instance_id: str) -> None:
    """Until the session's hand-over to another account was told, and its turn there ended."""
    for _ in range(400):
        if any(kind == "account" for kind, _text in bench.activity(instance_id)):
            break
        await asyncio.sleep(0.02)
    else:
        raise AssertionError("The session was not handed over.")
    while bench.workbench._handing:  # pyright: ignore[reportPrivateUsage]
        await asyncio.sleep(0.02)
    await _idle(bench, instance_id)


def test_a_session_at_its_plan_limit_goes_on_by_itself_on_the_next_account_in_a_copy(
    bench: Callable[[Screen | None], Bench], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _main_sessions(monkeypatch)
    monkeypatch.setenv("FAKE_CLAUDE_SESSION", ORIGINAL)
    b = bench(Screen())
    site = b.script("site", {"say": "Half of it is done."}, {"limit": LATER})
    b.script("site", {"account": True}, {"result": "All of it is done."}, resumed=True)

    async def scenario() -> tuple[Any, ...]:
        workbench = b.workbench
        added = await b.accounts.add_account("claude-code", "company-b")
        await workbench.switch_executor_account(added.name, "")
        run = await workbench.start("claude-code", site)
        await workbench.prompt(run.id, "Build the site.")
        await _handed_over(b, run.id)
        await _settled(b)
        original = list((root / "projects").glob(f"*/{ORIGINAL}.jsonl"))
        moved = workbench.get(run.id)
        await b.assistant.close()
        return added, moved, original

    added, moved, original = _run(scenario)

    assert (moved.name, moved.account) == (added.name, "other@example.com"), "the next account"
    assert moved.session_id, "a session there"
    assert moved.session_id != ORIGINAL, "a copy of its own there"
    assert original, "the original stays on the main account"
    assert _said(moved) == "All of it is done."
    prompts = [e["message"]["content"] for e in b.log() if e.get("type") == "user"]
    # Compacted first, by default, its harness having `/compact` (decided on 25/10/2026).
    assert prompts == ["Build the site.", "/compact", "Continue the task from where you stopped."]
    assert ("account", "other@example.com") in b.activity(moved.id), "the card says where"
    resumed = _started(b, "site")[-1]["argv"]
    assert "--fork-session" in resumed


def test_the_hand_over_follows_the_users_order_passes_over_an_account_not_logged_in_and_compacts(
    bench: Callable[[Screen | None], Bench], monkeypatch: pytest.MonkeyPatch
) -> None:
    _main_sessions(monkeypatch)
    monkeypatch.setenv("FAKE_CLAUDE_SESSION", "s-{folder}")
    b = bench(Screen())
    site = b.script("site", {"limit": LATER})
    b.script("site", {"account": True}, {"result": "Done there."}, resumed=True)

    async def scenario() -> tuple[Any, ...]:
        workbench = b.workbench
        first = await b.accounts.add_account("claude-code", "first")
        second = await b.accounts.add_account("claude-code", "second")
        await workbench.switch_executor_account(first.name, "")
        # The order: second, the main account, first. After the main one comes `first`... which
        # is logged in; `second` is not, so round to the start it is passed over.
        listed = await b.accounts.set_failover(
            "claude-code", [second.account_id, "", first.account_id], enabled=True, compact=True
        )
        run = await workbench.start("claude-code", site)
        await workbench.prompt(run.id, "Build the site.")
        await _handed_over(b, run.id)
        moved = workbench.get(run.id)
        await b.assistant.close()
        return first, second, listed, moved

    first, second, listed, moved = _run(scenario)

    assert [a.account_id for a in listed.accounts] == [second.account_id, "", first.account_id]
    assert (listed.failover, listed.compact) == (True, True)
    assert moved.name == first.name
    prompts = [e["message"]["content"] for e in b.log() if e.get("type") == "user"]
    assert prompts[1:] == ["/compact", "Continue the task from where you stopped."]


def test_with_the_hand_over_off_a_session_at_its_limit_stays_on_its_account(
    bench: Callable[[Screen | None], Bench], monkeypatch: pytest.MonkeyPatch
) -> None:
    _main_sessions(monkeypatch)
    b = bench(Screen())
    site = b.script("site", {"limit": LATER})

    async def scenario() -> ExecutorRun:
        workbench = b.workbench
        added = await b.accounts.add_account("claude-code", "company-b")
        await workbench.switch_executor_account(added.name, "")
        await b.accounts.set_failover("claude-code", [], enabled=False, compact=False)
        run = await workbench.start("claude-code", site)
        await workbench.prompt(run.id, "Build the site.")
        await _idle(b, run.id)
        await asyncio.sleep(0.2)
        kept = workbench.get(run.id)
        with pytest.raises(ExecutorError, match="has no account 'nobody'"):
            await b.accounts.set_failover("claude-code", ["nobody"], enabled=True, compact=False)
        await b.assistant.close()
        return kept

    kept = _run(scenario)

    assert kept.name == "claude-code", "the main account still"
    assert all(kind != "account" for kind, _text in b.activity(kept.id))


# -- going on when the plan's limit resets (decided with the user on 03/11/2026) -----------------


def _turns(b: Bench, folder: str, *turns: list[dict[str, Any]]) -> Path:
    """What the fake `claude` does in `folder`, a turn after another, the last one again after."""
    project = b.root / "work" / folder
    project.mkdir(parents=True, exist_ok=True)
    (b.scripts / f"{folder}.json").write_text(json.dumps({"turns": list(turns)}), encoding="utf-8")
    return project


async def _resumed(bench: Bench, instance_id: str) -> None:
    """Until a second turn ran on the instance and ended."""
    for _ in range(500):
        if sum(kind == "prompted" for kind, _text in bench.activity(instance_id)) >= 2:
            break
        await asyncio.sleep(0.02)
    else:
        raise AssertionError("The session did not go on.")
    await _idle(bench, instance_id)


def test_a_session_stopped_at_its_plans_limit_goes_on_once_a_minute_after_it_resets(
    bench: Callable[[Screen | None], Bench], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(workbench_module, "RESUME_CHECK_SECONDS", 0.02)
    b = bench(Screen())
    # Reset two minutes ago: a computer that slept past it goes on as it wakes.
    reset = int(datetime.now(UTC).timestamp()) - 120
    site = _turns(b, "site", [{"say": "Half of it."}, {"limit": reset}], [{"result": "All of it."}])

    async def scenario() -> ExecutorRun:
        workbench = b.workbench
        run = await workbench.start("claude-code", site)
        await workbench.prompt(run.id, "Build the site.")
        await _resumed(b, run.id)
        await asyncio.sleep(0.1)
        kept = workbench.get(run.id)
        await b.assistant.close()
        return kept

    kept = _run(scenario)

    assert kept.name == "claude-code", "where it was"
    assert _said(kept) == "All of it."
    prompts = [e["message"]["content"] for e in b.log() if e.get("type") == "user"]
    assert prompts == ["Build the site.", "Continue the task from where you stopped."], "once"
    told = [text for kind, text in b.activity(kept.id) if kind == "resume"]
    assert told == [format_utc(datetime.fromtimestamp(reset + 60, UTC))], "the card says when"


def test_a_session_taken_up_by_hand_or_whose_limit_says_no_reset_does_not_go_on_by_itself(
    bench: Callable[[Screen | None], Bench], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(workbench_module, "RESUME_CHECK_SECONDS", 0.02)
    monkeypatch.setattr(workbench_module, "RESUME_AFTER_SECONDS", 0)
    b = bench(Screen())
    # A whole second at least before the reset, so the user's own prompt always comes first.
    soon = int(datetime.now(UTC).timestamp()) + 2
    site = _turns(b, "site", [{"limit": soon}], [{"result": "Done by hand."}])
    docs = b.script("docs", {"say": "Stopped."}, {"limit": None})

    async def scenario() -> tuple[ExecutorRun, ExecutorRun]:
        workbench = b.workbench
        run = await workbench.start("claude-code", site)
        await workbench.prompt(run.id, "Build the site.")
        await _idle(b, run.id)
        await workbench.prompt(run.id, "Go on, please.")
        await _idle(b, run.id)
        unsaid = await workbench.start("claude-code", docs)
        await workbench.prompt(unsaid.id, "Write the docs.")
        await _idle(b, unsaid.id)
        await asyncio.sleep(max(0.0, soon - datetime.now(UTC).timestamp()) + 0.5)  # past it
        kept = workbench.get(run.id), workbench.get(unsaid.id)
        await b.assistant.close()
        return kept

    by_hand, unsaid = _run(scenario)

    prompts = [e["message"]["content"] for e in b.log() if e.get("type") == "user"]
    assert prompts == ["Build the site.", "Go on, please.", "Write the docs."], "never again"
    assert ("resume", "") not in b.activity(by_hand.id)
    assert ("resume", "") in b.activity(unsaid.id), "the card says it waits for the user"


# -- a session's own route, across executors (asked for by the user on 21/10/2026) ---------------


def _with_codex(b: Bench, monkeypatch: pytest.MonkeyPatch, *steps: dict[str, Any]) -> Path:
    """Codex beside Claude Code, the fake `codex` on the user's own folder doing `steps` for every
    prompt; its log, each line Atlas wrote to it."""
    codex_home = b.root / ".codex"
    codex_home.mkdir(exist_ok=True)
    script = b.root / "codex-script.json"
    script.write_text(json.dumps(list(steps)), encoding="utf-8")
    log = b.root / "codex-log.jsonl"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("FAKE_CODEX_SCRIPT", str(script))
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    executors: dict[str, Any] = {"claude-code": claude_code(), "codex": codex()}
    b.assistant.set_executors(executors)
    return log


def _codex_sent(log: Path, method: str) -> list[dict[str, Any]]:
    lines = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    return [line["params"] for line in lines if line.get("method") == method]


def _packet(log: Path) -> str:
    """The first prompt Codex was given."""
    first = _codex_sent(log, "turn/start")[0]
    return "".join(str(part.get("text", "")) for part in first["input"])


async def _told(bench: Bench, instance_id: str, kind: str, times: int = 1) -> None:
    """Until the session went on by its route (or said why it could not) `times` times, and its
    turn there ended."""
    for _ in range(500):
        if sum(told == kind for told, _text in bench.activity(instance_id)) >= times:
            break
        await asyncio.sleep(0.02)
    else:
        raise AssertionError("The session did not follow its route.")
    while bench.workbench._handing:  # pyright: ignore[reportPrivateUsage]
        await asyncio.sleep(0.02)
    await _idle(bench, instance_id)


def test_a_route_inside_one_executor_goes_to_the_account_it_names_not_the_executors_order(
    bench: Callable[[Screen | None], Bench], monkeypatch: pytest.MonkeyPatch
) -> None:
    _main_sessions(monkeypatch)
    monkeypatch.setenv("FAKE_CLAUDE_SESSION", "s-{folder}")
    b = bench(Screen())
    site = b.script("site", {"say": "Half done."}, {"limit": LATER})
    b.script("site", {"account": True}, {"result": "Done on the route's account."}, resumed=True)

    async def scenario() -> tuple[Any, ...]:
        workbench = b.workbench
        first = await b.accounts.add_account("claude-code", "first")
        second = await b.accounts.add_account("claude-code", "second")
        await workbench.switch_executor_account(first.name, "")
        await workbench.switch_executor_account(second.name, "")
        run = await workbench.start("claude-code", site)
        # The executor's own order would take `first`; the session's route names `second`.
        route = await b.accounts.set_route(
            run.id,
            [Stop("claude-code"), Stop("claude-code", second.account_id)],
            enabled=True,
        )
        await workbench.prompt(run.id, "Build the site.")
        await _told(b, run.id, "account")
        moved = workbench.get(run.id)
        kept = await b.accounts.route(run.id)
        await b.assistant.close()
        return second, route, moved, kept

    second, route, moved, kept = _run(scenario)

    assert [(s.executor, s.account_id, s.nickname) for s in route.stops] == [
        ("claude-code", "", ""),
        ("claude-code", second.account_id, "second"),
    ]
    assert moved.name == second.name, "the route's next destination"
    assert moved.session_id != "s-site", "a copy of its own there"
    assert _said(moved) == "Done on the route's account."
    prompts = [e["message"]["content"] for e in b.log() if e.get("type") == "user"]
    # A route compacts the copy first, by default (decided on 25/10/2026).
    assert prompts == ["Build the site.", "/compact", "Continue the task from where you stopped."]
    assert "--fork-session" in _started(b, "site")[-1]["argv"], "the same harness: a copy"
    assert [s.name for s in kept.stops] == [s.name for s in route.stops], "kept for the card"
    # The account at its limit is said to be, passed over until it resets.
    assert [s.limited for s in kept.stops] == [True, False]


@pytest.mark.parametrize(
    ("started", "stop", "model", "effort", "note"),
    [
        # The stop's own choices; a model that takes no effort goes on with none.
        (None, {"model": "haiku", "effort": "high"}, "haiku", None, ""),
        # The effort it ran on, onto the levels the stop's model takes.
        ("max", {"model": "sonnet"}, "sonnet", "high", ""),
        # A model the destination does not offer gives way to its default, which is said.
        ("low", {"model": "mythos"}, None, "low", "does not offer mythos"),
        # None chosen anywhere: it goes on on the harness's default, as it ran, and its effort.
        ("xhigh", {}, None, "xhigh", ""),
    ],
)
def test_a_switch_keeps_the_model_and_effort_or_takes_the_stops_own(
    bench: Callable[[Screen | None], Bench],
    monkeypatch: pytest.MonkeyPatch,
    started: str | None,
    stop: dict[str, str],
    model: str | None,
    effort: str | None,
    note: str,
) -> None:
    """Decided with the user on 25/10/2026: the model and the effort are kept at every switch, or
    are the ones the route's stop gives."""
    _main_sessions(monkeypatch)
    monkeypatch.setenv("FAKE_CLAUDE_SESSION", "s-{folder}")
    b = bench(Screen())
    site = b.script("site", {"say": "Half done."}, {"limit": LATER})
    b.script("site", {"account": True}, {"result": "Done there."}, resumed=True)

    async def scenario() -> str:
        workbench = b.workbench
        second = await b.accounts.add_account("claude-code", "second")
        await workbench.switch_executor_account(second.name, "")
        run = await workbench.start("claude-code", site, effort=started)
        other = Stop("claude-code", second.account_id, **stop)
        targets = [Stop("claude-code"), other]
        await b.accounts.set_route(run.id, targets, enabled=True)
        await workbench.prompt(run.id, "Build the site.")
        await _told(b, run.id, "account")
        await b.assistant.close()
        return run.id

    instance = _run(scenario)

    argv: list[str] = _started(b, "site")[-1]["argv"]
    assert "--fork-session" in argv, "the copy on the other account"
    assert (argv[argv.index("--model") + 1] if "--model" in argv else None) == model
    assert (argv[argv.index("--effort") + 1] if "--effort" in argv else None) == effort
    notes = [text for kind, text in b.activity(instance) if kind == "note"]
    assert notes == [] if not note else note in notes[0]


def test_a_copy_compacts_first_only_where_its_harness_can_and_goes_on_when_that_fails(
    bench: Callable[[Screen | None], Bench],
) -> None:
    b = bench(Screen())
    asked: list[str] = []

    class Copy:
        id = "copy"

        def __init__(self, fails: bool = False) -> None:
            self.fails = fails

        async def turn(self, text: str) -> None:
            asked.append(text)
            if self.fails:
                raise ExecutorError("the harness stopped")

    def before(*commands: str) -> ExecutorRun:
        return cast("ExecutorRun", SimpleNamespace(commands=commands))

    async def scenario() -> None:
        compact = b.workbench._compact  # pyright: ignore[reportPrivateUsage]
        await compact(cast("ExecutorRun", Copy()), before("review"), True)
        await compact(cast("ExecutorRun", Copy()), before("compact"), False)
        await compact(cast("ExecutorRun", Copy()), before("compact"), True)
        await compact(cast("ExecutorRun", Copy(fails=True)), before("compact"), True)
        await b.assistant.close()

    _run(scenario)

    assert asked == ["/compact", "/compact"], "only where it has /compact, and only when wanted"
    assert b.activity("copy") == [("note", not_compacted(ExecutorError("the harness stopped")))]


def test_a_route_across_executors_starts_a_new_session_there_with_the_handoff_packet(
    bench: Callable[[Screen | None], Bench], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _main_sessions(monkeypatch)
    monkeypatch.setenv("FAKE_CLAUDE_SESSION", ORIGINAL)
    screen = Screen()
    screen.answers.append(Verdict(True))
    b = bench(screen)
    log = _with_codex(b, monkeypatch, {"say": "Read the packet; finishing the footer."})
    site = b.script(
        "site",
        {"say": "The header is fixed; the footer is next."},
        {"tool": "Edit", "input": {"file_path": "index.html"}},
        {"limit": LATER},
    )

    async def scenario() -> tuple[Any, ...]:
        workbench = b.workbench
        run = await workbench.start("claude-code", site, effort="max")
        transcript = run.transcript.id
        await b.accounts.set_route(run.id, [Stop("claude-code"), Stop("codex")], enabled=True)
        await workbench.prompt(run.id, "Fix the header and the footer.")
        await _told(b, run.id, "route")
        moved = workbench.get(run.id)
        await _idle(b, moved.id)
        await _settled(b)
        await b.assistant.close()
        return transcript, moved

    transcript, moved = _run(scenario)

    assert list((root / "projects").glob(f"*/{ORIGINAL}.jsonl")), (
        "the packet is not all of it: it stays"
    )
    assert moved.name == "codex", "the next destination is another executor"
    assert moved.transcript.id == transcript, "on the same card"
    assert ("route", "Codex · main account") in b.activity(moved.id)
    assert _codex_sent(log, "thread/start"), "a new session there"
    assert not _codex_sent(log, "thread/resume"), "a Claude Code session is not resumed in Codex"
    assert not _codex_sent(log, "thread/fork")
    packet = _packet(log)
    assert packet.startswith(HANDOFF_MARK), "the first prompt is the packet, no model call first"
    assert "## The task\nFix the header and the footer." in packet
    assert f"## Working folder\n{site}" in packet
    assert "Claude Code · main account reached its plan's limit" in packet
    assert "- Agent: The header is fixed; the footer is next." in packet
    assert "- Tool call Edit:" in packet
    assert "index.html" in packet
    assert "- Tool result: done" in packet
    assert f"session {ORIGINAL}" in packet, "the session it takes over, by reference"
    prompts = [e["message"]["content"] for e in b.log() if e.get("type") == "user"]
    assert prompts == ["Fix the header and the footer."], "Claude Code is not prompted again"
    assert _said(moved) == "Read the packet; finishing the footer."
    # Its default model there, and the effort it ran on, onto Codex's levels (25/10/2026).
    first = _codex_sent(log, "turn/start")[0]
    assert "model" not in first
    assert first["effort"] == "high"


def test_a_route_passes_over_a_destination_not_logged_in_and_says_when_none_can_go_on(
    bench: Callable[[Screen | None], Bench], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Claude Code's main account at its limit; the account company-b never logged in, passed
    over; Codex takes over and reaches its own limit; round to the start, the main account is
    still at its own, and nothing is left."""
    b = bench(Screen())
    log = _with_codex(b, monkeypatch, {"say": "Starting."}, {"limit": True})
    site = b.script("site", {"say": "Half of it."}, {"limit": LATER})

    async def scenario() -> ExecutorRun:
        workbench = b.workbench
        added = await b.accounts.add_account("claude-code", "company-b")
        run = await workbench.start("claude-code", site)
        await b.accounts.set_route(
            run.id,
            [
                Stop("claude-code"),
                Stop("claude-code", added.account_id),
                Stop("codex"),
            ],
            enabled=True,
        )
        await workbench.prompt(run.id, "Build the site.")
        await _told(b, run.id, "route")
        await _told(b, run.id, "account")
        moved = workbench.get(run.id)
        await b.assistant.close()
        return moved

    moved = _run(scenario)

    told = [(kind, text) for kind, text in b.activity(moved.id) if kind in ("route", "account")]
    assert told == [
        ("route", "Codex · main account"),
        ("account", "No destination in the session's route can go on now."),
    ], "company-b was passed over; then nothing was left"
    assert moved.name == "codex", "it stays where the route ended"
    assert len(_codex_sent(log, "turn/start")) == 1, "Codex is not prompted again at its limit"
    packet = _packet(log)
    assert "## The task\nBuild the site." in packet
    assert "- Agent: Half of it." in packet


def test_a_route_is_checked_kept_for_a_session_opened_again_and_off_leaves_the_accounts_order(
    bench: Callable[[Screen | None], Bench], monkeypatch: pytest.MonkeyPatch
) -> None:
    _main_sessions(monkeypatch)
    monkeypatch.setenv("FAKE_CLAUDE_SESSION", "s-{folder}")
    b = bench(Screen())
    log = _with_codex(b, monkeypatch, {"say": "Never asked."})
    site = b.script("site", {"result": "First."})

    async def scenario() -> tuple[Any, ...]:
        workbench = b.workbench
        run = await workbench.start("claude-code", site)
        with pytest.raises(ExecutorError, match="has no account 'nobody'"):
            await b.accounts.set_route(run.id, [Stop("codex", "nobody")], enabled=True)
        with pytest.raises(ExecutorError, match="names an account"):
            await b.accounts.set_route(run.id, [Stop("claude-code@ab12")], enabled=True)
        with pytest.raises(ExecutorError, match="not installed"):
            await b.accounts.set_route(run.id, [Stop("pi")], enabled=True)
        empty = await b.accounts.route(run.id)
        await b.accounts.set_route(run.id, [Stop("codex"), Stop("codex")], enabled=False)
        await workbench.prompt(run.id, "Start.")
        await _idle(b, run.id)
        session = workbench.get(run.id).session_id
        assert session is not None
        await workbench.stop(run.id)
        again = await workbench.start("claude-code", site, resume=session)
        kept = await b.accounts.route(again.id)
        await b.assistant.close()
        return run, empty, again, kept

    run, empty, again, kept = _run(scenario)

    assert (empty.stops, empty.enabled) == ((), True), "none until the user sets one"
    assert again.transcript.id != run.transcript.id, "a card of its own"
    assert [s.name for s in kept.stops] == ["codex"], "the route its session had, each once"
    assert kept.enabled is False
    assert not log.exists() or not _codex_sent(log, "turn/start")


def test_with_its_route_off_a_session_at_its_limit_follows_its_executors_accounts(
    bench: Callable[[Screen | None], Bench], monkeypatch: pytest.MonkeyPatch
) -> None:
    _main_sessions(monkeypatch)
    monkeypatch.setenv("FAKE_CLAUDE_SESSION", "s-{folder}")
    b = bench(Screen())
    log = _with_codex(b, monkeypatch, {"say": "Never asked."})
    site = b.script("site", {"limit": LATER})
    b.script("site", {"result": "On the next account."}, resumed=True)

    async def scenario() -> tuple[Any, ...]:
        workbench = b.workbench
        added = await b.accounts.add_account("claude-code", "company-b")
        await workbench.switch_executor_account(added.name, "")
        run = await workbench.start("claude-code", site)
        await b.accounts.set_route(run.id, [Stop("codex")], enabled=False)
        await workbench.prompt(run.id, "Build the site.")
        await _told(b, run.id, "account")
        moved = workbench.get(run.id)
        await b.assistant.close()
        return added, moved

    added, moved = _run(scenario)

    assert moved.name == added.name, "the executor's own order, as before routes"
    assert not log.exists() or not _codex_sent(log, "turn/start")


def test_an_account_at_its_limit_is_known_by_its_usage_however_its_reset_is_written(
    bench: Callable[[Screen | None], Bench],
) -> None:
    """Found by the route's rehearsal on 21/10/2026: Codex writes its reset with no fraction of a
    second, which broke the hand-over at the very moment an account was full."""
    b = bench(Screen())
    workbench = b.workbench

    def told(name: str, used: float, resets: str, model: str = "") -> bool:
        window = UsageWindowView("w", "A window", used, resets, 300, model)
        workbench.notify(ExecutorUsage(name, (window,)))
        return workbench.limited(name)

    assert told("codex", 100, "2099-01-01T00:00:00Z"), "Codex's own way of writing it"
    assert told("claude-code", 100, "2099-01-01T00:00:00.000Z")
    assert not told("a", 100, "2000-01-01T00:00:00Z"), "reset already"
    assert told("b", 100, ""), "no reset said: at its limit until it says"
    assert told("c", 100, "next tuesday"), "a reset not read: at its limit, rather than a crash"
    assert not told("d", 99, "2099-01-01T00:00:00Z")
    assert not told("e", 100, "2099-01-01T00:00:00Z", model="Opus"), "one model's alone"
