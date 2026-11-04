"""The rest of a plugin's Python (step 16l, SDK version 16): a slash command with code of
its own, a terminal command, the user's model, a message to a conversation, a model provider, and
the memory's limits (version 26).

The plugins are real folders in a temporary home, loaded as Atlas loads them; the conversations
run on the assistant against the fake model, so what reaches a model is what a provider would get.
"""

import asyncio
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from atlas.__main__ import SCHEMAS, main
from atlas.agent import TurnEnded
from atlas.assistant import Assistant, AssistantPluginHost, ConversationEvent
from atlas.home import (
    Database,
    Home,
    MemorySettings,
    ModelSettings,
    Settings,
    WorkspaceSettings,
    migrate,
)
from atlas.memory import Action, Operation, Target
from atlas.models import (
    AssistantMessage,
    CallLedger,
    CatalogError,
    Models,
    UserMessage,
    with_providers,
)
from atlas.plugins import LlmError, LlmMessage, ManifestError, Plugins
from atlas.plugins.model import parse_manifest
from atlas.plugins.service import SCREEN_BYTES
from atlas.secrets import Vault
from fake_model import FakeModelServer, FakeReply
from model_support import Connect

CHAT = "/v1/chat/completions"

CODE = """
from atlas.plugins.sdk import LlmError, PluginContext


def register(ctx: PluginContext) -> None:
    async def shout(arguments, host):
        if arguments == "boom":
            raise RuntimeError("it broke")
        return f"{arguments.upper()}! (in {host.conversation})"

    async def summary(arguments, host):
        said = await ctx.llm.complete(arguments, system="Be brief.", max_tokens=20)
        return f"{said.text} [{said.model}]"

    async def later(arguments, host):
        sent = await ctx.notify(host.conversation, "The render is done.")
        return f"sent: {sent}"

    def hello(argv):
        print("hello " + " ".join(argv))
        return 3

    ctx.register_command("shout", shout, "Shout it back.", hint="<text>")
    ctx.register_command("summary", summary, "Summarize with your model.")
    ctx.register_command("later", later, "Write here later.")
    ctx.register_cli_command("echo-hello", hello, "Say hello.")
"""


def _plugin(root: Path, atlas: dict[str, Any], code: str = CODE, name: str = "echo") -> Path:
    folder = root / name
    (folder / ".claude-plugin").mkdir(parents=True)
    manifest = {"name": name, "version": "1.0.0", "atlas": atlas}
    (folder / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), "utf-8")
    (folder / "__init__.py").write_text(code, encoding="utf-8")
    return folder


ALL = {
    "api": 16,
    "commands": ["shout", "summary", "later"],
    "cli": ["echo-hello"],
    "llm": True,
    "notify": True,
}


@pytest.fixture
def plugins(home: Home, vault: Vault, tmp_path: Path) -> Plugins:
    database = Database(home.database_file)
    migrate(database, SCHEMAS)
    return Plugins(home, database, vault, official_folder=tmp_path / "bundled")


def _on(plugins: Plugins, name: str = "echo") -> None:
    manifest = plugins.view(name).manifest
    assert manifest is not None
    plugins.enable(name, manifest.fingerprint())


def _assistant(
    home: Home, vault: Vault, fake: FakeModelServer, connect: Connect, tmp_path: Path
) -> tuple[Assistant, list[tuple[str, ConversationEvent]]]:
    chosen = ModelSettings(provider="openrouter")
    models = connect(
        fake, "openrouter", settings=chosen, ledger=CallLedger(Database(home.database_file))
    )
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(work))
    heard: list[tuple[str, ConversationEvent]] = []
    assistant = Assistant(home, settings, models, vault, listener=lambda c, e: heard.append((c, e)))
    return assistant, heard


# -- the manifest ----------------------------------------------------------------------------------


def test_what_the_python_does_on_its_own_is_declared_and_shown() -> None:
    manifest = parse_manifest(
        json.dumps(
            {
                "name": "echo",
                "atlas": {
                    **ALL,
                    "providers": {
                        "local-llm": {"base_url": "http://localhost:8000/v1", "key": "LOCAL_KEY"}
                    },
                },
            }
        ),
        {"__init__.py"},
        folder_name="echo",
    )
    assert manifest.slash_commands == ("shout", "summary", "later")
    assert manifest.cli_commands == ("echo-hello",)
    assert (manifest.llm, manifest.notify) == (True, True)
    shown = manifest.declaration()
    assert (
        "Adds the commands /echo:shout, /echo:summary, /echo:later, which its Python runs when "
        "you type them." in shown
    )
    assert "Adds the terminal commands `atlas echo-hello`, which run its Python." in shown
    assert any(line.startswith("Calls your model on its own") for line in shown)
    assert any(line.startswith("Writes to your conversations on its own") for line in shown)
    assert (
        "Adds the model provider local-llm at http://localhost:8000/v1: what you choose there is "
        "sent to that address." in shown
    )
    assert "Reads the secret LOCAL_KEY from the vault." in shown, "a provider's key is shown"
    bare = parse_manifest(
        json.dumps({"name": "echo", "atlas": {**ALL, "llm": False}}),
        {"__init__.py"},
        folder_name="echo",
    )
    assert bare.fingerprint() != manifest.fingerprint()


@pytest.mark.parametrize(
    ("atlas", "present", "problem"),
    [
        ({"api": 15, "commands": ["x"]}, {"__init__.py"}, "need atlas.api 16"),
        ({"api": 16, "llm": True}, set(), "brings no __init__.py"),
        ({"api": 16, "notify": "yes"}, {"__init__.py"}, "true or false"),
        ({"providers": {"x": {"key": "K"}}}, set(), "needs base_url"),
        ({"providers": {"x": {"base_url": "http://a", "key": "lower"}}}, set(), "secret's name"),
        ({"providers": {"Bad Id": {"base_url": "http://a"}}}, set(), "an id is"),
    ],
)
def test_what_the_python_does_is_refused_when_not_declared_well(
    atlas: dict[str, Any], present: set[str], problem: str
) -> None:
    with pytest.raises(ManifestError, match=problem):
        parse_manifest(json.dumps({"name": "echo", "atlas": atlas}), present, folder_name="echo")


def test_a_command_or_a_capability_it_does_not_declare_marks_the_plugin(
    plugins: Plugins, home: Home
) -> None:
    _plugin(home.plugins_folder, {"api": 16, "commands": ["shout"]})
    _on(plugins)
    parts = asyncio.run(plugins.parts())
    assert parts.commands == []
    assert "the command summary, which it does not declare" in str(plugins.view("echo").problem)


# -- slash commands --------------------------------------------------------------------------------


def test_a_plugin_s_slash_command_answers_with_no_model_call_and_is_kept(
    plugins: Plugins,
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    tmp_path: Path,
) -> None:
    _plugin(home.plugins_folder, ALL)
    _on(plugins)
    assistant, heard = _assistant(home, vault, fake, connect, tmp_path)

    async def scenario() -> tuple[str, list[str]]:
        parts = await plugins.parts()
        await assistant.set_plugins([], {}, {}, slash=parts.commands)
        conversation = assistant.conversation(assistant.conversations.create().id)
        offered = [view.name for view in assistant.slash_commands(conversation.id)]
        conversation.send("/echo:shout hello there")
        await conversation.wait()
        conversation.send("/echo:shout boom")
        await conversation.wait()
        await assistant.close()
        await plugins.stop()
        return conversation.id, offered

    conversation_id, offered = asyncio.run(scenario())
    assert {"echo:shout", "echo:summary", "echo:later"} <= set(offered)
    assert fake.bodies(CHAT) == [], "no model call"
    ended = [e for c, e in heard if c == conversation_id and isinstance(e, TurnEnded)]
    assert ended[0].text == f"HELLO THERE! (in {conversation_id})"
    assert ended[0].outcome.value == "answered"
    assert ended[1].outcome.value == "failed"
    assert ended[1].text == "/echo:shout failed: RuntimeError: it broke"
    history = assistant.conversations.open(conversation_id).history
    said = [
        (type(m).__name__, m.text) for m in history if isinstance(m, UserMessage | AssistantMessage)
    ]
    assert said[:2] == [
        ("UserMessage", "/echo:shout hello there"),
        ("AssistantMessage", f"HELLO THERE! (in {conversation_id})"),
    ], "kept, so the model reads it at the next turn"


# -- the user's model and the conversations ------------------------------------------------------


def test_a_plugin_calls_the_user_s_model_through_atlas_and_is_counted_as_its_own(
    plugins: Plugins,
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    tmp_path: Path,
) -> None:
    _plugin(home.plugins_folder, ALL)
    _on(plugins)
    assistant, heard = _assistant(home, vault, fake, connect, tmp_path)
    plugins.host = AssistantPluginHost(assistant)
    fake.reply(FakeReply(text="Short."))

    async def scenario() -> str:
        parts = await plugins.parts()
        await assistant.set_plugins([], {}, {}, slash=parts.commands)
        conversation = assistant.conversation(assistant.conversations.create().id)
        conversation.send("/echo:summary A very long text.")
        await conversation.wait()
        await assistant.close()
        await plugins.stop()
        return conversation.id

    conversation_id = asyncio.run(scenario())
    [body] = fake.bodies(CHAT)
    assert body["messages"] == [
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": "A very long text."},
    ]
    assert body.get("tools") in (None, [])
    ended = [e for c, e in heard if c == conversation_id and isinstance(e, TurnEnded)]
    assert ended[0].text.startswith("Short. [openrouter/")
    purposes = [call.purpose for call in CallLedger(Database(home.database_file)).recent()]
    assert purposes == ["plugin:echo"]


def test_a_structured_answer_is_parsed_and_checked(
    home: Home, vault: Vault, fake: FakeModelServer, connect: Connect, tmp_path: Path
) -> None:
    assistant, _ = _assistant(home, vault, fake, connect, tmp_path)
    host = AssistantPluginHost(assistant)
    schema = {
        "type": "object",
        "properties": {"urgent": {"type": "boolean"}},
        "required": ["urgent"],
    }
    fake.reply(FakeReply(text='```json\n{"urgent": true}\n```'))
    fake.reply(FakeReply(text='{"urgent": "very"}'))

    async def scenario() -> list[object]:
        first = await host.complete(
            "echo", [LlmMessage("user", "Is it urgent?")], system="", max_tokens=None,
            json_schema=schema,
        )  # fmt: skip
        second = await host.complete(
            "echo", "Is it urgent?", system="", max_tokens=None, json_schema=schema
        )
        return [first.parsed, second.parsed, second.text]

    first, second, text = asyncio.run(scenario())
    assert first == {"urgent": True}
    assert (second, text) == (None, '{"urgent": "very"}'), "does not fit: the text is kept"
    asked = fake.bodies(CHAT)[0]["messages"][0]["content"]
    assert asked.startswith("Answer with one JSON value that fits this JSON Schema")


@dataclass(frozen=True)
class _Host:
    conversation: str = "c-1"


@pytest.mark.parametrize(
    ("declared", "problem"),
    [
        (False, "echo called the user's model, which it does not declare (atlas.llm)."),
        (True, "Atlas is not serving here, so there is no model to call."),
    ],
)
def test_a_plugin_that_does_not_declare_the_model_or_has_no_host_is_told(
    plugins: Plugins, home: Home, declared: bool, problem: str
) -> None:
    _plugin(home.plugins_folder, {**ALL, "llm": declared})
    _on(plugins)

    async def scenario() -> str:
        parts = await plugins.parts()
        summary = next(c for c in parts.commands if c.name == "summary")
        try:
            await summary.run("x", _Host())
        except LlmError as error:
            return str(error)
        finally:
            await plugins.stop()
        return ""

    assert asyncio.run(scenario()) == problem


def test_a_plugin_writes_to_a_conversation_and_a_turn_answers_it(
    plugins: Plugins,
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    tmp_path: Path,
) -> None:
    _plugin(home.plugins_folder, ALL)
    _on(plugins)
    assistant, heard = _assistant(home, vault, fake, connect, tmp_path)
    plugins.host = AssistantPluginHost(assistant)
    fake.reply(FakeReply(text="Great, the video is ready."))

    async def scenario() -> str:
        parts = await plugins.parts()
        await assistant.set_plugins([], {}, {}, slash=parts.commands)
        conversation = assistant.conversation(assistant.conversations.create().id)
        conversation.send("/echo:later")
        await conversation.wait()
        for _ in range(100):
            if fake.bodies(CHAT):
                break
            await asyncio.sleep(0.05)
        await conversation.wait()
        assert not await AssistantPluginHost(assistant).notify("echo", "nowhere", "x")
        await assistant.close()
        await plugins.stop()
        return conversation.id

    conversation_id = asyncio.run(scenario())
    ended = [e.text for c, e in heard if c == conversation_id and isinstance(e, TurnEnded)]
    assert ended == ["sent: True", "Great, the video is ready."]
    last = fake.bodies(CHAT)[0]["messages"][-1]["content"]
    assert "[From the plugin echo]\nThe render is done." in str(last)


LIMITS_CODE = """
from atlas.plugins.sdk import PluginContext


def register(ctx: PluginContext) -> None:
    async def limits(arguments, host):
        return repr(ctx.memory_limits())

    ctx.register_command("limits", limits, "The memory's limits.")
"""


def test_a_plugin_reads_the_memorys_limits_as_they_change_and_none_without_atlas(
    plugins: Plugins,
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    tmp_path: Path,
) -> None:
    _plugin(home.plugins_folder, {"api": 26, "commands": ["limits"]}, LIMITS_CODE)
    _on(plugins)
    assistant, _ = _assistant(home, vault, fake, connect, tmp_path)
    assistant.bots.create("finance", body="Keep the bills paid.")
    assistant.memory.change(Target.USER, [Operation(Action.ADD, "Name: Ana")])
    fake.reply(FakeReply(text="Paid."))

    async def scenario() -> list[str]:
        parts = await plugins.parts()
        [limits] = parts.commands
        said = [await limits.run("", _Host())]
        plugins.host = AssistantPluginHost(assistant)
        said.append(await limits.run("", _Host()))
        changed = MemorySettings(user_limit=600, memory_limit=4_000)
        assistant.use_settings(replace(assistant.settings, memory=changed))
        said.append(await limits.run("", _Host()))
        conversation = assistant.conversation(assistant.bot_conversation("finance"))
        conversation.send("Is the rent paid?")
        await conversation.wait()
        await assistant.close()
        await plugins.stop()
        return said

    assert asyncio.run(scenario()) == [
        "None",
        "MemoryLimits(user=1375, memory=2200)",
        "MemoryLimits(user=600, memory=4000)",
    ]
    system = str(fake.bodies(CHAT)[0]["messages"][0]["content"])
    assert "9/600 chars" in system, "a bot's stores follow the home's limits"


# -- the terminal --------------------------------------------------------------------------------


def test_a_plugin_s_terminal_command_runs_with_its_arguments(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "home"
    environ = {"ATLAS_HOME": str(root)}
    assert main(["plugins", "list"], environ) == 0
    capsys.readouterr()
    home_plugins = root / "plugins"
    _plugin(home_plugins, ALL)
    with pytest.raises(SystemExit):
        main(["echo-hello", "a"], dict(environ))  # off: argparse says it is unknown
    capsys.readouterr()
    manifest = parse_manifest(
        (home_plugins / "echo" / ".claude-plugin" / "plugin.json").read_text("utf-8"),
        {"__init__.py"},
        folder_name="echo",
    )
    assert main(["plugins", "enable", "echo", "--yes"], dict(environ)) == 0
    capsys.readouterr()
    assert main(["--home", "default", "echo-hello", "a", "--b"], dict(environ)) == 3
    assert capsys.readouterr().out == "hello a --b\n"
    assert manifest.cli_commands == ("echo-hello",)


# -- model providers -------------------------------------------------------------------------------


def test_a_plugin_s_model_provider_joins_the_catalog_and_never_replaces_one(
    plugins: Plugins, home: Home, vault: Vault, fake: FakeModelServer, connect: Connect
) -> None:
    folder = home.plugins_folder / "llm"
    (folder / ".claude-plugin").mkdir(parents=True)
    provider = {"name": "My vLLM", "base_url": "http://localhost:8000/v1", "key": "VLLM_KEY"}
    (folder / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "llm", "atlas": {"providers": {"my-vllm": provider}}}), "utf-8"
    )
    assert plugins.providers() == {}
    _on(plugins, "llm")
    tables = plugins.providers()
    assert tables == {"llm": {"my-vllm": provider}}
    models: Models = connect(fake, "openrouter")
    catalog = with_providers(models.catalog, tables["llm"], "the plugin llm")
    added = catalog.provider("my-vllm")
    assert (added.name, added.base_url, added.key) == provider_fields(provider)
    with pytest.raises(CatalogError, match="already has"):
        with_providers(models.catalog, {"openrouter": provider}, "the plugin llm")
    with pytest.raises(CatalogError, match="unknown fields: color"):
        with_providers(models.catalog, {"x": {**provider, "color": "red"}}, "the plugin llm")


def provider_fields(provider: dict[str, str]) -> tuple[str, str, str]:
    return provider["name"], provider["base_url"], provider["key"]


# -- the screen (step 16m) -------------------------------------------------------------------------


SCREEN = "export default { id: 'echo', register(ctx) {} }\n"
CALLS = """
def register(ctx):
    async def count(params):
        return {"n": params["n"] + 1}

    ctx.register_call("count", count)
"""


def test_a_screen_and_its_calls_are_declared_and_shown_with_what_they_may_do(
    plugins: Plugins, home: Home
) -> None:
    folder = _plugin(home.plugins_folder, {"api": 17, "calls": ["count"]}, code=CALLS)
    assert plugins.screens() == []
    (folder / "desktop").mkdir()
    (folder / "desktop" / "plugin.js").write_bytes(SCREEN.encode())
    manifest = plugins.view("echo").manifest
    assert manifest is not None
    assert (manifest.screen, manifest.calls) == (True, ("count",))
    shown = manifest.declaration()
    assert any("all the app's access" in line and "no sandbox" in line for line in shown)
    assert any(line.startswith("Its screen calls its Python (count)") for line in shown)
    assert plugins.screens() == [], "off: the app loads nothing of it"
    _on(plugins)
    [screen] = plugins.screens()
    assert (screen.plugin, screen.source) == ("echo", SCREEN)

    async def scenario() -> object:
        parts = await plugins.parts()
        [call] = parts.calls
        result = await call.run({"n": 1})
        await plugins.stop()
        return result

    assert asyncio.run(scenario()) == {"n": 2}


@pytest.mark.parametrize(
    ("atlas", "code", "problem"),
    [
        ({"api": 16, "calls": ["count"]}, CALLS, "need atlas.api 17"),
        ({"api": 17, "calls": ["other"]}, CALLS, "the call count, which it does not declare"),
    ],
)
def test_a_call_it_does_not_declare_is_refused(
    plugins: Plugins, home: Home, atlas: dict[str, Any], code: str, problem: str
) -> None:
    _plugin(home.plugins_folder, atlas, code=code)
    view = plugins.view("echo")
    if view.manifest is None:
        assert problem in str(view.problem)
        return
    _on(plugins)
    assert asyncio.run(plugins.parts()).calls == []
    assert problem in str(plugins.view("echo").problem)


def test_a_screen_past_its_size_marks_the_plugin(plugins: Plugins, home: Home) -> None:
    folder = _plugin(home.plugins_folder, {"api": 17}, code="def register(ctx):\n    pass\n")
    (folder / "desktop").mkdir()
    (folder / "desktop" / "plugin.js").write_text("x" * (SCREEN_BYTES + 1), encoding="utf-8")
    _on(plugins)
    assert plugins.screens() == []
    assert "over 2 MB" in str(plugins.view("echo").problem)
