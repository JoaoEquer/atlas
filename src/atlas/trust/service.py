"""The approval gate: whether a tool call runs, is refused, or waits for the user.

It is the agent loop's tool gate (`AgentLoop(gate=...)`), one per conversation.

**A command for run_command**, in this order:

1. The floors, which nothing lifts: hardline commands (wiping the root or the home, formatting a
   disk, a fork bomb, shutting down), `sudo -S`, and the user's `[trust] deny` globs.
2. An outward action (`gh pr merge`, `gws gmail +send`; `atlas.trust.outward`) or a command matching
   the user's `[trust] ask` globs goes straight to the user, never the guardian, even in auto mode.
   The answer covers this one call; with nobody to answer it is refused. `[trust] ask_skip` takes an
   action off Atlas's list.
3. Auto mode (the ⚡) lets the rest run.
4. An approval answered "always" (kept in the database) or "for this conversation" lets a command
   flagged for the same reason run.
5. The dangerous-command patterns, over the command and its de-obfuscated variants. A command
   none of them flags runs.
6. With nobody to answer, a flagged command is refused.
7. The guardian, a short request to the conversation's model, reads it: APPROVE runs this command
   only; DENY refuses it, and the user may overrule that for this one call; ESCALATE, an empty
   answer or a failed call asks the user. The request asks the model not to reason: a reasoning
   model spent its 16 tokens thinking and answered nothing, so every flagged command went to the
   user (seen in Atlas's proof on gpt-6-luna, 10/10/2026). A model that cannot stop reasoning gets
   its lowest effort and room to think, which is Atlas's fix. 8. The user answers once, for the
   conversation, always, or no. Silence is not consent.

**A command routine** the agent makes or changes (`cronjob_manage` with a `command`) passes as a
command for run_command would: the routine runs it later with nobody to approve, so it is judged
when it is scheduled.

**A file write** to a system path, the home's settings or its plugins' folders is refused; one to
the SSH configuration asks the user (the SSH config can run commands through `ProxyCommand`).
**A tool that needs approval** (an untrusted MCP server's tool that may write) asks the user every
call, unless answered for the conversation or always.
"""

import asyncio
import codecs
import fnmatch
import json
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from atlas.agent import Tool, Unanswered
from atlas.home import TrustSettings, inside, utc_now
from atlas.models import (
    ModelClient,
    ModelInfo,
    ModelRequest,
    ModelsError,
    SystemMessage,
    UserMessage,
)
from atlas.trust.detection import (
    PARSER_LIMIT_DESCRIPTIONS,
    check_sudo_stdin_guard,
    deny_command_variants,
    detect_dangerous_command,
    detect_hardline_command,
    fold_atlas_home,
)
from atlas.trust.model import (
    GUARDIAN_SYSTEM,
    PROTECTED_FOLDERS,
    PROTECTED_SETTINGS,
    SENSITIVE_EXACT,
    SENSITIVE_PREFIXES,
    SUDO_MESSAGE,
    ApprovalAnswer,
    ApprovalRequest,
    Approver,
    Choice,
    Outcome,
    Verdict,
    ask_rule_reason,
    breaker,
    guardian_prompt,
    hardline_message,
    outward_unattended_message,
    parse_verdict,
    plugins_message,
    refused_message,
    sensitive_message,
    settings_message,
    unattended_message,
    user_deny_message,
)
from atlas.trust.outward import email_recipients, outward_actions
from atlas.trust.store import TrustStore

#: How the guardian's calls are named in the call ledger.
GUARDIAN_PURPOSE = "approval"
#: The guardian answers one word, in at most 16 tokens.
GUARDIAN_TOKENS = 16
#: The room a model that cannot stop reasoning gets to think before its one word.
GUARDIAN_THINKING_TOKENS = 2_048
#: The effort that turns reasoning off, where a model lists it.
NO_REASONING = "none"
COMMAND_TOOL = "run_command"
#: The tool that schedules a command routine.
ROUTINE_TOOL = "cronjob_manage"
WRITE_TOOLS = frozenset({"write_file", "edit_file"})
#: Shown of a call's arguments on the approval screen.
TARGET_CHARS = 2_000

_logger = logging.getLogger("atlas.trust")
# Why a command whose script nests past what the reader can follow is refused.
_TOO_DEEP = "a script nested too deeply to read"


class ApprovalGate:
    """Decides each tool call of one conversation."""

    def __init__(
        self,
        *,
        conversation: str | None = None,
        approver: Approver | None = None,
        guardian: ModelClient | None = None,
        store: TrustStore | None = None,
        settings: TrustSettings | None = None,
        home: Path | None = None,
        resolve: Callable[[str], Path] | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._conversation = conversation
        self._approver = approver
        self._guardian = guardian
        self._store = store
        self._settings = settings or TrustSettings()
        self._home = home
        self._resolve = resolve
        self._clock = clock
        #: Reasons answered "for this conversation".
        self._approved: set[str] = set()
        self._auto = False
        #: Guardian denials in a row, for the circuit breaker.
        self._denials = 0

    # -- auto mode --------------------------------------------------------------------------------

    @property
    def auto_mode(self) -> bool:
        if self._store is not None and self._conversation is not None:
            return self._store.auto_mode(self._conversation)
        return self._auto

    def set_auto_mode(self, enabled: bool) -> None:
        """The ⚡ of this conversation: kept with it when there is a store."""
        if self._store is not None and self._conversation is not None:
            self._store.set_auto_mode(self._conversation, enabled, self._clock())
        self._auto = enabled

    # -- the gate ---------------------------------------------------------------------------------

    async def __call__(self, tool: Tool, arguments: dict[str, Any]) -> str | None:
        if tool.name == COMMAND_TOOL:
            command = arguments.get("command")
            cwd = arguments.get("cwd")
            if not isinstance(command, str):
                return None
            return await self._command(command, cwd if isinstance(cwd, str) else None)
        if tool.name == ROUTINE_TOOL:
            command = arguments.get("command")
            if isinstance(command, str) and command.strip():
                return await self._command(command, tool=ROUTINE_TOOL)
        if tool.name in WRITE_TOOLS:
            path = arguments.get("path")
            refused = self._write(path)
            if refused is not None:
                return refused
            asked = await self._ssh_write(path)
            if asked is not None:
                return asked
        if tool.needs_approval and not tool.read_only:
            return await self._action(
                f"tool:{tool.name}",
                tool.name,
                _target(arguments),
                f"{tool.source} is not trusted, and this call may change something",
            )
        return None

    # -- commands ---------------------------------------------------------------------------------

    async def _command(
        self, command: str, cwd: str | None = None, *, tool: str = COMMAND_TOOL
    ) -> str | None:
        seen = fold_atlas_home(command, str(self._home)) if self._home is not None else command
        floor, outward = await asyncio.to_thread(self._read_command, seen, command, cwd)
        if floor is not None:
            return floor
        if outward is not None:
            return await self._ask_outward(command, outward, tool)
        if self.auto_mode:
            return None
        flagged, _, reason = await asyncio.to_thread(detect_dangerous_command, seen)
        if not flagged or reason is None or self._granted(reason):
            return None
        approver = self._approver
        if approver is None:
            _logger.warning("A flagged command was refused with nobody to approve it: %s", reason)
            return unattended_message(reason, command=True)
        verdict = await self.review(command, reason)
        if verdict is Verdict.APPROVE:
            self._denials = 0
            _logger.info("The guardian approved a flagged command: %s", reason)
            return None
        denied = verdict is Verdict.DENY
        if denied:
            self._denials += 1
        request = ApprovalRequest(
            tool,
            command,
            reason,
            allow_conversation=not denied,
            allow_always=not denied,
            guardian_denied=denied,
        )
        answer = await self._wait(approver, request)
        if answer.outcome is Outcome.ANSWERED and answer.choice not in (None, Choice.DENY):
            self._denials = 0
            if not denied:  # overruling the guardian is one call, never kept
                self._keep(reason, answer.choice)
            return None
        # The breaker counts the guardian's denials, not the user's.
        return _refused(answer, command=True, more=breaker(self._denials))

    def _read_command(
        self, seen: str, command: str, cwd: str | None
    ) -> tuple[str | None, str | None]:
        """The floor's refusal, or else why the command or a script it runs is outward. Run off
        the event loop: reading a long text as shell takes its time, and the whole server waited
        on it."""
        try:
            floor = self._floor(seen)
            if floor is not None:
                return floor, None
            outward = self._outward(seen)
            if outward is None and self._resolve is not None:
                for text in script_texts(command, self._resolve, cwd):
                    outward = self._outward(text)
                    if outward is not None:
                        return None, f"{outward} (in a script the command runs)"
            return None, outward
        except RecursionError:
            # Substitutions nested past the reader's stack, which no real script has. A command
            # that long is refused by the parser's limit first; a script's text is not, and the
            # error ended the turn. Refused the same way instead.
            _logger.warning("Hardline block: %s", _TOO_DEEP)
            return hardline_message(_TOO_DEEP, parser_limit=False), None

    def _floor(self, command: str) -> str | None:
        hardline, why = detect_hardline_command(command)
        if hardline and why is not None:
            _logger.warning("Hardline block: %s", why)
            return hardline_message(why, parser_limit=why in PARSER_LIMIT_DESCRIPTIONS)
        guessing, _ = check_sudo_stdin_guard(command)
        if guessing:
            return SUDO_MESSAGE
        rules = [rule.strip() for rule in self._settings.deny if rule.strip()]
        if rules:
            for variant in deny_command_variants(command):
                candidate = variant.lower().strip()
                for rule in rules:
                    if fnmatch.fnmatchcase(candidate, rule.lower()):
                        _logger.warning("The user's deny rule %r blocked a command.", rule)
                        return user_deny_message(rule)
        return None

    def _outward(self, command: str) -> str | None:
        """Why the user must approve this command first: one of the user's `ask` globs, or an
        outward action no `ask_skip` glob takes off the list."""
        variants = [
            variant.lower().strip() for variant in dict.fromkeys(deny_command_variants(command))
        ]
        asks = [rule.strip() for rule in self._settings.ask if rule.strip()]
        for variant in variants:
            for rule in asks:
                if fnmatch.fnmatchcase(variant, rule.lower()):
                    return ask_rule_reason(rule)
        skips = [rule.strip().lower() for rule in self._settings.ask_skip if rule.strip()]
        for variant in variants:
            for action, reason in outward_actions(variant):
                if not any(fnmatch.fnmatchcase(action, rule) for rule in skips):
                    return reason
        return None

    async def _ask_outward(self, command: str, reason: str, tool: str = COMMAND_TOOL) -> str | None:
        approver = self._approver
        if approver is None:
            _logger.warning("An outward command was refused with nobody to approve it: %s", reason)
            return outward_unattended_message(reason)
        recipients = email_recipients(command)
        new = self._store.unknown(recipients) if self._store is not None else recipients
        if new:
            reason += f" (the first email to {', '.join(new)})"
        request = ApprovalRequest(
            tool, command, reason, allow_conversation=False, allow_always=False
        )
        answer = await self._wait(approver, request)
        if answer.outcome is Outcome.ANSWERED and answer.choice not in (None, Choice.DENY):
            _logger.info("The user approved an outward command: %s", reason)
            if self._store is not None and recipients:
                self._store.remember(recipients, self._clock())
            return None
        return _refused(answer, command=True)

    async def ask_first(self, tool: str, target: str, reason: str) -> str | None:
        """Ask the user before something Atlas may do only when they say so (step 16c: steering
        or stopping the user's own executor instance), every time, past the guardian and auto
        mode, as an outward action is: what the model is told when they refuse, or `None`."""
        approver = self._approver
        if approver is None:
            return (
                f"[Atlas: BLOCKED: the user approves this every time ({reason}), and nobody is "
                "here to approve it. Do NOT retry; tell the user what you meant to do.]"
            )
        request = ApprovalRequest(
            tool, target, reason, allow_conversation=False, allow_always=False
        )
        answer = await self._wait(approver, request)
        if answer.outcome is Outcome.ANSWERED and answer.choice not in (None, Choice.DENY):
            _logger.info("The user approved what only they may ask for: %s", reason)
            return None
        return _refused(answer, command=False)

    def use_settings(self, settings: TrustSettings) -> None:
        """The user's rules changed on the settings screen (step 15b): they apply from the
        next request."""
        self._settings = settings

    def use_guardian(self, guardian: ModelClient) -> None:
        """The guardian is the conversation's model: it changes when the conversation switches."""
        self._guardian = guardian

    async def review(self, command: str, reason: str) -> Verdict:
        """The guardian's verdict on a command, or on what an outside executor asks to do (step
        13): approve, deny, or escalate to the user, which is also the answer when it cannot say."""
        if self._guardian is None:
            return Verdict.ESCALATE
        effort, tokens = guardian_effort(self._guardian.info)
        request = ModelRequest(
            (SystemMessage(GUARDIAN_SYSTEM), UserMessage(guardian_prompt(command, reason))),
            purpose=GUARDIAN_PURPOSE,
            scope=self._conversation,
            effort=effort,
            max_output_tokens=tokens,
        )
        try:
            reply = await self._guardian.reply(request)
        except ModelsError as error:
            _logger.warning("The guardian could not answer, so the user decides: %s", error)
            return Verdict.ESCALATE
        if not reply.text.strip():
            # A reasoning model may spend the whole budget thinking.
            _logger.warning(
                "The guardian gave an empty answer (finish %s), so the user decides.",
                reply.finish.value,
            )
            return Verdict.ESCALATE
        return parse_verdict(reply.text)

    # -- files ------------------------------------------------------------------------------------

    def _write(self, raw: object) -> str | None:
        if self._resolve is None or not isinstance(raw, str):
            return None
        try:
            path = self._resolve(raw)
        except ValueError:
            return None  # the file tool says why
        resolved = _resolved(path)
        posix = resolved.as_posix()
        if posix.startswith(SENSITIVE_PREFIXES) or posix in SENSITIVE_EXACT:
            return sensitive_message(raw)
        # By what the path leads to, not how it is written (`atlas.home.places`).
        if self._home is not None and any(
            inside(resolved, self._home / name) for name in PROTECTED_SETTINGS
        ):
            return settings_message(raw)
        if self._home is not None and any(
            inside(resolved, self._home / name) for name in PROTECTED_FOLDERS
        ):
            return plugins_message(raw)
        return None

    async def _ssh_write(self, raw: object) -> str | None:
        """The SSH configuration asks before a write."""
        if self._resolve is None or not isinstance(raw, str):
            return None
        try:
            path = _resolved(self._resolve(raw))
        except ValueError:
            return None
        if not inside(path, Path.home() / ".ssh" / "config"):
            return None
        return await self._action(
            "write:ssh-config",
            "write_file",
            raw,
            "the SSH configuration can run commands through ProxyCommand",
        )

    # -- actions ----------------------------------------------------------------------------------

    async def _action(self, key: str, tool: str, target: str, reason: str) -> str | None:
        if self.auto_mode or self._granted(key):
            return None
        approver = self._approver
        if approver is None:
            return unattended_message(reason, command=False)
        answer = await self._wait(approver, ApprovalRequest(tool, target, reason))
        if answer.outcome is Outcome.ANSWERED and answer.choice not in (None, Choice.DENY):
            self._keep(key, answer.choice)
            return None
        return _refused(answer, command=False)

    # -- the user's answers -----------------------------------------------------------------------

    async def _wait(self, approver: Approver, request: ApprovalRequest) -> ApprovalAnswer:
        try:
            return await asyncio.wait_for(
                approver(request), timeout=self._settings.approval_timeout
            )
        except TimeoutError:
            return ApprovalAnswer(Outcome.TIMED_OUT)

    def _granted(self, key: str) -> bool:
        if key in self._approved:
            return True
        return self._store is not None and self._store.allowed(key)

    def _keep(self, key: str, choice: Choice | None) -> None:
        if choice is Choice.CONVERSATION:
            self._approved.add(key)
        elif choice is Choice.ALWAYS:
            self._approved.add(key)
            if self._store is not None:
                self._store.allow(key, self._clock())


def guardian_effort(info: ModelInfo | None) -> tuple[str | None, int]:
    """The guardian request's effort and output budget on a model: reasoning off where the model
    lists `none`; its lowest effort and room to think where it reasons and cannot stop; nothing
    set where it does not reason or nobody knows."""
    if info is None or not info.reasoning:
        return None, GUARDIAN_TOKENS
    if info.efforts is None:
        return None, GUARDIAN_THINKING_TOKENS
    if NO_REASONING in info.efforts:
        return NO_REASONING, GUARDIAN_TOKENS
    return (info.efforts[0] if info.efforts else None), GUARDIAN_THINKING_TOKENS


def _refused(answer: ApprovalAnswer, *, command: bool, more: str = "") -> str:
    """What the model is told when the user did not approve, with `more` at its end; marked
    `Unanswered` when nobody answered in time, so the call ends `unanswered`, not `denied`."""
    text = refused_message(answer, command=command)
    if more:
        text = text[:-1] + more + "]"
    return Unanswered(text) if answer.outcome is Outcome.TIMED_OUT else text


def _target(arguments: dict[str, Any]) -> str:
    text = json.dumps(arguments, ensure_ascii=False, default=str)
    return text if len(text) <= TARGET_CHARS else text[:TARGET_CHARS] + "…"


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


#: A script read for the ask rules is read only up to this size.
SCRIPT_BYTES = 256 * 1024
#: Folders that hold installed programs, not scripts the agent wrote: `gws`'s own launcher
#: (`node_modules/@googleworkspace/cli/run.js`) names `gws`, and a `Test-Path` on it asked the user
#: in step 14b's proof.
INSTALLED = frozenset({"node_modules", "site-packages", "dist-packages", ".venv"})


def script_texts(command: str, resolve: Callable[[str], Path], cwd: str | None = None) -> list[str]:
    """The text of each file a command names that exists, read as a script the command may run:
    the agent writing `gws gmail +send` into `mail.cmd` and running `mail.cmd` must ask as the
    command itself would (found by step 14b's proof, 13/10/2026: gpt-6-luna, fighting Windows
    PowerShell 5.1's quoting, wrote its `gws` call into a `.cmd` and ran that). Claude Code says
    its rules cannot see into a script; Atlas reads the files the command names, so a script made
    another way (downloaded, generated by a program) is still past them."""
    texts: list[str] = []
    seen: set[Path] = set()
    for token in command.split():
        word = token.strip("\"'`();|&,")
        if not word or word.startswith("-") or len(word) > 400:
            continue
        for candidate in (word, f"{cwd}/{word}" if cwd else None):
            if candidate is None:
                continue
            try:
                path = _resolved(resolve(candidate))
            except (ValueError, OSError):
                continue
            if path in seen or INSTALLED.intersection(path.parts):
                continue
            seen.add(path)
            try:
                if not path.is_file() or path.stat().st_size > SCRIPT_BYTES:
                    continue
                text = _script_text(path.read_bytes())
            except OSError:
                continue
            if text is not None:
                texts.append(text)
    return texts


def _script_text(data: bytes) -> str | None:
    """A file's bytes as a script's text, or None for a program: a NUL byte is never in a script
    but is all over an executable, whose bytes read as shell kept the server's loop busy for
    minutes (`& 'C:/.../python.exe' run.py`, 01/11/2026). Windows PowerShell 5.1's `Out-File`
    writes UTF-16 with a byte order mark, and is read as such, not passed over."""
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", errors="replace")
    if b"\0" in data:
        return None
    return data.decode("utf-8-sig", errors="replace")
