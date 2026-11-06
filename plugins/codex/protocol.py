"""Codex's `app-server` protocol, read as the SDK's events and written back. Pure.

Decided with the user on 20/10/2026 (ROADMAP, Codex, Gemini CLI e DeepSeek; step 16o): the
JSON-RPC 2.0 Codex speaks over stdio to its own app and its VS Code extension, read in the schema
`codex app-server generate-json-schema` writes (0.163.0):

- **The user's own Codex**, run as it is: the `codex` installed on the machine, its own login and
  config, in the sandbox its terminal uses by default (`workspace-write`, asking when it must leave
  it: `on-request`). What it would ask in a terminal, it asks here, through Atlas.
- **Its own automatic mode** is its reviewer (`approvalsReviewer: "auto_review"`), a subagent of
  Codex's that approves or denies each request by its risk, in the same sandbox; Atlas adds no rule
  of its own to it, as for Claude Code's `auto` (decided with the user on 17/10/2026).
- **Its login is never touched**: a key in Atlas's environment would be billed in place of the
  user's ChatGPT plan, so those variables are left out of its environment.
- **Every list is read with no effect**: `thread/list` with `useStateDbOnly`, since without it
  Codex repairs its database from its session files, which is writing.
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from atlas.plugins.sdk import (
    Account,
    Answers,
    Called,
    Compacted,
    Connector,
    ConnectorStatus,
    ExecutorEvent,
    ExecutorFile,
    Finished,
    LimitReached,
    ModelChoice,
    Option,
    Permission,
    PlanUsage,
    Question,
    Returned,
    Said,
    SessionInfo,
    Thought,
    TranscriptEntry,
    UsageChanged,
    UsageWindow,
)

#: The executor's name, as the manifest declares it and the delegate tool takes it.
CODEX = "codex"
#: How the tool and the screen call it.
TITLE = "Codex"
#: How Atlas names itself to Codex. Not "atlas": Codex already gives that origin to another
#: product (ROADMAP, Bancada de harnesses).
CLIENT_NAME = "atlas_workbench"
CLIENT_TITLE = "Atlas workbench"
#: Variables that would make Codex bill something else than the user's own login. `CODEX_HOME`
#: is not one: it names the user's own Codex folder, its login and its sessions; an instance on
#: another account has it set to that account's folder.
_CREDENTIAL_PREFIXES = ("OPENAI_",)
_CREDENTIAL_NAMES = ("CODEX_API_KEY",)
#: The variable that names Codex's folder for an account: its login, config and sessions.
HOME_VARIABLE = "CODEX_HOME"
#: Its sandbox and approval policy as its terminal has them by default.
SANDBOX = "workspace-write"
APPROVAL_POLICY = "on-request"
#: Who answers what it asks: the user (through Atlas), or its own reviewer.
USER_REVIEWER = "user"
AUTO_REVIEWER = "auto_review"
#: Longest text of a tool's input or result kept for the screen and the transcript.
SHOWN_CHARS = 4_000
#: The images it reads as themselves; any other file goes by its path.
IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
#: The note that names the files attached to a prompt.
ATTACHED_TAG = "atlas-attachments"
_ATTACHED = re.compile(rf"<{ATTACHED_TAG}>(.*)</{ATTACHED_TAG}>", re.DOTALL)
_ATTACHED_LINE = re.compile(r"^- (.+) \([^()]*\)$", re.MULTILINE)
_NUMBERS = re.compile(r"\d+(\.\d+)*")
#: How a turn that did not complete says why, in the SDK's words.
_STOPPED = {"interrupted": "interrupted", "failed": "failed"}
#: Codex's tool items, and how the screen names each.
_TOOLS = {
    "commandExecution": "shell",
    "fileChange": "apply_patch",
    "webSearch": "web_search",
    "imageGeneration": "image_generation",
    "imageView": "view_image",
}


def environment(environ: Mapping[str, str], home: str | None = None) -> dict[str, str]:
    """Atlas's environment without what would replace the user's own login; with `home`, Codex's
    folder for another account, whose login, config and sessions it then runs on. Its tracing is
    kept to warnings."""
    kept = {
        name: value
        for name, value in environ.items()
        if not name.upper().startswith(_CREDENTIAL_PREFIXES)
        and name.upper() not in _CREDENTIAL_NAMES
    }
    kept.setdefault("RUST_LOG", "warn")
    if home:
        kept[HOME_VARIABLE] = home
    return kept


# -- what Atlas writes ----------------------------------------------------------------------------


def request(request_id: int, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": request_id, "method": method, "params": dict(params)}


def notification(method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {"method": method, "params": dict(params or {})}


def response(request_id: object, result: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": request_id, "result": dict(result)}


def error_response(request_id: object, message: str) -> dict[str, Any]:
    return {"id": request_id, "error": {"code": -32603, "message": message}}


def initialize_params(version: str) -> dict[str, Any]:
    """The handshake: who Atlas is, and that it reads the experimental parts (the questions)."""
    return {
        "clientInfo": {"name": CLIENT_NAME, "title": CLIENT_TITLE, "version": version},
        "capabilities": {"experimentalApi": True},
    }


def thread_params(folder: str, *, auto: bool, model: str | None) -> dict[str, Any]:
    """What a session starts, resumes or forks on: its folder, the terminal's sandbox and policy,
    and who reviews what it asks."""
    params: dict[str, Any] = {
        "cwd": folder,
        "sandbox": SANDBOX,
        "approvalPolicy": APPROVAL_POLICY,
        "approvalsReviewer": AUTO_REVIEWER if auto else USER_REVIEWER,
    }
    if model is not None:
        params["model"] = model
    return params


def turn_params(
    thread: str,
    text: str,
    files: Sequence[ExecutorFile],
    *,
    auto: bool,
    model: str | None,
    effort: str | None,
) -> dict[str, Any]:
    """A prompt: its input, and what changed since the last turn (Codex takes the reviewer, the
    model and the effort only here)."""
    params: dict[str, Any] = {
        "threadId": thread,
        "input": user_input(text, files),
        "approvalsReviewer": AUTO_REVIEWER if auto else USER_REVIEWER,
    }
    if model is not None:
        params["model"] = model
    if effort is not None:
        params["effort"] = effort
    return params


def user_input(text: str, files: Sequence[ExecutorFile] = ()) -> list[dict[str, Any]]:
    """A prompt as Codex takes it: the images by their paths (`localImage`), then the note naming
    every file, then the user's words."""
    items: list[dict[str, Any]] = [
        {"type": "localImage", "path": str(item.path)}
        for item in files
        if item.media_type in IMAGE_TYPES
    ]
    if files:
        items.append(_text(attached_note(files)))
    if text.strip() or not files:
        items.append(_text(text))
    return items


def _text(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text, "text_elements": []}


def attached_note(files: Sequence[ExecutorFile]) -> str:
    """What Codex reads of the files attached: each one's path and type."""
    lines = [
        f"- {item.path} ({item.media_type}"
        f"{', shown above' if item.media_type in IMAGE_TYPES else ''})"
        for item in files
    ]
    return "\n".join(
        [
            f"<{ATTACHED_TAG}>",
            "The user attached these files to this message, kept outside the project at these "
            "paths. Read one before answering when the request involves it, instead of asking "
            "the user to paste it.",
            *lines,
            f"</{ATTACHED_TAG}>",
        ]
    )


def attached_paths(text: str) -> tuple[str, ...] | None:
    """The paths a prompt's note names, or `None` when it has none."""
    found = _ATTACHED.search(text)
    return None if found is None else tuple(_ATTACHED_LINE.findall(found.group(1)))


def refused_note(reason: str) -> str:
    """What Codex reads of why a request was refused: its `decline` carries no reason."""
    return f"[Atlas: the request was refused: {reason}]"


# -- what Codex writes ----------------------------------------------------------------------------


@dataclass(slots=True)
class TurnState:
    """What one session's notifications say across a turn: its last words, and how many tokens
    its context held before a compaction."""

    last_text: str = ""
    tokens: int = 0
    turns: int = 0


def events_of(message: Mapping[str, Any], state: TurnState) -> list[ExecutorEvent]:
    """The events one notification makes, the SDK's for every harness."""
    method = str(message.get("method") or "")
    params = _dict(message.get("params"))
    if method == "item/started":
        return _started(_dict(params.get("item")))
    if method == "item/completed":
        return _completed(_dict(params.get("item")), state)
    if method == "thread/tokenUsage/updated":
        state.tokens = _tokens(params.get("tokenUsage"))
        return []
    if method == "account/rateLimits/updated":
        windows = windows_of(_dict(params.get("rateLimits")))
        return [UsageChanged(windows)] if windows else []
    if method == "error" and not params.get("willRetry"):
        error = _dict(params.get("error"))
        if error.get("codexErrorInfo") == "usageLimitExceeded":
            return [LimitReached()]
        return []
    if method == "turn/completed":
        return [finished_of(_dict(params.get("turn")), str(params.get("threadId") or ""), state)]
    return []


def finished_of(turn: Mapping[str, Any], thread: str, state: TurnState) -> Finished:
    """A turn's end: its last words, or what went wrong."""
    status = str(turn.get("status") or "completed")
    error = _dict(turn.get("error"))
    failed = status == "failed"
    text = state.last_text
    if failed and error:
        text = str(error.get("message") or "") or text
    state.turns += 1
    finished = Finished(
        text=text,
        failed=failed,
        session=thread,
        turns=1,
        reason=_STOPPED.get(status, "completed"),
    )
    state.last_text = ""
    return finished


def _started(item: Mapping[str, Any]) -> list[ExecutorEvent]:
    kind = str(item.get("type") or "")
    call = str(item.get("id") or "")
    if kind == "commandExecution":
        arguments = {"command": item.get("command") or "", "cwd": item.get("cwd") or ""}
        return [Called(call, _TOOLS[kind], _json(arguments))]
    if kind == "fileChange":
        paths = tuple(str(_dict(change).get("path") or "") for change in _list(item.get("changes")))
        return [Called(call, _TOOLS[kind], _json({"files": list(paths)}), paths)]
    if kind == "mcpToolCall":
        tool = f"mcp__{item.get('server') or 'mcp'}__{item.get('tool') or 'tool'}"
        return [Called(call, tool, cut(_json(item.get("arguments") or {})))]
    if kind == "dynamicToolCall":
        return [Called(call, str(item.get("tool") or "tool"), cut(_json(item.get("arguments"))))]
    if kind == "webSearch":
        return [Called(call, _TOOLS[kind], _json({"query": item.get("query") or ""}))]
    return []


def _completed(item: Mapping[str, Any], state: TurnState) -> list[ExecutorEvent]:
    kind = str(item.get("type") or "")
    call = str(item.get("id") or "")
    if kind == "agentMessage":
        text = str(item.get("text") or "")
        state.last_text = text
        return [Said(text)] if text else []
    if kind == "reasoning":
        text = "\n".join(str(part) for part in _list(item.get("summary")))
        return [Thought(text)] if text.strip() else []
    if kind == "contextCompaction":
        return [Compacted(state.tokens)]
    if kind in _TOOLS or kind in ("mcpToolCall", "dynamicToolCall"):
        text, failed = tool_result(item)
        return [Returned(call, text, failed)]
    return []


def tool_result(item: Mapping[str, Any]) -> tuple[str, bool]:
    """What one of its tool items returned, and whether it failed."""
    kind = str(item.get("type") or "")
    status = str(item.get("status") or "")
    if kind == "commandExecution":
        output = str(item.get("aggregatedOutput") or "")
        code = item.get("exitCode")
        failed = status in ("failed", "declined") or (isinstance(code, int) and code != 0)
        said = output if code is None else f"{output}\n[exit code {code}]".lstrip()
        return cut(said), failed
    if kind == "fileChange":
        changes = _list(item.get("changes"))
        return f"{status or 'done'}: {len(changes)} file(s)", status in ("failed", "declined")
    if kind == "mcpToolCall":
        error = item.get("error")
        if error:
            return cut(_json(error)), True
        return cut(_json(item.get("result") or "")), status == "failed"
    if kind == "dynamicToolCall":
        content = item.get("contentItems")
        return cut(_json(content) if content else ""), item.get("success") is False
    return status or "done", status == "failed"


def _tokens(usage: object) -> int:
    """How many tokens the context holds now, as `thread/tokenUsage/updated` says it."""
    last = _dict(_dict(usage).get("last"))
    total = _dict(_dict(usage).get("total"))
    found = last.get("inputTokens") or total.get("inputTokens") or 0
    return int(found) if isinstance(found, int | float) else 0


# -- what it asks ---------------------------------------------------------------------------------

#: The requests that ask the user's permission.
PERMISSION_METHODS = frozenset(
    {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/permissions/requestApproval",
        "execCommandApproval",
        "applyPatchApproval",
    }
)
QUESTION_METHOD = "item/tool/requestUserInput"


def permission_of(method: str, params: Mapping[str, Any]) -> Permission:
    """A permission request, for the user's card."""
    reason = str(params.get("reason") or "")
    if method in ("item/commandExecution/requestApproval", "execCommandApproval"):
        command = params.get("command")
        if isinstance(command, list):
            command = " ".join(str(part) for part in cast("list[object]", command))
        summary = str(command or "a command")
        return Permission("shell", summary, "Run a command", reason)
    if method in ("item/fileChange/requestApproval", "applyPatchApproval"):
        root = str(params.get("grantRoot") or "")
        summary = f"write under {root}" if root else "change files"
        return Permission("apply_patch", summary, "Change files", reason)
    permissions = _json(params.get("permissions") or {})
    return Permission("permissions", cut(permissions), "Get more permissions", reason)


def verdict_result(method: str, params: Mapping[str, Any], allow: bool) -> dict[str, Any]:
    """The answer to a permission request, in the shape its method takes."""
    if method == "item/permissions/requestApproval":
        granted = _dict(params.get("permissions")) if allow else {}
        return {"permissions": granted, "scope": "turn"}
    if method in ("execCommandApproval", "applyPatchApproval"):
        return {"decision": "approved" if allow else "denied"}
    return {"decision": "accept" if allow else "decline"}


def questions_of(params: Mapping[str, Any]) -> list[tuple[str, Question]]:
    """A question request's questions, each with Codex's id for it."""
    found: list[tuple[str, Question]] = []
    for raw in _list(params.get("questions")):
        asked = _dict(raw)
        options = tuple(
            Option(
                str(_dict(option).get("label") or ""), str(_dict(option).get("description") or "")
            )
            for option in _list(asked.get("options"))
        )
        question = Question(
            str(asked.get("question") or ""), str(asked.get("header") or ""), options
        )
        found.append((str(asked.get("id") or ""), question))
    return found


def answers_result(asked: Sequence[tuple[str, Question]], answers: Answers) -> dict[str, Any]:
    """The answers, as Codex reads them: a list of strings per question id."""
    result: dict[str, Any] = {}
    for question_id, question in asked:
        said = None if answers is None else answers.get(question.text)
        if said is None:
            continue
        result[question_id] = {"answers": [said] if isinstance(said, str) else list(said)}
    return {"answers": result}


# -- what it has ----------------------------------------------------------------------------------


def account_of(answer: Mapping[str, Any]) -> Account:
    """Who `account/read` says Codex is logged in as."""
    account = _dict(answer.get("account"))
    kind = account.get("type")
    if kind == "chatgpt":
        return Account(True, str(account.get("email") or ""), str(account.get("planType") or ""))
    if kind in ("apiKey", "amazonBedrock"):
        return Account(True, "API key" if kind == "apiKey" else "Amazon Bedrock")
    return Account(logged_in=False)


def models_of(answer: Mapping[str, Any]) -> tuple[ModelChoice, ...]:
    """What `model/list` offers, its default first, the hidden ones left out."""
    found: list[tuple[bool, ModelChoice]] = []
    for raw in _list(answer.get("data")):
        model = _dict(raw)
        if model.get("hidden"):
            continue
        efforts = tuple(
            str(_dict(effort).get("reasoningEffort") or "")
            for effort in _list(model.get("supportedReasoningEfforts"))
        )
        choice = ModelChoice(
            str(model.get("id") or model.get("model") or ""),
            str(model.get("displayName") or model.get("model") or ""),
            str(model.get("description") or ""),
            tuple(effort for effort in efforts if effort),
            str(model.get("model") or ""),
        )
        found.append((not model.get("isDefault"), choice))
    return tuple(choice for _, choice in sorted(found, key=lambda pair: pair[0]))


def connectors_of(answer: Mapping[str, Any]) -> tuple[Connector, ...]:
    """What `mcpServerStatus/list` says of its MCP servers."""
    found: list[Connector] = []
    for raw in _list(answer.get("data")):
        server = _dict(raw)
        origin = "plugin" if server.get("pluginId") else "user"
        found.append(Connector(str(server.get("name") or ""), _connector_status(server), origin))
    return tuple(found)


def _connector_status(server: Mapping[str, Any]) -> ConnectorStatus:
    if server.get("authStatus") == "notLoggedIn":
        return "needs-auth"
    if server.get("toolsError"):
        return "failed"
    return "connected"


def usage_of(answer: Mapping[str, Any]) -> PlanUsage:
    """What `account/rateLimits/read` says of the plan's windows, and whether past them the
    account's credits pay on (`credits`, `hasCredits` or `unlimited`), as Claude's extra usage."""
    limits = _dict(answer.get("rateLimits"))
    plan = str(limits.get("planType") or "")
    windows = windows_of(limits)
    if not windows:
        return PlanUsage(plan=plan, unavailable="This login has no plan limits: an API key pays.")
    credits = _dict(limits.get("credits"))
    paid = None
    if credits:
        paid = bool(credits.get("hasCredits")) or bool(credits.get("unlimited"))
    return PlanUsage(windows, plan, extra_usage=paid)


def windows_of(limits: Mapping[str, Any]) -> tuple[UsageWindow, ...]:
    """A snapshot's windows: Codex's `primary` and `secondary`."""
    found: list[UsageWindow] = []
    for key in ("primary", "secondary"):
        window = _dict(limits.get(key))
        used = window.get("usedPercent")
        if not isinstance(used, int | float):
            continue
        minutes = window.get("windowDurationMins")
        minutes = int(minutes) if isinstance(minutes, int | float) else 0
        found.append(
            UsageWindow(
                key, _window_title(minutes), float(used), _iso(window.get("resetsAt")), minutes
            )
        )
    return tuple(found)


def _window_title(minutes: int) -> str:
    if minutes == 300:
        return "5-hour limit"
    if minutes == 10_080:
        return "Weekly limit"
    if minutes and minutes % 1_440 == 0:
        return f"{minutes // 1_440}-day limit"
    if minutes and minutes % 60 == 0:
        return f"{minutes // 60}-hour limit"
    return "Limit"


def session_of(thread: Mapping[str, Any]) -> SessionInfo:
    """A thread as the executors tab lists it."""
    title = str(thread.get("name") or "") or _first_line(str(thread.get("preview") or ""))
    return SessionInfo(
        str(thread.get("id") or ""),
        _path(thread.get("cwd")),
        title,
        _iso(thread.get("updatedAt")),
        _iso(thread.get("createdAt")),
    )


def entries_of(turns: Sequence[Mapping[str, Any]]) -> tuple[TranscriptEntry, ...]:
    """A session's turns, oldest first, as the entries every harness shares."""
    entries: list[TranscriptEntry] = []
    for turn in turns:
        at = _iso(turn.get("startedAt"))
        for raw in _list(turn.get("items")):
            entries.extend(_entries(_dict(raw), at))
    return tuple(entries)


def _entries(item: Mapping[str, Any], at: str) -> list[TranscriptEntry]:
    kind = str(item.get("type") or "")
    call = str(item.get("id") or "")
    if kind == "userMessage":
        texts = [
            str(_dict(part).get("text") or "")
            for part in _list(item.get("content"))
            if _dict(part).get("type") == "text"
        ]
        images = tuple(
            str(_dict(part).get("path") or "")
            for part in _list(item.get("content"))
            if _dict(part).get("type") == "localImage"
        )
        named = next((paths for text in texts if (paths := attached_paths(text)) is not None), None)
        said = "\n".join(text for text in texts if attached_paths(text) is None)
        files = named if named is not None else images
        return [TranscriptEntry("text", said, by="user", at=at, files=files)]
    if kind == "agentMessage":
        text = str(item.get("text") or "")
        return [TranscriptEntry("text", text, at=at)] if text else []
    if kind == "contextCompaction":
        return [TranscriptEntry("compacted", "The conversation was compacted.", at=at)]
    called = _started(item)
    if called and isinstance(called[0], Called):
        text, failed = tool_result(item)
        return [
            TranscriptEntry("tool", called[0].arguments, tool=called[0].tool, at=at, call=call),
            TranscriptEntry("result", text, at=at, call=call, failed=failed),
        ]
    return []


# -- reading --------------------------------------------------------------------------------------


def version_of(output: str) -> str:
    """`codex --version`'s version (`codex-cli 0.163.0-alpha.10.2`)."""
    for word in output.split():
        if _NUMBERS.match(word):
            return word
    return ""


def at_least(version: str, minimum: str) -> bool:
    """Whether a version is `minimum` or later, by its numbers (`0.163.0-alpha.10.2` is 0.163.0);
    one Atlas cannot read is not."""
    have, need = _numbers(version), _numbers(minimum)
    return bool(have) and bool(need) and have >= need


def _numbers(version: str) -> tuple[int, ...]:
    found = _NUMBERS.match(version.strip())
    return () if found is None else tuple(int(part) for part in found.group().split("."))


def effort_problem(
    models: Sequence[ModelChoice], model: str | None, effort: str | None
) -> str | None:
    """Why an effort cannot be asked of a model, or `None`: one it does not take."""
    if effort is None or not models:
        return None
    chosen = next((choice for choice in models if choice.id == model), None) if model else models[0]
    if chosen is None or not chosen.efforts or effort in chosen.efforts:
        return None
    return f"{chosen.name} takes the efforts {', '.join(chosen.efforts)}, not {effort}."


def cut(text: str) -> str:
    """Text kept for the screen and the transcript, cut where it is long."""
    return text if len(text) <= SHOWN_CHARS else text[:SHOWN_CHARS] + " […]"


def _first_line(text: str) -> str:
    line = text.strip().split("\n", 1)[0].strip()
    return line[:120]


def _iso(moment: object) -> str:
    """A Unix time in seconds as ISO 8601 in UTC; empty when there is none."""
    if not isinstance(moment, int | float) or isinstance(moment, bool) or moment <= 0:
        return ""
    return datetime.fromtimestamp(moment, UTC).isoformat().replace("+00:00", "Z")


def _path(raw: object) -> Path:
    return Path(str(raw or ""))


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def records(page: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The objects of a listed page (`data`)."""
    return [found for item in _list(page.get("data")) if (found := _dict(item))]


def _dict(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _list(value: object) -> list[Any]:
    return cast("list[Any]", value) if isinstance(value, list) else []
