"""The plugins' watch (step 16n): a real server loads a plugin again when its files are saved, when
it is turned on or off from the terminal, and on `plugin.reload`."""

import json
import os
import time
from collections.abc import Callable
from pathlib import Path

from atlas.home import Database, Home
from atlas.plugins import Plugins
from atlas.secrets import Vault
from server_support import Serve

SCREEN = "export default { id: 'video', register(ctx) {} } // one"

CALL = """
def register(ctx):
    async def which(params):
        return {said!r}

    ctx.register_call("which", which)
"""


def _wait(check: Callable[[], bool], seconds: float = 20) -> None:
    deadline = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < deadline, "not in time"
        time.sleep(0.05)


def _save(file: Path, text: str) -> None:
    """Write, a moment later than before as the file system sees it."""
    before = file.stat().st_mtime_ns if file.exists() else 0
    file.write_bytes(text.encode())
    os.utime(file, ns=(before + 2_000_000_000, before + 2_000_000_000))


def _on(plugins: Plugins, name: str) -> None:
    manifest = plugins.view(name).manifest
    assert manifest is not None
    plugins.enable(name, manifest.fingerprint())


def test_a_saved_plugin_is_loaded_again_by_the_server_serving_it(
    serve: Serve, home: Home, vault: Vault
) -> None:
    folder = home.plugins_folder / "video"
    (folder / ".claude-plugin").mkdir(parents=True)
    manifest = {"name": "video", "atlas": {"api": 17, "calls": ["which"]}}
    (folder / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), "utf-8")
    _save(folder / "__init__.py", CALL.format(said="one"))
    (folder / "desktop").mkdir()
    _save(folder / "desktop" / "plugin.js", SCREEN)
    server = serve(plugin_watch_seconds=0.05)
    plugins = Plugins(home, Database(home.database_file), vault)
    _on(plugins, "video")
    call = {"plugin": "video", "name": "which", "params": None}
    with server.client() as client:
        # Turned on from the terminal: the server sees it, and loads it.
        _wait(lambda: "result" in client.call("plugin.call", call))
        assert client.result("plugin.call", call) == {"result": "one"}

        _save(folder / "__init__.py", CALL.format(said="two"))
        _save(folder / "desktop" / "plugin.js", SCREEN.replace("one", "two"))
        client.event("plugins.changed")
        _wait(lambda: client.call("plugin.call", call).get("result") == {"result": "two"})
        [screen] = client.result("plugin.screens")["screens"]
        assert screen["source"] == SCREEN.replace("one", "two"), "the app loads it again"

        # A save that breaks it marks it; the next that fixes it runs again.
        _save(folder / "__init__.py", "def register(ctx):\n    raise RuntimeError('half saved')\n")
        _wait(lambda: "error" in client.call("plugin.call", call))
        [row] = [p for p in client.result("plugin.list")["plugins"] if p["name"] == "video"]
        assert "half saved" in row["problem"]
        _save(folder / "__init__.py", CALL.format(said="three"))
        _wait(lambda: client.call("plugin.call", call).get("result") == {"result": "three"})

        # Turned off from the terminal: its call goes.
        plugins.disable("video")
        _wait(lambda: "error" in client.call("plugin.call", call))
        _on(plugins, "video")
        _wait(lambda: "result" in client.call("plugin.call", call))

        listed = client.result("plugin.reload")["plugins"]
        assert [p["name"] for p in listed if p["name"] == "video"] == ["video"]
        assert client.result("plugin.call", call) == {"result": "three"}


def test_a_call_made_while_the_plugins_load_waits_for_them(
    serve: Serve, home: Home, vault: Vault
) -> None:
    folder = home.plugins_folder / "slow"
    (folder / ".claude-plugin").mkdir(parents=True)
    manifest = {"name": "slow", "atlas": {"api": 17, "calls": ["which"]}}
    (folder / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), "utf-8")
    slow = "import time\n\ntime.sleep(1)\n" + CALL.format(said="loaded")
    (folder / "__init__.py").write_text(slow, "utf-8")
    _on(Plugins(home, Database(home.database_file), vault), "slow")

    with serve().client() as client:
        said = client.call("plugin.call", {"plugin": "slow", "name": "which", "params": None})

    assert said.get("result") == {"result": "loaded"}, said
