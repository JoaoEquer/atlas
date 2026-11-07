"""The outward actions of gh, gws and gcloud, and the gate asking the user about them: in auto mode,
past the guardian, through every disguise the detection unwraps and the ones PowerShell adds."""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from atlas.agent import Tool
from atlas.home import Database, Home, TrustSettings, migrate
from atlas.models import SCHEMA as MODELS_SCHEMA
from atlas.models import CallLedger, Models
from atlas.trust import (
    SCHEMA,
    ApprovalAnswer,
    ApprovalGate,
    ApprovalRequest,
    Choice,
    Outcome,
    TrustStore,
)
from atlas.trust.outward import email_recipients, outward_actions
from fake_model import FakeModelServer
from model_support import Connect


async def _nothing(arguments: dict[str, Any]) -> str:
    return "ran"


RUN = Tool("run_command", "Runs a command.", {}, _nothing)


class _User:
    def __init__(self, *answers: ApprovalAnswer) -> None:
        self.answers = list(answers)
        self.asked: list[ApprovalRequest] = []

    async def __call__(self, request: ApprovalRequest) -> ApprovalAnswer:
        self.asked.append(request)
        return self.answers.pop(0)


def _yes() -> ApprovalAnswer:
    return ApprovalAnswer(Outcome.ANSWERED, Choice.ONCE)


def _run(gate: ApprovalGate, command: str) -> str | None:
    return asyncio.run(gate(RUN, {"command": command}))


def _reasons(command: str) -> list[str]:
    return [reason for _, reason in outward_actions(command)]


# -- the rules ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "gh pr merge 12 --squash",
        "gh pr create --title x --body y",
        "gh issue create -t bug -b text",
        "gh issue comment 3 --body thanks",
        "gh repo delete owner/repo --yes",
        "gh release create v1.0",
        "gh secret set TOKEN",
        "gh workflow run deploy.yml",
        "gh auth token",
        "gh api -X PUT repos/o/r/pulls/1/merge",
        "gh api --method=DELETE repos/o/r",
        "gh api -XPOST repos/o/r/issues",
        "gh api repos/o/r/issues -f title=x",
        "gh api graphql --input query.json",
    ],
)
def test_what_changes_github_is_an_outward_action(command: str) -> None:
    assert _reasons(command), command


@pytest.mark.parametrize(
    "command",
    [
        "gh pr list --search create",
        "gh pr view 12 --comments",
        "gh pr checks 12",
        "gh issue list",
        "gh auth status",
        "gh repo clone owner/repo",
        "gh api repos/o/r/pulls",
        "gh api -X GET search/issues -f q=bug",
        "gh --version",
        "winget install GitHub.cli gh",
        "which gh",
    ],
)
def test_what_only_reads_github_is_not(command: str) -> None:
    assert _reasons(command) == [], command


@pytest.mark.parametrize(
    ("command", "said"),
    [
        ("gws gmail +send --to a@b.c --subject s --body b", "sends an email"),
        ("gws gmail +reply --message-id 1 --body ok", "sends an email"),
        ("gws gmail +forward --message-id 1 --to x@y.z", "sends an email"),
        ("gws gmail users messages send --json '{}'", "sends an email"),
        ("gws gmail users drafts send --json '{}'", "sends an email"),
        ("gws gmail users messages trash --params '{}'", "changes something"),
        ("gws calendar +insert --summary s --start a --end b", "changes something"),
        ("gws calendar events delete --params '{}'", "changes something"),
        ("gws calendar events quickAdd --params '{}'", "changes something"),
        ("gws drive +upload report.pdf", "changes something"),
        ("gws auth export --unmasked", "prints the Google credentials"),
        ("gws auth setup", "creates a Google Cloud project"),
        ("gws --format table gmail users messages send", "cannot read"),
    ],
)
def test_what_changes_google_workspace_is_an_outward_action(command: str, said: str) -> None:
    (reason,) = _reasons(command)
    assert said in reason


@pytest.mark.parametrize(
    "command",
    [
        "gws gmail +triage",
        "gws gmail +read --message-id 1",
        "gws calendar +agenda",
        'gws gmail users messages list --params \'{"q": "is:unread"}\'',
        "gws gmail users messages get --params '{}'",
        "gws calendar events list --params '{}'",
        "gws gmail +send --to a@b.c --subject s --body b --dry-run",
        "gws schema gmail.users.messages.send",
        "gws auth login -s gmail,calendar",
        "gws auth status",
        "gws --version",
        "gws gmail --help",
    ],
)
def test_what_only_reads_google_workspace_is_not(command: str) -> None:
    assert _reasons(command) == [], command


@pytest.mark.parametrize(
    ("command", "outward"),
    [
        ("gcloud projects delete my-project", True),
        ("gcloud projects delete listing-project", True),
        ("gcloud services enable gmail.googleapis.com", True),
        ("gcloud auth print-access-token", True),
        ("gcloud config set project p", True),
        ("gcloud projects list", False),
        ("gcloud compute instances describe vm --zone z", False),
        ("gcloud config get-value project", False),
        ("gcloud auth list", False),
        ("gcloud --version", False),
        ("gcloud projects delete --help", False),
    ],
)
def test_gcloud_asks_unless_it_reads(command: str, outward: bool) -> None:
    assert bool(_reasons(command)) is outward, command


@pytest.mark.parametrize(
    "command",
    [
        "cd repo && gh pr merge 1",
        "git fetch; gh pr merge 1",
        'echo "$(gh pr merge 1)"',
        "sh -c 'gh pr merge 1'",
        'bash -c "gws gmail +send --to a@b.c --subject s --body b"',
        "FOO=1 timeout 30 gh pr merge 1",
        "env GH_TOKEN=x gh pr merge 1",
        "/usr/local/bin/gh pr merge 1",
        'g""h pr merge 1',
        "GH PR MERGE 1",
        '& "C:\\Program Files\\GitHub CLI\\gh.exe" pr merge 1',
        "cmd /c gh pr merge 1",
        'powershell -Command "gh pr merge 1"',
        "gh.exe pr merge 1",
        "gws.cmd gmail +send --to a@b.c --subject s --body b",
    ],
)
def test_every_disguise_still_asks(command: str) -> None:
    user = _User(ApprovalAnswer(Outcome.ANSWERED, Choice.DENY))
    gate = ApprovalGate(approver=user)

    said = _run(gate, command)

    assert said is not None
    assert said.startswith("[Atlas: BLOCKED")
    assert len(user.asked) == 1, command


# -- the gate -------------------------------------------------------------------------------------


def test_an_outward_action_asks_even_in_auto_mode_and_the_yes_covers_one_call() -> None:
    user = _User(_yes(), _yes())
    gate = ApprovalGate(approver=user)
    gate.set_auto_mode(True)

    assert _run(gate, "gh pr merge 1") is None
    assert _run(gate, "gh pr merge 2") is None
    assert _run(gate, "gh pr view 3") is None

    first, second = user.asked
    assert first.target == "gh pr merge 1"
    assert "changes something on GitHub" in first.reason
    assert not first.allow_conversation
    assert not first.allow_always
    assert second.target == "gh pr merge 2"


def test_a_refusal_tells_the_model_not_to_try_another_way() -> None:
    user = _User(ApprovalAnswer(Outcome.ANSWERED, Choice.DENY, "not this one"))

    said = _run(ApprovalGate(approver=user), "gws gmail +send --to a@b.c --subject s --body b")

    assert said is not None
    assert 'user: "not this one"' in said
    assert "do NOT reach the same result with another command" in said


def test_with_nobody_to_answer_an_outward_action_is_refused() -> None:
    said = _run(ApprovalGate(), "gws gmail +send --to a@b.c --subject s --body b")

    assert said is not None
    assert "acts outside this machine" in said
    assert "nobody is here to approve it" in said


def test_the_users_ask_rules_add_commands_and_ask_skip_takes_atlass_off() -> None:
    settings = TrustSettings(ask=("git push*",), ask_skip=("gh issue comment*",))
    user = _User(_yes())
    gate = ApprovalGate(approver=user, settings=settings)
    gate.set_auto_mode(True)

    assert _run(gate, "git push origin main") is None
    assert _run(gate, "cd x && gh issue comment 3 --body ok") is None
    refused = _run(ApprovalGate(settings=settings), "gh issue create -t x")

    (request,) = user.asked
    assert request.target == "git push origin main"
    assert "ask rule 'git push*'" in request.reason
    assert refused is not None  # ask_skip names one action only


def test_a_deny_rule_still_wins_over_asking() -> None:
    user = _User()
    gate = ApprovalGate(approver=user, settings=TrustSettings(deny=("gh repo delete*",)))

    said = _run(gate, "gh repo delete owner/repo --yes")

    assert said is not None
    assert "deny rule" in said
    assert user.asked == []


def _guarded(
    connect: Connect, fake: FakeModelServer, user: _User, home: Home
) -> tuple[ApprovalGate, Models]:
    database = Database(home.database_file)
    migrate(database, (MODELS_SCHEMA,))
    models = connect(fake, "openrouter", ledger=CallLedger(database))
    client = models.client("openrouter", "openai/gpt-4o-mini")
    return ApprovalGate(approver=user, guardian=client, conversation="c1"), models


def _in(models: Models, work: Callable[[], Awaitable[Any]]) -> Any:
    async def main() -> Any:
        try:
            return await work()
        finally:
            await models.close()

    return asyncio.run(main())


def test_an_outward_action_never_reaches_the_guardian(
    connect: Connect, fake: FakeModelServer, home: Home
) -> None:
    user = _User(_yes())
    gate, models = _guarded(connect, fake, user, home)

    async def work() -> str | None:
        return await gate(RUN, {"command": "gh repo delete owner/repo --yes"})

    assert _in(models, work) is None
    assert len(user.asked) == 1
    assert fake.requests == []


# -- the known destinations -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "addresses"),
    [
        ("gws gmail +send --to A@x.io --subject s --body b", ["a@x.io"]),
        (
            "gws gmail +send --to 'a@x.io,b@y.io' --cc c@z.io --bcc=a@x.io",
            ["a@x.io", "b@y.io", "c@z.io"],
        ),
        ("gws gmail +forward --message-id 1 --to <d@x.io>", ["d@x.io"]),
        ("gws gmail +reply --message-id 1 --body ok", []),
        ("gws gmail +send --to a@x.io --subject s --body b --dry-run", []),
        ("gh issue create --body 'mail a@x.io'", []),
    ],
)
def test_the_recipients_of_an_email_are_read_from_its_flags(
    command: str, addresses: list[str]
) -> None:
    assert email_recipients(command) == addresses


def test_the_first_email_to_an_address_says_so_and_an_approved_one_becomes_known(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "atlas.db")
    migrate(database, (SCHEMA,))
    store = TrustStore(database)
    deny = ApprovalAnswer(Outcome.ANSWERED, Choice.DENY)
    user = _User(deny, _yes(), _yes())
    gate = ApprovalGate(approver=user, store=store, conversation="c1")
    send = "gws gmail +send --to a@x.io --subject s --body b"

    _run(gate, send)  # refused: the address stays unknown
    _run(gate, send)
    _run(gate, send)

    refused, approved, again = user.asked
    assert "(the first email to a@x.io)" in refused.reason
    assert "(the first email to a@x.io)" in approved.reason
    assert "first email" not in again.reason
    assert "sends an email" in again.reason


# -- scripts the command runs ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "text", "command", "cwd"),
    [
        (
            "mail.cmd",
            "@echo off\ngws gmail +send --to a@x.io --subject s --body b\n",
            "mail.cmd",
            None,
        ),
        (
            "send.py",
            'import subprocess\nsubprocess.run(["gws", "gmail", "+send", "--to", "a@x.io"])\n',
            "python send.py",
            None,
        ),
        ("merge.ps1", "gh pr merge 3 --squash\n", "& ./merge.ps1", None),
        ("go.sh", "gh issue create -t x -b y\n", "bash go.sh", "sub"),
    ],
)
def test_an_outward_action_in_a_script_the_command_runs_asks_too(
    tmp_path: Path, name: str, text: str, command: str, cwd: str | None
) -> None:
    folder = tmp_path / (cwd or "")
    folder.mkdir(exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")
    user = _User(ApprovalAnswer(Outcome.ANSWERED, Choice.DENY))
    gate = ApprovalGate(approver=user, resolve=lambda raw: tmp_path / raw)
    arguments: dict[str, Any] = {"command": command}
    if cwd:
        arguments["cwd"] = cwd

    said = asyncio.run(gate(RUN, arguments))

    assert said is not None
    (request,) = user.asked
    assert "(in a script the command runs)" in request.reason


def test_a_script_powershell_wrote_in_utf16_is_read_and_asks(tmp_path: Path) -> None:
    (tmp_path / "merge.ps1").write_bytes("gh pr merge 3 --squash\r\n".encode("utf-16"))
    user = _User(ApprovalAnswer(Outcome.ANSWERED, Choice.DENY))
    gate = ApprovalGate(approver=user, resolve=lambda raw: tmp_path / raw)

    assert _run(gate, "& ./merge.ps1") is not None
    (request,) = user.asked
    assert "(in a script the command runs)" in request.reason


def test_a_program_the_command_names_is_not_read_as_a_script(tmp_path: Path) -> None:
    """An executable's bytes are full of `$(` and quotes; read as shell they kept the server busy
    for minutes. A NUL byte says it is a program, and the command alone is checked: the words in
    its bytes ask nothing."""
    program = tmp_path / "tool.exe"
    program.write_bytes(b"MZ\x90\x00" + b'"$( a ' * 20 + b"\ngh pr merge 3 --squash\n")
    user = _User()
    gate = ApprovalGate(approver=user, resolve=lambda raw: tmp_path / raw)

    assert _run(gate, "& ./tool.exe run.py") is None
    assert user.asked == []


def test_a_script_nested_past_the_readers_stack_is_refused(tmp_path: Path) -> None:
    """A command that deep is refused by the parser's limit; a script's text is not, and reading
    it ran out of stack and ended the turn. It is refused instead, with nobody asked."""
    (tmp_path / "deep.sh").write_text('"$( a ' * 1600 + "\ngh pr merge 3\n", encoding="utf-8")
    user = _User()
    gate = ApprovalGate(approver=user, resolve=lambda raw: tmp_path / raw)

    refusal = _run(gate, "bash ./deep.sh")

    assert refusal is not None
    assert "nested too deeply to read" in refusal
    assert user.asked == []


def test_a_script_that_only_reads_does_not_ask(tmp_path: Path) -> None:
    (tmp_path / "list.cmd").write_text("gws gmail +triage\n", encoding="utf-8")
    user = _User()
    gate = ApprovalGate(approver=user, resolve=lambda raw: tmp_path / raw)

    assert _run(gate, "list.cmd") is None
    assert user.asked == []


def test_an_installed_programs_files_are_not_read_as_scripts(tmp_path: Path) -> None:
    launcher = tmp_path / "node_modules" / "@googleworkspace" / "cli" / "run.js"
    launcher.parent.mkdir(parents=True)
    launcher.write_text('spawn("gws", ["gmail", "+send"])\n', encoding="utf-8")
    user = _User()
    gate = ApprovalGate(approver=user, resolve=lambda raw: tmp_path / raw)

    assert _run(gate, "Test-Path 'node_modules/@googleworkspace/cli/run.js'") is None
    assert user.asked == []


# -- Atlas's own decisions (step 16) ---------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "atlas plugins enable telegram --yes",
        "atlas plugins disable telegram",
        "atlas plugins secret telegram TELEGRAM_BOT_TOKEN",
        "atlas plugins set telegram api_url http://example.com",
        # It writes a plugin in the home's plugins folder (step 16j).
        "atlas plugins new my-plugin",
        # What lands in the home's plugins folder, by its address (step 16n).
        "atlas plugins install owner/repo",
        "atlas plugins install ./my-plugin --force",
        "atlas plugins update my-plugin",
        "atlas plugins remove my-plugin",
        "atlas pairing approve ABCD2345",
        "atlas pairing forget telegram 100",
        # An MCP server from the marketplace connects as it is installed (step 16s).
        "atlas marketplace install docs",
        "atlas marketplace update pdf --force",
        "atlas marketplace remove video",
        "uv run atlas marketplace install docs",
        "atlas --home work pairing approve ABCD2345",
        "atlas --home=work plugins enable telegram",
        "python -m atlas plugins enable telegram --yes",
        "uv run atlas plugins enable telegram",
        r'& "C:\Users\x\AppData\Local\atlas\bin\atlas.cmd" plugins enable telegram',
        "cp -r bot ~/.atlas/plugins/",
        "git clone https://example.com/bot.git ~/.atlas/plugins/bot",
        "echo x > ~/.atlas/plugin-data/telegram/state",
        'sqlite3 ~/.atlas/atlas.db "UPDATE plugins_state SET enabled = 1"',
        r"Copy-Item bot C:\Users\x\.atlas\plugins\bot -Recurse",
    ],
)
def test_atlas_s_own_decisions_are_the_users_whatever_the_spelling(command: str) -> None:
    assert _reasons(command), command


@pytest.mark.parametrize(
    "command",
    [
        "atlas plugins list",
        # It loads the plugin in a home of its own, thrown away after.
        "atlas plugins validate ./my-plugin",
        "atlas pairing list",
        "atlas marketplace list --refresh",
        "atlas marketplace show docs",
        # The marketplace's CI: a plugin's Python loaded in a home thrown away after.
        "atlas marketplace check items/video.yaml --load",
        "atlas update",
        "ls ~/.atlas/skills",
        "cat ~/.atlas/plugins-notes.txt",
        "echo atlas.db is a file name",
    ],
)
def test_reading_atlas_s_lists_is_not_a_decision(command: str) -> None:
    assert _reasons(command) == [], command


def test_turning_a_plugin_on_asks_the_user_even_in_auto_mode_and_never_the_guardian() -> None:
    user = _User(ApprovalAnswer(Outcome.ANSWERED, Choice.DENY))
    gate = ApprovalGate(approver=user)
    gate.set_auto_mode(True)

    said = _run(gate, "atlas plugins enable telegram --yes")

    assert said is not None
    assert said.startswith("[Atlas: BLOCKED")
    [asked] = user.asked
    assert "`atlas plugins enable` is the user's own decision" in asked.reason
    assert not asked.allow_always
    assert _run(ApprovalGate(), "atlas pairing approve ABCD2345") is not None  # nobody to ask


def test_the_file_tools_never_write_into_the_plugins_folders(tmp_path: Path) -> None:
    home = tmp_path / "home"
    write = Tool("write_file", "Writes a file.", {}, _nothing)
    gate = ApprovalGate(home=home, resolve=lambda raw: Path(str(raw)))
    for target in (
        home / "plugins" / "evil" / "__init__.py",
        home / "plugin-data" / "telegram" / "state",
    ):
        said = asyncio.run(gate(write, {"path": str(target)}))
        assert said is not None
        assert "Refusing to write to Atlas's plugins" in said
    assert asyncio.run(gate(write, {"path": str(home / "notes.txt")})) is None
