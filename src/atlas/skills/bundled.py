"""The skills Atlas ships, copied into the home's library and kept up to date there without ever
writing over what the user or the agent changed.

The sync:

- The repository's `atlas/skills/bundled/<name>/` folders are copied into the home's `skills/`,
  and `skills/.bundled_manifest` keeps, per skill, the hash of what was copied (`name:hash`
  lines).
- A skill new to the manifest is copied, unless the library already has a skill by that name,
  which is kept (and adopted only when it is the same bytes).
- A skill that changed in the repository is replaced only while the home's copy still matches the
  hash it was copied with; one the user or the agent changed is left alone, and `atlas skills
  reset` puts the bundled one back.
- A skill in the manifest but gone from the home was deleted by the user and is not copied again.
- A skill gone from the repository leaves the manifest once no copy of it is left (the entry is
  the copy's only record of where it came from).

What a sync writes goes through the library (`SkillLibrary.put`), so an update keeps the previous
files as a version and `atlas skills revert` undoes it. There are no categories (Atlas's skills
are one folder deep), no external skill folders or optional skills, no essential-only marker, no
recovery of a skill renamed upstream, and no list of skills the curator pruned (Atlas's curator
never archives a bundled skill: it archives only the review's).
"""

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from atlas.home import replace_file
from atlas.skills.errors import SkillChangeError
from atlas.skills.library import SkillLibrary
from atlas.skills.model import NAME
from atlas.skills.store import skill_file

#: Where the skills Atlas ships live, inside the installed package.
BUNDLED_FOLDER = Path(__file__).parent / "bundled"
MANIFEST_FILE = ".bundled_manifest"
#: What running a skill's scripts leaves behind, never part of the skill.
_RUNTIME = frozenset({"__pycache__", ".pytest_cache", "node_modules"})

_logger = logging.getLogger("atlas.skills")


@dataclass(frozen=True, slots=True)
class SyncResult:
    copied: list[str] = field(default_factory=lambda: [])
    updated: list[str] = field(default_factory=lambda: [])
    #: Changed by the user or the agent, so left as they are.
    kept: list[str] = field(default_factory=lambda: [])
    #: A skill of the library already had the name; it was kept.
    taken: list[str] = field(default_factory=lambda: [])
    #: Gone from the repository and from the home: dropped from the manifest.
    dropped: list[str] = field(default_factory=lambda: [])
    failed: list[str] = field(default_factory=lambda: [])


def skill_files(folder: Path) -> dict[PurePosixPath, bytes]:
    """A skill's files, without what running its scripts left behind."""
    files: dict[PurePosixPath, bytes] = {}
    for path in sorted(folder.rglob("*")):
        relative = PurePosixPath(path.relative_to(folder).as_posix())
        if path.is_file() and not _RUNTIME.intersection(relative.parts) and path.suffix != ".pyc":
            files[relative] = path.read_bytes()
    return files


def files_digest(files: dict[PurePosixPath, bytes]) -> str:
    digest = hashlib.sha256()
    for relative, data in sorted(files.items()):
        digest.update(str(relative).encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()


def bundled_skills(source: Path = BUNDLED_FOLDER) -> dict[str, Path]:
    """The skills Atlas ships, by name."""
    found: dict[str, Path] = {}
    if source.is_dir():
        for folder in sorted(source.iterdir()):
            if folder.is_dir() and NAME.fullmatch(folder.name) and skill_file(folder) is not None:
                found[folder.name] = folder
    return found


def read_manifest(skills: Path) -> dict[str, str]:
    """`{name: hash of what was copied}`; a damaged line is skipped."""
    try:
        text = (skills / MANIFEST_FILE).read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    manifest: dict[str, str] = {}
    for line in text.splitlines():
        name, _, digest = line.strip().partition(":")
        if name and digest:
            manifest[name] = digest
    return manifest


def _write_manifest(skills: Path, manifest: dict[str, str]) -> None:
    skills.mkdir(parents=True, exist_ok=True)
    text = "".join(f"{name}:{digest}\n" for name, digest in sorted(manifest.items()))
    replace_file(skills / MANIFEST_FILE, text.encode("utf-8"))


def changed(library: SkillLibrary, name: str, manifest: dict[str, str]) -> bool | None:
    """Whether a bundled skill's copy in the home was changed since it was copied; `None` for a
    skill that is not one Atlas copied."""
    digest = manifest.get(name)
    folder = library.folder / name
    if digest is None or not folder.is_dir():
        return None
    return files_digest(skill_files(folder)) != digest


def sync_bundled(library: SkillLibrary, source: Path = BUNDLED_FOLDER) -> SyncResult:
    """Copy the bundled skills into the library and update the untouched ones."""
    result = SyncResult()
    shipped = bundled_skills(source)
    manifest = read_manifest(library.folder)
    for name, folder in shipped.items():
        files = skill_files(folder)
        digest = files_digest(files)
        dest = library.folder / name
        try:
            if name not in manifest:
                if dest.exists():
                    result.taken.append(name)
                    if files_digest(skill_files(dest)) == digest:
                        manifest[name] = digest
                    continue
                library.put(name, files)
                manifest[name] = digest
                result.copied.append(name)
            elif not dest.exists() or manifest[name] == digest:
                continue  # deleted by the user, or unchanged in the repository
            elif files_digest(skill_files(dest)) != manifest[name]:
                result.kept.append(name)
            else:
                library.put(name, files)
                manifest[name] = digest
                result.updated.append(name)
        except (OSError, SkillChangeError) as error:
            _logger.warning("The bundled skill %s could not be copied: %s", name, error)
            result.failed.append(name)
    for name in sorted(set(manifest) - set(shipped)):
        if not (library.folder / name).exists():
            del manifest[name]
            result.dropped.append(name)
    _write_manifest(library.folder, manifest)
    return result


def retire_bundled(library: SkillLibrary, names: set[str]) -> tuple[list[str], list[str]]:
    """Skills Atlas shipped that a plugin of Atlas's brings now (step 26: `github` and the `gws-*`
    ones, in the GitHub and Google Workspace plugins): a copy the user never changed is archived,
    `atlas skills restore` bringing it back; one they changed stays, theirs now. Either leaves the
    manifest. The archived and the kept, by name."""
    manifest = read_manifest(library.folder)
    archived: list[str] = []
    kept: list[str] = []
    for name in sorted(names & manifest.keys()):
        if changed(library, name, manifest) is False:
            try:
                library.archive(name)
            except SkillChangeError as error:
                _logger.warning("A skill a plugin brings now was not archived: %s", error)
                continue
            archived.append(name)
        else:
            kept.append(name)
        del manifest[name]
    _write_manifest(library.folder, manifest)
    return archived, kept


def reset_bundled(library: SkillLibrary, name: str, source: Path = BUNDLED_FOLDER) -> None:
    """Put a bundled skill back as Atlas ships it, so it receives updates again. What the copy
    held is kept as a version."""
    folder = bundled_skills(source).get(name)
    if folder is None:
        raise SkillChangeError(f"'{name}' is not a skill Atlas ships.")
    files = skill_files(folder)
    library.put(name, files)
    manifest = read_manifest(library.folder)
    manifest[name] = files_digest(files)
    _write_manifest(library.folder, manifest)
