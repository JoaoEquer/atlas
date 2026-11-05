"""Write the installer's pins into `scripts/install.ps1` and `scripts/install.sh`.

The installer brings its own tools, each at a fixed version and checked by its SHA-256 before use,
never what the machine has (docs/ARCHITECTURE.md, The installer):

- `scripts/install_pins.toml` is the record: each tool's version and the SHA-256 of its file for
  each target. What each file is called and where its programs sit inside it is here, in `TOOLS`.
- `--refresh` reads the SHA-256s from where each project publishes them, after a version changes:
  GitHub's release assets for uv and Git for Windows, Node's `SHASUMS256.txt`. Nothing is
  hashed from a download, so a refresh fixes what the project said, not what a network served.
- With no option, the blocks between the BEGIN and END markers are written again;
  `tests/installation/test_install_pins.py` fails when a committed script differs from them.

A version must be at least 14 days old, the rule of the app's `.npmrc`: Atlas waits out the
first days of a release, when a poisoned one is found and pulled.

Run with `uv run python scripts/gen_install_pins.py [--refresh]`; `--check` only says whether the
scripts are current.
"""

import json
import re
import sys
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PINS = ROOT / "scripts" / "install_pins.toml"
POWERSHELL = ROOT / "scripts" / "install.ps1"
SHELL = ROOT / "scripts" / "install.sh"

BEGIN = "# --- BEGIN GENERATED: install pins (scripts/gen_install_pins.py) ---"
END = "# --- END GENERATED: install pins ---"

#: Every target the installer knows: the system and the machine's architecture.
TARGETS = ("win32-x64", "win32-arm64", "linux-x64", "linux-arm64", "darwin-x64", "darwin-arm64")


@dataclass(frozen=True, slots=True)
class Tool:
    """How one tool's files are named, and where its programs sit once unpacked."""

    #: The targets it is downloaded for: Git only on Windows, where the system has none.
    targets: tuple[str, ...]
    #: The file's name for a version and a target.
    file: dict[str, str]
    #: Its address, from the version and the file's name.
    url: str
    #: The programs the toolchain names, by name, as paths under the unpacked folder.
    programs: dict[str, dict[str, str]]


def _uv_file(target: str) -> str:
    triple = {
        "win32-x64": "x86_64-pc-windows-msvc",
        "win32-arm64": "aarch64-pc-windows-msvc",
        "linux-x64": "x86_64-unknown-linux-gnu",
        "linux-arm64": "aarch64-unknown-linux-gnu",
        "darwin-x64": "x86_64-apple-darwin",
        "darwin-arm64": "aarch64-apple-darwin",
    }[target]
    return f"uv-{triple}.zip" if target.startswith("win32") else f"uv-{triple}.tar.gz"


def _uv_programs(target: str) -> dict[str, str]:
    if target.startswith("win32"):
        return {"uv": "uv.exe"}
    return {"uv": f"{_uv_file(target).removesuffix('.tar.gz')}/uv"}


def _node_name(target: str) -> str:
    system, arch = target.split("-")
    return {"win32": "win", "linux": "linux", "darwin": "darwin"}[system] + f"-{arch}"


def _node_file(version: str, target: str) -> str:
    # The .tar.gz, never the .tar.xz: minimal Debian has no xz.
    suffix = "zip" if target.startswith("win32") else "tar.gz"
    return f"node-v{version}-{_node_name(target)}.{suffix}"


def _node_programs(version: str, target: str) -> dict[str, str]:
    folder = f"node-v{version}-{_node_name(target)}"
    if target.startswith("win32"):
        return {
            "node": f"{folder}/node.exe",
            "npm": f"{folder}/npm.cmd",
            "npx": f"{folder}/npx.cmd",
        }
    return {"node": f"{folder}/bin/node", "npm": f"{folder}/bin/npm", "npx": f"{folder}/bin/npx"}


def _git_file(version: str, target: str) -> str:
    # 2.55.0.5 is the fifth Windows build of Git 2.55.0, tagged v2.55.0.windows.5.
    arch = {"win32-x64": "64-bit", "win32-arm64": "arm64"}[target]
    return f"PortableGit-{version}-{arch}.7z.exe"


def _git_tag(version: str) -> str:
    base, build = version.rsplit(".", 1)
    return f"v{base}.windows.{build}"


def tools(versions: dict[str, str]) -> dict[str, Tool]:
    """Each tool's files and programs at the versions the record pins."""
    uv, node, git = versions["uv"], versions["node"], versions["git"]
    windows = tuple(t for t in TARGETS if t.startswith("win32"))
    return {
        "uv": Tool(
            TARGETS,
            {t: _uv_file(t) for t in TARGETS},
            f"https://github.com/astral-sh/uv/releases/download/{uv}/{{file}}",
            {t: _uv_programs(t) for t in TARGETS},
        ),
        "node": Tool(
            TARGETS,
            {t: _node_file(node, t) for t in TARGETS},
            f"https://nodejs.org/dist/v{node}/{{file}}",
            {t: _node_programs(node, t) for t in TARGETS},
        ),
        "git": Tool(
            windows,
            {t: _git_file(git, t) for t in windows},
            f"https://github.com/git-for-windows/git/releases/download/{_git_tag(git)}/{{file}}",
            {t: {"git": "cmd/git.exe"} for t in windows},
        ),
    }


# -- the record ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Pin:
    tool: str
    version: str
    target: str
    url: str
    sha256: str
    #: `zip`, `tar.gz`, or `7z.exe` (PortableGit's self-extractor).
    archive: str
    programs: dict[str, str]


def read_pins(text: str) -> list[Pin]:
    """Every pinned file, in the record's order of tools and `TARGETS`' order of targets."""
    record = tomllib.loads(text)
    versions = {name: str(entry["version"]) for name, entry in record.items()}
    pins: list[Pin] = []
    for name, tool in tools(versions).items():
        hashes: dict[str, str] = record[name].get("sha256", {})
        for target in tool.targets:
            sha256 = hashes.get(target, "")
            if not re.fullmatch(r"[0-9a-f]{64}", sha256):
                raise SystemExit(f"{PINS.name}: {name} has no SHA-256 for {target}; run --refresh.")
            file = tool.file[target]
            archive = next(s for s in ("7z.exe", "tar.gz", "zip") if file.endswith(s))
            pins.append(
                Pin(
                    name,
                    versions[name],
                    target,
                    tool.url.format(file=file),
                    sha256,
                    archive,
                    tool.programs[target],
                )
            )
    return pins


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "atlas-gen-install-pins"})  # noqa: S310 - fixed https addresses
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        return response.read()


def refresh(text: str) -> str:
    """The record with every SHA-256 read again from where its project publishes it."""
    record = tomllib.loads(text)
    versions = {name: str(entry["version"]) for name, entry in record.items()}
    published: dict[str, dict[str, str]] = {}
    for name, tool in tools(versions).items():
        if name == "node":
            sums = _fetch(tool.url.format(file="SHASUMS256.txt")).decode()
            listed: dict[str, str] = {}
            for line in sums.splitlines():
                digest, _, file = line.partition("  ")
                if file:
                    listed[file.strip()] = digest.strip()
        else:
            release = tool.url.split("/releases/download/")
            repository = release[0].removeprefix("https://github.com/")
            tag = release[1].split("/")[0]
            api = f"https://api.github.com/repos/{repository}/releases/tags/{tag}"
            assets: list[dict[str, object]] = json.loads(_fetch(api))["assets"]
            listed = {
                str(asset["name"]): str(asset.get("digest") or "").removeprefix("sha256:")
                for asset in assets
            }
        published[name] = {target: listed[tool.file[target]] for target in tool.targets}
    lines: list[str] = []
    for line in text.splitlines():
        found = re.match(r'^sha256\.([\w-]+) = "[0-9a-f]*"$', line)
        if found and lines:
            section = next(seen for seen in reversed(lines) if seen.startswith("[")).strip("[]")
            line = f'sha256.{found[1]} = "{published[section][found[1]]}"'
        lines.append(line)
    return "\n".join(lines) + "\n"


# -- the blocks ------------------------------------------------------------------------------------


def powershell_block(pins: list[Pin]) -> str:
    """A hashtable of `"<tool>/<target>"` to its pin, which `Get-Pin` reads."""
    lines = [
        BEGIN,
        "# Written from scripts/install_pins.toml. DO NOT EDIT BY HAND: run the generator.",
        "$script:Pins = @{",
    ]
    for pin in pins:
        if not pin.target.startswith("win32"):
            continue
        programs = "; ".join(f'"{name}" = "{path}"' for name, path in pin.programs.items())
        lines += [
            f'    "{pin.tool}/{pin.target}" = @{{',
            f'        Version = "{pin.version}"',
            f'        Url = "{pin.url}"',
            f'        Sha256 = "{pin.sha256}"',
            f'        Archive = "{pin.archive}"',
            f"        Programs = [ordered]@{{ {programs} }}",
            "    }",
        ]
    lines += ["}", END]
    return "\n".join(lines)


def shell_block(pins: list[Pin]) -> str:
    """`pin TOOL TARGET`, which sets PIN_VERSION, PIN_URL, PIN_SHA256, PIN_ARCHIVE and PIN_PROGRAMS
    (`name=path` pairs, space-separated). A `case`, since macOS's bash 3.2 has no hash tables."""
    lines = [
        BEGIN,
        "# Written from scripts/install_pins.toml. DO NOT EDIT BY HAND: run the generator.",
        "pin() {",
        '    case "$1/$2" in',
    ]
    for pin in pins:
        if pin.target.startswith("win32"):
            continue
        programs = " ".join(f"{name}={path}" for name, path in pin.programs.items())
        lines += [
            f"        {pin.tool}/{pin.target})",
            f'            PIN_VERSION="{pin.version}"',
            f'            PIN_URL="{pin.url}"',
            f'            PIN_SHA256="{pin.sha256}"',
            f'            PIN_ARCHIVE="{pin.archive}"',
            f'            PIN_PROGRAMS="{programs}" ;;',
        ]
    lines += ["        *) return 1 ;;", "    esac", "}", END]
    return "\n".join(lines)


def written(script: str, block: str) -> str:
    """`script` with its generated block replaced by `block`."""
    start, end = script.find(BEGIN), script.find(END)
    if start < 0 or end < start:
        raise SystemExit("The script has no generated block (its BEGIN and END markers).")
    return script[:start] + block + script[end + len(END) :]


def main(argv: list[str]) -> int:
    text = PINS.read_text(encoding="utf-8")
    if "--refresh" in argv:
        text = refresh(text)
        PINS.write_text(text, encoding="utf-8", newline="\n")
    pins = read_pins(text)
    stale: list[str] = []
    for path, block in ((POWERSHELL, powershell_block(pins)), (SHELL, shell_block(pins))):
        current = path.read_text(encoding="utf-8")
        wanted = written(current, block)
        if wanted == current:
            continue
        if "--check" in argv:
            stale.append(path.name)
        else:
            path.write_text(wanted, encoding="utf-8", newline="\n")
            sys.stdout.write(f"wrote {path.relative_to(ROOT)}\n")
    if stale:
        sys.stdout.write(f"stale: {', '.join(stale)}; run scripts/gen_install_pins.py\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
