"""Prove a session's route across executors, an account's hand-over at a plan's limit, and step
16h's switch, against the user's own Claude Code and Codex (asked for by the user on 21/10/2026).

    uv run python scripts/prove_route.py                  # step 1 only: reads, no prompt
    uv run python scripts/prove_route.py --steps 1,2,3 --yes
    uv run python scripts/prove_route.py --steps 1,4 --yes   # once Codex has room again

The accounts are the accounts plugin's (step 22): the proof runs it on a data folder of its own,
with the user's accounts by their nicknames. No account is driven to its limit on purpose: the
proof waits for one of the user's Claude Code accounts to be at its limit already (its usage says
so, or `--limited` names it), and the turn sent there stops at once, spending nothing of it.
Atlas's own model takes no part: no Atlas credit.

1. **What is there, and where a route goes (free).** Each account of Claude Code the user's home
   keeps, and Codex: who it is logged in as, its plan's usage, whether past its limits it bills
   (extra usage), and whether it is at its limit. Then where a route from the account at its
   limit, through Codex, to an account with room, goes next, from that real usage alone: Codex
   when it has room, passed over when it is at its limit.
2. **A route inside Claude Code, and 16h's switch.** On the account with room a session is told a
   word to keep (one short turn), and switched to the account at its limit in a copy. There, the
   route [at its limit, with room] takes the next prompt, which stops at the limit at once: the
   session goes on in a copy on the account with room, and writes the word to a file (one short
   turn). Atlas deletes no session (decided with the user on 03/11/2026): each account keeps the
   sessions it had.
3. **The account's own hand-over** (`b748cef`), the same with no route: the executor's own order.
4. **A route to Codex.** As step 2, the route [at its limit, Codex]: a new Codex session, its
   first prompt the handoff packet, writes the word it read there (one short Codex turn).
5. **16h's folder account and warning (no prompt).** A folder whose account is the one with room
   starts its sessions there; switching it to the main account reads both accounts' connectors
   and says which the main one lacks.
6. **16h's leak (#80580).** On the account with room, which has no `CLAUDE.md` of its own, a
   session is asked for the first line of the user-level memory it was given (one short turn): the
   main account's (`~/.claude/CLAUDE.md` and what it imports) reaching it is the leak.

Steps 2 to 4 run only with `--yes`, after step 1, and refuse an account whose extra usage is on,
or, without `--accept-unknown-extra`, one that does not say. The sessions the proof made are
deleted after, from the temporary project folders only, unless `--keep`. Exit code 0 only when
every step run did what it should.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from atlas.__main__ import SCHEMAS
from atlas.assistant import (
    HANDOFF_MARK,
    Assistant,
    InstanceChanged,
    WorkbenchEvent,
    other_accounts,
)
from atlas.assistant.plugin_host import AssistantPluginHost
from atlas.home import (
    Database,
    ModelSettings,
    Redactor,
    Settings,
    WorkspaceSettings,
    configure_logging,
    load_settings,
    migrate,
    open_home,
)
from atlas.jobs import AccountStore
from atlas.models import Models, UserMessage, load_catalog
from atlas.plugins import (
    OFFICIAL_FOLDER,
    Answers,
    Executor,
    ExecutorError,
    NextStop,
    Permission,
    Question,
    Verdict,
)
from atlas.plugins.store import import_plugin
from atlas.secrets import Vault

CLAUDE = "claude-code"
CODEX = "codex"
#: The longest a step waits for a real model.
STEP_SECONDS = 300.0
#: The word a session is told to keep, and must write after it went on elsewhere.
WORD = "girassol"
KEEP = f"Guarde esta palavra para depois: {WORD}. Responda só: ok."
ASK_MEMORY = (
    "Copie literalmente as três primeiras linhas das instruções de memória de usuário (o CLAUDE.md "
    "do usuário e o que ele importa) que você recebeu nesta sessão. Se não recebeu nenhuma, "
    "responda só: NENHUMA."
)
WRITE = (
    "Crie o arquivo prova.txt nesta pasta contendo só a palavra que eu pedi para você guardar. "
    "Não procure a palavra em arquivos. Depois responda só: feito."
)


def say(text: str) -> None:
    sys.stdout.write(f"{text}\n")
    sys.stdout.flush()


class Desk:
    """The user's cards: the proof works in a temporary folder, so what is asked is allowed."""

    async def ask(self, asker: InstanceChanged, questions: Sequence[Question]) -> Answers:
        return None

    async def approve(self, asker: InstanceChanged, permission: Permission) -> Verdict:
        say(f"    allowed: {permission.title or permission.tool}")
        return Verdict(True)


def executor(plugin: str, program: str) -> Executor:
    """The plugin's executor, as Atlas makes it, on `program` (a JSON list) or the one found."""
    package = import_plugin(OFFICIAL_FOLDER / plugin, plugin)
    made: Executor = (package.ClaudeCode if plugin == CLAUDE else package.Codex)(
        package.program(program), logger=logging.getLogger(f"prove_route.{plugin}")
    )
    return made


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove a session's route end to end.")
    parser.add_argument("--steps", default="1", help="the steps to run, such as 1,2,3")
    parser.add_argument("--yes", action="store_true", help="run the steps that prompt")
    parser.add_argument("--limited", default="", help="the account at its limit: a nickname, main")
    parser.add_argument("--spare", default="", help="the account with room: a nickname, main")
    parser.add_argument("--accept-unknown-extra", action="store_true")
    parser.add_argument("--keep", action="store_true", help="keep the sessions it made")
    parser.add_argument("--claude", default="", help="the claude program, a JSON list")
    parser.add_argument("--codex", default="", help="the codex program, a JSON list")
    args = parser.parse_args()
    settings = load_settings(os.environ).settings
    real = open_home(settings.home)
    redactor = Redactor()
    configure_logging("WARNING", redactor)
    # The user's accounts as their home keeps them, read only: the proof's home is its own.
    kept = _nicknames(real.plugin_data_folder / "accounts" / "accounts.json", real.database_file)
    vault = Vault(real.secrets_folder, settings.vault, redactor)
    models = Models(load_catalog(real.models_file), vault, ModelSettings())
    with tempfile.TemporaryDirectory(prefix="atlas-prove-route-") as folder:
        return asyncio.run(
            prove(
                Path(folder),
                models,
                vault,
                (executor(CLAUDE, args.claude), executor(CODEX, args.codex)),
                kept,
                {int(step) for step in args.steps.split(",") if step.strip()},
                yes=args.yes,
                limited=args.limited,
                spare=args.spare,
                unknown_extra=args.accept_unknown_extra,
                keep=args.keep,
            )
        )


async def prove(
    root: Path,
    models: Models,
    vault: Vault,
    executors: tuple[Executor, Executor],
    nicknames: Sequence[str],
    steps: set[int],
    *,
    yes: bool = False,
    limited: str = "",
    spare: str = "",
    unknown_extra: bool = False,
    keep: bool = False,
) -> int:
    """Every step asked for, on a home of its own under `root`, with the user's accounts by their
    `nicknames`, on the executors given (the fakes, to rehearse)."""
    home = open_home(root / "home")
    migrate(Database(home.database_file), SCHEMAS)
    work = root / "work"
    work.mkdir()
    settings = Settings(home=home.root, model=ModelSettings(), workspace=WorkspaceSettings(work))
    heard: list[WorkbenchEvent] = []
    claude, codex = executors
    assistant = Assistant(
        home,
        settings,
        models,
        vault,
        listener=lambda _conversation, _event: None,
        desk=Desk(),
        executors={CLAUDE: claude, CODEX: codex},
        on_workbench=heard.append,
    )
    plugin = import_plugin(OFFICIAL_FOLDER / "accounts", "accounts")
    host = AssistantPluginHost(assistant).workbench()
    accounts = plugin.PluginAccounts(plugin.Book(root / "accounts.json"), lambda: host)
    assistant.set_accounts(accounts)
    proof = _Proof(assistant, heard, work, plugin.Calls(accounts, lambda: host))
    results: dict[str, bool] = {}
    try:
        if os.environ.get("CLAUDE_CONFIG_DIR"):
            say(
                f"CLAUDE_CONFIG_DIR is {os.environ['CLAUDE_CONFIG_DIR']}: that folder is the main "
                "account here. The app runs without it; unset it to prove what the app does."
            )
        for nickname in nicknames:
            # The same folder as the user's own account of that nickname, as Atlas names it.
            try:
                await proof.calls.add({"executor": CLAUDE, "nickname": nickname})
            except Exception as error:  # noqa: BLE001 - the plugin's refusal, told
                say(f"  the account {nickname} is left out: {error}")
        ready = await proof.look(limited, spare)
        if 1 in steps:
            results["1 where a route goes"] = await proof.route_choice()
        costly = sorted(steps - {1})
        if costly and not yes:
            say("Steps 2 to 4 prompt real models: run again with --yes to run them.")
        elif costly and not ready:
            say("Steps 2 to 4 need an account at its limit and one with room: none ran.")
            results.update({f"{step} (not run)": False for step in costly})
        elif costly and not proof.billing_checked(unknown_extra):
            results.update({f"{step} (refused)": False for step in costly})
        elif costly:
            if 2 in steps:
                results["2 a route inside Claude Code"] = await proof.across(route=True)
            if 3 in steps:
                results["3 the account's own hand-over"] = await proof.across(route=False)
            if 4 in steps:
                results["4 a route to Codex"] = await proof.to_codex()
            if 5 in steps:
                results["5 a folder's account and the warning"] = await proof.folder_account()
            if 6 in steps:
                results["6 the main account's memory leaking"] = await proof.leak()
            await proof.usage_after()
    finally:
        if not keep:
            await proof.clean()
        await assistant.close()
    for step, ok in results.items():
        say(f"{'ok  ' if ok else 'FAIL'} {step}")
    say(json.dumps(results))
    return 0 if results and all(results.values()) else 1


class _Proof:
    def __init__(
        self, assistant: Assistant, heard: list[WorkbenchEvent], work: Path, calls: Any
    ) -> None:
        self.assistant = assistant
        #: The accounts plugin's calls, as its screen makes them.
        self.calls = calls
        self.workbench = assistant.workbench
        self.heard = heard
        self.work = work
        self.accounts: dict[str, str] = {}
        self.extra: dict[str, bool | None] = {}
        self.limited = ""
        self.spare = ""
        self.projects: list[Path] = []

    # -- step 1 -----------------------------------------------------------------------------------

    async def look(self, limited: str, spare: str) -> bool:
        """Each account, read with no prompt; the one at its limit and the one with room."""
        workbench = self.workbench
        kept = other_accounts(self.assistant.accounts, CLAUDE)
        self.accounts = {"main": CLAUDE, **{a.nickname: f"{CLAUDE}@{a.id}" for a in kept}}
        say("Accounts:")
        for label, name in [*self.accounts.items(), ("codex", CODEX)]:
            found = self.assistant.executor(name)
            if found is None:
                say(f"  {label}: not installed")
                continue
            detected = await found.detect()
            login = detected.account.login if detected.account else ""
            logged = bool(detected.account and detected.account.logged_in)
            try:
                usage = await workbench.usage(name)
            except ExecutorError as error:
                say(f"  {label}: {login or 'nobody'}; usage not read: {error}")
                self.extra[name] = None
                continue
            used = max((w.used for w in usage.windows if not w.model), default=0) if usage else 0
            extra = None if usage is None else usage.extra_usage
            self.extra[name] = extra
            say(
                f"  {label}: {login or 'nobody'} (logged in: {logged}), plan "
                f"{usage.plan if usage else '?'}, most used {used:.0f}%, extra usage "
                f"{'on' if extra else 'off' if extra is False else 'unknown'}, at its limit: "
                f"{workbench.limited(name)}"
            )
        claude = [name for name in self.accounts.values() if self.assistant.executor(name)]
        self.limited = self.accounts.get(limited, "") or next(
            (name for name in claude if workbench.limited(name)), ""
        )
        self.spare = self.accounts.get(spare, "") or next(
            (n for n in claude if n != self.limited and not workbench.limited(n)), ""
        )
        say(f"At its limit: {self.limited or 'none'}; with room: {self.spare or 'none'}.")
        return bool(self.limited and self.spare)

    async def route_choice(self) -> bool:
        """Where a route from the account at its limit, through Codex, to one with room, goes."""
        source = self.limited or CLAUDE
        others = [n for n in self.accounts.values() if n != source]
        after = self.spare or (others[0] if others else "")
        stops = [_target(source), _target(CODEX), _target(after)]
        await self.calls.set_route({"conversation": "proof-route", "stops": stops})
        target = await self.workbench.next_stop(source, "proof-route")
        codex_full = self.workbench.limited(CODEX)
        wanted = after if codex_full else CODEX
        went = target.executor if isinstance(target, NextStop) else ""
        say(f"  from {source}, Codex at its limit: {codex_full}; the route goes to {went}")
        return went == wanted or (not after and codex_full and not went)

    def billing_checked(self, unknown: bool) -> bool:
        """No account the proof prompts bills past its limits."""
        used = [self.limited, self.spare, CODEX]
        on = [name for name in used if self.extra.get(name)]
        unsaid = [name for name in used if self.extra.get(name) is None]
        if on:
            say(f"Refused: extra usage is on for {', '.join(on)}; turn it off first.")
            return False
        if unsaid and not unknown:
            say(
                f"Refused: {', '.join(unsaid)} does not say whether it bills past its limits; "
                "check it, then run again with --accept-unknown-extra."
            )
            return False
        return True

    # -- steps 2 to 4 -----------------------------------------------------------------------------

    async def across(self, *, route: bool) -> bool:
        """A word kept on the account with room; the session switched to the one at its limit,
        which the next prompt stops at; it goes on on another account, by the route or by the
        executor's own order, and writes the word."""
        workbench = self.workbench
        project = self._project("route" if route else "failover")
        run = await workbench.start(self.spare, project, auto=True)
        await self._turn(run.id, KEEP)
        first = self.workbench.get(run.id)
        await workbench.use_account(run.id, _account(self.limited))
        original = _transcript(first.executor.account_folder(), first.session_id or "")
        say(f"  the first account's session file: {original}")
        if route:
            stops = [_target(self.limited), _target(self.spare)]
            await self.calls.set_route({"conversation": run.transcript.id, "stops": stops})
        await self._turn(run.id, WRITE, handed="account")
        moved = workbench.get(run.id)
        written = _read(project / "prova.txt")
        say(f"  went on on {moved.name}; prova.txt: {written!r}")
        await self._replaced()
        there = [view.session_id for view in await workbench.sessions(self.limited, str(project))]
        here = [view.session_id for view in await workbench.sessions(moved.name, str(project))]
        kept = bool(there) and moved.session_id in here
        say(f"  each account keeps its sessions, none deleted: {kept}")
        say(f"    on {self.limited}: {there}; on {moved.name}: {here}")
        return (
            moved.name != self.limited
            and (not route or moved.name == self.spare)
            and WORD in written.lower()
            and kept
        )

    async def _replaced(self) -> None:
        """Until the hand-overs under way are done."""
        await asyncio.sleep(0.1)
        while self.workbench._handing:  # pyright: ignore[reportPrivateUsage]
            await asyncio.sleep(0.05)

    async def to_codex(self) -> bool:
        """As step 2, the route [at its limit, Codex]: a new Codex session from the packet."""
        if self.workbench.limited(CODEX):
            say("  Codex is at its limit: run step 4 again once it has room.")
            return False
        workbench = self.workbench
        project = self._project("codex")
        run = await workbench.start(self.spare, project, auto=True)
        await self._turn(run.id, KEEP)
        await workbench.use_account(run.id, _account(self.limited))
        stops = [_target(self.limited), _target(CODEX)]
        await self.calls.set_route({"conversation": run.transcript.id, "stops": stops})
        await self._turn(run.id, WRITE, handed="route")
        moved = workbench.get(run.id)
        prompts = [m.text for m in moved.history if isinstance(m, UserMessage)]
        packet = any(text.startswith(HANDOFF_MARK) for text in prompts)
        written = _read(project / "prova.txt")
        say(f"  went on on {moved.name}; its first prompt the packet: {packet}; {written!r}")
        return moved.name == CODEX and packet and WORD in written.lower()

    async def folder_account(self) -> bool:
        """16h: a folder's sessions start on its account; the switch says what the other lacks."""
        workbench = self.workbench
        project = self._project("folder")
        folder = {"executor": CLAUDE, "folder": str(project), "account": _account(self.spare)}
        await self.calls.set_folder(folder)
        run = await workbench.start(CLAUDE, project, auto=True)
        check = await workbench.account_switch(run.id, "")
        await workbench.stop(run.id)
        say(
            f"  started on {run.name} ({run.account}); to the main account ({check.login}): "
            f"connectors compared {check.compared}, missing there: {list(check.missing)}"
        )
        return run.name == self.spare and bool(run.account) and check.compared

    async def leak(self) -> bool:
        """16h's risk #80580: the main account's memory reaching another account's session."""
        main = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
        spare = self.assistant.executor(self.spare)
        if spare is None or (spare.account_folder() / "CLAUDE.md").exists():
            say("  the account with room has a CLAUDE.md of its own: nothing to tell apart.")
            return False
        lines = _memory_lines(main)
        if not lines:
            say("  the main account has no user memory: nothing could leak.")
            return True
        project = self._project("leak")
        run = await self.workbench.start(self.spare, project, auto=True)
        await self._turn(run.id, ASK_MEMORY)
        said = self.workbench.get(run.id).finished
        answer = "" if said is None else said.text.strip()
        leaked = any(line in answer for line in lines)
        say(f"  answered {len(answer)} characters; the main account's memory in it: {leaked}")
        if leaked:
            say("  LEAK: the main account's user memory reached another account (#80580).")
        return not leaked

    async def usage_after(self) -> None:
        say("Usage after:")
        for name in dict.fromkeys((self.limited, self.spare, CODEX)):
            try:
                usage = await self.workbench.usage(name)
            except ExecutorError as error:
                say(f"  {name}: not read: {error}")
                continue
            windows = [] if usage is None else usage.windows
            say(f"  {name}: " + ", ".join(f"{w.title} {w.used:.0f}%" for w in windows))

    async def clean(self) -> None:
        """The sessions the proof made, deleted from its own folders, on every account."""
        for name in (*self.accounts.values(), CODEX):
            found = self.assistant.executor(name)
            if found is None or not found.capabilities().delete_sessions:
                continue
            for project in self.projects:
                try:
                    sessions = await self.workbench.sessions(name, str(project))
                    for session in sessions:
                        if session.session_id:
                            await self.workbench.delete_session(
                                name, session.session_id, str(project)
                            )
                except ExecutorError as error:
                    say(f"  a session of {name} was not deleted: {error}")

    # -- helpers ----------------------------------------------------------------------------------

    def _project(self, name: str) -> Path:
        project = self.work / f"rota-{name}-{os.getpid()}"
        project.mkdir()
        self.projects.append(project)
        return project

    async def _turn(self, instance_id: str, text: str, handed: str = "") -> None:
        """A prompt, and its turn to its end; with `handed`, until the session went on elsewhere
        (or said it could not) and its turn there ended."""
        say(f"  prompt: {text}")
        await self.workbench.prompt(instance_id, text)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + STEP_SECONDS
        while loop.time() < deadline:
            await asyncio.sleep(0.2)
            told = [e for e in self.heard if _activity(e, instance_id) in ("account", "route")]
            if handed and not told:
                continue
            if self.workbench._handing:  # pyright: ignore[reportPrivateUsage]
                continue
            if not self.workbench.get(instance_id).working:
                for event in told:
                    say(f"    {getattr(event, 'kind', '')}: {getattr(event, 'text', '')}")
                return
        raise TimeoutError("The step took too long.")


def _activity(event: WorkbenchEvent, instance_id: str) -> str:
    if getattr(event, "instance_id", None) != instance_id or isinstance(event, InstanceChanged):
        return ""
    return str(getattr(event, "kind", ""))


def _memory_lines(folder: Path) -> list[str]:
    """The distinctive lines of an account's user memory: its `CLAUDE.md` and the files it
    imports with `@`, never printed."""
    memory = folder / "CLAUDE.md"
    if not memory.exists():
        return []
    texts = [memory.read_text(encoding="utf-8")]
    for line in texts[0].splitlines():
        imported = folder / line.strip().removeprefix("@")
        if line.strip().startswith("@") and imported.is_file():
            texts.append(imported.read_text(encoding="utf-8"))
    found = [line.strip().lstrip("#").strip() for text in texts for line in text.splitlines()]
    return [line for line in found if len(line) >= 12]


def _account(name: str) -> str:
    return name.partition("@")[2]


def _target(name: str) -> dict[str, str]:
    """A stop of a route, as the accounts plugin's screen sends it."""
    executor, _, account = name.partition("@")
    return {"executor": executor, "account": account}


def _nicknames(book: Path, database: Path) -> list[str]:
    """The nicknames of the user's other Claude Code accounts: the accounts plugin's file, or,
    before an update moved them there, what Atlas kept itself."""
    if book.exists():
        kept = json.loads(book.read_text(encoding="utf-8")).get("executors", {}).get(CLAUDE, {})
        return [str(account.get("nickname", "")) for account in kept.get("accounts", [])]
    return [account.name for account in AccountStore(Database(database)).accounts(CLAUDE)]


def _transcript(folder: Path, session: str) -> Path | None:
    if not session:
        return None
    return next(folder.glob(f"projects/*/{session}.jsonl"), None)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


if __name__ == "__main__":
    sys.exit(main())
