"""What the DeepSeek Harness (`dsh`) writes and reads, apart from the channel: pure, so every
shape is tested without it (step 16q).

Its sessions are its own files, one each (`sessions/<folder>/<id>/session.v4.jsonl.zstd` in its
home): a log of events, one JSON object a line, in Zstandard frames. Its turn is the events
`user/message`, `assistant/message` (text, reasoning, the tool calls it makes), `tool/call` and
`tool/result`; the rest (its steps, its titles, its jobs, its uploads) are its own bookkeeping.
Beside them a projection it keeps in plain JSON (`storages/session_projcache/sessions/<id>.json`)
says each session's folder, title and how many prompts it had, so sessions are listed without
reading a log.

Its balance is DeepSeek's (`GET /user/balance`): what is left to spend, in its currency. DeepSeek
bills each request by its tokens, so there are no plan windows to show.
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from atlas.plugins.acp import IMAGE_TYPES, AcpError, cut
from atlas.plugins.sdk import (
    ExecutorFile,
    Finished,
    ModelChoice,
    PlanUsage,
    SessionInfo,
    TranscriptEntry,
)

DEEPSEEK = "deepseek"
#: How Atlas names it to the user.
TITLE = "DeepSeek"
#: Who Atlas is in the handshake.
CLIENT_NAME = "atlas"
CLIENT_TITLE = "Atlas workbench"
CLIENT_VERSION = "1"
#: Moves its whole home: credentials, sessions, profiles.
HOME_VARIABLE = "DSH_HOME"
#: Its home, when the variable names none.
HOME_FOLDER = ".dsh"
#: The process's permission preset, read once at start: it cannot change in a session.
MODE_VARIABLE = "DSH_PERMISSION_MODE"
#: The preset that asks (it writes only in the session's folder), and its automatic one, which
#: approves everything with no review.
ASKING_MODE = "workspace-write"
AUTO_MODE = "danger-full-access"
#: The plugin's secret, and the variable it reads its key from.
KEY_SECRET = "DEEPSEEK_API_KEY"  # noqa: S105 - a name, not the key
#: Its credentials file, in its home.
CREDENTIALS = ".credentials.yaml"
#: The patch that turns off the session logs it sends DeepSeek with each request
#: (`session-log-deepseek`, on by default since its 2026-09-14 decision).
LOGS_OFF = "- id: session-log-deepseek\n  config:\n    enabled: false\n"
#: Its configuration options for the model and the effort (`session/set_config_option`).
MODEL_OPTION = "model"
EFFORT_OPTION = "reasoning_effort"
#: Where DeepSeek says the account's balance.
BALANCE_URL = "https://api.deepseek.com/user/balance"
ATTACHED_TAG = "atlas-attachments"
_NUMBERS = re.compile(r"\d+(\.\d+)*")
#: How a prompt's end becomes the turn's reason.
_STOPS = {"end_turn": "completed", "cancelled": "interrupted"}
#: What a request refused for its balance says (DeepSeek's 402).
_LIMITS = ("insufficient balance", "402")


def environment(
    environ: Mapping[str, str],
    home: str | None = None,
    *,
    auto: bool = False,
    api_key: str | None = None,
) -> dict[str, str]:
    """Atlas's environment as `dsh` gets it: the user's own (its key may be there), its permission
    preset for this process, with `home` another account's home, and with `api_key` the key the
    plugin's secret keeps, over the user's own."""
    kept = dict(environ)
    kept[MODE_VARIABLE] = AUTO_MODE if auto else ASKING_MODE
    if home:
        kept[HOME_VARIABLE] = home
    if api_key:
        kept[KEY_SECRET] = api_key
    return kept


# -- what Atlas writes ----------------------------------------------------------------------------


def attached_note(files: Sequence[ExecutorFile]) -> str:
    """What `dsh` reads of the files attached: each one's path and type. Its prompts take no
    images over ACP, so every file goes by its path, which its tools read."""
    if not files:
        return ""
    lines = [f"- {item.path} ({item.media_type})" for item in files]
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
    """What `dsh` reads of why a request was refused: ACP's answer carries no reason."""
    return f"[Atlas: the request was refused: {reason}]"


def images(files: Sequence[ExecutorFile]) -> tuple[ExecutorFile, ...]:
    return tuple(item for item in files if item.media_type in IMAGE_TYPES)


# -- what it answers ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Models:
    """Its models as a session offers them: Atlas's choices, and each one's option value."""

    choices: tuple[ModelChoice, ...] = ()
    values: tuple[tuple[str, str], ...] = ()
    current: str = ""

    def value(self, model: str) -> str | None:
        return next((value for name, value in self.values if name == model), None)


def models_of(answer: Mapping[str, Any]) -> Models:
    """The models and efforts a `session/new` offers in its `configOptions`, its order kept."""
    options = {
        str(_dict(item).get("id")): _dict(item) for item in _list(answer.get("configOptions"))
    }
    efforts = tuple(
        str(_dict(choice).get("value"))
        for choice in _list(options.get(EFFORT_OPTION, {}).get("options"))
        if _dict(choice).get("value")
    )
    model = options.get(MODEL_OPTION, {})
    choices: list[ModelChoice] = []
    values: list[tuple[str, str]] = []
    current = ""
    for group in _list(model.get("options")):
        entries = _list(_dict(group).get("options")) or [group]
        for entry in entries:
            item = _dict(entry)
            value = str(item.get("value") or "")
            name = str(item.get("name") or value)
            if not value:
                continue
            choices.append(ModelChoice(name, name, str(_dict(group).get("name") or ""), efforts))
            values.append((name, value))
            if value == model.get("currentValue"):
                current = name
    return Models(tuple(choices), tuple(values), current)


def finished_of(result: Mapping[str, Any], session: str, last: str) -> Finished:
    """A turn's end, from what `session/prompt` answered."""
    stop = str(result.get("stopReason") or "end_turn")
    return Finished(last, session=session, turns=1, reason=_STOPS.get(stop, stop))


def is_limit(error: AcpError) -> bool:
    """Whether a prompt's error is the account's balance spent: DeepSeek's 402."""
    said = str(error).lower()
    return any(word in said for word in _LIMITS)


def balance_of(answer: Mapping[str, Any]) -> PlanUsage:
    """DeepSeek's balance, said as the plan: there are no windows, it bills by use."""
    infos = [_dict(item) for item in _list(answer.get("balance_infos"))]
    shown = ", ".join(
        f"{info.get('total_balance')} {info.get('currency')}"
        for info in infos
        if info.get("total_balance") is not None
    )
    if not shown:
        return PlanUsage(unavailable="DeepSeek said no balance for this key.")
    available = answer.get("is_available") is not False
    return PlanUsage(
        plan=f"balance {shown}",
        unavailable=(
            "DeepSeek bills each request from this balance; it has no plan limits."
            if available
            else "DeepSeek says this balance is spent: add to it on its platform."
        ),
    )


# -- its files ------------------------------------------------------------------------------------


def session_of(projection: Mapping[str, Any], session: str, updated: float) -> SessionInfo | None:
    """A session as its projection says it, or `None` for one with no prompt yet (a session
    opened and left, as Atlas's model probe leaves one)."""
    record = _dict(projection.get("record"))
    identity = _dict(record.get("identity"))
    rows = _dict(record.get("rows"))
    title_input = _dict(_dict(rows.get("titleInput")).get("val"))
    if not identity.get("cwd") or not title_input.get("count"):
        return None
    title = _dict(rows.get("title")).get("val")
    first = title_input.get("first")
    shown = str(title or (first if isinstance(first, str) else "") or "")
    return SessionInfo(
        session,
        Path(str(identity["cwd"])),
        shown.strip().split("\n", 1)[0][:120],
        _iso(updated),
        _iso(_number(identity.get("createdAt")) / 1000),
    )


def entries_of(lines: Sequence[str]) -> tuple[TranscriptEntry, ...]:
    """A session's log read back as the five kinds of entry every harness shares."""
    entries: list[TranscriptEntry] = []
    names: dict[str, str] = {}
    for line in lines:
        event = _dict(_loads(line))
        kind = event.get("type")
        data = _dict(event.get("data"))
        at = _iso(_number(event.get("time")) / 1000)
        if kind == "user/message" and _dict(data.get("source")).get("kind") == "user":
            # Only the user's own: its plugins and subagents write user messages too.
            if text := blocks_text(data.get("content")):
                entries.append(TranscriptEntry("text", text, by="user", at=at))
        elif kind == "assistant/message":
            if text := blocks_text(_dict(data.get("message")).get("content")):
                entries.append(TranscriptEntry("text", text, at=at))
        elif kind == "tool/call":
            call = str(data.get("callId") or "")
            name = names[call] = str(data.get("name") or "tool")
            arguments = str(data.get("arguments") or "")
            entries.append(
                TranscriptEntry(
                    "tool", arguments, tool=name, at=at, call=call, files=_paths(arguments)
                )
            )
        elif kind == "tool/result":
            message = _dict(data.get("message"))
            call = str(message.get("toolCallId") or "")
            entries.append(
                TranscriptEntry(
                    "result",
                    cut(blocks_text(message.get("content"))),
                    tool=names.get(call, ""),
                    at=at,
                    call=call,
                    failed=message.get("isError") is True,
                )
            )
    return tuple(entries)


def blocks_text(content: object) -> str:
    """The text blocks of a message's content, joined; its reasoning and calls left out."""
    return "".join(
        str(_dict(block).get("text") or "")
        for block in _list(content)
        if _dict(block).get("type") == "text"
    ).strip()


def _paths(arguments: str) -> tuple[str, ...]:
    found = _dict(_loads(arguments))
    return tuple(
        str(found[key])
        for key in ("file_path", "path", "filePath")
        if isinstance(found.get(key), str)
    )


def version_of(output: str) -> str:
    """`dsh --version`'s version (`0.2.1-rc.2`)."""
    for word in output.split():
        if _NUMBERS.match(word):
            return word
    return ""


def at_least(version: str, minimum: str) -> bool:
    """Whether a version is `minimum` or later, by its numbers (`0.2.1-rc.2` is 0.2.1); one Atlas
    cannot read is not."""
    have, need = _numbers(version), _numbers(minimum)
    return bool(have) and bool(need) and have >= need


def _numbers(version: str) -> tuple[int, ...]:
    found = _NUMBERS.match(version.strip())
    return () if found is None else tuple(int(part) for part in found.group().split("."))


def _iso(moment: float) -> str:
    if moment <= 0:
        return ""
    return datetime.fromtimestamp(moment, UTC).isoformat().replace("+00:00", "Z")


def _number(value: object) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def _loads(line: str) -> object:
    try:
        return json.loads(line)
    except ValueError:
        return None


def _dict(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _list(value: object) -> list[Any]:
    return cast("list[Any]", value) if isinstance(value, list) else []
