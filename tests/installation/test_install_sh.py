"""`scripts/install.sh`, run by bash: the installer's rules, each one a defect it guards
against, the same as `scripts/install.ps1`'s where the step is the same.

The script is sourced for its functions, which then run for real on real folders, real git
repositories and a real download from a local server. The whole install, with its downloads and
the app's build, runs in CI's install job, under `curl | bash` as the user runs it.
"""

import functools
import http.server
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

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the Linux and macOS installer")

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "install.sh"
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _quoted(text: str | Path) -> str:
    return "'" + str(text).replace("'", "'\\''") + "'"


def run(
    code: str,
    *,
    home: Path | None = None,
    environment: dict[str, str] | None = None,
    source: bool = True,
) -> subprocess.CompletedProcess[str]:
    """`code` in a new bash, after sourcing the installer when asked."""
    prelude = f"source {_quoted(SCRIPT)}\n" if source else ""
    env = {**os.environ, **(environment or {})}
    if home is not None:
        env["HOME"] = str(home)
    return subprocess.run(  # noqa: S603 - bash, the test's own code
        ["bash", "-c", prelude + code],  # noqa: S607 - the system's bash, the test's own code
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=180,
    )


# -- the file --------------------------------------------------------------------------------------


def test_the_script_parses_and_sourcing_it_installs_nothing(tmp_path: Path) -> None:
    syntax = run(f"bash -n {_quoted(SCRIPT)} && echo ok", source=False)
    sourced = run(
        "type install_atlas >/dev/null && echo loaded",
        environment={"ATLAS_INSTALL_DIR": str(tmp_path / "x")},
    )

    assert syntax.stdout.strip() == "ok", syntax.stderr
    assert sourced.stdout.strip() == "loaded", sourced.stderr
    assert not (tmp_path / "x").exists()


def test_under_curl_pipe_bash_a_folder_not_a_checkout_is_refused_before_any_download(
    tmp_path: Path,
) -> None:
    """`curl | bash` gives the script on standard input, where BASH_SOURCE is empty and `set -u`
    stops on it bare; a folder that is not a checkout is the user's."""
    root = tmp_path / "install"
    notes = root / "atlas" / "notes.txt"
    notes.parent.mkdir(parents=True)
    notes.write_text("the user's\n", encoding="utf-8")

    done = subprocess.run(
        ["bash"],  # noqa: S607
        input=SCRIPT.read_text(encoding="utf-8"),
        capture_output=True,
        text=True,
        env={**os.environ, "ATLAS_INSTALL_DIR": str(root), "HOME": str(tmp_path)},
        check=False,
        timeout=180,
    )

    assert done.returncode == 1
    assert "is not an Atlas checkout" in done.stderr
    assert not (root / "tools").exists()
    assert notes.read_text(encoding="utf-8") == "the user's\n"


def test_each_step_says_what_it_does() -> None:
    """A printf format starting with `-` is an option: the first real install printed an error
    for every step."""
    done = run("step 'Cloning'; ok 'Cloned'")

    assert done.stdout.splitlines() == ["-> Cloning", "[OK] Cloned"]
    assert done.stderr == ""


def test_the_machine_is_named_as_the_pins_name_it() -> None:
    done = run("target")

    system = {"linux": "linux", "darwin": "darwin"}[sys.platform]
    assert done.stdout.strip().startswith(f"{system}-"), done.stderr
    assert done.stdout.strip().split("-")[1] in ("x64", "arm64")


# -- downloads -------------------------------------------------------------------------------------


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[str, str]]:
    """A file on a local web server: its address and its SHA-256."""
    body, host = b"pinned bytes", "127.0.0.1"
    folder = tmp_path / "served"
    folder.mkdir()
    (folder / "tool.tar.gz").write_bytes(body)
    files = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(folder))
    with http.server.ThreadingHTTPServer((host, 0), files) as web:
        threading.Thread(target=web.serve_forever, daemon=True).start()
        port = web.server_port
        yield f"http://{host}:{port}/tool.tar.gz", sha256(body).hexdigest()
        web.shutdown()


def test_a_download_is_kept_only_when_its_sha256_is_the_pinned_one(
    tmp_path: Path, served: tuple[str, str]
) -> None:
    url, digest = served
    good, bad = tmp_path / "good", tmp_path / "bad"

    ok = run(f"download_verified {_quoted(url)} {digest} {_quoted(good)} && echo kept")
    refused = run(f"download_verified {_quoted(url)} {'0' * 64} {_quoted(bad)}")

    assert ok.stdout.strip() == "kept", ok.stderr
    assert good.read_bytes() == b"pinned bytes"
    assert refused.returncode == 1
    assert "is not the pinned file" in refused.stderr
    assert not bad.exists()


# -- the toolchain ---------------------------------------------------------------------------------


def test_the_toolchain_the_installer_writes_is_the_one_atlas_reads(tmp_path: Path) -> None:
    root = tmp_path / "install"
    done = run(
        f"ATLAS_INSTALL_DIR={_quoted(root)}; init_paths; TARGET=linux-x64\n"
        "for tool in uv node; do pin $tool $TARGET\n"
        "  for pair in $PIN_PROGRAMS; do\n"
        '    f="$TOOLS/$tool-$PIN_VERSION-$TARGET/${pair#*=}"\n'
        '    mkdir -p "$(dirname "$f")"; : > "$f"; done; done\n'
        "write_toolchain uv node"
    )
    assert done.returncode == 0, done.stderr

    toolchain = Toolchain.of(Checkout(root / "atlas"))

    assert sorted(toolchain.programs) == ["node", "npm", "npx", "uv"]
    assert toolchain.programs["uv"] == (
        root / "tools" / "uv-0.12.5-linux-x64" / "uv-x86_64-unknown-linux-gnu" / "uv"
    )


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
        f"ATLAS_INSTALL_DIR={_quoted(root)}; init_paths; REPO_URL={_quoted(origin)}\n"
        "sync_repository"
    )


def _origin(tmp_path: Path) -> Path:
    """The repository the installer clones: `main`, with one commit."""
    origin = tmp_path / "origin"
    _git(tmp_path, "init", "-q", "-b", "main", str(origin))
    _commit(origin, "README.md", "First")
    return origin


@needs_git
def test_a_fresh_clone_is_published_whole(tmp_path: Path) -> None:
    origin = _origin(tmp_path)
    root = tmp_path / "install"

    done = _sync(root, origin)

    assert done.returncode == 0, done.stdout + done.stderr
    assert _git(root / "atlas", "rev-parse", "HEAD") == _git(origin, "rev-parse", "HEAD")
    assert sorted(p.name for p in root.iterdir()) == ["atlas"]


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
    origin = _origin(tmp_path)
    root = tmp_path / "install"
    (root / "atlas").mkdir(parents=True)
    _git(root / "atlas", "init", "-q")

    done = _sync(root, origin)

    assert done.returncode == 0, done.stdout + done.stderr
    assert _git(root / "atlas", "rev-parse", "HEAD") == _git(origin, "rev-parse", "HEAD")
    assert [p.name.split("-")[0] for p in root.iterdir() if p.name != "atlas"] == ["atlas.broken"]


# -- the command and the PATH ----------------------------------------------------------------------


def test_the_command_replaces_an_old_link_instead_of_writing_through_it(tmp_path: Path) -> None:
    """`cat >` over an old symlink wrote into whatever it pointed at."""
    root = tmp_path / "install"
    environment_atlas = root / "atlas" / ".venv" / "bin" / "atlas"
    environment_atlas.parent.mkdir(parents=True)
    environment_atlas.write_text("#!/bin/sh\n", encoding="utf-8")
    environment_atlas.chmod(0o755)
    victim = tmp_path / "victim"
    victim.write_text("keep me\n", encoding="utf-8")
    command = tmp_path / ".local" / "bin" / "atlas"
    command.parent.mkdir(parents=True)
    command.symlink_to(victim)

    done = run(f"ATLAS_INSTALL_DIR={_quoted(root)}; init_paths; publish_command", home=tmp_path)

    assert done.returncode == 0, done.stderr
    assert victim.read_text(encoding="utf-8") == "keep me\n"
    assert not command.is_symlink()
    assert str(environment_atlas) in command.read_text(encoding="utf-8")


def test_bash_gets_the_path_line_once_and_never_a_new_bash_profile(tmp_path: Path) -> None:
    """A guarded line, once per file however many runs; .bash_login, read by a
    login bash in place of .profile, gets it too; no .bash_profile is made."""
    (tmp_path / ".bash_login").write_text("umask 022\n", encoding="utf-8")
    environment = {"SHELL": "/bin/bash"}

    run("wire_shell_path; wire_shell_path", home=tmp_path, environment=environment)

    for name in (".bashrc", ".profile", ".bash_login"):
        assert (tmp_path / name).read_text(encoding="utf-8").count("$HOME/.local/bin:$PATH") == 1
    assert not (tmp_path / ".bash_profile").exists()
    login = run("bash -lc 'echo \"$PATH\"'", home=tmp_path, environment=environment, source=False)
    assert login.stdout.strip().split(":").count(str(tmp_path / ".local" / "bin")) == 1


def test_a_shell_file_that_already_sets_the_path_is_left_alone(tmp_path: Path) -> None:
    """Fedora's .bashrc and Debian's .profile set it with a bare PATH= line."""
    bashrc = tmp_path / ".bashrc"
    bashrc.write_text('PATH="$HOME/.local/bin:$HOME/bin:$PATH"\n', encoding="utf-8")

    run("wire_shell_path", home=tmp_path, environment={"SHELL": "/bin/bash"})

    assert bashrc.read_text(encoding="utf-8") == 'PATH="$HOME/.local/bin:$HOME/bin:$PATH"\n'


def test_zsh_gets_its_own_files(tmp_path: Path) -> None:
    run("wire_shell_path", home=tmp_path, environment={"SHELL": "/usr/bin/zsh"})

    assert (tmp_path / ".zshrc").is_file()
    assert (tmp_path / ".zprofile").is_file()
    assert not (tmp_path / ".bashrc").exists()
