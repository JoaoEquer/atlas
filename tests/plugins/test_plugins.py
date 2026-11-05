"""Plugins: the manifest in Claude Code's format, consent, loading with a deadline, and pairing.

The plugins are real folders in a temporary home, their Python imported as Atlas imports it.
"""

import ast
import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from atlas.home import Database, Home, migrate
from atlas.plugins import (
    OFFICIAL_FOLDER,
    SCHEMA,
    ManifestError,
    PluginError,
    Plugins,
    install_official,
    install_plugin,
    official_plugins,
    refresh_official,
    remove_plugin,
)
from atlas.plugins.model import (
    PAIRING_ALPHABET,
    PAIRING_CODE_LENGTH,
    PAIRING_TTL,
    parse_manifest,
)
from atlas.secrets import Vault

REGISTER = """
from atlas.plugins.sdk import PluginContext


class Quiet:
    async def run(self, host): ...
    async def send(self, chat, text): ...
    async def working(self, chat, busy): ...
    async def ask(self, chat, prompt): ...
    async def settle(self, chat, prompt, said): ...


def register(ctx: PluginContext) -> None:
    ctx.register_channel("echo", Quiet)
"""


def _manifest(**atlas: Any) -> dict[str, Any]:
    return {
        "name": "echo",
        "version": "1.0.0",
        "description": "Echoes.",
        "author": {"name": "Someone"},
        "atlas": {"channels": ["echo"], "secrets": ["ECHO_TOKEN"], **atlas},
    }


def _plugin(
    folder: Path, name: str = "echo", manifest: dict[str, Any] | None = None, code: str = REGISTER
) -> Path:
    plugin = folder / name
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(manifest if manifest is not None else _manifest()), encoding="utf-8"
    )
    (plugin / "__init__.py").write_text(code, encoding="utf-8")
    return plugin


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 14, 12, 0, tzinfo=datetime.now().astimezone().tzinfo)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def plugins(home: Home, vault: Vault, clock: _Clock, tmp_path: Path) -> Plugins:
    database = Database(home.database_file)
    migrate(database, (SCHEMA,))
    return Plugins(home, database, vault, clock=clock, official_folder=tmp_path / "bundled")


# -- the manifest ----------------------------------------------------------------------------------


def test_the_manifest_is_claude_codes_with_atlas_s_own_part() -> None:
    manifest = parse_manifest(
        json.dumps(
            _manifest(
                settings=[{"name": "api_url", "default": "https://x", "description": "Where."}],
                network=["api.example.com"],
            )
        ),
        {"__init__.py", ".claude-plugin"},
        folder_name="echo",
    )
    assert (manifest.name, manifest.version, manifest.author) == ("echo", "1.0.0", "Someone")
    assert manifest.python
    assert manifest.channels == ("echo",)
    assert [s.name for s in manifest.secrets] == ["ECHO_TOKEN"]
    assert manifest.setting("api_url") is not None
    assert manifest.declaration() == (
        "Runs its own Python code inside Atlas, with your user's permissions on this computer: "
        "there is no sandbox.",
        "Adds the channel echo: the people you allow talk to Atlas through it, and Atlas "
        "answers, asks and asks for approvals there.",
        "Reads the secret ECHO_TOKEN from the vault.",
        "Says it reaches: api.example.com.",
    )
    # Only `name` is required, as in Claude Code.
    bare = parse_manifest('{"name": "bare"}', set(), folder_name="bare")
    assert (bare.python, bare.channels, bare.declaration()) == (False, (), ())


@pytest.mark.parametrize(
    ("text", "present", "problem"),
    [
        ("{", set(), "not JSON"),
        ("[]", set(), "not a JSON object"),
        ('{"name": "Echo Bot"}', set(), "kebab-case"),
        ('{"name": "other"}', set(), "folder named 'other'"),
        ('{"name": "echo", "atlas": {"channels": ["echo"]}}', set(), "brings no __init__.py"),
        ('{"name": "echo", "atlas": {"secrets": ["lower"]}}', set(), "bad name"),
        ('{"name": "echo", "atlas": {"secrets": [{"name": "A", "url": 3}]}}', set(), "of text"),
        ('{"name": "echo", "atlas": {"channels": "echo"}}', {"__init__.py"}, "list of names"),
        ('{"name": "echo", "atlas": {"api": 0}}', set(), "whole number"),
        ('{"name": "echo", "atlas": []}', set(), "not an object"),
    ],
)
def test_a_manifest_atlas_cannot_read_says_what_to_fix(
    text: str, present: set[str], problem: str
) -> None:
    with pytest.raises(ManifestError, match=problem):
        parse_manifest(text, present, folder_name="echo")


def test_the_fingerprint_changes_with_what_is_declared_not_with_the_words() -> None:
    first = parse_manifest(json.dumps(_manifest()), {"__init__.py"}, folder_name="echo")
    reworded = parse_manifest(
        json.dumps({**_manifest(), "description": "Other words.", "version": "1.0.1"}),
        {"__init__.py"},
        folder_name="echo",
    )
    more = parse_manifest(
        json.dumps(_manifest(secrets=["ECHO_TOKEN", "OTHER_TOKEN"])),
        {"__init__.py"},
        folder_name="echo",
    )
    assert first.fingerprint() == reworded.fingerprint()
    assert first.fingerprint() != more.fingerprint()


def test_executors_are_declared_consented_to_and_need_version_2() -> None:
    present = {"__init__.py"}
    declared = {"name": "echo", "atlas": {"api": 2, "executors": ["echo-cli"]}}
    manifest = parse_manifest(json.dumps(declared), present, folder_name="echo")
    assert manifest.executors == ("echo-cli",)
    assert manifest.declaration()[1] == (
        "Adds the executor echo-cli: it runs that program as installed on this computer, with "
        "your own login, in the folders you or Atlas give it."
    )
    more = {"name": "echo", "atlas": {"api": 2, "executors": ["echo-cli", "other"]}}
    assert (
        manifest.fingerprint()
        != parse_manifest(json.dumps(more), present, folder_name="echo").fingerprint()
    )
    with pytest.raises(ManifestError, match=r"need atlas.api 2"):
        parse_manifest(
            '{"name": "echo", "atlas": {"executors": ["echo-cli"]}}', present, folder_name="echo"
        )
    with pytest.raises(ManifestError, match="declares executors but brings no"):
        parse_manifest(json.dumps(declared), set(), folder_name="echo")


def test_a_plugin_of_version_1_keeps_the_consent_it_was_given() -> None:
    """Version 2 adds `executors` to what consent covers only where a plugin declares them: the
    fingerprint the user gave Telegram on version 1 still holds, so it stays on."""
    manifest = parse_manifest(json.dumps(_manifest()), {"__init__.py"}, folder_name="echo")
    version_1 = {
        "api": 1,
        "python": True,
        "channels": ["echo"],
        "secrets": ["ECHO_TOKEN"],
        "network": [],
    }
    text = json.dumps(version_1, sort_keys=True, separators=(",", ":"))
    assert manifest.fingerprint() == hashlib.sha256(text.encode("utf-8")).hexdigest()


# -- consent ---------------------------------------------------------------------------------------


def test_a_plugin_arrives_off_and_turns_on_only_with_consent_to_what_it_declares(
    plugins: Plugins, home: Home
) -> None:
    _plugin(home.plugins_folder)
    [view] = plugins.all()
    assert (view.name, view.enabled, view.consented, view.bundled) == ("echo", False, False, False)
    assert view.secrets == {"ECHO_TOKEN": False}
    manifest = view.manifest
    assert manifest is not None

    with pytest.raises(PluginError, match="other than what was shown"):
        plugins.enable("echo", "a-fingerprint-of-something-else")
    on = plugins.enable("echo", manifest.fingerprint())
    assert (on.enabled, on.consented, on.running) == (True, True, True)

    # An update that declares more: off until the user consents again.
    (home.plugins_folder / "echo" / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(_manifest(secrets=["ECHO_TOKEN", "BANK_PASSWORD"])), encoding="utf-8"
    )
    changed = plugins.view("echo")
    assert (changed.enabled, changed.consented, changed.running) == (True, False, False)

    off = plugins.disable("echo")
    assert (off.enabled, off.consented) == (False, False)
    with pytest.raises(PluginError, match="no plugin 'nope'"):
        plugins.view("nope")


def test_what_atlas_does_not_load_yet_or_a_newer_sdk_is_refused(
    plugins: Plugins, home: Home
) -> None:
    hooked = _plugin(home.plugins_folder, "hooked", {"name": "hooked"}, code="")
    (hooked / ".lsp.json").write_text("{}", encoding="utf-8")
    _plugin(home.plugins_folder, "agented", {"name": "agented", "agents": ["./a.md"]}, code="")
    _plugin(home.plugins_folder, "future", {"name": "future", "atlas": {"api": 99}}, code="")
    _plugin(home.plugins_folder, "broken", {"name": "Broken!"}, code="")
    views = {view.name: view for view in plugins.all()}
    assert views["hooked"].problem == "It brings LSP servers, which Atlas does not load yet."
    assert views["agented"].problem == "It brings agents, which Atlas does not load yet."
    assert "newer Atlas" in str(views["future"].problem)
    assert "kebab-case" in str(views["broken"].problem)
    for name in ("hooked", "agented", "future"):
        manifest = views[name].manifest
        assert manifest is not None
        with pytest.raises(PluginError):
            plugins.enable(name, manifest.fingerprint())
    with pytest.raises(PluginError, match="cannot be turned on"):
        plugins.enable("broken", "")


def test_a_plugin_of_atlass_is_installed_from_its_folder_and_none_takes_its_place(
    home: Home, vault: Vault, tmp_path: Path
) -> None:
    """Step 26: Atlas ships no plugin inside itself; one it makes is listed from the installation's
    folder, installed when the home records it, run from that folder, and an address with its
    name cannot replace it. Taken out, its record goes and the installation keeps its files."""
    database = Database(home.database_file)
    migrate(database, (SCHEMA,))
    official = tmp_path / "official"
    _plugin(official)
    plugins = Plugins(home, database, vault, official_folder=official)
    [offered] = plugins.all()
    assert (offered.bundled, offered.installed) == (True, False), "from Atlas's folder, not yet"

    _on(plugins)  # turning it on installs it
    [view] = plugins.all()
    other = tmp_path / "other"
    _plugin(other)

    assert (view.name, view.bundled, view.installed, view.running) == ("echo", True, True, True)
    assert view.folder == official / "echo", "run from the installation, at its version"
    with pytest.raises(PluginError, match="Atlas's own plugin echo is installed"):
        install_plugin(plugins, str(other / "echo"))
    remove_plugin(plugins, "echo")
    [gone] = plugins.all()
    assert (gone.installed, gone.enabled) == (False, False)
    assert (official / "echo" / "__init__.py").exists(), "the installation keeps its files"


def test_a_plugin_atlas_shipped_inside_itself_before_step_26_comes_back_as_it_was(
    home: Home, vault: Vault, tmp_path: Path
) -> None:
    """The first start after step 26 installs each plugin of Atlas's the user ever decided on,
    in the state they left it; one never used stays offered, and one of the user's of the same
    name is left alone."""
    database = Database(home.database_file)
    migrate(database, (SCHEMA,))
    official = tmp_path / "official"
    _plugin(official)
    _plugin(official, "unused", {**_manifest(), "name": "unused"})
    _plugin(official, "theirs", {**_manifest(), "name": "theirs"})
    _plugin(home.plugins_folder, "theirs", {**_manifest(), "name": "theirs"})
    plugins = Plugins(home, database, vault, official_folder=official)
    manifest = plugins.view("echo").manifest
    assert manifest is not None
    # Decided before step 26: on, with its consent, and nothing recorded it.
    now = datetime.now(UTC)
    plugins.store.decide("echo", enabled=True, consent=manifest.fingerprint(), now=now)
    plugins.store.decide("theirs", enabled=False, consent=None, now=now)

    assert refresh_official(plugins, folder=official) == ["echo"]
    assert refresh_official(plugins, folder=official) == [], "once"

    views = {view.name: view for view in plugins.all()}
    assert (views["echo"].installed, views["echo"].running) == (True, True), "as it was left"
    assert not views["unused"].installed, "never used: offered, not installed"
    assert views["theirs"].folder == home.plugins_folder / "theirs", "the user's own, left alone"


def test_a_consent_is_carried_when_a_plugin_of_atlass_only_declares_less(
    home: Home, vault: Vault, tmp_path: Path
) -> None:
    """Step 26: a plugin of Atlas's that lost a part to the core asks nothing new, so the user who
    turned it on keeps it on; a consent to anything else still waits for the user."""
    database = Database(home.database_file)
    migrate(database, (SCHEMA,))
    official = tmp_path / "official"
    _plugin(official)
    _plugin(official, "other", {**_manifest(), "name": "other"})
    plugins = Plugins(home, database, vault, official_folder=official)
    manifest = plugins.view("echo").manifest
    assert manifest is not None
    now = datetime.now(UTC)
    plugins.store.decide("echo", enabled=True, consent="what-it-declared-before", now=now)
    plugins.store.decide("other", enabled=True, consent="something-else", now=now)
    narrowed = {
        "echo": ("what-it-declared-before", manifest.fingerprint()),
        "other": ("what-it-declared-before", manifest.fingerprint()),
    }

    refresh_official(plugins, folder=official, narrowed=narrowed)

    views = {view.name: view for view in plugins.all()}
    assert (views["echo"].enabled, views["echo"].consented) == (True, True)
    assert views["other"].consented is False, "a consent to another declaration is not carried"


#: What each plugin Atlas ships declares, as its consent's fingerprint. A user who turned one on
#: consented to this; a manifest that declares anything else turns the plugin off for them at the
#: update, until they consent again (`consent again`). Changing a value here is that decision,
#: made on purpose: the commit's message says users will be asked to consent again. Found on
#: 01/11/2026, when Claude Code's `atlas.api` moved from 2 to 25 and nothing said so.
BUNDLED_CONSENTS = {
    "accounts": "ce1689166cb356c7a868cad08cfbdbd4558fffcb6ef2a941351493f067911040",
    "claude-code": "35414510c384728fbb4b07323160448e898a29b50cfb90595e3e07e2721f00e9",
    "codex": "693d68ea873b613f5d88b99ac5857c884e6da3038e79347efea6430db863703c",
    "deepseek": "4ff0b8f41f12fccd1b19a74dc37c164b1c8328da5bdfaa2f9353689ed3f70154",
    "gemini-cli": "f55aa0469062ac5b19b23f140e261a3126c327f53799daff15e3170e5ce36337",
    "github": "66ea55d962aec9007c7c569e702bb3b3511a882dff1f26c27636e719615ee5b0",
    "google": "d96a07239fb1feaa056b57aa8911d0937718cd2b7c412a8ab67d685104619fca",
    "telegram": "bc11fc8c276abf0e4bcbdce1282af46458728404923f1de826e4e4f1f723578f",
    "voice": "ffad1173ac77c4ba88ec32ec52bc408f27e9ae100e94bb747e8bbbfcccb9607c",
}


def test_a_shipped_plugin_asks_for_consent_again_only_on_purpose(home: Home, vault: Vault) -> None:
    """A change to what a shipped plugin declares (its `atlas.api` included, which the fingerprint
    holds) turns it off for everyone who had it on: this pins each one, so it never ships
    unnoticed. A bundled plugin keeps the `api` its consent was given for unless it must move."""
    database = Database(home.database_file)
    migrate(database, (SCHEMA,))
    found = {
        name: plugin.manifest.fingerprint()
        for name, plugin in official_plugins().items()
        if plugin.manifest is not None
    }
    assert found == BUNDLED_CONSENTS, (
        "A shipped plugin's declaration changed: users who turned it on will be asked to consent "
        "again. Update BUNDLED_CONSENTS only if that is meant, and say so in the commit message."
    )


def test_atlas_ships_its_channel_and_executor_plugins_off(home: Home, vault: Vault) -> None:
    database = Database(home.database_file)
    migrate(database, (SCHEMA,))
    plugins = Plugins(home, database, vault, official_folder=OFFICIAL_FOLDER)
    for name in official_plugins():
        install_official(plugins, name)
    accounts, claude, codex, deepseek, gemini, github, google, telegram, voice = plugins.all()
    assert (github.name, github.enabled, google.name, google.enabled) == (
        "github",
        False,
        "google",
        False,
    ), "GitHub and Google Workspace, plugins of Atlas's since step 26, off"
    assert (accounts.name, accounts.bundled, accounts.enabled) == ("accounts", True, False)
    assert accounts.manifest is not None
    assert accounts.manifest.accounts, "the harnesses' other accounts (step 22)"
    assert (telegram.name, telegram.bundled, telegram.enabled, telegram.problem) == (
        "telegram",
        True,
        False,
        None,
    )
    assert telegram.secrets == {"TELEGRAM_BOT_TOKEN": False}
    assert telegram.settings == {"api_url": "https://api.telegram.org"}
    assert (claude.name, claude.bundled, claude.enabled, claude.problem) == (
        "claude-code",
        True,
        False,
        None,
    )
    assert claude.manifest is not None
    assert claude.manifest.executors == ("claude-code",)
    assert claude.settings == {"program": "", "chrome": "off"}
    assert claude.secrets == {}, "it reads no secret: Claude Code keeps its own login"
    assert (codex.name, codex.bundled, codex.enabled, codex.problem) == ("codex", True, False, None)
    assert codex.manifest is not None
    assert codex.manifest.executors == ("codex",)
    assert codex.settings == {"program": ""}
    assert codex.secrets == {}, "Codex keeps its own login too"
    assert (gemini.name, gemini.bundled, gemini.enabled, gemini.problem) == (
        "gemini-cli",
        True,
        False,
        None,
    )
    assert gemini.manifest is not None
    assert gemini.manifest.executors == ("gemini-cli",)
    assert gemini.manifest.api == 18
    assert gemini.settings == {"program": ""}
    assert gemini.secrets == {"GEMINI_API_KEY": False}, "a personal account's key, kept by Atlas"
    assert (deepseek.name, deepseek.bundled, deepseek.enabled) == ("deepseek", True, False)
    assert deepseek.manifest is not None
    assert deepseek.manifest.executors == ("deepseek",)
    assert deepseek.settings == {"program": "", "session_logs": "off"}, "its uploads off"
    assert deepseek.secrets == {"DEEPSEEK_API_KEY": False}
    assert (voice.name, voice.bundled, voice.enabled, voice.problem) == ("voice", True, False, None)
    assert voice.manifest is not None
    assert (voice.manifest.readers, voice.manifest.desktop) == ((), ()), "both Atlas's own now"
    assert voice.secrets == {"OPENAI_API_KEY": False, "GEMINI_API_KEY": False}, (
        "the OpenAI and Gemini providers' own keys"
    )


#: The SDK: its pure module, and its ACP client since version 18.
SDK = frozenset({"atlas.plugins.sdk", "atlas.plugins.acp"})


def test_a_plugin_atlas_ships_imports_only_the_sdk_from_atlas() -> None:
    """import-linter cannot see a folder named with a hyphen (`claude-code` is no module name),
    so the rule every plugin keeps is read here, from each shipped plugin's own files."""
    checked: list[str] = []
    for source in sorted(OFFICIAL_FOLDER.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                if name == "atlas" or name.startswith("atlas."):
                    assert name in SDK, f"{source}: imports {name}"
        checked.append(source.parent.name)
    assert {"telegram", "claude-code", "codex", "gemini-cli", "deepseek", "voice"} <= set(checked)


def test_secrets_and_settings_are_only_those_declared(plugins: Plugins, home: Home) -> None:
    _plugin(
        home.plugins_folder,
        manifest=_manifest(settings=[{"name": "api_url", "default": "https://x"}]),
    )
    assert plugins.set_secret("echo", "ECHO_TOKEN", "  t0ken \n").secrets == {"ECHO_TOKEN": True}
    with pytest.raises(PluginError, match="declares no secret"):
        plugins.set_secret("echo", "OTHER", "x")
    with pytest.raises(PluginError, match="empty"):
        plugins.set_secret("echo", "ECHO_TOKEN", "  ")
    assert plugins.set_setting("echo", "api_url", "http://local").settings == {
        "api_url": "http://local"
    }
    assert plugins.set_setting("echo", "api_url", None).settings == {"api_url": "https://x"}
    with pytest.raises(PluginError, match="no setting"):
        plugins.set_setting("echo", "other", "x")


# -- loading ---------------------------------------------------------------------------------------


def _on(plugins: Plugins, name: str = "echo") -> None:
    manifest = plugins.view(name).manifest
    assert manifest is not None
    plugins.enable(name, manifest.fingerprint())


def test_an_enabled_plugin_s_channels_load_with_what_it_declares(
    plugins: Plugins, home: Home, vault: Vault
) -> None:
    code = REGISTER.replace(
        'ctx.register_channel("echo", Quiet)',
        'ctx.register_channel("echo", Quiet)\n'
        '    (ctx.data_folder / "seen").write_text(str(ctx.secret("ECHO_TOKEN")) + ctx.name)',
    )
    _plugin(home.plugins_folder, code=code)
    assert asyncio.run(plugins.channels()) == []  # off: never imported
    _on(plugins)
    vault.set("ECHO_TOKEN", "abc")
    [loaded] = asyncio.run(plugins.channels())
    assert (loaded.plugin, loaded.name) == ("echo", "echo")
    assert (home.plugin_data_folder / "echo" / "seen").read_text() == "abcecho"
    assert asyncio.run(plugins.channels(only={"other"})) == []


@pytest.mark.parametrize(
    ("body", "problem"),
    [
        ("    raise RuntimeError('boom')", "It failed to load: RuntimeError: boom"),
        ("    ctx.secret('BANK_PASSWORD')", "BANK_PASSWORD, which it does not declare"),
        ("    ctx.setting('other')", "the setting other, which it does not declare"),
        ("    ctx.register_channel('sms', object)", "the channel sms, which it does not declare"),
        ("    import time; time.sleep(5)", "It took more than 2 s to load."),
    ],
)
def test_a_plugin_that_fails_or_hangs_while_loading_is_marked_and_the_rest_goes_on(
    home: Home, vault: Vault, tmp_path: Path, body: str, problem: str
) -> None:
    database = Database(home.database_file)
    migrate(database, (SCHEMA,))
    # A slow runner takes more than a fraction of a second to import a plugin that is fine: only
    # the hanging case gets a short limit, and it hangs well past it.
    hangs = "sleep" in body
    plugins = Plugins(
        home, database, vault, official_folder=tmp_path / "none", load_seconds=2 if hangs else 30
    )
    _plugin(home.plugins_folder, code=f"def register(ctx):\n{body}\n")
    _plugin(home.plugins_folder, "fine", {**_manifest(), "name": "fine"})
    _on(plugins)
    _on(plugins, "fine")
    loaded = asyncio.run(plugins.channels())
    assert [c.plugin for c in loaded] == ["fine"]
    assert problem in str(plugins.view("echo").problem)
    assert not plugins.view("echo").running
    # Turned on again, it is tried again.
    _on(plugins)
    assert plugins.view("echo").problem is None


EXECUTOR = """
from atlas.plugins.sdk import Detection, PluginContext


class Echo:
    title = "Echo"

    async def detect(self):
        return Detection(True, "1.0", "1.0", True)


def register(ctx: PluginContext) -> None:
    ctx.register_executor("echo-cli", Echo)
"""


def test_an_enabled_plugin_s_executors_load_and_only_those_declared(
    plugins: Plugins, home: Home
) -> None:
    declared = {"name": "echo", "atlas": {"api": 2, "executors": ["echo-cli"]}}
    _plugin(home.plugins_folder, manifest=declared, code=EXECUTOR)
    assert asyncio.run(plugins.executors()) == []  # off: never imported
    _on(plugins)
    [loaded] = asyncio.run(plugins.executors())
    assert (loaded.plugin, loaded.name) == ("echo", "echo-cli")
    assert asyncio.run(loaded.make().detect()).supported
    assert asyncio.run(plugins.channels()) == [], "it brings no channel"

    (home.plugins_folder / "echo" / "__init__.py").write_text(
        EXECUTOR.replace('"echo-cli", Echo', '"other", Echo'), encoding="utf-8"
    )
    assert asyncio.run(plugins.executors()) == []
    assert "the executor other, which it does not declare" in str(plugins.view("echo").problem)


def test_a_plugin_without_register_is_marked(plugins: Plugins, home: Home) -> None:
    _plugin(home.plugins_folder, code="x = 1\n")
    _on(plugins)
    assert asyncio.run(plugins.channels()) == []
    assert "defines no register(ctx)" in str(plugins.view("echo").problem)


# -- pairing ---------------------------------------------------------------------------------------


def test_someone_new_gets_a_code_and_is_let_in_once_it_is_approved(
    plugins: Plugins, clock: _Clock
) -> None:
    first = plugins.admit("telegram", "100", "Ana")
    assert (first.allowed, first.new) == (False, True)
    code = str(first.code)
    assert len(code) == PAIRING_CODE_LENGTH
    assert set(code) <= set(PAIRING_ALPHABET)
    # Writing again while the code holds tells the same code, and makes no new one.
    again = plugins.admit("telegram", "100", "Ana")
    assert (again.allowed, again.code, again.new) == (False, code, False)
    assert not plugins.allowed("telegram", "100")

    approved = plugins.approve(f" {code.lower()[:4]} {code.lower()[4:]} ")
    assert (approved.user, approved.name) == ("100", "Ana")
    assert plugins.admit("telegram", "100", "Ana").allowed
    assert plugins.allowed("telegram", "100")
    assert not plugins.allowed("other", "100")
    with pytest.raises(PluginError, match="No code"):
        plugins.approve(code)  # used

    plugins.forget("telegram", "100")
    assert not plugins.allowed("telegram", "100")
    with pytest.raises(PluginError, match="does not know"):
        plugins.forget("telegram", "100")


def test_a_code_lasts_an_hour_and_at_most_three_wait(plugins: Plugins, clock: _Clock) -> None:
    codes = [plugins.admit("telegram", str(user), "x").code for user in range(3)]
    assert all(codes)
    full = plugins.admit("telegram", "9", "x")
    assert (full.allowed, full.code, full.new) == (False, None, False)
    assert len([p for p in plugins.waiting() if not p.approved]) == 3

    clock.now += PAIRING_TTL + timedelta(seconds=1)
    with pytest.raises(PluginError, match="No code"):
        plugins.approve(str(codes[0]))
    assert plugins.waiting() == []
    fresh = plugins.admit("telegram", "0", "x")
    assert fresh.new
    assert fresh.code != codes[0]


def test_the_chat_each_person_writes_from_is_where_what_belongs_to_no_chat_reaches_them(
    plugins: Plugins,
) -> None:
    """Claude Code's own Telegram relays a permission to every paired sender; Atlas reaches
    everyone let in at the chat they last wrote from, and nobody who waits with a code."""
    ana = plugins.admit("telegram", "100", "Ana", "chat-100")
    plugins.admit("telegram", "200", "Bia", "chat-200")
    assert plugins.reached("telegram") == [], "nobody is let in yet"

    plugins.approve(str(ana.code))
    assert plugins.reached("telegram") == ["chat-100"]
    plugins.admit("telegram", "100", "Ana", "chat-100b")
    assert plugins.reached("telegram") == ["chat-100b"], "the chat they wrote from last"
    assert plugins.reached("other") == []
    plugins.forget("telegram", "100")
    assert plugins.reached("telegram") == []


def test_someone_let_in_before_is_reached_at_their_own_chat_after_the_migration(
    home: Home, vault: Vault, tmp_path: Path
) -> None:
    """Pairings made before the chat was kept: someone with a chat named as they are (their own
    private chat, as Telegram names it) is reached there at once; the rest from their next
    message."""
    database = Database(home.database_file)
    migrate(database, (type(SCHEMA)(SCHEMA.subject, SCHEMA.migrations[:1]),))
    connection = database.connect()
    try:
        with connection:
            for user, approved in (("100", 1), ("200", 1), ("300", 0)):
                connection.execute(
                    "INSERT INTO plugins_pairing (channel, user, name, code, asked_at, approved) "
                    "VALUES ('telegram', ?, 'x', 'CODE', '2026-10-14T00:00:00.000Z', ?)",
                    (user, approved),
                )
            for chat in ("100", "300"):
                connection.execute(
                    "INSERT INTO plugins_chats (channel, chat, conversation_id) "
                    "VALUES ('telegram', ?, ?)",
                    (chat, f"c-{chat}"),
                )
    finally:
        connection.close()
    migrate(database, (SCHEMA,))

    plugins = Plugins(home, database, vault, official_folder=tmp_path / "bundled")
    assert plugins.reached("telegram") == ["100"]
