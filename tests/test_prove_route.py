"""The rehearsal of scripts/prove_route.py: its steps run as they do against the user's own Claude
Code and Codex, with tests/fake_claude.py and tests/fake_codex.py in their place, the main account
at its plan's limit, so the proof cannot break unseen before its real run."""

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

from atlas.home import Database, Home, ModelSettings
from atlas.models import CallLedger
from atlas.secrets import Vault
from claude_code_support import claude_code
from codex_support import codex
from fake_model import FakeModelServer
from model_support import Connect

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
WRITE = {"tool": "Write", "input": {"file_path": "prova.txt", "content": "girassol"}}


def _proof() -> ModuleType:
    spec = importlib.util.spec_from_file_location("prove_route", SCRIPTS / "prove_route.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["prove_route"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("steps", "codex_full", "expected"),
    [
        (
            {1, 2, 3},
            True,
            {
                "1 where a route goes": True,
                "2 a route inside Claude Code": True,
                "3 the account's own hand-over": True,
            },
        ),
        ({1, 4}, False, {"1 where a route goes": True, "4 a route to Codex": True}),
        (
            {5, 6},
            True,
            {
                "5 a folder's account and the warning": True,
                "6 the main account's memory leaking": True,
            },
        ),
    ],
)
def test_the_proofs_steps_pass_on_the_fakes(
    steps: set[int],
    codex_full: bool,
    expected: dict[str, bool],
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = Path(os.environ["CLAUDE_CONFIG_DIR"])
    script = tmp_path / "claude-script.json"
    script.write_text(json.dumps([WRITE, {"result": "feito"}]), encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(script))
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", str(tmp_path / "claude-auth.json"))
    monkeypatch.setenv("FAKE_CLAUDE_PROJECTS", str(root / "projects"))
    monkeypatch.setenv("FAKE_CLAUDE_SESSION", "s-{folder}")
    # The user's own account is at its limit; their other one, `work`, logged in, has room.
    monkeypatch.setenv("FAKE_CLAUDE_LIMITED", "own")
    work = claude_code().new_account_folder("work")
    work.mkdir(parents=True)
    (work / "fake-auth.json").write_text(
        json.dumps({"loggedIn": True, "email": "work@example.com"}), encoding="utf-8"
    )
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    codex_script = tmp_path / "codex-script.json"
    steps_there = [{"write": "prova.txt", "content": "girassol"}, {"say": "feito"}]
    codex_script.write_text(json.dumps(steps_there), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("FAKE_CODEX_SCRIPT", str(codex_script))
    if codex_full:
        monkeypatch.setenv("FAKE_CODEX_LIMITED", "1")
    ledger = CallLedger(Database(home.database_file))
    models = connect(fake, "openrouter", settings=ModelSettings(), ledger=ledger)
    proof = _proof()
    # A step takes about a second here; a busy Windows runner, starting each fake harness through
    # its launcher, once took more than 20 s for one.
    monkeypatch.setattr(proof, "STEP_SECONDS", 90.0)

    code = asyncio.run(
        proof.prove(
            tmp_path / "proof",
            models,
            vault,
            (claude_code(), codex()),
            ["work"],
            steps,
            yes=True,
            unknown_extra=True,
            keep=False,
        )
    )

    out = capsys.readouterr().out
    assert code == 0, out
    assert json.loads(out[out.rindex("{") :]) == expected
    assert "At its limit: claude-code; with room: claude-code@" in out
    assert "extra usage off" in out, "the fake Claude Code says its extra usage is off"
    assert not fake.bodies("/v1/chat/completions"), "no call to Atlas's model"


def test_the_costly_steps_wait_for_yes_and_refuse_an_account_that_bills(
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(tmp_path / "none.json"))
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", str(tmp_path / "claude-auth.json"))
    monkeypatch.setenv("FAKE_CLAUDE_LIMITED", "own")
    work = claude_code().new_account_folder("work")
    work.mkdir(parents=True)
    (work / "fake-auth.json").write_text(
        json.dumps({"loggedIn": True, "email": "work@example.com"}), encoding="utf-8"
    )
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    models = connect(fake, "openrouter", settings=ModelSettings())
    proof = _proof()
    runs = iter(range(10))

    def run(credits: str, **given: bool) -> tuple[int, str]:
        monkeypatch.setenv("FAKE_CODEX_CREDITS", credits)
        code = asyncio.run(
            proof.prove(
                tmp_path / f"proof-{next(runs)}",
                models,
                vault,
                (claude_code(), codex()),
                ["work"],
                {1, 2},
                **given,
            )
        )
        return code, capsys.readouterr().out

    waited, first = run("")
    billing, second = run("on", yes=True)
    unsaid, third = run("unsaid", yes=True)

    assert waited == 0, "step 1 alone ran, and passed"
    assert "run again with --yes" in first
    assert billing == 1
    assert "extra usage is on for codex" in second
    assert unsaid == 1
    assert "codex does not say whether it bills" in third
    assert "--accept-unknown-extra" in third
