"""A home always on: the service of this system, written, started and removed for real, and what
turning on, opening and the code do around it."""

import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest
from websockets.sync.client import connect

from atlas.__main__ import main
from atlas.home import Home, create_home, is_served, open_home, serving
from atlas.instances import (
    AlwaysOn,
    AlwaysOnHome,
    AlwaysOnServices,
    InstancesError,
    ServiceCommand,
    Tailnet,
    answers,
    decode_code,
    read_always_on,
    service_manager,
    tailnet,
)
from atlas.server import PROTOCOL, TOKEN_PREFIX

SHOP_TAILNET = Tailnet("100.101.102.103", "shop.example-tailnet.ts.net")


class Recording:
    """A service manager that only says what it was asked: the logic around the real one."""

    def __init__(self, fail_start: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.written: set[str] = set()
        self.fail_start = fail_start

    def install(self, label: str, command: ServiceCommand, description: str) -> None:
        del command, description
        self.calls.append(("install", label))
        self.written.add(label)

    def start(self, label: str) -> None:
        self.calls.append(("start", label))
        if self.fail_start:
            raise InstancesError("the system refused")

    def stop(self, label: str) -> None:
        self.calls.append(("stop", label))

    def uninstall(self, label: str) -> None:
        self.calls.append(("uninstall", label))
        self.written.discard(label)

    def installed(self, label: str) -> bool:
        return label in self.written


def _home(tmp_path: Path) -> Home:
    return open_home(tmp_path / "atlas")


def _no_tailnet() -> Tailnet:
    raise InstancesError("Tailscale is not installed on this machine.")


class FailingFor(Recording):
    """Refuses to start one service, as a system may."""

    def __init__(self) -> None:
        super().__init__()
        self.refused = ""

    def start(self, label: str) -> None:
        self.calls.append(("start", label))
        if label == self.refused:
            raise InstancesError("the system refused")


def test_an_update_stops_the_always_on_homes_and_starts_them_again(tmp_path: Path) -> None:
    """Closing the app leaves an always-on home served: the update would wait for it forever, and
    on Windows `uv sync` cannot replace what its Python loaded. Only homes always on, with their
    service written, are stopped; each one waits until its server let the home go."""
    root = tmp_path / "atlas"
    default = open_home(root)
    shop = Home(create_home(root, "shop"))
    create_home(root, "plain")
    stale = Home(create_home(root, "stale"))  # its file, but no service any more
    manager = FailingFor()
    for home in (default, shop):
        AlwaysOnHome(home, manager=manager).turn_on()
    AlwaysOnHome(stale, manager=Recording()).turn_on()
    manager.refused = AlwaysOnHome(default, manager=manager).label
    manager.calls.clear()
    held, let_go = threading.Event(), threading.Event()

    def serve_the_shop() -> None:
        with serving(shop):
            held.set()
            let_go.wait(5)

    threading.Thread(target=serve_the_shop, daemon=True).start()
    held.wait(5)
    threading.Timer(0.5, let_go.set).start()
    services = AlwaysOnServices(root, manager=manager)

    began = time.monotonic()
    stopped = services.stop()

    assert stopped == ["default", "shop"]
    assert time.monotonic() - began >= 0.4, "it waited for the shop's server to let it go"
    assert not is_served(shop.root)
    labels = [AlwaysOnHome(home, manager=manager).label for home in (default, shop)]
    assert manager.calls == [("stop", label) for label in labels]
    manager.calls.clear()

    # The default one refuses to start; the shop starts anyway, and nothing is started twice.
    services.start()
    services.start()
    assert manager.calls == [("start", label) for label in labels]


def test_turning_on_writes_the_port_once_and_starts_the_service(tmp_path: Path) -> None:
    home, manager = _home(tmp_path), Recording()
    service = AlwaysOnHome(home, manager=manager)
    first = service.turn_on()
    assert read_always_on(home) == first
    assert manager.calls == [("install", service.label), ("start", service.label)]
    assert service.turn_on().port == first.port
    assert service.label.startswith("atlas-default-")


def test_a_service_the_system_refuses_leaves_nothing_behind(tmp_path: Path) -> None:
    home, manager = _home(tmp_path), Recording(fail_start=True)
    with pytest.raises(InstancesError, match="refused"):
        AlwaysOnHome(home, manager=manager).turn_on()
    assert read_always_on(home) is None
    assert not manager.written


def test_turning_off_removes_the_service_and_the_file(tmp_path: Path) -> None:
    home, manager = _home(tmp_path), Recording()
    service = AlwaysOnHome(home, manager=manager)
    service.turn_on()
    service.turn_off()
    assert read_always_on(home) is None
    assert not manager.written


def test_opening_needs_the_home_on_and_tailscale_up(tmp_path: Path) -> None:
    home, manager = _home(tmp_path), Recording()
    with pytest.raises(InstancesError, match="not always on"):
        AlwaysOnHome(home, manager=manager, find_tailnet=lambda: SHOP_TAILNET).set_open(True)
    service = AlwaysOnHome(home, manager=manager, find_tailnet=_no_tailnet)
    port = service.turn_on().port
    with pytest.raises(InstancesError, match="not installed"):
        service.set_open(True)
    assert read_always_on(home) == AlwaysOn(port, open=False)


def test_opening_restarts_the_service_to_listen_on_the_tailnet(tmp_path: Path) -> None:
    home, manager = _home(tmp_path), Recording()
    service = AlwaysOnHome(home, manager=manager, find_tailnet=lambda: SHOP_TAILNET)
    port = service.turn_on().port
    manager.calls.clear()
    assert service.set_open(True) == AlwaysOn(port, open=True)
    assert manager.calls == [("stop", service.label), ("start", service.label)]
    status = service.status()
    assert (status.on, status.open, status.port, status.tailnet) == (True, True, port, SHOP_TAILNET)
    assert service.set_open(False) == AlwaysOn(port, open=False)


def test_the_code_names_the_home_its_port_and_the_tailnet(tmp_path: Path) -> None:
    home, manager = _home(tmp_path), Recording()
    service = AlwaysOnHome(home, manager=manager, find_tailnet=lambda: SHOP_TAILNET)
    token = "a-token-of-at-least-sixteen"
    assert service.code(token, 18790).port == 18790
    assert service.code(token, 18790).tailnet is None
    port = service.turn_on().port
    service.set_open(True)
    code = service.code(token, 18790)
    assert (code.home, code.port, code.token, code.tailnet) == (
        "default",
        port,
        token,
        "shop.example-tailnet.ts.net",
    )


def test_status_of_a_home_never_turned_on(tmp_path: Path) -> None:
    status = AlwaysOnHome(_home(tmp_path), manager=Recording()).status()
    assert (status.on, status.open, status.port, status.served) == (False, False, None, False)


def _run(argv: list[str], home: Path, **environ: str) -> str:
    out = io.StringIO()
    with redirect_stdout(out):
        assert main(argv, environ={"ATLAS_HOME": str(home), **environ}) == 0
    return out.getvalue()


def test_a_new_token_replaces_the_one_every_code_carried(tmp_path: Path) -> None:
    home = _home(tmp_path)
    first = _run(["server", "token"], home.root).strip()
    assert _run(["server", "token"], home.root).strip() == first
    second = _run(["server", "token", "--new"], home.root).strip()
    assert second != first
    code = decode_code(_run(["server", "code"], home.root))
    assert (code.token, code.port, code.home, code.tailnet) == (second, 18790, "default", None)


def test_a_code_through_the_relay_keeps_its_room_and_key(tmp_path: Path) -> None:
    """Step 20: the home's room and key are made once, so every code given stays valid."""
    home = _home(tmp_path)
    url = "wss://relay.example.com/relay"
    first = decode_code(_run(["server", "code", "--relay"], home.root, ATLAS_RELAY_URL=url))
    again = decode_code(_run(["server", "code", "--relay"], home.root, ATLAS_RELAY_URL=url))
    plain = decode_code(_run(["server", "code"], home.root, ATLAS_RELAY_URL=url))

    assert first.relay is not None
    assert first.relay.url == url
    assert again.relay == first.relay
    assert plain.relay is None
    with redirect_stdout(io.StringIO()):
        assert main(["server", "code", "--relay"], environ={"ATLAS_HOME": str(home.root)}) == 1


def _systemd_user_runs() -> bool:
    if shutil.which("systemctl") is None:
        return False
    state = subprocess.run(
        ["systemctl", "--user", "is-system-running"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return state in ("running", "degraded")


@pytest.mark.skipif(
    sys.platform == "darwin" or (sys.platform != "win32" and not _systemd_user_runs()),
    reason="the real service runs on Windows' Task Scheduler and a user's systemd",
)
def test_the_real_service_serves_the_home_with_the_vaults_token_and_goes(tmp_path: Path) -> None:
    """This system's service, for real: turned on, it starts `atlas serve --always-on` on its own,
    which serves the home on its kept port with the token `atlas server token` gives; turned off,
    the server is gone and so is the service."""
    home = _home(tmp_path)
    service = AlwaysOnHome(home)
    manager = service_manager()
    try:
        state = service.turn_on()
        assert manager.installed(service.label)
        assert service.wait_ready(90), "the service never served the home"
        with urllib.request.urlopen(
            f"http://127.0.0.1:{state.port}/health", timeout=10
        ) as response:
            assert json.loads(response.read())["status"] == "ok"
        token = _run(["server", "token"], home.root).strip()
        with connect(
            f"ws://127.0.0.1:{state.port}/ws",
            subprotocols=[PROTOCOL, TOKEN_PREFIX + token],  # pyright: ignore[reportArgumentType]
            open_timeout=10,
        ) as socket:
            assert socket.subprotocol == PROTOCOL
        # Stopped (the app's `ensure` finds it so after a crash): started again, and it answers.
        manager.stop(service.label)
        deadline = time.monotonic() + 30
        while answers(state.port) and time.monotonic() < deadline:
            time.sleep(0.25)
        assert not answers(state.port)
        assert _run(["always-on", "start"], home.root) == ""
        assert answers(state.port)
        told = json.loads(_run(["always-on", "status", "--json"], home.root))
        assert {key: told[key] for key in ("on", "open", "port", "installed", "served")} == {
            "on": True,
            "open": False,
            "port": state.port,
            "installed": True,
            "served": True,
        }
    finally:
        service.turn_off()
    deadline = time.monotonic() + 30
    while is_served(home.root) and time.monotonic() < deadline:
        time.sleep(0.25)
    assert not is_served(home.root)
    assert not manager.installed(service.label)
    assert read_always_on(home) is None


def test_an_open_home_with_no_tailnet_still_serves_this_machine(tmp_path: Path) -> None:
    """Tailscale missing, stopped or signed out (Windows starts it after the logon too): the
    always-on server serves on loopback anyway, says why the tailnet waits, and keeps trying."""
    home = _home(tmp_path)
    port = AlwaysOnHome(home, manager=Recording()).turn_on().port
    home.always_on_file.write_text(json.dumps({"port": port, "open": True}), encoding="utf-8")
    # A machine signed in to a tailnet listens there and never says it waits. Outside the PATH,
    # Tailscale is found where its installer puts it, and Windows sets ProgramFiles for every
    # process, so a test cannot hide it (seen on 22/10/2026, on a developer machine signed in).
    try:
        tailnet()
    except InstancesError:
        pass
    else:
        pytest.skip("this machine is on a tailnet; the test needs one that is not")
    environment = {**os.environ, "PATH": str(Path(sys.executable).parent)}
    # A file, not a pipe: the migrations' lines fill a pipe nobody reads, and the server blocks.
    log = tmp_path / "server.log"
    with log.open("w", encoding="utf-8") as errors:
        server = subprocess.Popen(  # noqa: S603 - this interpreter, running Atlas
            [sys.executable, "-m", "atlas", "serve", "--always-on", str(home.root)],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=errors,
            text=True,
        )
        try:
            deadline = time.monotonic() + 60
            while not answers(port):
                assert server.poll() is None, log.read_text(encoding="utf-8")
                assert time.monotonic() < deadline, "the always-on server never answered"
                time.sleep(0.2)
            deadline = time.monotonic() + 30
            while "Not listening on the tailnet yet" not in log.read_text(encoding="utf-8"):
                assert time.monotonic() < deadline, "the server never said why the tailnet waits"
                time.sleep(0.2)
        finally:
            _stop(server)


def _stop(server: "subprocess.Popen[str]") -> None:
    """Stop a server and the interpreter a virtual environment's launcher started under it."""
    if sys.platform == "win32":
        subprocess.run(  # noqa: S603 - Windows' own taskkill on the test's process
            ["taskkill", "/T", "/F", "/PID", str(server.pid)],  # noqa: S607
            capture_output=True,
            check=False,
        )
    else:
        server.terminate()
    server.wait(timeout=30)


def test_status_as_json_of_a_home_never_on(tmp_path: Path) -> None:
    told = json.loads(_run(["always-on", "status", "--json"], _home(tmp_path).root))
    assert (told["home"], told["on"], told["open"], told["port"]) == ("default", False, False, None)


def test_starting_a_home_never_on_says_so(tmp_path: Path) -> None:
    home = _home(tmp_path)
    errors = io.StringIO()
    with redirect_stderr(errors):
        assert main(["always-on", "start"], environ={"ATLAS_HOME": str(home.root)}) == 1
    assert "not always on" in errors.getvalue()
