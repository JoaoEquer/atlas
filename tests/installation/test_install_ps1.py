"""`scripts/install.ps1`, run by Windows PowerShell 5.1 (and PowerShell 7 where it is installed, as
on CI): the installer's rules, each one a defect it guards against.

The script is dot-sourced for its functions, which then run for real: on real folders, a real
registry key of the test's own, real git repositories, a real download from a local server. The
whole install, with its downloads and the app's build, runs in CI's install job.
"""

import functools
import http.server
import json
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from hashlib import sha256
from pathlib import Path

import pytest

from atlas.installation import Checkout, Toolchain

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the Windows installer")

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "install.ps1"
SHELLS = ["powershell", *(["pwsh"] if shutil.which("pwsh") else [])]
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _quoted(text: str | Path) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def _not_a_checkout(root: Path, notes: str) -> Path:
    """An install folder whose `atlas` holds a file of the user's and no checkout: the file."""
    kept = root / "atlas" / "notes.txt"
    kept.parent.mkdir(parents=True)
    kept.write_text(notes, encoding="utf-8")
    return kept


def run(
    code: str,
    *,
    shell: str = "powershell",
    policy: str = "Bypass",
    environment: dict[str, str] | None = None,
    dot_source: bool = True,
) -> subprocess.CompletedProcess[str]:
    """`code` in a new PowerShell, after dot-sourcing the installer when asked."""
    prelude = f". {_quoted(SCRIPT)}\n" if dot_source else ""
    env = {**os.environ, **(environment or {})}
    return subprocess.run(  # noqa: S603 - the system's PowerShell, the test's own code
        [
            shell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            policy,
            "-Command",
            prelude + code,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
        timeout=180,
    )


# -- the file --------------------------------------------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
def test_the_script_is_ascii_with_no_bom_and_every_powershell_parses_it(shell: str) -> None:
    """One em-dash broke Windows PowerShell 5.1, which reads a BOM-less script in the ANSI code
    page; a BOM breaks `irm`."""
    data = SCRIPT.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf")
    assert max(data) < 0x80
    parsed = run(
        "$errors = $null\n"
        f"[void][System.Management.Automation.Language.Parser]::ParseFile({_quoted(SCRIPT)}, "
        "[ref]$null, [ref]$errors)\n"
        "@($errors).Count",
        shell=shell,
        dot_source=False,
    )
    assert parsed.stdout.strip() == "0", parsed.stdout + parsed.stderr


def test_dot_sourcing_loads_the_functions_and_installs_nothing(tmp_path: Path) -> None:
    done = run(
        "(Get-Command Install-Atlas).Name", environment={"ATLAS_INSTALL_DIR": str(tmp_path / "x")}
    )

    assert done.stdout.strip() == "Install-Atlas", done.stderr
    assert not (tmp_path / "x").exists()


@pytest.mark.parametrize("shell", SHELLS)
def test_a_failure_under_iex_keeps_the_users_window_and_downloads_nothing(
    tmp_path: Path, shell: str
) -> None:
    """Under `irm | iex` the text runs in the user's own session: `exit` would close their window,
    and the Restricted policy of a fresh machine refuses any script loaded from disk. A folder
    that is not a checkout is refused before any download."""
    root = tmp_path / "install"
    notes = _not_a_checkout(root, "the user's\n")

    done = run(
        f"iex ([IO.File]::ReadAllText({_quoted(SCRIPT)}))\n'still here'\n$LASTEXITCODE",
        shell=shell,
        policy="Restricted",
        environment={"ATLAS_INSTALL_DIR": str(root)},
        dot_source=False,
    )

    lines = done.stdout.strip().splitlines()
    assert lines[-2:] == ["still here", "1"], done.stdout + done.stderr
    assert "is not an Atlas checkout" in done.stdout
    assert not (root / "tools").exists()
    assert notes.read_text(encoding="utf-8") == "the user's\n"


def test_an_install_folder_too_long_for_windows_is_refused_before_any_download(
    tmp_path: Path,
) -> None:
    """PortableGit's self-extractor failed under a long folder, found by the first real install
    (13/10/2026): its deepest file is 86 characters under its own folder."""
    root = tmp_path / ("long-" * 30)

    done = run("Initialize-Paths; Test-InstallTarget", environment={"ATLAS_INSTALL_DIR": str(root)})

    assert "Choose a shorter one with ATLAS_INSTALL_DIR (at most 120)" in done.stderr
    assert not root.exists()


def test_run_as_a_file_a_failure_is_its_exit_code(tmp_path: Path) -> None:
    root = tmp_path / "install"
    _not_a_checkout(root, "x")

    done = subprocess.run(  # noqa: S603
        [  # noqa: S607 - the system's PowerShell
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "ATLAS_INSTALL_DIR": str(root)},
        check=False,
        timeout=180,
    )

    assert done.returncode == 1
    assert "is not an Atlas checkout" in done.stdout


def test_the_resolved_paths_are_said_and_nothing_is_touched(tmp_path: Path) -> None:
    done = subprocess.run(  # noqa: S603
        [  # noqa: S607 - the system's PowerShell
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-ShowResolvedPaths",
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "ATLAS_INSTALL_DIR": str(tmp_path / "a b")},
        check=False,
        timeout=60,
    )

    paths = json.loads(done.stdout)
    assert Path(paths["checkout"]) == tmp_path / "a b" / "atlas"
    assert paths["target"] in ("win32-x64", "win32-arm64")
    assert not (tmp_path / "a b").exists()


# -- paths -----------------------------------------------------------------------------------------


def test_an_8_3_path_is_expanded_to_its_long_form(tmp_path: Path) -> None:
    """A profile folder with a space or a dot gets an alias (FIRST~1.LAS) that PowerShell's file
    cmdlets fail to find."""
    long = tmp_path / "First Last.x"
    long.mkdir()
    shown = subprocess.run(  # noqa: S602 - cmd's own expansion of a path the test made
        f'for %I in ("{long}") do @echo %~sI',
        shell=True,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if "~" not in Path(shown).name:
        pytest.skip("this volume makes no 8.3 names")

    done = run(f"ConvertTo-LongPath {_quoted(shown)}")

    assert Path(done.stdout.strip()) == long, done.stderr


# -- the user's PATH -------------------------------------------------------------------------------

KEY = r"Software\AtlasInstallTest\Environment"


@pytest.fixture
def test_key() -> Iterator[str]:
    yield KEY
    run(
        "[Microsoft.Win32.Registry]::CurrentUser.DeleteSubKeyTree("
        "'Software\\AtlasInstallTest', $false)",
        dot_source=False,
    )


def test_the_folder_joins_the_path_once_and_the_value_keeps_its_registry_type(
    test_key: str,
) -> None:
    """Compared as a whole entry, never with -like, where a [ in a user name is a wildcard;
    written back as REG_EXPAND_SZ, so %USERPROFILE% in it stays a variable (.NET's
    SetEnvironmentVariable freezes it)."""
    folder = r"C:\Users\a[1]\atlas\bin"
    seeded = (
        f"$k = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey({_quoted(test_key)})\n"
        "$k.SetValue('Path', '%USERPROFILE%\\tools;C:\\Users\\a[1]\\atlas\\bin2', "
        "[Microsoft.Win32.RegistryValueKind]::ExpandString); $k.Close()\n"
    )
    done = run(
        seeded
        + f"Add-UserPath {_quoted(folder)} {_quoted(test_key)}\n"
        + f"Add-UserPath {_quoted(folder + chr(92))} {_quoted(test_key)}\n"
        + f"Add-UserPath (Join-Path $env:USERPROFILE 'tools') {_quoted(test_key)}\n"
        + f"$k = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey({_quoted(test_key)})\n"
        + "$k.GetValueKind('Path')\n"
        + "$k.GetValue('Path', '', "
        + "[Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)"
    )

    assert done.stdout.strip().splitlines() == [
        "True",
        "False",  # the same folder, with a trailing backslash
        "False",  # an entry already there through its variable
        "ExpandString",
        r"C:\Users\a[1]\atlas\bin;%USERPROFILE%\tools;C:\Users\a[1]\atlas\bin2",
    ], done.stderr


def test_the_installers_own_window_gets_the_folder_at_once() -> None:
    done = run(
        r"Add-SessionPath 'C:\atlas test\bin'; Add-SessionPath 'C:\atlas test\bin\'; "
        r"($env:Path -split ';')[0]"
    )

    assert done.stdout.strip().splitlines() == ["False", "True", r"C:\atlas test\bin"], done.stderr


# -- downloads -------------------------------------------------------------------------------------


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[str, str]]:
    """A file on a local web server: its address and its SHA-256."""
    body, host = b"pinned bytes", "127.0.0.1"
    folder = tmp_path / "served"
    folder.mkdir()
    (folder / "tool.zip").write_bytes(body)
    files = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(folder))
    with http.server.ThreadingHTTPServer((host, 0), files) as web:
        threading.Thread(target=web.serve_forever, daemon=True).start()
        port = web.server_port
        yield f"http://{host}:{port}/tool.zip", sha256(body).hexdigest()
        web.shutdown()


def test_a_download_is_kept_only_when_its_sha256_is_the_pinned_one(
    tmp_path: Path, served: tuple[str, str]
) -> None:
    url, digest = served
    good, bad = tmp_path / "good.zip", tmp_path / "bad.zip"

    ok = run(f"Invoke-VerifiedDownload {_quoted(url)} {_quoted(digest)} {_quoted(good)}; 'kept'")
    refused = run(f"Invoke-VerifiedDownload {_quoted(url)} {_quoted('0' * 64)} {_quoted(bad)}")

    assert ok.stdout.strip() == "kept", ok.stderr
    assert good.read_bytes() == b"pinned bytes"
    assert "is not the pinned file" in refused.stderr
    assert not bad.exists()


# -- the toolchain ---------------------------------------------------------------------------------


def test_the_toolchain_the_installer_writes_is_the_one_atlas_reads(tmp_path: Path) -> None:
    """The file is the contract between the two languages: written here, read by
    atlas.installation (and by the app, with the same rules)."""
    root = tmp_path / "install"
    names = {
        "git": "git-2.55.0.5-win32-x64",
        "uv": "uv-0.12.5-win32-x64",
        "node": "node-24.21.0-win32-x64",
    }
    listed = "; ".join(f"{tool} = {_quoted(name)}" for tool, name in names.items())
    done = run(
        f"$script:Tools = {_quoted(root / 'tools')}\n"
        + f"$installed = @{{ {listed} }}\n"
        + "foreach ($tool in $installed.Keys) {\n"
        + "  foreach ($p in (Get-Pin $tool 'win32-x64').Programs.Values) {\n"
        + "    $f = Join-Path (Join-Path $script:Tools $installed[$tool]) $p\n"
        + "    New-Item -ItemType Directory -Force -Path (Split-Path $f) | Out-Null\n"
        + "    New-Item -ItemType File -Path $f | Out-Null } }\n"
        + "Write-Toolchain $installed 'win32-x64'"
    )
    assert done.returncode == 0, done.stderr

    toolchain = Toolchain.of(Checkout(root / "atlas"))

    assert not (root / "tools" / "toolchain.json").read_bytes().startswith(b"\xef\xbb\xbf")
    assert sorted(toolchain.programs) == ["git", "node", "npm", "npx", "uv"]
    assert toolchain.programs["git"] == root / "tools" / names["git"] / "cmd" / "git.exe"
    assert (
        toolchain.programs["npm"]
        == root / "tools" / names["node"] / "node-v24.21.0-win-x64" / "npm.cmd"
    )


@needs_git
def test_the_installations_git_asks_no_window_which_credential_helper_to_use(
    tmp_path: Path,
) -> None:
    """PortableGit names the helper selector, a window that held a `git fetch` the agent ran
    until the user clicked (14/10/2026); Git Credential Manager takes its place, and the rest of
    the file stays. Run again, it also replaces the path the selector writes there when told to
    always use a helper."""
    folder = tmp_path / "tools" / "git-2.55.0.5-win32-x64"
    (folder / "etc").mkdir(parents=True)
    config = folder / "etc" / "gitconfig"
    config.write_text(
        "[core]\n\tfscache = true\n[credential]\n\thelper = helper-selector\n"
        '\thelper = !"C:/tools/git/mingw64/bin/git-credential-wincred.exe"\n',
        encoding="utf-8",
    )

    done = run(
        f"Set-GitCredentialHelper 'git' {_quoted(folder)}\n"
        f"Set-GitCredentialHelper 'git' {_quoted(folder)}"
    )

    assert done.returncode == 0, done.stderr
    assert _git(tmp_path, "config", "-f", str(config), "--get-all", "credential.helper") == (
        "manager"
    )
    assert _git(tmp_path, "config", "-f", str(config), "core.fscache") == "true"


# -- the repository --------------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    """What git prints in `cwd`, trimmed; a command that fails fails the test."""
    command = ["git", *args]
    completed = subprocess.run(command, check=True, cwd=cwd, text=True, capture_output=True)  # noqa: S603
    return completed.stdout.strip()


def _commit(folder: Path, name: str, message: str) -> None:
    (folder / name).write_text(message + "\n", encoding="utf-8")
    _git(folder, "add", name)
    _git(
        folder, "-c", "user.email=d@example.com", "-c", "user.name=D", "commit", "-q", "-m", message
    )


def _sync(root: Path, origin: Path) -> subprocess.CompletedProcess[str]:
    return run(
        f"$script:Root = {_quoted(root)}; $script:Checkout = Join-Path $script:Root 'atlas'\n"
        f"$script:RepoUrl = {_quoted(origin)}\n"
        "Sync-Repository 'git'"
    )


def _origin(tmp_path: Path) -> Path:
    """The repository the installer clones: `main`, with one commit."""
    origin = tmp_path / "origin"
    _git(tmp_path, "init", "-q", "-b", "main", str(origin))
    _commit(origin, "README.md", "First")
    return origin


@needs_git
def test_a_fresh_clone_is_published_whole_with_long_paths(tmp_path: Path) -> None:
    origin = _origin(tmp_path)
    root = tmp_path / "install"

    done = _sync(root, origin)

    checkout = root / "atlas"
    assert done.returncode == 0, done.stdout + done.stderr
    assert _git(checkout, "rev-parse", "HEAD") == _git(origin, "rev-parse", "HEAD")
    assert _git(checkout, "config", "core.longpaths") == "true"
    assert _git(checkout, "config", "core.autocrlf") == "false"
    assert sorted(p.name for p in root.iterdir()) == ["atlas"]  # no staging folder left


@needs_git
def test_run_again_it_updates_and_refuses_local_changes(tmp_path: Path) -> None:
    origin = _origin(tmp_path)
    root = tmp_path / "install"
    _sync(root, origin)
    _commit(origin, "NEWS.md", "Second")

    updated = _sync(root, origin)
    (root / "atlas" / "README.md").write_text("edited in the installation\n", encoding="utf-8")
    refused = _sync(root, origin)

    assert updated.returncode == 0, updated.stdout + updated.stderr
    assert (root / "atlas" / "NEWS.md").is_file()
    assert "has local changes" in refused.stderr


@needs_git
def test_a_checkout_that_cannot_fast_forward_keeps_its_commits_in_a_rescue_ref(
    tmp_path: Path,
) -> None:
    """A release cut off the main line or a force-pushed origin cannot fast-forward; every later
    step needs the new tree, so it is reset, its commits kept."""
    origin = _origin(tmp_path)
    root = tmp_path / "install"
    _sync(root, origin)
    _commit(root / "atlas", "LOCAL.md", "Only here")
    _commit(origin, "NEWS.md", "Second")
    local = _git(root / "atlas", "rev-parse", "HEAD")

    done = _sync(root, origin)

    assert done.returncode == 0, done.stdout + done.stderr
    assert _git(root / "atlas", "rev-parse", "HEAD") == _git(origin, "rev-parse", "HEAD")
    rescued = _git(
        root / "atlas", "for-each-ref", "--format=%(objectname)", "refs/atlas-install-backups"
    )
    assert rescued == local


@needs_git
def test_an_interrupted_clone_is_moved_aside_and_cloned_again(tmp_path: Path) -> None:
    """A .git with no commit, where every later git command aborts."""
    origin = _origin(tmp_path)
    root = tmp_path / "install"
    (root / "atlas").mkdir(parents=True)
    _git(root / "atlas", "init", "-q")

    done = _sync(root, origin)

    assert done.returncode == 0, done.stdout + done.stderr
    assert _git(root / "atlas", "rev-parse", "HEAD") == _git(origin, "rev-parse", "HEAD")
    assert [p.name.split("-")[0] for p in root.iterdir() if p.name != "atlas"] == ["atlas.broken"]
