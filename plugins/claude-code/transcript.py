"""Claude Code's sessions, read back as the SDK's transcript.

Claude Code keeps each session as one JSON object a line, in
`~/.claude/projects/<folder>/<id>.jsonl` (the folder is the project's path with every character
but letters and digits made a hyphen), and deletes it 30 days after it was last used (its
`cleanupPeriodDays`). The session's id is unique, so it is looked for under every project, whatever
spelling of the path made the folder.

**The file is a tree, not a list** (step 16f): each line names its parent (`parentUuid`), and a
rewind, a retried reply or a side chain leaves branches the conversation no longer follows. The
session is the chain from its latest leaf back to its root, as the Claude Agent SDK reads it
(`get_session_messages` in 0.2.165, `_internal/sessions.py`), which is what Claude Code's VS Code
extension shows. Before step
16f every line was read in file order, branches and side chains included.

**A slash command reads as the user typed it** (18/10/2026): Claude Code keeps `/name args` as a
user line of tags (`<command-name>`, `<command-message>`, `<command-args>`), and what a command it
runs itself said as a `system` line of subtype `local_command` (`<local-command-stdout>`), a child
of the command's line. The SDK's chain stops at the last user or assistant line, so the output of
a session's last command was lost: the chain goes on through those lines. Before, the tags showed
as the user's words, and the output not at all.

**A prompt's files read as its files** (step 16i): the note this plugin writes with them
(`<atlas-attachments>`, `protocol.attached_note`) becomes the entry's `files`, not the user's
words, and the images sent beside it are left out, as the note names them too.
"""

import dataclasses
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, cast

from atlas.plugins.sdk import Transcript, TranscriptEntry

from .protocol import QUESTION_TOOL, attached_paths, blocks, compacted_of, cut, result_text
from .sessions import project_folder

#: A slash command as Claude Code keeps it, and what a command it runs itself said.
_COMMAND_NAME = re.compile(r"<command-name>\s*/?(.*?)\s*</command-name>", re.DOTALL)
_COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
_COMMAND_OUTPUT = re.compile(
    r"<local-command-(?:stdout|stderr)>(.*?)</local-command-(?:stdout|stderr)>", re.DOTALL
)

#: The lines that link into the tree (the SDK's `_TRANSCRIPT_ENTRY_TYPES`).
_LINKED = frozenset({"user", "assistant", "progress", "system", "attachment"})


def claude_folder(environ: dict[str, str] | None = None) -> Path:
    """Where Claude Code keeps its state: `CLAUDE_CONFIG_DIR`, or `~/.claude`."""
    named = (os.environ if environ is None else environ).get("CLAUDE_CONFIG_DIR")
    return Path(named) if named else Path.home() / ".claude"


def session_file(root: Path, session: str, folder: Path) -> Path | None:
    """The session's file, or `None` when Claude Code no longer keeps it."""
    if not session or any(part in session for part in ("/", "\\", "..")):
        return None
    projects = root / "projects"
    guessed = projects / project_folder(str(folder)) / f"{session}.jsonl"
    if guessed.is_file():
        return guessed
    return next(iter(sorted(projects.glob(f"*/{session}.jsonl"))), None)


def delete_session_files(root: Path, transcript: Path) -> None:
    """Delete one session's files under `root`: those `claude purge` deletes for a whole project
    (code.claude.com/docs, Explore the .claude directory, "Clear local data"), its transcript and
    the folder beside it (`<id>/`, its subagents and tool results), and its `file-history/`,
    `tasks/` and `debug/` entries; never another session's, nor the project's memory."""
    session = transcript.stem
    for kept in (
        transcript.with_suffix(""),
        root / "file-history" / session,
        root / "tasks" / session,
    ):
        if kept.is_dir():
            shutil.rmtree(kept)
    for log in (root / "debug").glob(f"{session}.*"):
        log.unlink(missing_ok=True)
    transcript.unlink(missing_ok=True)


def read_transcript(root: Path, session: str, folder: Path) -> Transcript:
    """A session's steps, in order: what the user and Claude Code said, its tools and their
    results, its questions and their answers. Its thinking is left out."""
    found = session_file(root, session, folder)
    if found is None:
        return Transcript(expired=True)
    linked: list[dict[str, Any]] = []
    with found.open(encoding="utf-8", errors="replace") as lines:
        for line in lines:
            try:
                raw: object = json.loads(line)
            except ValueError:
                continue
            if isinstance(raw, dict):
                record = cast("dict[str, Any]", raw)
                if record.get("type") in _LINKED and isinstance(record.get("uuid"), str):
                    linked.append(record)
    entries: list[TranscriptEntry] = []
    questions: set[str] = set()
    context: int | None = None
    for record in chain(linked):
        if record.get("type") == "system" and record.get("subtype") == "compact_boundary":
            compacted = compacted_of(record.get("compactMetadata"))
            context = compacted.after
            entries.append(
                TranscriptEntry("compacted", compacted.text, at=str(record.get("timestamp") or ""))
            )
        elif _local_command(record):
            entries += _said(str(record.get("content") or ""), "user", record)
        elif (steered := _steered(record)) is not None:
            entries += _said(steered, "user", record)
        elif _visible(record):
            entries += _entries(record, questions)
        context = _context(record, context)
    return Transcript(tuple(_compacted_where_asked(entries)), context=context)


def _context(record: dict[str, Any], before: int | None) -> int | None:
    """The tokens of context after `record`: what its reply read and wrote, from the usage Claude
    Code keeps with each of the model's replies; `before` for a line without one."""
    body: object = record.get("message")
    if record.get("type") != "assistant" or not isinstance(body, dict):
        return before
    usage: object = cast("dict[str, Any]", body).get("usage")
    if not isinstance(usage, dict):
        return before
    counted = cast("dict[str, Any]", usage)
    parts = (
        "input_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "output_tokens",
    )
    total = sum(int(n) for n in (counted.get(part) for part in parts) if isinstance(n, int))
    return total or before


def _compacted_where_asked(entries: list[TranscriptEntry]) -> list[TranscriptEntry]:
    """A `/compact` the user typed is answered by its compaction (asked for by the user on
    22/10/2026). Claude Code roots the chain at the compaction's boundary, so the boundary reads
    first, above the summary, and the command's own output, a bare "Compacted", reads where the
    user looks: the compaction, how many tokens became how many, goes in the output's place."""
    at = next((i for i, entry in enumerate(entries) if entry.kind == "compacted"), None)
    if at is None:
        return entries
    asked = next(
        (
            i
            for i in range(at + 1, len(entries))
            if entries[i].by == "user" and entries[i].text.split(" ")[0] == "/compact"
        ),
        None,
    )
    if asked is None:
        return entries
    moved = entries[at]
    rest = [*entries[:at], *entries[at + 1 : asked], entries[asked]]
    after = entries[asked + 1 :]
    said = after[0] if after else None
    if said and said.kind == "text" and said.text.strip().casefold() == "compacted":
        after = after[1:]
    return [*rest, moved, *after]


def chain(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The conversation as it stands: from the latest leaf of the main chain back to the root,
    in order (the SDK's `_build_conversation_chain`). A compaction's boundary is not crossed: its
    summary stands for what came before, as in the VS Code extension."""
    if not records:
        return []
    by_id = {str(record["uuid"]): record for record in records}
    place = {str(record["uuid"]): index for index, record in enumerate(records)}
    parents = {str(record.get("parentUuid")) for record in records if record.get("parentUuid")}
    leaves: list[dict[str, Any]] = []
    for terminal in (record for record in records if str(record["uuid"]) not in parents):
        seen: set[str] = set()
        walking: dict[str, Any] | None = terminal
        while walking is not None and str(walking["uuid"]) not in seen:
            seen.add(str(walking["uuid"]))
            if walking.get("type") in ("user", "assistant"):
                leaves.append(walking)
                break
            walking = by_id.get(str(walking.get("parentUuid") or ""))
    if not leaves:
        return []
    main = [
        leaf
        for leaf in leaves
        if not leaf.get("isSidechain") and not leaf.get("teamName") and not leaf.get("isMeta")
    ]
    leaf = max(main or leaves, key=lambda found: place.get(str(found["uuid"]), -1))
    walked: list[dict[str, Any]] = []
    seen = set[str]()
    current: dict[str, Any] | None = leaf
    while current is not None and str(current["uuid"]) not in seen:
        seen.add(str(current["uuid"]))
        walked.append(current)
        current = by_id.get(str(current.get("parentUuid") or ""))
    walked.reverse()
    # What a command run by Claude Code itself said hangs below the leaf, a `system` line.
    children: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        children.setdefault(str(record.get("parentUuid") or ""), []).append(record)
    tail = str(leaf["uuid"])
    while True:
        said = [child for child in children.get(tail, []) if _local_command(child)]
        if not said or str(said[-1]["uuid"]) in seen:
            return walked
        seen.add(str(said[-1]["uuid"]))
        walked.append(said[-1])
        tail = str(said[-1]["uuid"])


def _steered(record: dict[str, Any]) -> str | None:
    """What the user sent while Claude Code worked: kept as an `attachment` line of type
    `queued_command`, not as a user line, so it vanished once the session was read again."""
    if record.get("type") != "attachment":
        return None
    attachment: object = record.get("attachment")
    if not isinstance(attachment, dict):
        return None
    queued = cast("dict[str, Any]", attachment)
    prompt = queued.get("prompt")
    if queued.get("type") != "queued_command" or not isinstance(prompt, str):
        return None
    return prompt if queued.get("commandMode", "prompt") == "prompt" else None


def _local_command(record: dict[str, Any]) -> bool:
    """A `system` line of a slash command Claude Code ran itself: its name, or its output."""
    return record.get("type") == "system" and record.get("subtype") == "local_command"


def _said(text: str, by: str, record: dict[str, Any]) -> list[TranscriptEntry]:
    """A line's text: a slash command as the user typed it (`/name args`), what a command run by
    Claude Code itself said as its words, or the text as it is."""
    at = str(record.get("timestamp") or "")
    named = _COMMAND_NAME.search(text)
    if named is not None:
        given = _COMMAND_ARGS.search(text)
        arguments = given.group(1).strip() if given is not None else ""
        typed = f"/{named.group(1)} {arguments}".strip()
        return [TranscriptEntry("text", typed, by="user", at=at)]
    output = _COMMAND_OUTPUT.findall(text)
    if output:
        said = "\n".join(part.strip() for part in output if part.strip())
        return [TranscriptEntry("text", said, at=at)] if said else []
    if not text.strip():
        return []
    return [TranscriptEntry("text", text, by="user" if by == "user" else "harness", at=at)]


def _visible(record: dict[str, Any]) -> bool:
    """What the conversation shows of the chain (the SDK's `_is_visible_message`). A compaction's
    summary is the model's, not the user's words: its boundary stands for it."""
    if record.get("type") not in ("user", "assistant"):
        return False
    hidden = ("isMeta", "isSidechain", "teamName", "isCompactSummary")
    return not any(record.get(key) for key in hidden)


def _entries(record: dict[str, Any], questions: set[str]) -> list[TranscriptEntry]:
    kind = record.get("type")
    at = str(record.get("timestamp") or "")
    body: object = record.get("message")
    content = cast("dict[str, Any]", body).get("content") if isinstance(body, dict) else None
    if kind == "user" and isinstance(content, str):
        return _said(content, "user", record)
    found: list[TranscriptEntry] = []
    files: tuple[str, ...] = ()
    for block in blocks(record):
        part = block.get("type")
        attached = attached_paths(str(block.get("text") or "")) if kind == "user" else None
        if part == "text" and attached is not None:
            files += attached
        elif part == "text" and block.get("text"):
            by = "user" if kind == "user" else "harness"
            found += _said(str(block["text"]), by, record)
        elif part == "tool_use":
            tool = str(block.get("name", ""))
            call = str(block.get("id", ""))
            arguments: object = block.get("input") or {}
            if tool == QUESTION_TOOL:
                questions.add(call)
                asked = cast("dict[str, Any]", arguments) if isinstance(arguments, dict) else {}
                text = "\n".join(
                    str(cast("dict[str, Any]", q).get("question", ""))
                    for q in cast("list[object]", asked.get("questions") or [])
                    if isinstance(q, dict)
                )
                found.append(TranscriptEntry("question", text, tool=tool, at=at, call=call))
            else:
                text = cut(json.dumps(arguments, ensure_ascii=False))
                found.append(TranscriptEntry("tool", text, tool=tool, at=at, call=call))
        elif part == "tool_result":
            text = cut(result_text(block.get("content")))
            call = str(block.get("tool_use_id", ""))
            kind_of = "answer" if call in questions else "result"
            failed = bool(block.get("is_error"))
            found.append(TranscriptEntry(kind_of, text, at=at, call=call, failed=failed))
    if files:
        said = next((index for index, e in enumerate(found) if e.by == "user"), None)
        if said is None:
            found.insert(0, TranscriptEntry("text", "", by="user", at=at, files=files))
        else:
            found[said] = dataclasses.replace(found[said], files=files)
    return found
