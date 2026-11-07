"""What the Gemini CLI writes and reads, apart from the channel: pure, so every shape is tested
without a Gemini CLI (step 16p).

Its sessions are its own files, one JSON object a line (`session-<time>-<id>.jsonl` in
`.gemini/tmp/<project>/chats/`), which it appends to as a session goes and corrects with three
records of its own: `$set` (the session's fields, or its whole list of messages), `$patch` (one
message's content or tool results, messages removed or reordered) and `$rewindTo` (the session
cut back to a message). They are read here as its own reader reads them
(`createJsonlRecordAccumulator`, in its `chatRecordingService.ts`, Apache-2.0).

Its plan's limits are Google's Code Assist quota: one bucket for each model, the fraction left and
when it starts again, as its `/stats` shows them.
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from atlas.plugins.acp import IMAGE_TYPES, AcpError, cut
from atlas.plugins.sdk import (
    Account,
    ExecutorFile,
    Finished,
    ModelChoice,
    PlanUsage,
    TranscriptEntry,
    UsageWindow,
)

GEMINI = "gemini-cli"
#: How Atlas names it to the user.
TITLE = "Gemini CLI"
#: Who Atlas is in the handshake.
CLIENT_NAME = "atlas"
CLIENT_TITLE = "Atlas workbench"
CLIENT_VERSION = "1"
#: Moves the Gemini CLI's whole home: its `.gemini` folder is found inside.
HOME_VARIABLE = "GEMINI_CLI_HOME"
#: Its folder inside a home.
STATE = ".gemini"
#: The plugin's secret, and the variable the Gemini CLI reads it from: a personal Google
#: account's only way in since Google stopped its login for them (found on 21/10/2026).
KEY_SECRET = "GEMINI_API_KEY"  # noqa: S105 - a name, not the key
#: What Google answers a personal account's login with now.
NO_PERSONAL_LOGIN = "no longer supported"
#: Asks it for a login by a code typed in its terminal, which writes over the channel: never set.
_DROPPED = ("NO_BROWSER",)
#: Its modes: the one that asks, and its automatic one, which approves every tool.
ASKING_MODE = "default"
AUTO_MODE = "yolo"
#: What it answers when a folder it does not trust is asked for its automatic mode.
UNTRUSTED = "untrusted folder"
#: The note it says when its mode changes, a message of its own that is not the model's.
MODE_NOTE = "[MODE_UPDATE]"
#: Its login methods (`authMethods`): Google's, and the API key.
GOOGLE_LOGIN = "oauth-personal"
API_KEY_LOGIN = "gemini-api-key"
#: Where it asks Google for the account's quota (`CODE_ASSIST_ENDPOINT` moves it, as for it).
CODE_ASSIST = "https://cloudcode-pa.googleapis.com/v1internal"
#: How it names itself to Code Assist.
CLIENT_METADATA = {
    "ideType": "IDE_UNSPECIFIED",
    "platform": "PLATFORM_UNSPECIFIED",
    "pluginType": "GEMINI",
}
#: How long before its token expires it is no longer used.
TOKEN_MARGIN_SECONDS = 60
ATTACHED_TAG = "atlas-attachments"
_NUMBERS = re.compile(r"\d+(\.\d+)*")
#: How a prompt's end becomes the turn's reason.
_STOPS = {"end_turn": "completed", "cancelled": "interrupted"}
#: What a terminal quota error says (its `googleQuotaErrors.ts`): the plan's limit, not a pause.
_LIMITS = ("quota", "usage limit", "exhausted", "credits")
#: What a user message starts with when it was only a command or context it added itself (its
#: `isIgnoredUserContent`).
_IGNORED = ("/", "?", "<session_context>", "<hook_context>")
#: Its tiers, as the plan is named.
_TIERS = {"free-tier": "free", "legacy-tier": "legacy", "standard-tier": "standard"}


def environment(
    environ: Mapping[str, str], home: str | None = None, api_key: str | None = None
) -> dict[str, str]:
    """Atlas's environment as the Gemini CLI gets it: the user's own, whose login it may be (an
    API key), without what would make it write a login prompt over the channel; with `home`, the
    home of another account, whose `.gemini` it then runs on; with `api_key`, the key the
    plugin's secret keeps, over the user's own."""
    kept = {name: value for name, value in environ.items() if name.upper() not in _DROPPED}
    if home:
        kept[HOME_VARIABLE] = home
    if api_key:
        kept[KEY_SECRET] = api_key
    return kept


# -- what Atlas writes ----------------------------------------------------------------------------


def attached_note(files: Sequence[ExecutorFile]) -> str:
    """What the Gemini CLI reads of the files attached: each one's path and type. Images go with
    the prompt too; every file is in a folder it may read (`--include-directories`)."""
    if not files:
        return ""
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


def refused_note(reason: str) -> str:
    """What the Gemini CLI reads of why a request was refused: ACP's answer carries no reason."""
    return f"[Atlas: the request was refused: {reason}]"


# -- what it answers ------------------------------------------------------------------------------


def finished_of(result: Mapping[str, Any], session: str, last: str) -> Finished:
    """A turn's end, from what `session/prompt` answered."""
    stop = str(result.get("stopReason") or "end_turn")
    return Finished(last, session=session, turns=1, reason=_STOPS.get(stop, stop))


def is_limit(error: AcpError) -> bool:
    """Whether a prompt's error is the plan's limit: Google's 429 for a quota spent, not a pause
    of a minute."""
    said = str(error).lower()
    return error.code == 429 and any(word in said for word in _LIMITS)


def models_of(answer: Mapping[str, Any]) -> tuple[ModelChoice, ...]:
    """The models a `session/new` offers (`models.availableModels`), its own order kept."""
    found: list[ModelChoice] = []
    for item in _list(_dict(answer.get("models")).get("availableModels")):
        model = _dict(item)
        if not model.get("modelId"):
            continue
        found.append(
            ModelChoice(
                str(model["modelId"]),
                str(model.get("name") or model["modelId"]),
                str(model.get("description") or ""),
            )
        )
    return tuple(found)


def current_model(answer: Mapping[str, Any]) -> str:
    return str(_dict(answer.get("models")).get("currentModelId") or "")


def current_mode(answer: Mapping[str, Any]) -> str:
    return str(_dict(answer.get("modes")).get("currentModeId") or "")


# -- its folder -----------------------------------------------------------------------------------


def account_of(
    settings: Mapping[str, Any],
    accounts: Mapping[str, Any],
    has_credentials: bool,
    environ: Mapping[str, str],
) -> Account:
    """Who the Gemini CLI is logged in as, read from its files as they are: the login it chose
    (`security.auth.selectedType`), Google's account it names (`google_accounts.json`), and
    whether it keeps a token. An API key logs in with no account to name."""
    chosen = chosen_login(settings)
    if chosen == GOOGLE_LOGIN:
        login = str(accounts.get("active") or "")
        return Account(logged_in=has_credentials and bool(login), login=login)
    if chosen in ("", API_KEY_LOGIN):
        keyed = bool(environ.get("GEMINI_API_KEY"))
        return Account(logged_in=keyed, login="API key" if keyed else "")
    if chosen == "vertex-ai":
        vertex = bool(environ.get("GOOGLE_API_KEY") or environ.get("GOOGLE_CLOUD_PROJECT"))
        return Account(logged_in=vertex, login="Vertex AI" if vertex else "")
    return Account(logged_in=True, login=chosen)


def chosen_login(settings: Mapping[str, Any]) -> str:
    """The login its settings chose (`security.auth.selectedType`)."""
    return str(_dict(_dict(settings.get("security")).get("auth")).get("selectedType") or "")


def usable_token(credentials: Mapping[str, Any], now: float) -> str:
    """Its Google token while it is still good, else nothing: Atlas never renews it."""
    token = str(credentials.get("access_token") or "")
    expiry: object = credentials.get("expiry_date")
    if not token or not isinstance(expiry, int | float):
        return ""
    return token if expiry / 1000 > now + TOKEN_MARGIN_SECONDS else ""


def load_request() -> dict[str, Any]:
    """`loadCodeAssist`'s body, as the Gemini CLI asks it with no project of its own."""
    return {"metadata": dict(CLIENT_METADATA)}


def quota_project(loaded: Mapping[str, Any]) -> str:
    return str(loaded.get("cloudaicompanionProject") or "")


def plan_of(loaded: Mapping[str, Any]) -> str:
    """The account's tier, as `loadCodeAssist` names it."""
    tier = _dict(loaded.get("paidTier")) or _dict(loaded.get("currentTier"))
    tier_id = str(tier.get("id") or "")
    return _TIERS.get(tier_id, str(tier.get("name") or tier_id))


def usage_of(plan: str, quota: Mapping[str, Any]) -> PlanUsage:
    """`retrieveUserQuota`'s buckets as windows: one a model, used the share not left."""
    windows: list[UsageWindow] = []
    seen: set[str] = set()
    for item in _list(quota.get("buckets")):
        bucket = _dict(item)
        model = str(bucket.get("modelId") or "")
        left: object = bucket.get("remainingFraction")
        if not model or model in seen or not isinstance(left, int | float):
            continue
        seen.add(model)
        used = round(max(0.0, min(1.0, 1.0 - float(left))) * 100, 1)
        windows.append(
            UsageWindow(model, model, used, str(bucket.get("resetTime") or ""), model=model)
        )
    if not windows:
        return PlanUsage(plan=plan, unavailable="Google said no limits for this account.")
    return PlanUsage(tuple(windows), plan)


@dataclass(slots=True)
class Conversation:
    """One session file, read: its fields and its messages in order."""

    fields: dict[str, Any] = field(default_factory=dict[str, Any])
    messages: dict[str, dict[str, Any]] = field(default_factory=dict[str, dict[str, Any]])

    @property
    def id(self) -> str:
        return str(self.fields.get("sessionId") or "")

    @property
    def subagent(self) -> bool:
        return self.fields.get("kind") == "subagent"

    def resumable(self) -> bool:
        """Whether its own `--resume` would list it: a message beyond a command."""
        return any(_resumable(message) for message in self.messages.values())

    def title(self) -> str:
        """Its summary, else its first prompt's first line."""
        if summary := str(self.fields.get("summary") or "").strip():
            return summary.split("\n", 1)[0][:120]
        for message in self.messages.values():
            if message.get("type") == "user" and _resumable(message):
                return parts_text(message.get("content")).strip().split("\n", 1)[0][:120]
        return ""


def conversation(lines: Sequence[str]) -> Conversation:
    """A session file's lines, read as the Gemini CLI reads them."""
    read = Conversation()
    messages = read.messages
    for line in lines:
        if not line.strip():
            continue
        try:
            raw: object = json.loads(line)
        except ValueError:
            continue
        record = _dict(raw)
        if "$rewindTo" in record:
            ids = list(messages)
            target = str(record["$rewindTo"])
            kept = ids[: ids.index(target)] if target in ids else []
            read.messages = messages = {key: messages[key] for key in kept}
        elif "$patch" in record:
            _patch(messages, _dict(record["$patch"]))
        elif isinstance(record.get("id"), str):
            messages[str(record["id"])] = record
        elif isinstance(record.get("$set"), dict):
            changed = cast("dict[str, Any]", record["$set"])
            if isinstance(changed.get("messages"), list):
                messages.clear()
                for item in cast("list[object]", changed["messages"]):
                    message = _dict(item)
                    if isinstance(message.get("id"), str):
                        messages[str(message["id"])] = message
            read.fields.update({k: v for k, v in changed.items() if k != "messages"})
        elif isinstance(record.get("sessionId"), str):
            read.fields.update({k: v for k, v in record.items() if k != "messages"})
    return read


def _patch(messages: dict[str, dict[str, Any]], patch: Mapping[str, Any]) -> None:
    for update in [patch, *(_dict(item) for item in _list(patch.get("updates")))]:
        found = messages.get(str(update.get("id") or ""))
        if found is None:
            continue
        if "content" in update and update["content"] is not None:
            found["content"] = update["content"]
        for change in _list(update.get("toolCalls")):
            call = _dict(change)
            for tool in _list(found.get("toolCalls")):
                if _dict(tool).get("id") == call.get("id") and "result" in call:
                    tool["result"] = call["result"]
    for removed in _list(patch.get("removeIds")):
        messages.pop(str(removed), None)
    order = [str(key) for key in _list(patch.get("orderIds")) if str(key) in messages]
    if order:
        ordered = {key: messages[key] for key in messages if key not in order}
        ordered.update({key: messages[key] for key in order})
        messages.clear()
        messages.update(ordered)


def entries_of(read: Conversation) -> tuple[TranscriptEntry, ...]:
    """A session read back as the five kinds of entry every harness shares."""
    entries: list[TranscriptEntry] = []
    for message in read.messages.values():
        at = str(message.get("timestamp") or "")
        kind = message.get("type")
        if kind == "user" and _resumable(message):
            text = parts_text(message.get("content"))
            entries.append(TranscriptEntry("text", text, by="user", at=at))
        elif kind == "gemini":
            if text := parts_text(message.get("content")).strip():
                entries.append(TranscriptEntry("text", text, at=at))
            for item in _list(message.get("toolCalls")):
                entries.extend(_tool_entries(_dict(item), at))
    return tuple(entries)


def _tool_entries(call: Mapping[str, Any], at: str) -> list[TranscriptEntry]:
    name = str(call.get("name") or "tool")
    call_id = str(call.get("id") or "")
    arguments = _dict(call.get("args"))
    paths = tuple(
        str(arguments[key]) for key in ("file_path", "path", "absolute_path") if arguments.get(key)
    )
    shown = call.get("resultDisplay")
    result = shown if isinstance(shown, str) and shown else _response_text(call.get("result"))
    return [
        TranscriptEntry(
            "tool",
            json.dumps(arguments, ensure_ascii=False),
            tool=name,
            at=at,
            call=call_id,
            files=paths,
        ),
        TranscriptEntry(
            "result",
            cut(result),
            tool=name,
            at=str(call.get("timestamp") or at),
            call=call_id,
            failed=call.get("status") not in (None, "success"),
        ),
    ]


def _response_text(raw: object) -> str:
    """A tool's result as the model got it: its `functionResponse`'s output or error."""
    lines: list[str] = []
    for item in _list(raw):
        response = _dict(_dict(_dict(item).get("functionResponse")).get("response"))
        said = response.get("output", response.get("error", ""))
        lines.append(said if isinstance(said, str) else json.dumps(said, ensure_ascii=False))
    return "\n".join(line for line in lines if line)


def parts_text(content: object) -> str:
    """A message's content, text or a list of parts, as its text."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return str(cast("dict[str, Any]", content).get("text") or "")
    return "".join(
        part if isinstance(part, str) else str(_dict(part).get("text") or "")
        for part in _list(content)
    )


def _resumable(message: Mapping[str, Any]) -> bool:
    text = parts_text(message.get("content")).strip()
    if message.get("type") == "user":
        return bool(text) and not text.startswith(_IGNORED)
    if message.get("type") == "gemini":
        return bool(text or _list(message.get("toolCalls")) or _list(message.get("thoughts")))
    return False


def session_file_name(session: str, now: datetime) -> str:
    """The name the Gemini CLI gives a session's file: its start, to the minute, and its id's first
    eight characters."""
    return f"session-{now.strftime('%Y-%m-%dT%H-%M')}-{session[:8]}.jsonl"


def renamed(lines: Sequence[str], session: str, now: datetime) -> list[str]:
    """A session's lines as a copy of its own: the new id in its first record, started now."""
    copied = list(lines)
    for index, line in enumerate(copied):
        record = _dict(_loads(line))
        if isinstance(record.get("sessionId"), str):
            moment = now.astimezone(UTC).isoformat().replace("+00:00", "Z")
            record.update(sessionId=session, startTime=moment, lastUpdated=moment)
            copied[index] = json.dumps(record, ensure_ascii=False) + "\n"
            break
    return copied


def version_of(output: str) -> str:
    """`gemini --version`'s version (`0.65.0`)."""
    for word in output.split():
        if _NUMBERS.match(word):
            return word
    return ""


def at_least(version: str, minimum: str) -> bool:
    """Whether a version is `minimum` or later, by its numbers; one Atlas cannot read is not."""
    have, need = _numbers(version), _numbers(minimum)
    return bool(have) and bool(need) and have >= need


def _numbers(version: str) -> tuple[int, ...]:
    found = _NUMBERS.match(version.strip())
    return () if found is None else tuple(int(part) for part in found.group().split("."))


def _loads(line: str) -> object:
    try:
        return json.loads(line)
    except ValueError:
        return None


def _dict(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _list(value: object) -> list[Any]:
    return cast("list[Any]", value) if isinstance(value, list) else []
