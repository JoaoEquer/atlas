"""The installer's pins: every tool pinned for every target it needs, and the scripts' generated
blocks current with `scripts/install_pins.toml`. Run `uv run python scripts/gen_install_pins.py`
when this fails."""

import importlib.util
import re
from pathlib import Path
from types import ModuleType

GENERATOR = Path(__file__).resolve().parents[2] / "scripts" / "gen_install_pins.py"


def _generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_install_pins", GENERATOR)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_scripts_blocks_are_current() -> None:
    assert _generator().main(["--check"]) == 0


def test_every_tool_is_pinned_by_its_sha256_for_every_target_it_needs() -> None:
    generator = _generator()
    pins = generator.read_pins(generator.PINS.read_text(encoding="utf-8"))
    found = {(pin.tool, pin.target) for pin in pins}

    for tool in ("uv", "node"):
        assert {target for name, target in found if name == tool} == set(generator.TARGETS)
    # Git only on Windows: Linux and macOS use the system's.
    assert {target for name, target in found if name == "git"} == {"win32-x64", "win32-arm64"}
    for pin in pins:
        assert pin.url.startswith("https://")
        assert re.fullmatch(r"[0-9a-f]{64}", pin.sha256)
        assert pin.programs
        # The .tar.gz, never the .tar.xz: minimal Debian has no xz.
        assert not pin.url.endswith(".xz")


def test_each_script_carries_only_its_own_systems_pins() -> None:
    generator = _generator()
    powershell = generator.POWERSHELL.read_text(encoding="utf-8")
    shell = generator.SHELL.read_text(encoding="utf-8")

    assert '"node/win32-x64"' in powershell
    assert "linux" not in powershell.split(generator.BEGIN)[1].split(generator.END)[0]
    assert "node/linux-x64)" in shell
    assert "win32" not in shell
