"""What a workspace changed, line by line (step 21, A2): its folder against where it left its branch
of origin, committed or not, its new files whole, for the app to show and the user to comment on.

Each line keeps its number on each side, so a comment names the line the agent finds in the file.
Big changes are cut, said so: the app shows a diff to read, not to replace the editor.
"""

from dataclasses import dataclass, field
from pathlib import Path

from atlas.workspaces import git

#: How many lines of one file are shown, and how many files.
MAX_LINES = 3000
MAX_FILES = 300
#: A new file bigger than this is said, not shown.
MAX_NEW_BYTES = 512_000


@dataclass(frozen=True, slots=True)
class DiffLine:
    #: `same`, `plus`, `minus`, or `gap` between two hunks.
    kind: str
    text: str
    #: Its number in the file before and after, 0 where it has none (a line added has no old).
    old: int = 0
    new: int = 0


@dataclass(slots=True)
class DiffFile:
    path: str
    #: `added`, `modified`, `deleted` or `renamed`.
    status: str
    added: int = 0
    removed: int = 0
    binary: bool = False
    #: Cut at `MAX_LINES`.
    truncated: bool = False
    lines: list[DiffLine] = field(default_factory=list[DiffLine])


def changes(folder: Path, base: str) -> tuple[list[DiffFile], bool]:
    """Every file `folder` changed since it left `base`, and whether some were left out past
    `MAX_FILES`."""
    start = git.run(folder, "merge-base", base, "HEAD").strip()
    patch = git.run(
        folder, "-c", "core.quotepath=off", "diff", "--no-color", "--no-ext-diff",
        "--find-renames", "--unified=3", start,
    )  # fmt: skip
    files = parse(patch)
    untracked = git.run(folder, "-c", "core.quotepath=off", "ls-files", "--others",
                        "--exclude-standard").splitlines()  # fmt: skip
    files += [_new_file(folder, name) for name in untracked if name]
    files.sort(key=lambda found: found.path)
    return files[:MAX_FILES], len(files) > MAX_FILES


def parse(patch: str) -> list[DiffFile]:
    """A unified diff of git's as files of numbered lines."""
    files: list[DiffFile] = []
    current: DiffFile | None = None
    old = new = 0
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            current = DiffFile(path=_path_of(line), status="modified")
            files.append(current)
            continue
        if current is None:
            continue
        if line.startswith("new file mode"):
            current.status = "added"
        elif line.startswith("deleted file mode"):
            current.status = "deleted"
        elif line.startswith("rename to "):
            current.status, current.path = "renamed", line.removeprefix("rename to ")
        elif line.startswith("Binary files "):
            current.binary = True
        elif line.startswith("+++ ") or line.startswith("--- "):
            continue
        elif line.startswith("@@"):
            old, new = _hunk_start(line)
            if current.lines:
                _add(current, DiffLine("gap", "⋯"))
        elif line.startswith("+"):
            current.added += 1
            _add(current, DiffLine("plus", line[1:], 0, new))
            new += 1
        elif line.startswith("-"):
            current.removed += 1
            _add(current, DiffLine("minus", line[1:], old, 0))
            old += 1
        elif line.startswith(" "):
            _add(current, DiffLine("same", line[1:], old, new))
            old += 1
            new += 1
    return files


def _add(file: DiffFile, line: DiffLine) -> None:
    if len(file.lines) >= MAX_LINES:
        file.truncated = True
        return
    file.lines.append(line)


def _path_of(header: str) -> str:
    """The path after `b/` in `diff --git a/<path> b/<path>`."""
    rest = header.removeprefix("diff --git ")
    marker = rest.rfind(" b/")
    return rest[marker + 3 :] if marker >= 0 else rest


def _hunk_start(header: str) -> tuple[int, int]:
    """The first line of each side in `@@ -12,3 +12,4 @@`."""
    parts = header.split()
    try:
        old = int(parts[1].lstrip("-").split(",")[0])
        new = int(parts[2].lstrip("+").split(",")[0])
    except (IndexError, ValueError):
        return 1, 1
    return max(old, 1), max(new, 1)


def _new_file(folder: Path, name: str) -> DiffFile:
    """A file git does not track yet, every line of it added."""
    found = DiffFile(path=name, status="added")
    path = folder / name
    try:
        if path.stat().st_size > MAX_NEW_BYTES:
            found.truncated = True
            return found
        data = path.read_bytes()
    except OSError:
        return found
    if b"\0" in data[:8000]:
        found.binary = True
        return found
    for number, text in enumerate(data.decode("utf-8", errors="replace").splitlines(), start=1):
        found.added += 1
        _add(found, DiffLine("plus", text, 0, number))
    return found
