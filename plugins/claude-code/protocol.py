"""Claude Code's stdio control protocol, read as the SDK's events and written back. Pure.

Decided with the user on 12/10/2026 (docs/ARCHITECTURE.md, Outside executors), moved into this
plugin on 14/10/2026 (step 16c): the protocol the Claude Agent SDK speaks (`claude_agent_sdk`
0.2.165, `_internal/transport/subprocess_cli.py`, `_internal/query.py`,
`_internal/message_parser.py`):

- **The user's own Claude Code**, run as it is: the `claude` installed on the machine, its own
  login, the user's settings, permission rules and the project's CLAUDE.md (`--setting-sources`
  user, project, local), in its default permission mode unless its card turns `auto` on. What it
  would ask the user in a terminal, it asks here, through Atlas. `auto` is its own classifier's
  mode, never `bypassPermissions`, which checks nothing, and Atlas adds no rule of its own to it:
  an outside program keeps its own rules (decided with the user on 17/10/2026).
- **Its login is never touched**: an API key in Atlas's environment would be billed in place of
  the user's subscription in a program Claude Code runs without a terminal, so those variables are
  left out of its environment (Anthropic: a third party may not route requests through a plan's
  credentials, nor intermediate them).
- **A file the user attaches goes as the SDK sends one** (step 16i): an image as an `image` block
  of the user message, in base64, as the SDK's streaming input does; every file, the image too,
  named with its path in a note of this plugin's own (`<atlas-attachments>`), for Claude Code's
  Read tool, which reads a PDF, an image or a notebook itself. The home's attachments folder is
  one of its working directories (`--add-dir`, the SDK's `additionalDirectories`), so reading one
  asks no permission. Its transcript keeps the note, which this plugin reads back as the files.
"""

import base64
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from atlas.plugins.sdk import (
    Account,
    Answers,
    Called,
    CommandsChanged,
    Compacted,
    Connector,
    ConnectorOrigin,
    ConnectorStatus,
    ExecutorEvent,
    Finished,
    LimitReached,
    ModelChoice,
    Option,
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

#: The executor's name, as the manifest declares it and the delegate tool takes it.
CLAUDE_CODE = "claude-code"
#: How the tool and the screen call it.
TITLE = "Claude Code"

#: Variables that would make Claude Code bill something else than the user's own login.
#: `CLAUDE_CONFIG_DIR` is not one: it names the user's own Claude Code folder, its login and its
#: sessions, which the Agent SDK passes on and its `list_sessions` reads, as this plugin reads it;
#: an instance on another account has it set to that account's folder (step 16h).
_CREDENTIAL_PREFIXES = ("ANTHROPIC_", "CLAUDE_CODE_USE_")
_CREDENTIAL_NAMES = ("CLAUDECODE", "CLAUDE_CODE_OAUTH_TOKEN")
#: The variable that names Claude Code's folder for an account (code.claude.com/docs,
#: Authentication, "Log in with multiple accounts").
CONFIG_DIR = "CLAUDE_CONFIG_DIR"
#: Longest text of a tool's input or result kept for the screen and the stored transcript.
SHOWN_CHARS = 4_000
#: Claude Code's permission modes this plugin uses: its default, which asks, and `auto`.
DEFAULT_MODE = "default"
AUTO_MODE = "auto"
#: The modes the box offers, its default first (SDK version 28): edits taken without asking, and
#: a plan made before any change. Never `bypassPermissions`, which checks nothing.
MODES = (DEFAULT_MODE, "acceptEdits", "plan", AUTO_MODE)
#: The model its picker calls its default: the one the account and the user's settings make it.
DEFAULT_MODEL = "default"
#: The images Claude reads as themselves (the Messages API's); any other file goes by its path.
IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
#: The note that names the files attached to a user message (`attached_note`).
ATTACHED_TAG = "atlas-attachments"
_ATTACHED = re.compile(rf"<{ATTACHED_TAG}>(.*)</{ATTACHED_TAG}>", re.DOTALL)
#: A file in the note: its path, then its type in parentheses, which hold no parenthesis.
_ATTACHED_LINE = re.compile(r"^- (.+) \([^()]*\)$", re.MULTILINE)
#: Its tools that write a file, and where they name it.
_WRITERS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path"}
_NOTEBOOK_WRITERS = {"NotebookEdit": "notebook_path"}


def command(
    cli: Sequence[str],
    *,
    resume: str | None = None,
    auto: bool = False,
    fork: bool = False,
    model: str | None = None,
    effort: str | None = None,
    directories: Sequence[str] = (),
    chrome: bool = False,
) -> list[str]:
    """The `claude` command line the Agent SDK builds for a session that talks over stdio, with
    Claude Code's own system prompt and the user's settings. `cli` is the program, and the
    arguments that come before Claude Code's own. Without `model` and `effort`, those of the
    user's settings. `directories` are read as its own (`--add-dir`). With `chrome`, it starts
    with Claude in Chrome (`--chrome`); without, as Claude Code's own settings say."""
    line = [
        *cli,
        "--output-format", "stream-json", "--verbose",
        "--append-system-prompt", "",
        "--permission-prompt-tool", "stdio",
        "--permission-mode", AUTO_MODE if auto else DEFAULT_MODE,
        "--setting-sources=user,project,local",
    ]  # fmt: skip
    if model is not None:
        line += ["--model", model]
    if effort is not None:
        line += ["--effort", effort]
    for directory in directories:
        line += ["--add-dir", directory]
    if chrome:
        line.append("--chrome")
    if resume is not None:
        line.append(f"--resume={resume}")
        if fork:
            # A copy of the session, with an id of its own: the original stays as it was.
            line.append("--fork-session")
    line += ["--input-format", "stream-json"]
    return line


def environment(environ: Mapping[str, str], account_folder: str | None = None) -> dict[str, str]:
    """Atlas's environment without what would replace the user's own login, marked as the SDK
    marks it; with `account_folder`, Claude Code's folder for another account, whose login,
    settings and sessions it then runs on."""
    kept = {
        name: value
        for name, value in environ.items()
        if not name.startswith(_CREDENTIAL_PREFIXES) and name not in _CREDENTIAL_NAMES
    }
    kept["CLAUDE_CODE_ENTRYPOINT"] = "sdk-py"
    if account_folder:
        kept[CONFIG_DIR] = account_folder
    return kept


@dataclass(frozen=True, slots=True)
class Attached:
    """A file attached to a user message: where it is, and an image's bytes, sent as itself."""

    path: str
    media_type: str
    #: An image Claude reads as itself (`IMAGE_TYPES`); `None` for any other file.
    image: bytes | None = None


def user_message(text: str, files: Sequence[Attached] = ()) -> dict[str, Any]:
    """A message from the user, as the SDK writes it: a turn's prompt, or a steer while one
    runs. With `files`, its content is blocks, as the SDK's streaming input sends an image: the
    images first, then the note naming every file, then the user's words."""
    content: str | list[dict[str, Any]] = text
    if files:
        images = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": item.media_type,
                    "data": base64.b64encode(item.image).decode("ascii"),
                },
            }
            for item in files
            if item.image is not None
        ]
        said = [{"type": "text", "text": text}] if text.strip() else []
        content = [*images, {"type": "text", "text": attached_note(files)}, *said]
    return {
        "type": "user",
        "message": {"role": "user", "content": content},
        "parent_tool_use_id": None,
        "session_id": "default",
    }


def attached_note(files: Sequence[Attached]) -> str:
    """What Claude Code reads of the files attached: each one's path and type, Atlas's attachment
    note in Claude Code's words."""
    lines = [
        f"- {item.path} ({item.media_type}{', shown above' if item.image is not None else ''})"
        for item in files
    ]
    return "\n".join(
        [
            f"<{ATTACHED_TAG}>",
            "The user attached these files to this message, kept outside the project at these "
            "paths. Read one with the Read tool before answering when the request involves it, "
            "instead of asking the user to paste it.",
            *lines,
            f"</{ATTACHED_TAG}>",
        ]
    )


def attached_paths(text: str) -> tuple[str, ...] | None:
    """The paths a note of `attached_note` names; `None` when `text` is not one."""
    found = _ATTACHED.fullmatch(text.strip())
    if found is None:
        return None
    return tuple(line.group(1) for line in _ATTACHED_LINE.finditer(found.group(1)))


def control_request(request_id: str, request: dict[str, Any]) -> dict[str, Any]:
    return {"type": "control_request", "request_id": request_id, "request": request}


def control_response(request_id: str, response: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "control_response",
        "response": {"subtype": "success", "request_id": request_id, "response": response},
    }


def control_error(request_id: str, error: str) -> dict[str, Any]:
    return {
        "type": "control_response",
        "response": {"subtype": "error", "request_id": request_id, "error": error},
    }


# -- what Claude Code says ------------------------------------------------------------------------


def events_of(message: Mapping[str, Any]) -> list[ExecutorEvent]:
    """What one stream-json message from Claude Code means for Atlas (the SDK's
    `parse_message`); messages Atlas does not use say nothing."""
    kind = message.get("type")
    if kind == "system" and message.get("subtype") == "init":
        return [
            Started(
                str(message.get("session_id", "")),
                str(message.get("model", "")),
                str(message.get("apiKeySource", "")),
                commands_of(message.get("slash_commands")),
            )
        ]
    if kind == "system" and message.get("subtype") == "compact_boundary":
        return [compacted_of(message.get("compact_metadata"))]
    if kind == "system" and message.get("subtype") == "commands_changed":
        # The whole list again, to replace the one kept (a skill found in a folder it worked in).
        return [CommandsChanged(slash_commands_of(message.get("commands")))]
    if kind == "system" and message.get("subtype") == "local_command_output":
        # What a command it runs itself says (`/voice`), shown as its words.
        content = message.get("content")
        return [Said(content)] if isinstance(content, str) and content.strip() else []
    if kind == "rate_limit_event":
        raw: object = message.get("rate_limit_info")
        info = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
        windows = windows_of_event(info)
        found: list[ExecutorEvent] = [UsageChanged(windows)] if windows else []
        if info.get("status") == "rejected":
            resets = info.get("resetsAt")
            found.append(LimitReached(int(resets) if isinstance(resets, int | float) else None))
        return found
    if kind == "assistant":
        if message.get("error") == "rate_limit":
            # Its words are the limit's notice, not the task's: what it last said stays its own.
            return [LimitReached()]
        return [event for block in blocks(message) for event in _assistant_block(block)]
    if kind == "user":
        return [
            Returned(
                str(block.get("tool_use_id", "")),
                cut(result_text(block.get("content"))),
                bool(block.get("is_error")),
            )
            for block in blocks(message)
            if block.get("type") == "tool_result"
        ]
    if kind == "result":
        cost = message.get("total_cost_usd")
        failed = bool(message.get("is_error")) or message.get("subtype") != "success"
        text = message.get("result")
        if not isinstance(text, str) or not text:
            text = "; ".join(str(error) for error in message.get("errors") or ()) or str(
                message.get("subtype", "")
            )
        return [
            Finished(
                text,
                failed,
                str(message.get("session_id", "")),
                None if not isinstance(cost, int | float) else Decimal(str(cost)),
                int(message.get("num_turns") or 0),
                str(message.get("terminal_reason") or message.get("subtype") or ""),
            )
        ]
    return []


def compacted_of(metadata: object) -> Compacted:
    """A compaction's metadata, as stream-json says it (`compact_metadata`, snake case) or as the
    session file keeps it (`compactMetadata`, camel case)."""
    found = cast("dict[str, Any]", metadata) if isinstance(metadata, dict) else {}
    before = found.get("pre_tokens", found.get("preTokens"))
    after = found.get("post_tokens", found.get("postTokens"))
    return Compacted(
        int(before) if isinstance(before, int | float) else 0,
        int(after) if isinstance(after, int | float) else None,
        found.get("trigger") != "manual",
    )


def commands_of(listed: object) -> tuple[str, ...]:
    """The slash commands Claude Code says its channel takes, names without the slash: the
    `slash_commands` of its `init` (names), or the `commands` its `initialize` answers (objects
    with a `name`)."""
    if not isinstance(listed, list):
        return ()
    names: list[str] = []
    for item in cast("list[object]", listed):
        name: object = cast("dict[str, Any]", item).get("name") if isinstance(item, dict) else item
        if isinstance(name, str) and name.strip("/ "):
            names.append(name.strip().lstrip("/"))
    return tuple(dict.fromkeys(names))


def slash_commands_of(listed: object) -> tuple[SlashCommand, ...]:
    """The slash commands Claude Code takes, described: the `commands` its `initialize` answers
    and its `commands_changed` says again (the SDK's `SlashCommand`: `name`, `description`,
    `argumentHint`, `builtin`), or bare names. Two rows may share a name, and `/name` then runs
    the one marked `builtin` (its own words for the field): that one is kept."""
    if not isinstance(listed, list):
        return ()
    found: dict[str, SlashCommand] = {}
    for item in cast("list[object]", listed):
        row = cast("dict[str, Any]", item) if isinstance(item, dict) else {"name": item}
        name = row.get("name")
        if not isinstance(name, str) or not name.strip("/ "):
            continue
        command = SlashCommand(
            name.strip().lstrip("/"),
            str(row.get("description") or ""),
            str(row.get("argumentHint") or ""),
            row.get("builtin") is True,
        )
        kept = found.get(command.name)
        if kept is None or (command.builtin and not kept.builtin):
            found[command.name] = command
    return tuple(found.values())


def models_of(listed: object) -> tuple[ModelChoice, ...]:
    """The models its `initialize` answers (`models`), its default first, as its own `/model`
    lists them for this account: `value` chooses one, and `supportedEffortLevels` are the efforts
    it takes (the SDK's `ModelInfo`)."""
    if not isinstance(listed, list):
        return ()
    found: list[ModelChoice] = []
    for item in cast("list[object]", listed):
        if not isinstance(item, dict):
            continue
        model = cast("dict[str, Any]", item)
        value = model.get("value")
        if not isinstance(value, str) or not value:
            continue
        raw: object = model.get("supportedEffortLevels") if model.get("supportsEffort") else None
        efforts = cast("list[object]", raw) if isinstance(raw, list) else []
        found.append(
            ModelChoice(
                value,
                str(model.get("displayName") or value),
                str(model.get("description") or ""),
                tuple(effort for effort in efforts if isinstance(effort, str)),
                str(model.get("resolvedModel") or ""),
            )
        )
    return tuple(found)


#: Claude Code's scopes, as a connector's origin: `dynamic` is a server given to one run alone
#: (`--mcp-config`), so one folder's too.
_ORIGINS: dict[str, ConnectorOrigin] = {
    "claudeai": "account",
    "user": "user",
    "project": "project",
    "local": "local",
    "dynamic": "local",
    "enterprise": "managed",
    "managed": "managed",
}
_STATUSES: tuple[ConnectorStatus, ...] = (
    "connected",
    "needs-auth",
    "failed",
    "pending",
    "disabled",
)
#: The prefix Claude Code gives the account's hosted connectors.
_ACCOUNT_PREFIX = "claude.ai "


def connectors_of(listed: object) -> tuple[Connector, ...]:
    """The MCP servers `mcp_status` answers (`mcpServers`, the SDK's `McpServerStatus`), as its
    `/mcp` lists them: a plugin's named `plugin:<plugin>:<server>`, the account's `claude.ai
    <name>`. A state it does not know reads as failed, with the state as its error."""
    if not isinstance(listed, list):
        return ()
    found: list[Connector] = []
    for item in cast("list[object]", listed):
        if not isinstance(item, dict):
            continue
        server = cast("dict[str, Any]", item)
        name = server.get("name")
        if not isinstance(name, str) or not name:
            continue
        origin = _ORIGINS.get(str(server.get("scope")), "user")
        plugin = ""
        parts = name.split(":", 2)
        if len(parts) == 3 and parts[0] == "plugin":
            origin, plugin, name = "plugin", parts[1], parts[2]
        elif origin == "account":
            name = name.removeprefix(_ACCOUNT_PREFIX)
        status = str(server.get("status"))
        known: ConnectorStatus | None = next((s for s in _STATUSES if s == status), None)
        error = str(server.get("error") or "")
        tools: object = server.get("tools")
        found.append(
            Connector(
                name,
                "failed" if known is None else known,
                origin,
                plugin,
                len(cast("list[object]", tools)) if isinstance(tools, list) else 0,
                error if known else error or status,
            )
        )
    return tuple(found)


# -- its plan's limits ----------------------------------------------------------------------------

#: The plan's windows Claude Code names, as its own `/usage` titles them, with how long each lasts
#: and the model it counts alone. Others it may add (code names, for windows not yet shown) are
#: left out, as its `/usage` leaves them out.
_WINDOWS: dict[str, tuple[str, int, str]] = {
    "five_hour": ("Current session", 300, ""),
    "seven_day": ("Current week (all models)", 10_080, ""),
    "seven_day_opus": ("Current week (Opus)", 10_080, "Opus"),
    "seven_day_sonnet": ("Current week (Sonnet)", 10_080, "Sonnet"),
    "seven_day_oauth_apps": ("Current week (apps)", 10_080, ""),
}


def usage_of(answer: Mapping[str, Any]) -> PlanUsage:
    """What `get_usage` answers (Claude Code's structured `/usage`, which its SDK calls
    `usage_EXPERIMENTAL_MAY_CHANGE_DO_NOT_RELY_ON_THIS_API_YET`): the plan's windows, each used
    0 to 100 with its reset as ISO 8601, the per-model weekly windows of `model_scoped`, and
    whether its extra usage is on: past the limits it bills rather than stops."""
    plan = str(answer.get("subscription_type") or "")
    raw: object = answer.get("rate_limits")
    limits = cast("dict[str, Any]", raw) if isinstance(raw, dict) else None
    if not answer.get("rate_limits_available", limits is not None) or limits is None:
        return PlanUsage(
            plan=plan,
            unavailable="This login has no plan limits: an API key or a cloud provider pays.",
        )
    windows: list[UsageWindow] = []
    for key, (title, minutes, model) in _WINDOWS.items():
        window = _window(limits.get(key), 1)
        if window is not None:
            used, resets = window
            windows.append(UsageWindow(key, title, used, resets, minutes, model))
    scoped: object = limits.get("model_scoped")
    for item in cast("list[object]", scoped) if isinstance(scoped, list) else []:
        if not isinstance(item, dict):
            continue
        scoped_window = cast("dict[str, Any]", item)
        model = str(scoped_window.get("display_name") or "")
        window = _window(scoped_window, 1)
        if model and window is not None:
            used, resets = window
            title = f"Current week ({model})"
            windows.append(UsageWindow(f"model:{model}", title, used, resets, 10_080, model))
    extra: object = limits.get("extra_usage")
    enabled = cast("dict[str, Any]", extra).get("is_enabled") if isinstance(extra, dict) else None
    return PlanUsage(
        tuple(windows), plan, extra_usage=enabled if isinstance(enabled, bool) else None
    )


def windows_of_event(info: Mapping[str, Any]) -> tuple[UsageWindow, ...]:
    """The windows a `rate_limit_event` names, used 0 to 1 and reset as a Unix time: all of
    them in `unifiedWindows` (2.1.296), or the one it is about (`rateLimitType` and its
    `utilization`) in a Claude Code that says only that."""
    raw: object = info.get("unifiedWindows")
    named = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
    if not named and isinstance(info.get("rateLimitType"), str):
        named = {
            info["rateLimitType"]: {
                "utilization": info.get("utilization"),
                "resetsAt": info.get("resetsAt"),
            }
        }
    windows: list[UsageWindow] = []
    for key, value in named.items():
        known = _WINDOWS.get(key)
        window = _window(value, 100)
        if known is not None and window is not None:
            title, minutes, model = known
            windows.append(UsageWindow(key, title, window[0], window[1], minutes, model))
    return tuple(windows)


def _window(raw: object, scale: float) -> tuple[float, str] | None:
    """A window's use, 0 to 100, and its reset as ISO 8601 in UTC; `None` when it says no use.
    `get_usage` says `utilization` 0 to 100 and `resets_at` ISO; the event 0 to 1 and `resetsAt`
    a Unix time."""
    if not isinstance(raw, dict):
        return None
    window = cast("dict[str, Any]", raw)
    used = window.get("utilization")
    if not isinstance(used, int | float) or isinstance(used, bool):
        return None
    resets = window.get("resets_at", window.get("resetsAt"))
    return min(100.0, max(0.0, float(used) * scale)), _iso(resets)


def _iso(moment: object) -> str:
    """A reset as Atlas writes every time, `2026-10-18T19:10:00.000Z`, from a Unix time or ISO
    8601 text; empty when it is neither."""
    try:
        if isinstance(moment, int | float) and not isinstance(moment, bool):
            stamp = datetime.fromtimestamp(moment, UTC)
        elif isinstance(moment, str) and moment:
            stamp = datetime.fromisoformat(moment).astimezone(UTC)
        else:
            return ""
    except (ValueError, OverflowError, OSError):
        return ""
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.") + f"{stamp.microsecond // 1000:03d}Z"


def hosts_connectors(account: object) -> bool:
    """Whether the login `initialize` answers (`account`) brings the account's hosted connectors:
    a claude.ai plan's does (`subscriptionType`), an API key's does not."""
    return isinstance(account, dict) and bool(
        cast("dict[str, Any]", account).get("subscriptionType")
    )


def effort_problem(
    models: Sequence[ModelChoice], model: str | None, effort: str | None
) -> str | None:
    """Why `effort` cannot go with `model` (`None`: Claude Code's `default`), or `None` when it
    can. Claude Code takes an effort it does not know and quietly drops it, so Atlas checks it
    against the list first (found on 16/10/2026)."""
    if effort is None:
        return None
    chosen = next((found for found in models if found.id == (model or DEFAULT_MODEL)), None)
    if chosen is None:
        return None if not models else f"Claude Code does not offer the model {model!r} here."
    if effort not in chosen.efforts:
        takes = ", ".join(chosen.efforts) or "none"
        return f"{chosen.name} does not take the effort {effort!r} (it takes {takes})."
    return None


def blocks(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A message's content blocks."""
    body: object = message.get("message")
    content = cast("dict[str, Any]", body).get("content") if isinstance(body, dict) else None
    if not isinstance(content, list):
        return []
    return [
        cast("dict[str, Any]", block)
        for block in cast("list[object]", content)
        if isinstance(block, dict)
    ]


def _assistant_block(block: Mapping[str, Any]) -> list[ExecutorEvent]:
    kind = block.get("type")
    if kind == "text" and block.get("text"):
        return [Said(str(block["text"]))]
    if kind == "thinking" and block.get("thinking"):
        return [Thought(str(block["thinking"]))]
    if kind == "tool_use":
        tool = str(block.get("name", ""))
        raw: object = block.get("input") or {}
        given = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
        arguments = json.dumps(given, ensure_ascii=False)
        return [Called(str(block.get("id", "")), tool, arguments, written(tool, given))]
    return []


def written(tool: str, arguments: Mapping[str, Any]) -> tuple[str, ...]:
    """The file a call of `tool` writes, when it is one of Claude Code's writing tools."""
    key = _WRITERS.get(tool) or _NOTEBOOK_WRITERS.get(tool)
    value = arguments.get(key) if key is not None else None
    return (value,) if isinstance(value, str) and value else ()


def result_text(content: object) -> str:
    """A tool result's text, whole."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(cast("dict[str, Any]", part).get("text", ""))
            for part in cast("list[object]", content)
            if isinstance(part, dict)
        ]
        return "\n".join(part for part in parts if part)
    return ""


def cut(text: str) -> str:
    if len(text) <= SHOWN_CHARS:
        return text
    return f"{text[:SHOWN_CHARS]}\n…({len(text) - SHOWN_CHARS:,} more characters)"


# -- its account ----------------------------------------------------------------------------------


def account_of(status: Mapping[str, Any]) -> Account:
    """`claude auth status --json`, read."""
    return Account(
        bool(status.get("loggedIn")),
        str(status.get("email") or ""),
        str(status.get("subscriptionType") or ""),
    )


def status_of(output: str) -> dict[str, Any] | None:
    """The object `claude auth status --json` writes, past anything printed around it; `None`
    when there is none. Claude Code writes it with exit code 1 when nobody is logged in (2.1.296,
    found on 18/10/2026), so it is read whatever the exit code."""
    start, end = output.find("{"), output.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        found: object = json.loads(output[start : end + 1])
    except ValueError:
        return None
    return cast("dict[str, Any]", found) if isinstance(found, dict) else None


#: A version as `claude --version` writes it: `2.1.296`, or `2.1.300-beta.1`.
_VERSION = re.compile(r"\d+(?:\.\d+)+(?:[-+][0-9A-Za-z.-]+)?")
_NUMBERS = re.compile(r"\d+(?:\.\d+)*")


def version_of(output: str) -> str:
    """`claude --version`'s version: `2.1.140 (Claude Code)` is `2.1.140`. The line that names
    Claude Code is read first, so a notice printed around it is passed over."""
    lines = output.splitlines()
    for line in [*(line for line in lines if "Claude Code" in line), *lines]:
        found = _VERSION.search(line)
        if found is not None:
            return found.group()
    return ""


def at_least(version: str, minimum: str) -> bool:
    """Whether a version is `minimum` or later, by its numbers (`2.1.300-beta.1` is 2.1.300); one
    Atlas cannot read is not."""
    have, need = _numbers(version), _numbers(minimum)
    return bool(have) and bool(need) and have >= need


def _numbers(version: str) -> tuple[int, ...]:
    found = _NUMBERS.match(version.strip())
    return () if found is None else tuple(int(part) for part in found.group().split("."))


# -- what it asks ---------------------------------------------------------------------------------


#: The tool through which Claude Code asks the user questions.
QUESTION_TOOL = "AskUserQuestion"


@dataclass(frozen=True, slots=True)
class Asked:
    """A `can_use_tool` control request: Claude Code asks to use a tool, or asks the user
    questions when the tool is `AskUserQuestion`."""

    tool: str
    arguments: dict[str, Any]
    #: Claude Code's own sentence for the prompt, and what it adds.
    title: str = ""
    description: str = ""
    questions: tuple[Question, ...] = field(default=())

    @property
    def is_question(self) -> bool:
        return self.tool == QUESTION_TOOL

    @property
    def permission(self) -> Permission:
        return Permission(self.tool, self.summary, self.title, self.description)

    @property
    def summary(self) -> str:
        """The action in one line: the command, the file, or the tool and its input."""
        arguments = self.arguments
        for key in ("command", "file_path", "path", "url", "pattern"):
            value = arguments.get(key)
            if isinstance(value, str) and value:
                return value
        return cut(json.dumps(arguments, ensure_ascii=False))


def asked_of(request: Mapping[str, Any]) -> Asked:
    """A `can_use_tool` control request, read."""
    raw: object = request.get("input")
    arguments = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
    questions: list[Question] = []
    if request.get("tool_name") == QUESTION_TOOL:
        for item_raw in cast("list[object]", arguments.get("questions") or []):
            if not isinstance(item_raw, dict):
                continue
            item = cast("dict[str, Any]", item_raw)
            options = tuple(
                Option(str(option.get("label", "")), str(option.get("description", "")))
                for option in (
                    cast("dict[str, Any]", raw_option)
                    for raw_option in cast("list[object]", item.get("options") or [])
                    if isinstance(raw_option, dict)
                )
            )
            questions.append(
                Question(
                    str(item.get("question", "")),
                    str(item.get("header", "")),
                    options,
                    bool(item.get("multiSelect")),
                )
            )
    return Asked(
        str(request.get("tool_name", "")),
        arguments,
        str(request.get("title") or ""),
        str(request.get("description") or ""),
        tuple(questions),
    )


def allowed(asked: Asked) -> dict[str, Any]:
    """The answer that lets the tool run with its input as asked."""
    return {"behavior": "allow", "updatedInput": asked.arguments}


def denied(message: str) -> dict[str, Any]:
    """The answer that refuses the tool; Claude Code reads `message` and goes on."""
    return {"behavior": "deny", "message": message}


def judged(asked: Asked, verdict: Verdict) -> dict[str, Any]:
    """A permission's answer, as Claude Code reads it."""
    return allowed(asked) if verdict.allow else denied(verdict.reason or "Denied.")


def answered(asked: Asked, answers: Answers) -> dict[str, Any]:
    """The answer to `AskUserQuestion`: the questions passed through and an answer for each, keyed
    by the question's text, a label or a list of labels, or someone's own words. Unanswered, the
    tool is refused, and Claude Code goes on without them."""
    if answers is None:
        return denied("The questions went unanswered. Go on without them, or stop.")
    given = {
        question: answer if isinstance(answer, str) else [str(item) for item in answer]
        for question, answer in answers.items()
    }
    return {
        "behavior": "allow",
        "updatedInput": {"questions": asked.arguments.get("questions", []), "answers": given},
    }
