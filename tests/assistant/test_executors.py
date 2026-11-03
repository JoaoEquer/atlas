"""Outside executors for real: a conversation delegates to Claude Code through its delegate tool,
and tests/fake_claude.py stands in for `claude` over real pipes and its real control protocol; the
parent's model is the fake model server. What Claude Code asks reaches a desk the test plays as the
user's screen, or Atlas answers it. The database and the tools are real, in a temporary home."""

import asyncio
import json
import os
import weakref
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from accounts_support import AccountsPlugin
from atlas.__main__ import SCHEMAS
from atlas.assistant import (
    HANDOFF_MARK,
    Assistant,
    ConversationEvent,
    ConversationRole,
    Decides,
    InstanceActivity,
    InstanceChanged,
    OpenConversation,
    SubagentChanged,
    WorkbenchEvent,
)
from atlas.home import Database, Home, ModelSettings, Settings, WorkspaceSettings, migrate
from atlas.jobs import SubagentStatus, SubagentStore
from atlas.models import AssistantMessage, CallLedger, ToolResult, UserMessage
from atlas.plugins import Answers, Executor, Permission, Question, Verdict
from atlas.secrets import Vault
from atlas.trust import ApprovalAnswer, ApprovalRequest, Choice, Outcome
from claude_code_support import claude_code
from codex_support import codex
from fake_model import FakeCall, FakeModelServer, FakeReply
from model_support import Connect

CHAT = "/v1/chat/completions"
PARENT = "Skills come first"
GUARDIAN = "You are a security reviewer"
ANSWERING = "Claude Code is working on a task you delegated"
CLAUDE_CODE = "claude-code"

#: What the screen shows: a permission, or a card of questions.
type Asked = Permission | tuple[Question, ...]


class Screen:
    """The user's screen, as the desk sees it: what was asked, and the answers to give, in
    order (a verdict for a permission, answers for questions)."""

    def __init__(self) -> None:
        self.asked: list[Asked] = []
        self.executors: list[str] = []
        self.answers: list[Verdict | Answers] = []
        self.hold = asyncio.Event()
        self.withdrawn = 0

    async def _answer(self, executor: str, asked: Asked) -> Verdict | Answers:
        self.asked.append(asked)
        self.executors.append(executor)
        if not self.answers:
            try:
                await self.hold.wait()
            except asyncio.CancelledError:
                self.withdrawn += 1
                raise
        return self.answers.pop(0)

    async def ask(self, asker: InstanceChanged, questions: Sequence[Question]) -> Answers:
        answer = await self._answer(asker.executor, tuple(questions))
        assert not isinstance(answer, Verdict)
        return answer

    async def approve(self, asker: InstanceChanged, permission: Permission) -> Verdict:
        answer = await self._answer(asker.executor, permission)
        assert isinstance(answer, Verdict)
        return answer

    @property
    def permissions(self) -> list[Permission]:
        return [a for a in self.asked if isinstance(a, Permission)]


class Approvals:
    """The user's approvals in Atlas's conversation: what was asked, and yes or no."""

    def __init__(self, *yes: bool) -> None:
        self.asked: list[ApprovalRequest] = []
        self._yes = list(yes)

    async def __call__(self, request: ApprovalRequest) -> ApprovalAnswer:
        self.asked.append(request)
        choice = Choice.ONCE if self._yes.pop(0) else Choice.DENY
        return ApprovalAnswer(Outcome.ANSWERED, choice)


@pytest.fixture
def claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    script = tmp_path / "claude-script.json"
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(script))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "claude-log.jsonl"))
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", str(tmp_path / "claude-auth.json"))

    def write(*steps: dict[str, Any]) -> None:
        script.write_text(json.dumps(list(steps)), encoding="utf-8")

    return write


#: What the assistant told: a conversation's events, and the workbench's (with no conversation).
type Heard = list[tuple[str, ConversationEvent | WorkbenchEvent]]
type Make = Callable[..., tuple[Assistant, Heard]]


@pytest.fixture
def make(home: Home, vault: Vault, fake: FakeModelServer, connect: Connect, tmp_path: Path) -> Make:
    migrate(Database(home.database_file), SCHEMAS)

    def make(
        desk: Screen | None = None,
        executors: dict[str, Executor] | None = None,
        approvals: Approvals | None = None,
    ) -> tuple[Assistant, Heard]:
        chosen = ModelSettings(provider="openrouter")
        ledger = CallLedger(Database(home.database_file))
        models = connect(fake, "openrouter", settings=chosen, ledger=ledger)
        work = tmp_path / "work"
        (work / "project").mkdir(parents=True, exist_ok=True)
        settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(work))
        heard: Heard = []
        assistant = Assistant(
            home,
            settings,
            models,
            vault,
            listener=lambda conversation, event: heard.append((conversation, event)),
            approver=lambda _conversation: approvals,
            desk=desk,
            executors={CLAUDE_CODE: claude_code()} if executors is None else executors,
            on_workbench=lambda event: heard.append(("", event)),
        )
        return assistant, heard

    return make


#: The accounts plugin on each test's assistant, made on its first use.
_PLUGINS: "weakref.WeakKeyDictionary[Assistant, AccountsPlugin]" = weakref.WeakKeyDictionary()


def _accounts(assistant: Assistant, tmp_path: Path) -> AccountsPlugin:
    """The accounts plugin Atlas ships, on `assistant`, as a home that uses other accounts has
    it (step 22)."""
    found = _PLUGINS.get(assistant)
    if found is None:
        found = _PLUGINS[assistant] = AccountsPlugin(assistant, tmp_path / "accounts-plugin")
    return found


def _run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())  # pyright: ignore[reportArgumentType]


def _delegate(
    goal: str = "Fix the failing test.",
    folder: str = "project",
    approval: str | None = "manual",
    executor: str = CLAUDE_CODE,
    route: Sequence[str] = (),
) -> FakeReply:
    """A delegation, manual unless `approval` says otherwise (`None`: the tool's default), with
    its `route` when one is given."""
    arguments: dict[str, Any] = {"executor": executor, "tasks": [{"goal": goal, "folder": folder}]}
    if approval is not None:
        arguments["approval"] = approval
    if route:
        arguments["route"] = list(route)
    return FakeReply(calls=[FakeCall("delegate", json.dumps(arguments))], when=PARENT)


def _started(log: Path, folder: Path) -> list[list[str]]:
    """The command lines the fake `claude` ran a session in `folder` with, in order."""
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    return [e["argv"] for e in entries if "argv" in e and Path(e["cwd"]) == folder]


def _prompts(log: Path) -> list[str]:
    """The prompts the fake `claude` was given, in order."""
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    return [e["message"]["content"] for e in entries if e.get("type") == "user"]


async def _settle(assistant: Assistant, conversation: OpenConversation) -> None:
    for _ in range(40):
        await conversation.wait()
        await assistant.subagents.settled()
        await asyncio.sleep(0.05)
        if not conversation.busy:
            return


def _notice(conversation: OpenConversation) -> str:
    notices = [
        m.text
        for m in conversation.history
        if isinstance(m, UserMessage) and m.text.startswith("[ASYNC DELEGATION")
    ]
    assert len(notices) == 1, notices
    return notices[0]


async def _start(assistant: Assistant, text: str) -> OpenConversation:
    stored = assistant.conversations.create()
    conversation = assistant.conversation(stored.id)
    conversation.send(text)
    return conversation


def test_claude_code_works_its_permission_reaches_the_user_and_its_result_comes_back(
    make: Make, fake: FakeModelServer, claude: Callable[..., None], home: Home, tmp_path: Path
) -> None:
    claude(
        {"say": "The test imports the wrong module."},
        {"tool": "Bash", "input": {"command": "pytest -x"}},
        {"result": "Fixed the import; the tests pass."},
    )
    fake.reply(
        _delegate(),
        FakeReply(text="Claude Code is on it.", when=PARENT),
        FakeReply(text="Claude Code fixed the import.", when=PARENT),
    )
    screen = Screen()
    screen.answers.append(Verdict(True))
    assistant, heard = make(screen)

    async def scenario() -> OpenConversation:
        conversation = await _start(assistant, "Fix my failing test.")
        await _settle(assistant, conversation)
        await assistant.close()
        return conversation

    conversation = _run(scenario)

    assert [(a.tool, a.summary) for a in screen.permissions] == [("Bash", "pytest -x")]
    assert screen.executors == [CLAUDE_CODE]
    notice = _notice(conversation)
    assert "TASK 1/1: Fix the failing test.  (status=completed" in notice
    assert "Fixed the import; the tests pass." in notice
    assert "Model: Claude Code" in notice
    updates = [e for c, e in heard if isinstance(e, SubagentChanged)]
    assert updates[0].executor == CLAUDE_CODE
    assert {u.status for u in updates} == {"running", "completed"}
    activity = [e.kind for c, e in heard if isinstance(e, InstanceActivity)]
    assert activity == ["prompted", "said", "called", "returned"]
    cards = [e for c, e in heard if isinstance(e, InstanceChanged)]
    assert {(card.owner, card.parent_id) for card in cards} == {("atlas", conversation.id)}
    assert cards[-1].state == "ended", "its card goes once its task ends"
    assert assistant.workbench.runs() == []
    [record] = SubagentStore(Database(home.database_file)).delegation(updates[0].delegation_id)
    assert (record.executor, record.session_id) == (CLAUDE_CODE, "fake-session-0001")
    assert Path(record.folder) == tmp_path / "work" / "project"
    kept = assistant.conversations.open(record.conversation_id).history
    assert isinstance(kept[0], UserMessage)
    assert kept[0].text == "Fix the failing test."
    assert any(isinstance(m, AssistantMessage) and m.tool_calls for m in kept)
    assert any(isinstance(m, ToolResult) and m.text == "done" for m in kept)
    parent_tools = {t["function"]["name"] for t in fake.bodies(CHAT)[0]["tools"]}
    delegate = next(t for t in fake.bodies(CHAT)[0]["tools"] if t["function"]["name"] == "delegate")
    assert "delegate" in parent_tools
    assert delegate["function"]["parameters"]["properties"]["executor"]["enum"] == [CLAUDE_CODE]


def test_a_denial_and_a_question_answered_by_the_user_reach_claude_code(
    make: Make, fake: FakeModelServer, claude: Callable[..., None]
) -> None:
    questions = [
        {"question": "Which runner?", "header": "Runner", "options": [{"label": "Jest"},
         {"label": "Vitest"}], "multiSelect": False},
    ]  # fmt: skip
    claude(
        {"tool": "Bash", "input": {"command": "rm -rf node_modules"}},
        {"questions": questions},
        {"result": "Moved to Vitest, node_modules kept."},
    )
    fake.reply(
        _delegate(), FakeReply(text="On it.", when=PARENT), FakeReply(text="Done.", when=PARENT)
    )
    screen = Screen()
    screen.answers += [
        Verdict(False, "The user denied this: keep node_modules"),
        {"Which runner?": "Vitest"},
    ]
    assistant, _ = make(screen)

    async def scenario() -> OpenConversation:
        conversation = await _start(assistant, "Move my tests to a faster runner.")
        await _settle(assistant, conversation)
        await assistant.close()
        return conversation

    conversation = _run(scenario)

    assert [isinstance(a, tuple) for a in screen.asked] == [False, True]
    assert "Moved to Vitest, node_modules kept." in _notice(conversation)


def test_atlas_answers_for_the_user_when_asked_to(
    make: Make, fake: FakeModelServer, claude: Callable[..., None]
) -> None:
    questions = [
        {"question": "Which runner?", "header": "Runner", "options": [{"label": "Jest"},
         {"label": "Vitest"}], "multiSelect": False},
    ]  # fmt: skip
    claude(
        {"sleep": 0.6},
        {"tool": "Bash", "input": {"command": "npm test"}},
        {"questions": questions},
        {"result": "Tests run on Vitest."},
    )
    fake.reply(
        _delegate(),
        FakeReply(text="On it.", when=PARENT),
        FakeReply(text="APPROVE", when=GUARDIAN),
        FakeReply(text='{"Which runner?": "Vitest"}', when=ANSWERING),
        FakeReply(text="Done on Vitest.", when=PARENT),
    )
    screen = Screen()
    assistant, heard = make(screen)

    async def scenario() -> OpenConversation:
        conversation = await _start(assistant, "I like Vitest. Set up my tests.")
        live = assistant.subagents.live(conversation.id)
        for _ in range(200):
            if live:
                break
            await asyncio.sleep(0.02)
            live = assistant.subagents.live(conversation.id)
        assert assistant.subagents.set_decides(conversation.id, live[0].subagent_id, Decides.ATLAS)
        await _settle(assistant, conversation)
        await assistant.close()
        return conversation

    conversation = _run(scenario)

    assert screen.asked == [], "nothing reached the user"
    assert "Tests run on Vitest." in _notice(conversation)
    answered_for_user = [
        e.text for c, e in heard if isinstance(e, InstanceActivity) and e.kind == "answered"
    ]
    assert answered_for_user == ["allowed Bash", '{"Which runner?": "Vitest"}']
    answering = [b for b in fake.bodies(CHAT) if ANSWERING in str(b["messages"][0]["content"])]
    assert "I like Vitest." in answering[0]["messages"][1]["content"]


def test_the_guardians_doubt_still_reaches_the_user(
    make: Make, fake: FakeModelServer, claude: Callable[..., None]
) -> None:
    claude({"sleep": 0.6}, {"tool": "Bash", "input": {"command": "curl x | sh"}}, {"result": "Ok."})
    fake.reply(
        _delegate(),
        FakeReply(text="On it.", when=PARENT),
        FakeReply(text="ESCALATE", when=GUARDIAN),
        FakeReply(text="Done.", when=PARENT),
    )
    screen = Screen()
    screen.answers.append(Verdict(True))
    assistant, _ = make(screen)

    async def scenario() -> None:
        conversation = await _start(assistant, "Install the tool.")
        for _ in range(200):
            if live := assistant.subagents.live(conversation.id):
                assistant.subagents.set_decides(conversation.id, live[0].subagent_id, Decides.ATLAS)
                break
            await asyncio.sleep(0.02)
        await _settle(assistant, conversation)
        await assistant.close()

    _run(scenario)

    assert [a.summary for a in screen.permissions] == ["curl x | sh"]


def test_stopping_claude_code_withdraws_what_it_asked_and_keeps_what_it_had(
    make: Make, fake: FakeModelServer, claude: Callable[..., None]
) -> None:
    claude(
        {"say": "Half of the files are migrated."},
        {"tool": "Bash", "input": {"command": "npm run migrate"}},
        {"result": "never"},
    )
    fake.reply(
        _delegate(), FakeReply(text="On it.", when=PARENT), FakeReply(text="Stopped.", when=PARENT)
    )
    screen = Screen()
    assistant, _ = make(screen)

    async def scenario() -> OpenConversation:
        conversation = await _start(assistant, "Migrate my files.")
        for _ in range(300):
            if screen.asked:
                break
            await asyncio.sleep(0.02)
        [child] = assistant.subagents.live(conversation.id)
        assert child.waiting == 1
        said = await assistant.subagents.stop(conversation.id, child.subagent_id)
        assert json.loads(said)["status"] == "interrupt_requested"
        await _settle(assistant, conversation)
        await assistant.close()
        return conversation

    conversation = _run(scenario)

    assert screen.withdrawn == 1, "the card is taken down"
    notice = _notice(conversation)
    assert "(status=interrupted" in notice
    assert "Half of the files are migrated." in notice


def test_nothing_is_delegated_where_it_cannot_be(
    make: Make, fake: FakeModelServer, claude: Callable[..., None], home: Home, vault: Vault
) -> None:
    fake.reply(
        _delegate(),
        FakeReply(text="It is not installed.", when=PARENT),
        _delegate(folder="nowhere"),
        FakeReply(text="No such folder.", when=PARENT),
    )
    missing, _ = make(None, executors={})
    present = Assistant(
        home,
        missing.settings,
        missing.models,
        vault,
        listener=lambda conversation, event: None,
        executors={CLAUDE_CODE: claude_code()},
    )

    async def scenario() -> list[str]:
        said: list[str] = []
        for assistant in (missing, present):
            conversation = await _start(assistant, "Fix it.")
            await _settle(assistant, conversation)
            said += [m.text for m in conversation.history if isinstance(m, ToolResult)]
            await assistant.close()
        return said

    results = _run(scenario)

    assert "There is no executor 'claude-code'. Installed: none here." in results[0]
    assert "does not exist, so nothing was delegated" in results[1]


def test_a_routine_cannot_hand_work_to_claude_code(
    make: Make, fake: FakeModelServer, claude: Callable[..., None]
) -> None:
    arguments = {"executor": CLAUDE_CODE, "tasks": [{"goal": "Fix it.", "folder": "project"}]}
    fake.reply(
        FakeReply(calls=[FakeCall("delegate", json.dumps(arguments))]),
        FakeReply(text="Could not."),
    )
    assistant, _ = make(None)

    async def scenario() -> list[str]:
        stored = assistant.conversations.create()
        conversation = assistant.conversation(stored.id, role=ConversationRole.ROUTINE)
        conversation.send("Fix the nightly build.")
        await _settle(assistant, conversation)
        await assistant.close()
        return [m.text for m in conversation.history if isinstance(m, ToolResult)]

    [result] = _run(scenario)

    assert "would have nobody to answer it. Nothing was delegated" in result
    assert SubagentStatus.RUNNING  # nothing started


# -- its own automatic mode, the default since 20/10/2026 (decided with the user on 17/10/2026) ----


def test_a_delegation_is_auto_by_default_and_manual_only_when_the_model_asks_for_it(
    make: Make, fake: FakeModelServer, claude: Callable[..., None], tmp_path: Path
) -> None:
    claude(
        {"tool": "Bash", "input": {"command": "pytest -x"}},
        {"result": "Fixed; the tests pass."},
    )
    fake.reply(
        _delegate(approval=None),
        FakeReply(text="Claude Code is on it, approving its own requests.", when=PARENT),
        _delegate("Fix the next test."),
        FakeReply(text="And now the next one.", when=PARENT),
        FakeReply(text="Both are fixed.", when=PARENT),
    )
    screen = Screen()
    screen.answers.append(Verdict(True))
    approvals = Approvals(True)
    assistant, heard = make(screen, approvals=approvals)
    project = tmp_path / "work" / "project"
    log = tmp_path / "claude-log.jsonl"

    async def scenario() -> None:
        conversation = await _start(assistant, "Fix my tests.")
        await _settle(assistant, conversation)
        await assistant.close()

    _run(scenario)

    assert approvals.asked == [], "the automatic mode starts without a question"
    auto, manual = _started(log, project)
    assert auto[auto.index("--permission-mode") + 1] == "auto"
    assert "--settings" not in auto, "Atlas adds no rule to an outside program's own"
    assert "bypassPermissions" not in auto
    assert "--dangerously-skip-permissions" not in auto
    assert manual[manual.index("--permission-mode") + 1] == "default"
    assert "--settings" not in manual
    assert [p.summary for p in screen.permissions] == ["pytest -x"], "only the manual run asked"
    first, second = _prompts(log)
    assert first.startswith("Fix the failing test.")
    assert "Work only inside" not in first, "Atlas adds no rule to the task either (21/10/2026)"
    assert second == "Fix the next test."
    cards = [e for _, e in heard if isinstance(e, InstanceChanged)]
    assert {card.auto for card in cards} == {True, False}


def test_another_executors_delegation_runs_in_its_own_default_mode(
    claude: Callable[..., None],
    fake: FakeModelServer,
    make: Make,
    tmp_path: Path,
    home: Home,
    vault: Vault,
) -> None:
    claude({"result": "Done."})
    fake.reply(
        _delegate(approval="auto", executor="other-code"),
        FakeReply(text="On it.", when=PARENT),
        FakeReply(text="Done.", when=PARENT),
    )
    made, _ = make(Screen())
    approvals = Approvals(True)
    assistant = Assistant(
        home,
        made.settings,
        made.models,
        vault,
        listener=lambda conversation, event: None,
        approver=lambda _conversation: approvals,
        desk=Screen(),
        executors={"other-code": claude_code()},
    )

    async def scenario() -> None:
        conversation = await _start(assistant, "Fix it.")
        await _settle(assistant, conversation)
        await assistant.close()
        await made.close()

    _run(scenario)

    [started] = _started(tmp_path / "claude-log.jsonl", tmp_path / "work" / "project")
    assert started[started.index("--permission-mode") + 1] == "default"
    assert approvals.asked == [], "another executor's automatic mode is never even offered"


# -- accounts (decided with the user on 12/10/2026) ------------------------------------------------


def _log(tmp_path: Path) -> list[dict[str, Any]]:
    lines = (tmp_path / "claude-log.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_the_plan_limit_stops_claude_code_another_account_takes_over_and_the_session_goes_on(
    make: Make, fake: FakeModelServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: Home
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "project.json").write_text(
        json.dumps([{"say": "Half of it is done."}, {"limit": 1760296800}]), encoding="utf-8"
    )
    (scripts / "project.resume.json").write_text(
        json.dumps([{"say": "Going on."}, {"result": "All of it is done."}]), encoding="utf-8"
    )
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(scripts))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "claude-log.jsonl"))
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", str(tmp_path / "claude-auth.json"))
    fake.reply(
        _delegate(),
        FakeReply(text="Claude Code is on it.", when=PARENT),
        FakeReply(text="The account hit its limit.", when=PARENT),
        FakeReply(text="Finished on the other account.", when=PARENT),
    )
    assistant, _ = make(Screen())

    async def scenario() -> tuple[OpenConversation, str, str, str]:
        conversation = await _start(assistant, "Fix my failing test.")
        await _settle(assistant, conversation)
        store = assistant.subagent_store
        [stopped] = [
            r for batch in [store.delegation(d) for d in _delegations(home)] for r in batch
        ]
        switched = await assistant.subagents.switch_account("other@example.com")
        resumed = await assistant.subagents.resume(conversation, stopped.id, "")
        await _settle(assistant, conversation)
        await assistant.close()
        return conversation, switched, resumed, stopped.id

    conversation, switched, resumed, stopped_id = _run(scenario)

    notices = [
        m.text for m in conversation.history if isinstance(m, UserMessage) and "ASYNC" in m.text
    ]
    assert len(notices) == 2
    assert "someone@example.com reached its Claude Code plan limit" in notices[0]
    assert "It resets at" in notices[0]
    assert f"subagent_id='{stopped_id}'" in notices[0], "the id to resume, not a placeholder"
    assert "Half of it is done." in notices[0]
    assert json.loads(switched)["account"] == "other@example.com"
    assert json.loads(resumed)["status"] == "dispatched"
    assert "All of it is done." in notices[1]
    log = _log(tmp_path)
    assert ["auth", "login", "--claudeai", "--email", "other@example.com"] in [
        entry["argv"] for entry in log if "argv" in entry
    ]
    runs = [entry["argv"] for entry in log if "argv" in entry and "auth" not in entry["argv"]]
    assert "--resume=fake-session-0001" in runs[-1], "the same session goes on"
    resumed_mode = runs[-1][runs[-1].index("--permission-mode") + 1]
    assert resumed_mode == "auto", "it goes on in the automatic mode every delegation starts in"
    records = [
        r
        for batch in [assistant.subagent_store.delegation(d) for d in _delegations(home)]
        for r in batch
    ]
    assert sorted(r.account for r in records) == ["other@example.com", "someone@example.com"]
    assert len({r.conversation_id for r in records}) == 1, "one stored conversation, continued"


def test_no_account_switch_while_claude_code_works_and_none_without_a_login(
    make: Make, fake: FakeModelServer, claude: Callable[..., None], tmp_path: Path
) -> None:
    claude({"tool": "Bash", "input": {"command": "npm test"}}, {"result": "Ok."})
    fake.reply(
        _delegate(), FakeReply(text="On it.", when=PARENT), FakeReply(text="Ok.", when=PARENT)
    )
    screen = Screen()
    assistant, _ = make(screen)

    async def scenario() -> tuple[str, list[str]]:
        conversation = await _start(assistant, "Run the tests.")
        for _ in range(300):
            if screen.asked:
                break
            await asyncio.sleep(0.02)
        refused = await assistant.subagents.switch_account("other@example.com")
        screen.answers.append(Verdict(True))
        screen.hold.set()
        await _settle(assistant, conversation)
        (tmp_path / "claude-auth.json").write_text(json.dumps({"loggedIn": False}), "utf-8")
        fake.reply(_delegate(), FakeReply(text="Not logged in.", when=PARENT))
        conversation.send("Again.")
        await _settle(assistant, conversation)
        await assistant.close()
        return refused, [m.text for m in conversation.history if isinstance(m, ToolResult)]

    refused, results = _run(scenario)

    assert "still working on a task" in refused
    assert "Claude Code is not logged in on this computer" in results[-1]


def _delegations(home: Home) -> list[str]:
    connection = Database(home.database_file).connect()
    try:
        rows = connection.execute("SELECT DISTINCT delegation_id FROM jobs_subagents").fetchall()
    finally:
        connection.close()
    return [str(row[0]) for row in rows]


def test_a_delegation_runs_on_its_folders_account_even_with_the_main_one_logged_out(
    make: Make, fake: FakeModelServer, claude: Callable[..., None], tmp_path: Path, home: Home
) -> None:
    """Step 16h: a folder's account is the account of every session there, the user's or Atlas's;
    on another account, the main one's login is not needed."""
    claude({"account": True}, {"result": "Fixed, on the work account."})
    fake.reply(
        _delegate(),
        FakeReply(text="Claude Code is on it.", when=PARENT),
        FakeReply(text="Done.", when=PARENT),
    )
    assistant, _ = make(Screen())
    (tmp_path / "claude-auth.json").write_text(json.dumps({"loggedIn": False}), "utf-8")
    project = tmp_path / "work" / "project"

    async def scenario() -> tuple[OpenConversation, str, str]:
        work = await _accounts(assistant, tmp_path).add_account(CLAUDE_CODE, "work")
        await assistant.workbench.switch_executor_account(work.name, "w@example.com")
        await _accounts(assistant, tmp_path).set_folder_account(
            CLAUDE_CODE, str(project), work.account_id
        )
        conversation = await _start(assistant, "Fix my failing test.")
        await _settle(assistant, conversation)
        await assistant.close()
        return conversation, work.name, work.folder

    conversation, name, folder = _run(scenario)

    notice = _notice(conversation)
    assert "Fixed, on the work account." in notice
    started = [e for e in _log(tmp_path) if "argv" in e and Path(e["cwd"]) == project]
    assert [e["account"] for e in started] == [folder]
    [record] = [
        r
        for batch in [assistant.subagent_store.delegation(d) for d in _delegations(home)]
        for r in batch
    ]
    assert (record.executor, record.account) == (name, "w@example.com")


# -- Atlas picks the account and the route (asked for by the user on 21/10/2026) -----------------

#: A plan's limit that resets in 2100.
LATER = 4102444800


def _records(assistant: Assistant, home: Home) -> list[Any]:
    return [
        r
        for batch in [assistant.subagent_store.delegation(d) for d in _delegations(home)]
        for r in batch
    ]


def _with_codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *steps: dict[str, Any]) -> Path:
    """The fake `codex` on the user's own folder, doing `steps` for every prompt; its log."""
    codex_home = tmp_path / ".codex"
    codex_home.mkdir(exist_ok=True)
    script = tmp_path / "codex-script.json"
    script.write_text(json.dumps(list(steps)), encoding="utf-8")
    log = tmp_path / "codex-log.jsonl"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("FAKE_CODEX_SCRIPT", str(script))
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    return log


def _codex_sent(log: Path, method: str) -> list[dict[str, Any]]:
    lines = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    return [line["params"] for line in lines if line.get("method") == method]


def _codex_prompts(log: Path) -> list[str]:
    return [
        "".join(str(part.get("text", "")) for part in params["input"])
        for params in _codex_sent(log, "turn/start")
    ]


def _scripts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **scripts: list[Any]) -> None:
    """The fake `claude`'s scripts by folder (`project`; `project_resume` for a resumed session)."""
    folder = tmp_path / "scripts"
    folder.mkdir(exist_ok=True)
    for name, steps in scripts.items():
        target = folder / f"{name.replace('_resume', '.resume')}.json"
        target.write_text(json.dumps(steps), encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(folder))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "claude-log.jsonl"))
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", str(tmp_path / "claude-auth.json"))


def test_atlas_names_the_account_a_delegation_runs_on_over_the_folders(
    make: Make, fake: FakeModelServer, claude: Callable[..., None], tmp_path: Path, home: Home
) -> None:
    claude({"account": True}, {"result": "Done on the account Atlas named."})
    fake.reply(
        _delegate(executor="claude-code@nobody"),
        _delegate(executor="claude-code@second"),
        FakeReply(text="Claude Code is on it.", when=PARENT),
        FakeReply(text="Done.", when=PARENT),
    )
    assistant, _ = make(Screen())
    project = tmp_path / "work" / "project"

    async def scenario() -> tuple[OpenConversation, Any]:
        workbench = assistant.workbench
        first = await _accounts(assistant, tmp_path).add_account(CLAUDE_CODE, "first")
        second = await _accounts(assistant, tmp_path).add_account(CLAUDE_CODE, "second")
        await workbench.switch_executor_account(first.name, "f@example.com")
        await workbench.switch_executor_account(second.name, "s@example.com")
        await _accounts(assistant, tmp_path).set_folder_account(
            CLAUDE_CODE, str(project), first.account_id
        )
        conversation = await _start(assistant, "Fix my failing test on the second account.")
        await _settle(assistant, conversation)
        await assistant.close()
        return conversation, second

    conversation, second = _run(scenario)

    delegate = next(t for t in fake.bodies(CHAT)[0]["tools"] if t["function"]["name"] == "delegate")
    assert delegate["function"]["parameters"]["properties"]["executor"]["enum"] == [
        CLAUDE_CODE, "claude-code@main", "claude-code@first", "claude-code@second",
    ]  # fmt: skip
    results = [m.text for m in conversation.history if isinstance(m, ToolResult)]
    assert results[0].startswith("[Atlas: Claude Code has no account 'nobody'. Its accounts: ")
    assert "main, first, second" in results[0]
    assert "Done on the account Atlas named." in _notice(conversation)
    started = [e for e in _log(tmp_path) if "argv" in e and Path(e["cwd"]) == project]
    assert [e["account"] for e in started] == [second.folder], "not the folder's account"
    [record] = _records(assistant, home)
    assert (record.executor, record.account) == (second.name, "s@example.com")


def test_a_delegations_route_goes_on_in_codex_from_the_handoff_packet(
    make: Make,
    fake: FakeModelServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    home: Home,
) -> None:
    """Claude Code's account reaches its limit mid-task; the task's route names Codex next, which
    starts a session of its own whose first prompt is the packet: no model call before it."""
    _scripts(
        tmp_path,
        monkeypatch,
        project=[
            {"say": "The import is fixed; the fixture is next."},
            {"tool": "Edit", "input": {"file_path": "conftest.py"}},
            {"limit": LATER},
        ],
    )
    log = _with_codex(tmp_path, monkeypatch, {"say": "Read the handoff; fixed the fixture too."})
    fake.reply(
        _delegate(approval=None, route=[CLAUDE_CODE, "codex"]),
        FakeReply(text="Claude Code is on it.", when=PARENT),
        FakeReply(text="Done.", when=PARENT),
    )
    assistant, heard = make(Screen(), executors={CLAUDE_CODE: claude_code(), "codex": codex()})
    _accounts(assistant, tmp_path)  # a route is the accounts plugin's

    async def scenario() -> OpenConversation:
        conversation = await _start(assistant, "Fix my failing test.")
        await _settle(assistant, conversation)
        await assistant.close()
        return conversation

    conversation = _run(scenario)

    notice = _notice(conversation)
    assert "TASK 1/1: Fix the failing test.  (status=completed" in notice
    assert "Read the handoff; fixed the fixture too." in notice
    assert "Model: Codex" in notice
    assert _codex_sent(log, "thread/start"), "a new session there"
    assert not _codex_sent(log, "thread/resume")
    [packet] = _codex_prompts(log)
    assert packet.startswith(HANDOFF_MARK)
    assert "## The task\nFix the failing test." in packet
    assert "Claude Code · main account reached its plan's limit" in packet
    assert "by the task's route" in packet
    assert "The import is fixed; the fixture is next." in packet
    assert _prompts(tmp_path / "claude-log.jsonl") == ["Fix the failing test."]
    routed = [e for _c, e in heard if isinstance(e, InstanceActivity) and e.kind == "route"]
    assert [e.text for e in routed] == ["Codex · main account"]
    [record] = _records(assistant, home)
    assert (record.executor, record.model, record.status) == (
        "codex",
        "Codex",
        SubagentStatus.COMPLETED,
    )


def test_a_stopped_task_resumes_on_another_account_in_a_copy_then_on_another_executor(
    make: Make,
    fake: FakeModelServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    home: Home,
) -> None:
    """With the hand-over off, a task stops at its limit; resumed on another account it goes on in
    a copy there, and resumed on Codex, from the packet."""
    projects = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects"
    monkeypatch.setenv("FAKE_CLAUDE_PROJECTS", str(projects))
    monkeypatch.setenv("FAKE_CLAUDE_SESSION", "s-{folder}")
    _scripts(
        tmp_path,
        monkeypatch,
        project=[{"say": "Half of it."}, {"limit": LATER}],
        project_resume=[{"account": True}, {"say": "More of it."}, {"limit": LATER}],
    )
    log = _with_codex(tmp_path, monkeypatch, {"say": "All of it, in Codex."})
    fake.reply(
        _delegate(approval=None),
        *[FakeReply(text="Noted.", when=PARENT) for _ in range(4)],
    )
    assistant, _ = make(Screen(), executors={CLAUDE_CODE: claude_code(), "codex": codex()})

    async def scenario() -> tuple[OpenConversation, Any]:
        workbench = assistant.workbench
        work = await _accounts(assistant, tmp_path).add_account(CLAUDE_CODE, "work")
        await workbench.switch_executor_account(work.name, "w@example.com")
        await _accounts(assistant, tmp_path).set_failover(
            CLAUDE_CODE, [], enabled=False, compact=False
        )
        conversation = await _start(assistant, "Fix my failing test.")
        await _settle(assistant, conversation)
        resumed: list[str] = []
        for executor in ("claude-code@work", "codex"):
            stopped = next(
                r
                for r in _records(assistant, home)
                if r.status is SubagentStatus.FAILED and r.id not in resumed
            )
            resumed.append(stopped.id)
            await assistant.subagents.resume(conversation, stopped.id, "", executor)
            await _settle(assistant, conversation)
        await assistant.close()
        return conversation, work

    conversation, work = _run(scenario)

    runs = [e for e in _log(tmp_path) if "argv" in e and not {"auth", "--version"} & set(e["argv"])]
    copied = runs[-1]
    assert copied["account"] == work.folder, "on the account Atlas named"
    assert "--fork-session" in copied["argv"], "the same harness: a copy"
    [packet] = _codex_prompts(log)
    assert packet.startswith(HANDOFF_MARK)
    assert "Atlas moved this task here from Claude Code · work" in packet
    records = sorted(_records(assistant, home), key=lambda r: r.started_at)
    assert [(r.executor, r.status) for r in records] == [
        (CLAUDE_CODE, SubagentStatus.FAILED),
        (work.name, SubagentStatus.FAILED),
        ("codex", SubagentStatus.COMPLETED),
    ]
    assert len({r.conversation_id for r in records}) == 1, "one stored conversation, continued"
    notices = [
        m.text for m in conversation.history if isinstance(m, UserMessage) and "ASYNC" in m.text
    ]
    assert "All of it, in Codex." in notices[-1]


def test_executor_accounts_tells_each_accounts_login_usage_and_limit(
    make: Make, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scripts(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_CLAUDE_LIMITED", "own")
    assistant, _ = make(Screen())
    project = tmp_path / "work" / "project"

    async def scenario() -> tuple[str, str, str]:
        workbench = assistant.workbench
        work = await _accounts(assistant, tmp_path).add_account(CLAUDE_CODE, "work")
        await workbench.switch_executor_account(work.name, "w@example.com")
        await _accounts(assistant, tmp_path).set_folder_account(
            CLAUDE_CODE, str(project), work.account_id
        )
        listed = await assistant.subagents.accounts()
        unknown = await assistant.subagents.accounts("pi")
        twice = await assistant.subagents.switch_account("w@example.com", CLAUDE_CODE)
        await assistant.close()
        return listed, unknown, twice

    listed, unknown, twice = _run(scenario)

    [executor] = json.loads(listed)["executors"]
    assert executor["executor"] == CLAUDE_CODE
    assert executor["hand_over"] == {"on": True, "compacts_first": True}
    main, work = executor["accounts"]
    assert (main["name"], main["login"], main["logged_in"]) == (
        "claude-code@main",
        "someone@example.com",
        True,
    )
    assert main["at_limit"] is True, "its session window is used up"
    assert any(limit["used_percent"] == 100 for limit in main["limits"])
    assert main["extra_usage"] is False
    assert (work["name"], work["login"], work["at_limit"]) == (
        "claude-code@work",
        "w@example.com",
        False,
    )
    assert unknown.startswith("[Atlas: There is no executor 'pi'")
    assert twice == (
        "[Atlas: w@example.com is logged in already on claude-code@work: delegate with "
        "executor='claude-code@work' (or resume with it) instead of logging in again.]"
    )
