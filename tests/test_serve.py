"""The real path: `atlas secrets set` and `atlas serve`, each in its own process, on a fresh home.

Nothing is stubbed: the commands run as a user runs them, and the test reads what they left.
"""

import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect

from atlas.home import parse_utc, unclassified
from atlas.server import PROTOCOL, READY_LINE, TOKEN_PREFIX

SECRET_VALUE = "sk-proj-end-to-end-0123456789"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def _stop(server: subprocess.Popen[str]) -> None:
    """Stop a server and everything under it. On Windows the environment's python.exe is a
    launcher that starts the real interpreter as its child: terminating the launcher left the
    child holding the home's server.lock while the test read the home (CI, 14/10/2026)."""
    if sys.platform == "win32":
        subprocess.run(  # noqa: S603 - Windows' own taskkill on the test's process
            ["taskkill", "/T", "/F", "/PID", str(server.pid)],  # noqa: S607
            capture_output=True,
            check=False,
        )
    else:
        server.terminate()
    server.wait(timeout=30)


def _read_once_released(database: Path, query: str) -> tuple[str, ...]:
    """A row of the home's database once the stopped server let go of it. `taskkill /F` ends the
    server wherever it was, and on Windows the interpreter under the launcher can still hold the
    WAL's files a moment after `_stop` returns: reading then is a "disk I/O error" (CI,
    01/11/2026)."""
    deadline = time.monotonic() + 15
    while True:
        connection = sqlite3.connect(database)
        try:
            row = connection.execute(query).fetchone()
        except sqlite3.OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)
            continue
        finally:
            connection.close()
        assert row is not None
        return tuple(str(value) for value in row)


def _health(port: int, deadline: float) -> dict[str, str]:
    while True:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                return json.loads(response.read())
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.1)


def test_a_fresh_home_is_built_served_and_left_as_declared(tmp_path: Path) -> None:
    home = tmp_path / "home"
    port = _free_port()
    environ = {**os.environ, "ATLAS_HOME": str(home), "ATLAS_SERVER_PORT": str(port)}
    atlas = [sys.executable, "-m", "atlas"]

    subprocess.run(  # noqa: S603
        [*atlas, "secrets", "set", "OPENAI_API_KEY"],
        input=f"{SECRET_VALUE}\n",
        env=environ,
        text=True,
        capture_output=True,
        check=True,
    )
    log = tmp_path / "stderr.log"
    with log.open("w", encoding="utf-8") as sink:
        server = subprocess.Popen(  # noqa: S603
            [*atlas, "serve"], env=environ, stdout=subprocess.DEVNULL, stderr=sink, text=True
        )
    try:
        health = _health(port, time.monotonic() + 30)
    finally:
        _stop(server)
    stderr = log.read_text(encoding="utf-8")

    assert health["status"] == "ok"

    # Every line on stderr is JSON, uvicorn's included, in UTC.
    lines = [json.loads(line) for line in stderr.splitlines() if line.strip()]
    loggers = {line["logger"] for line in lines}
    assert {"atlas.cli", "uvicorn.error", "uvicorn.access"} <= loggers
    for line in lines:
        parse_utc(line["ts"])
    assert not any(line["level"] == "ERROR" for line in lines), stderr

    # The log file holds the same JSON lines.
    log_lines = (home / "logs" / "atlas.log").read_text(encoding="utf-8").splitlines()
    assert any(json.loads(line)["msg"] == "Serving." for line in log_lines)

    # The database is in WAL mode and has its ledger.
    assert _read_once_released(home / "atlas.db", "PRAGMA journal_mode") == ("wal",)
    assert _read_once_released(
        home / "atlas.db", "SELECT name FROM sqlite_master WHERE name = 'home_migrations'"
    ) == ("home_migrations",)

    # The skills Atlas ships were copied into the library when it started.
    assert (home / "skills" / "gws-gmail" / "SKILL.md").is_file()
    assert (home / "skills" / ".bundled_manifest").is_file()

    # Everything at the top of the home is declared in its boundary.
    assert unclassified({entry.name: entry.is_dir() for entry in home.iterdir()}) == []

    # The secret is nowhere in plain text: not in the vault, not in a log, not on stderr.
    assert SECRET_VALUE not in stderr
    for path in home.rglob("*"):
        if path.is_file():
            assert SECRET_VALUE.encode() not in path.read_bytes(), path


def test_on_port_zero_the_server_says_its_port_and_leaves_with_the_app(tmp_path: Path) -> None:
    """As the desktop starts it: port 0, the ready line on standard output, and the app's PID."""
    app = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    environ = {**os.environ, "ATLAS_HOME": str(tmp_path / "home")}
    log = tmp_path / "stderr.log"
    with log.open("w", encoding="utf-8") as sink:
        server = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "atlas", "serve", "--port", "0", "--parent-pid", str(app.pid)],
            env=environ,
            stdout=subprocess.PIPE,
            stderr=sink,  # a pipe nobody reads fills up (4 KB on Windows) and stops the server
            text=True,
        )
    try:
        assert server.stdout is not None
        line = server.stdout.readline()
        assert line.startswith(READY_LINE), line
        port = int(line.removeprefix(READY_LINE))
        assert port > 0
        assert _health(port, time.monotonic() + 30)["status"] == "ok"

        app.kill()
        app.wait(timeout=30)
        # The watchdog looks every 2 seconds, and the stop is graceful.
        code = server.wait(timeout=30)
    finally:
        app.kill()
        if server.poll() is None:
            server.kill()
        server.communicate(timeout=30)
    stderr = log.read_text(encoding="utf-8")

    assert code == 0
    assert "The app that started the server is gone" in stderr
    lines = [json.loads(line) for line in stderr.splitlines() if line.strip()]
    assert not any(line["level"] == "ERROR" for line in lines), stderr


def test_a_client_with_the_token_it_was_started_with_talks_to_atlas_serve(tmp_path: Path) -> None:
    home = tmp_path / "home"
    port = _free_port()
    token = "a-token-given-by-the-desktop-0123"
    environ = {
        **os.environ,
        "ATLAS_HOME": str(home),
        "ATLAS_SERVER_PORT": str(port),
        "ATLAS_SERVER_TOKEN": token,
    }
    # Its log goes to a file: a pipe nobody reads fills up (4 KB on Windows) and stops the server.
    log = tmp_path / "stderr.log"
    with log.open("w", encoding="utf-8") as sink:
        server = subprocess.Popen(
            [sys.executable, "-m", "atlas", "serve"],
            env=environ,
            stdout=subprocess.DEVNULL,
            stderr=sink,
            text=True,
        )
    try:
        _health(port, time.monotonic() + 30)
        url = f"ws://127.0.0.1:{port}/ws"
        with connect(url, subprotocols=[PROTOCOL, TOKEN_PREFIX + token]) as socket:  # pyright: ignore[reportArgumentType]
            ready = json.loads(socket.recv(timeout=10))
            socket.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "conversation.create"}))
            created = json.loads(socket.recv(timeout=10))
        with pytest.raises(InvalidStatus), connect(url, subprotocols=[PROTOCOL]):  # pyright: ignore[reportArgumentType]
            pass
    finally:
        _stop(server)
    stderr = log.read_text(encoding="utf-8")

    assert ready["params"]["type"] == "server.ready"
    assert created["result"]["conversation_id"]
    assert token not in stderr
    assert "A client was refused" in stderr
    for path in home.rglob("*"):
        if path.is_file():
            assert token.encode() not in path.read_bytes(), path


def _start(environ: dict[str, str], *argv: str) -> tuple[subprocess.Popen[str], int]:
    """`atlas [argv] serve --port 0`, once it says its port."""
    server = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "atlas", *argv, "serve", "--port", "0"],
        env=environ,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,  # a pipe nobody reads fills up and stops the server
        text=True,
    )
    assert server.stdout is not None
    line = server.stdout.readline()
    assert line.startswith(READY_LINE), server.communicate(timeout=30)
    return server, int(line.removeprefix(READY_LINE))


def test_one_server_per_home_and_a_named_home_serves_its_list(tmp_path: Path) -> None:
    """Two servers never share one home; `--home` serves another, which lists them all."""
    token = "a-token-for-the-homes-test-0123"
    environ = {**os.environ, "ATLAS_HOME": str(tmp_path / "atlas"), "ATLAS_SERVER_TOKEN": token}
    first, _ = _start(environ)
    try:
        second = subprocess.run(
            [sys.executable, "-m", "atlas", "serve", "--port", "0"],
            env=environ,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert second.returncode == 1
        assert "Another Atlas server is serving" in second.stderr

        subprocess.run(
            [sys.executable, "-m", "atlas", "home", "create", "work"],
            env=environ,
            capture_output=True,
            check=True,
        )
        work, port = _start(environ, "--home", "work")
        try:
            url = f"ws://127.0.0.1:{port}/ws"
            with connect(url, subprotocols=[PROTOCOL, TOKEN_PREFIX + token]) as socket:  # pyright: ignore[reportArgumentType]
                socket.recv(timeout=10)
                socket.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "home.list"}))
                listed = json.loads(socket.recv(timeout=10))["result"]
        finally:
            work.terminate()
            work.communicate(timeout=30)
    finally:
        first.terminate()
        first.communicate(timeout=30)

    assert listed == {
        "current": "work",
        "homes": [{"name": "default", "active": True}, {"name": "work", "active": False}],
    }
    assert (tmp_path / "atlas" / "homes" / "work" / "atlas.db").is_file()


def test_a_section_an_older_atlas_kept_is_read_past_and_the_server_moves_it(
    tmp_path: Path,
) -> None:
    """Found on 15/10/2026: a tester's `atlas update` failed on the `[executors]` step 16c moved
    to the claude-code plugin, since every command, the update's build among them, refused it; and
    the update went back to the version before. Now commands read past it, and the server moves
    it, the file kept first, as a config is migrated after an update."""
    home = tmp_path / "atlas"
    home.mkdir()
    config = home / "config.toml"
    config.write_text(
        "# set up by hand\n"
        '[executors]\nclaude_code = ["C:/tools/claude.exe", "--verbose"]\n\n'
        '[log]\nlevel = "INFO"\n',
        encoding="utf-8",
    )
    environ = {**os.environ, "ATLAS_HOME": str(home), "ATLAS_SERVER_TOKEN": "a-token-0123456789"}

    # What the update's build runs first: the settings, read by every command.
    listed = subprocess.run(
        [sys.executable, "-m", "atlas", "plugins", "list"],
        env=environ,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert listed.returncode == 0, listed.stderr
    assert "[executors]" in config.read_text(encoding="utf-8")

    server, _ = _start(environ)
    _stop(server)

    assert config.read_text(encoding="utf-8") == '# set up by hand\n[log]\nlevel = "INFO"\n'
    [kept] = list((home / "backups").glob("config-*.toml"))
    assert "claude_code" in kept.read_text(encoding="utf-8")
    (program,) = _read_once_released(
        home / "atlas.db",
        "SELECT value FROM plugins_settings WHERE plugin = 'claude-code' AND name = 'program'",
    )
    assert json.loads(program) == ["C:/tools/claude.exe", "--verbose"]
