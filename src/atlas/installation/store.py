"""The installation's I/O: its checkout, the commands it runs, the stamps, the launcher, the app."""

import hashlib
import json
import logging
import os
import shutil
import socket
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol, TextIO, cast

from atlas.installation.errors import InstallationError
from atlas.installation.host_updater import Request, request_line
from atlas.installation.model import (
    APP_NAME,
    BUILD_INPUTS,
    HOST_SOCKET,
    LINUX_ICON,
    TOOLCHAIN_FILE,
    TOOLS_FOLDER,
    WINDOWS_ICON,
    desktop_entry,
    parse_toolchain,
)

_logger = logging.getLogger("atlas.installation")


class Toolchain:
    """The installation's own programs (uv, Node, Git on Windows; `gh` in an install from before
    05/11/2026), which the installer pins
    and names in `tools/toolchain.json` beside the checkout. They never go on the user's PATH
    (with its environment there, every terminal's `python` would be Atlas's);
    Atlas's own processes get them first on theirs. A development checkout has no toolchain, and
    its commands come from the PATH."""

    def __init__(self, folder: Path | None = None, programs: Mapping[str, Path] | None = None):
        self.folder = folder
        self.programs = dict(programs or {})

    @classmethod
    def of(cls, checkout: "Checkout") -> "Toolchain":
        """The toolchain beside `checkout`, or none when the installer left no file there."""
        folder = checkout.root.parent / TOOLS_FOLDER
        try:
            text = (folder / TOOLCHAIN_FILE).read_text(encoding="utf-8")
        except FileNotFoundError:
            return cls()
        programs = {name: folder / where for name, where in parse_toolchain(text).items()}
        for name, program in programs.items():
            if not program.is_file():
                raise InstallationError(
                    f"The installation's {name} is missing ({program}); run the installer again "
                    "to repair it."
                )
        return cls(folder, programs)

    def folders(self) -> list[Path]:
        """The folders the programs sit in, in the file's order, each once."""
        return list(dict.fromkeys(program.parent for program in self.programs.values()))

    def environment(self, base: Mapping[str, str]) -> dict[str, str]:
        """`base` with the programs' folders first on its PATH."""
        environment = dict(base)
        key = next((name for name in environment if name.upper() == "PATH"), "PATH")
        ahead = [str(folder) for folder in self.folders()]
        rest = environment.pop(key, "")
        environment[key] = os.pathsep.join([*ahead, rest] if rest else ahead)
        return environment

    def find(self, program: str, environment: Mapping[str, str]) -> str | None:
        """The program by its name in the toolchain, else on `environment`'s PATH."""
        if program in self.programs:
            return str(self.programs[program])
        path = next((value for name, value in environment.items() if name.upper() == "PATH"), "")
        return shutil.which(program, path=path)


#: What a command run where nobody watches is given, so nothing waits on a person: no credential
#: asked on a terminal nobody sees, and no window of Git Credential Manager's.
_UNATTENDED = {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}


class Runner:
    """Runs the installation's commands with its toolchain, their output going to the terminal as
    they work, or to `log`. `unattended`, as the app's update runs them, nothing they start asks
    anyone anything, and on Windows none opens a window of its own."""

    def __init__(
        self,
        toolchain: Toolchain | None = None,
        *,
        log: TextIO | None = None,
        unattended: bool = False,
    ) -> None:
        self.toolchain = toolchain or Toolchain()
        self.log = log
        self.unattended = unattended

    def run(self, command: Sequence[str], cwd: Path) -> None:
        """Run `command`; raises `InstallationError` when it fails."""
        program, environment = self._resolve(command)
        log = self.log
        if log is not None:
            log.write(f"\n$ {' '.join(command)}\n")
            log.flush()
        completed = subprocess.run(  # noqa: S603
            [program, *command[1:]],
            cwd=cwd,
            env=environment,
            check=False,
            stdin=subprocess.DEVNULL if self.unattended else None,
            stdout=log,
            stderr=None if log is None else subprocess.STDOUT,
            creationflags=self._flags,
        )
        if completed.returncode != 0:
            where = "above" if log is None else f"in {getattr(log, 'name', 'the log')}"
            said = f"`{' '.join(command)}` failed (exit {completed.returncode})"
            raise InstallationError(f"{said}; its output is {where}.")

    def output(self, command: Sequence[str], cwd: Path, *, seconds: float | None = None) -> str:
        """Run `command` and return what it wrote; raises `InstallationError` when it fails, or
        takes longer than `seconds`."""
        program, environment = self._resolve(command)
        try:
            completed = subprocess.run(  # noqa: S603
                [program, *command[1:]],
                cwd=cwd,
                env=environment,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=seconds,
                stdin=subprocess.DEVNULL if self.unattended else None,
                creationflags=self._flags,
            )
        except subprocess.TimeoutExpired as error:
            raise InstallationError(
                f"`{' '.join(command)}` did not finish in {seconds:.0f} seconds."
            ) from error
        if completed.returncode != 0:
            raise InstallationError(
                f"`{' '.join(command)}` failed: {completed.stderr.strip() or completed.stdout}"
            )
        return completed.stdout

    @property
    def _flags(self) -> int:
        return subprocess.CREATE_NO_WINDOW if self.unattended and sys.platform == "win32" else 0

    def _resolve(self, command: Sequence[str]) -> tuple[str, dict[str, str]]:
        environment = self.toolchain.environment(os.environ)
        if self.unattended:
            environment.update(_UNATTENDED)
        program = self.toolchain.find(command[0], environment)
        if program is None:
            raise InstallationError(f"{command[0]} is not installed, or not on the PATH.")
        return program, environment


class Checkout:
    """A checkout of the repository: the one this Atlas runs from."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.desktop = root / "apps" / "desktop"

    @classmethod
    def running(cls) -> "Checkout":
        """The checkout the running Atlas comes from (`src/atlas` sits two folders under it)."""
        root = Path(__file__).resolve().parents[3]
        if not (root / "apps" / "desktop" / "package.json").is_file():
            raise InstallationError(
                f"{root} is not a checkout of the Atlas repository, so it has no app to build. "
                "Run this from a clone of the repository (the packaged app comes with the "
                "installer)."
            )
        return cls(root)

    def stamp(self) -> str:
        """A hash of what the app's build is made from, by path and content."""
        digest = hashlib.sha256()
        for path in self._inputs():
            digest.update(path.relative_to(self.desktop).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()

    def file_hash(self, relative: str) -> str:
        return hashlib.sha256((self.desktop / relative).read_bytes()).hexdigest()

    def read_stamp(self, relative: str) -> str | None:
        try:
            return (self.desktop / relative).read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None

    def write_stamp(self, relative: str, value: str) -> None:
        path = self.desktop / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value + "\n", encoding="utf-8")

    def has_electron(self) -> bool:
        """Whether Electron's program is there. Electron 43 no longer downloads it when its
        package installs: it does on the first `require('electron')`, which `atlas desktop` never
        makes (found when the installation was first built, on 11/10/2026)."""
        try:
            return self.electron().is_file()
        except InstallationError:
            return False

    def electron(self) -> Path:
        """The Electron the app's packages installed (its `path.txt` names the program)."""
        package = self.desktop / "node_modules" / "electron"
        try:
            name = (package / "path.txt").read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            raise InstallationError(
                "The app's packages are not installed. Run `atlas desktop` to install them."
            ) from None
        return package / "dist" / name

    def _inputs(self) -> list[Path]:
        found: list[Path] = []
        for name in BUILD_INPUTS:
            path = self.desktop / name
            if path.is_dir():
                found.extend(p for p in path.rglob("*") if p.is_file() and not _is_test(p))
            elif path.is_file():
                found.append(path)
        return sorted(found)


def _is_test(path: Path) -> bool:
    return ".test." in path.name


def write_launcher(checkout: Checkout, home_folder: Path) -> Path | None:
    """The launcher the user opens the app from, written again on every build: the Start Menu
    shortcut on Windows, the app grid entry on Linux, nothing elsewhere yet. It opens the built app
    directly. Best effort: a launcher that cannot be written is said and the build goes on."""
    try:
        electron = checkout.electron()
        if sys.platform == "win32":
            return _windows_shortcut(electron, checkout.desktop)
        if sys.platform.startswith("linux"):
            return _linux_entry(electron, checkout.desktop, home_folder)
    except (OSError, subprocess.SubprocessError, InstallationError) as error:
        _logger.warning("The launcher could not be written: %s", error)
    return None


def _windows_shortcut(electron: Path, desktop: Path) -> Path:
    programs = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu"
    link = programs / "Programs" / f"{APP_NAME}.lnk"
    link.parent.mkdir(parents=True, exist_ok=True)

    def quoted(text: str) -> str:
        return "'" + text.replace("'", "''") + "'"

    script = (
        "$shell = New-Object -ComObject WScript.Shell; "
        f"$link = $shell.CreateShortcut({quoted(str(link))}); "
        f"$link.TargetPath = {quoted(str(electron))}; "
        f"$link.Arguments = {quoted(chr(34) + str(desktop) + chr(34))}; "
        f"$link.WorkingDirectory = {quoted(str(desktop))}; "
        f"$link.IconLocation = {quoted(str(desktop / WINDOWS_ICON) + ',0')}; "
        f"$link.Description = {quoted(APP_NAME)}; "
        "$link.Save()"
    )
    shell = shutil.which("powershell") or "powershell"
    completed = subprocess.run(  # noqa: S603
        [shell, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise InstallationError(completed.stderr.strip() or "PowerShell could not save it.")
    return link


def _linux_entry(electron: Path, desktop: Path, home_folder: Path) -> Path:
    data = Path(os.environ.get("XDG_DATA_HOME") or home_folder / ".local" / "share")
    entry = data / "applications" / "atlas.desktop"
    entry.parent.mkdir(parents=True, exist_ok=True)
    text = desktop_entry(f'"{electron}" "{desktop}"', str(desktop), str(desktop / LINUX_ICON))
    if not entry.is_file() or entry.read_text(encoding="utf-8") != text:
        entry.write_text(text, encoding="utf-8")
    return entry


def launch(checkout: Checkout, flags_given: Sequence[str] = ()) -> None:
    """Start the app detached from the terminal, so the shell can be closed, with `flags_given`
    after it: the update opens the app again as it was
    opened. An editor built on Electron sets ELECTRON_RUN_AS_NODE for its children, which would
    start the app as Node."""
    environment = {k: v for k, v in os.environ.items() if k != "ELECTRON_RUN_AS_NODE"}
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(  # noqa: S603
        [str(checkout.electron()), str(checkout.desktop), *flags_given],
        cwd=checkout.desktop,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        start_new_session=sys.platform != "win32",
    )


def sandbox_problem(checkout: Checkout) -> str:
    """Why the app, rebuilt, cannot open itself, or nothing when it can. On Linux Electron's
    `chrome-sandbox` must be root's with the setuid bit, which the installer sets and a new install
    of the app's packages undoes. As the installer does, `sudo` is tried when it needs no password;
    a kernel that lets users make namespaces needs no helper, nor does a sandbox turned off."""
    if not sys.platform.startswith("linux"):
        return ""
    try:
        sandbox = checkout.electron().parent / "chrome-sandbox"
    except InstallationError:
        return ""
    if not sandbox.is_file() or sandbox.is_symlink():
        return ""
    found = sandbox.stat()
    if found.st_uid == 0 and found.st_mode & stat.S_ISUID:
        return ""
    if os.environ.get("ELECTRON_DISABLE_SANDBOX", "").lower() in ("1", "true"):
        return ""
    if _succeeds(["unshare", "--user", "--map-root-user", "true"]):
        return ""
    fixed = _succeeds(["sudo", "-n", "true"]) and all(
        _succeeds(["sudo", "-n", *step, str(sandbox)])
        for step in (["chown", "root:root"], ["chmod", "4755"])
    )
    if fixed:
        return ""
    return (
        "The app was updated, but its sandbox needs root to own its helper before it opens: "
        f"sudo chown root:root '{sandbox}' && sudo chmod 4755 '{sandbox}'"
    )


def _succeeds(command: list[str]) -> bool:
    try:
        return (
            subprocess.run(  # noqa: S603 - fixed tools, no user text
                command, capture_output=True, timeout=10, check=False, stdin=subprocess.DEVNULL
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


# -- the way to a Compose host's updater -----------------------------------------------------------

#: The longest answer the host updater gives: its log's tail is 64 KiB.
_ANSWER_BYTES = 1024 * 1024


class HostChannel(Protocol):
    """The way to the host updater (`atlas.installation.host_updater`): one request, one answer."""

    def ask(self, request: Request) -> Mapping[str, object]: ...


class SocketChannel:
    """The host updater's Unix socket, which the Compose file mounts into the container."""

    def __init__(self, path: str = HOST_SOCKET, seconds: float = 60.0) -> None:
        self.path = path
        self.seconds = seconds

    def ask(self, request: Request) -> Mapping[str, object]:
        if sys.platform == "win32":
            raise InstallationError("The host updater is reached from Linux only.")
        data = b""
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.seconds)
                connection.connect(self.path)
                connection.sendall(request_line(request))
                while b"\n" not in data and len(data) < _ANSWER_BYTES:
                    part = connection.recv(65536)
                    if not part:
                        break
                    data += part
        except FileNotFoundError:
            raise InstallationError(
                "The host updater is not installed on this server: see docs/COMPOSE_HOST.md."
            ) from None
        except OSError as error:
            raise InstallationError(f"The host updater did not answer ({error}).") from None
        try:
            found: object = json.loads(data.split(b"\n", 1)[0].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise InstallationError("The host updater answered something unreadable.") from None
        if not isinstance(found, dict):
            raise InstallationError("The host updater answered something unreadable.")
        return cast("dict[str, object]", found)
