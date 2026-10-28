"""What a plugin brings beside its channels and executors (step 16j): its skills, its MCP servers
and its tools, and what stops when it is turned off.

The plugins are real folders in a temporary home; the MCP server is the tests' real stdio server,
started from the plugin's own folder; the turn is the assistant's, against the fake model.
"""

import asyncio
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from atlas.__main__ import SCHEMAS, main
from atlas.agent import Toolset
from atlas.assistant import Assistant
from atlas.home import Database, Home, ModelSettings, Settings, WorkspaceSettings, migrate
from atlas.jobs import DelegationError, parse_agent
from atlas.mcp import McpClient, parse_config
from atlas.models import TOOL_SEARCH, CallLedger
from atlas.plugins import ManifestError, Plugins, new_plugin, validate
from atlas.plugins.model import parse_manifest, plugin_servers
from atlas.plugins.store import read_plugin
from atlas.secrets import Vault
from atlas.skills import Origin, SkillChangeError, SkillLibrary, SkillOperation, SkillUsage
from atlas.tools import Commands, Workspace
from fake_model import FakeCall, FakeModelServer, FakeReply
from model_support import Connect

MCP_SERVER = Path(__file__).parent.parent / "mcp_server.py"
CHAT = "/v1/chat/completions"

TOOLS = """
import asyncio
from atlas.plugins.sdk import PluginContext


def register(ctx: PluginContext) -> None:
    async def upper(arguments, host):
        host.progress("working")
        return str(arguments.get("text", "")).upper()

    def stopped():
        (ctx.data_folder / "stops").open("a").write("sync\\n")

    async def stopped_later():
        await asyncio.sleep(0)
        (ctx.data_folder / "stops").open("a").write("async\\n")

    ctx.register_tool(
        "echo_upper",
        "Repeat a text in capitals.",
        {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        upper,
        read_only=True,
    )
    ctx.on_unload(stopped)
    ctx.on_unload(stopped_later)
"""

SKILL = """---
name: shout
description: Say it loud.
---
Say everything in capitals.
"""


def _plugin(
    root: Path,
    name: str = "echo",
    *,
    atlas: dict[str, Any] | None = None,
    code: str | None = None,
    skills: dict[str, str] | None = None,
    mcp: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    folder = root / name
    (folder / ".claude-plugin").mkdir(parents=True)
    manifest = {"name": name, "version": "1.0.0", **(extra or {})}
    if atlas is not None:
        manifest["atlas"] = atlas
    (folder / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), "utf-8")
    if code is not None:
        (folder / "__init__.py").write_text(code, encoding="utf-8")
    for skill, text in (skills or {}).items():
        (folder / "skills" / skill).mkdir(parents=True)
        (folder / "skills" / skill / "SKILL.md").write_text(text, encoding="utf-8")
    if mcp is not None:
        (folder / ".mcp.json").write_text(json.dumps({"mcpServers": mcp}), encoding="utf-8")
    return folder


def _manifest(folder: Path) -> Any:
    found = read_plugin(folder, bundled=False)
    assert found.manifest is not None, found.problem
    return found.manifest


@pytest.fixture
def plugins(home: Home, vault: Vault, tmp_path: Path) -> Plugins:
    database = Database(home.database_file)
    migrate(database, SCHEMAS)
    return Plugins(home, database, vault, bundled_folder=tmp_path / "bundled")


def _on(plugins: Plugins, name: str = "echo") -> None:
    manifest = plugins.view(name).manifest
    assert manifest is not None
    plugins.enable(name, manifest.fingerprint())


# -- the manifest ----------------------------------------------------------------------------------


def test_skills_mcp_servers_and_tools_are_declared_and_consented_to(tmp_path: Path) -> None:
    folder = _plugin(
        tmp_path,
        atlas={"api": 14, "tools": ["echo_upper"], "secrets": ["ERP_TOKEN"]},
        code=TOOLS,
        skills={"shout": SKILL, "whisper": SKILL.replace("shout", "whisper")},
        mcp={"erp": {"command": "node", "args": ["${CLAUDE_PLUGIN_ROOT}/server.js"]}},
        extra={"mcpServers": {"api": {"type": "http", "url": "https://erp.example/mcp"}}},
    )
    (folder / "skills" / "not-a-skill").mkdir()
    manifest = _manifest(folder)
    assert manifest.skills == ("shout", "whisper")
    assert manifest.tools == ("echo_upper",)
    assert set(manifest.mcp_servers) == {"erp", "api"}
    assert manifest.declaration() == (
        "Runs its own Python code inside Atlas, with your user's permissions on this computer: "
        "there is no sandbox.",
        "Adds the tools echo_upper, which the model may call and its Python runs.",
        "Starts the MCP server erp (node ${CLAUDE_PLUGIN_ROOT}/server.js); the model may call its "
        "tools.",
        "Connects to the MCP server api at https://erp.example/mcp; the model may call its tools.",
        "Adds the skills echo:shout, echo:whisper: instructions the model may load and follow.",
        "Reads the secret ERP_TOKEN from the vault.",
    )

    # A server that runs another command, or a new skill, asks again.
    before = manifest.fingerprint()
    (folder / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"erp": {"command": "node", "args": ["other.js"]}}}), "utf-8"
    )
    assert _manifest(folder).fingerprint() != before
    (folder / "skills" / "third").mkdir()
    (folder / "skills" / "third" / "SKILL.md").write_text(SKILL, encoding="utf-8")
    assert _manifest(folder).skills == ("shout", "third", "whisper")


def test_the_manifest_may_name_its_servers_file_by_path_as_claude_code_reads_it(
    tmp_path: Path,
) -> None:
    # Cloudflare's plugin: `"mcpServers": "./.mcp.json"`, the file holding the address only.
    single = _plugin(
        tmp_path,
        "single",
        mcp={"cloud": {"type": "http", "url": "https://mcp.example.com/mcp"}},
        extra={"mcpServers": "./.mcp.json"},
    )
    assert set(_manifest(single).mcp_servers) == {"cloud"}

    # A list of a file elsewhere, holding the servers alone, and servers written in place.
    both = _plugin(
        tmp_path,
        "both",
        extra={
            "mcpServers": [
                "./servers/more.json",
                {"api": {"type": "http", "url": "https://api.example.com/mcp"}},
            ]
        },
    )
    (both / "servers").mkdir()
    (both / "servers" / "more.json").write_text(
        json.dumps({"docs": {"type": "http", "url": "https://docs.example.com/mcp"}}), "utf-8"
    )
    assert set(_manifest(both).mcp_servers) == {"docs", "api"}


@pytest.mark.parametrize(
    ("path", "problem"),
    [
        (".mcp.json", "must start with ./"),
        ("./../outside.json", "leads outside the plugin"),
        ("./missing.json", "is not there"),
    ],
)
def test_a_servers_file_the_manifest_names_is_inside_the_plugin_and_there(
    tmp_path: Path, path: str, problem: str
) -> None:
    (tmp_path / "outside.json").write_text("{}", encoding="utf-8")
    folder = _plugin(tmp_path / "plugins", extra={"mcpServers": path})
    found = read_plugin(folder, bundled=False)
    assert found.manifest is None
    assert found.problem is not None
    assert problem in found.problem


@pytest.mark.parametrize(
    ("atlas", "mcp", "problem"),
    [
        ({}, {"x": {"command": "x", "env": {"K": "${lower}"}}}, "neither a secret's name"),
        ({}, {"x": {"command": "x", "args": ["${user_config.k}"]}}, "user_config.k, which"),
        ({}, {"bad name!": {"command": "x"}}, "server named"),
        ({}, {"x": "node"}, "is not an object"),
        ({"tools": ["echo_upper"]}, None, "need atlas.api 14"),
        ({"api": 14, "tools": ["Bad-Name"]}, None, "list of names"),
    ],
)
def test_a_plugin_s_servers_read_only_what_it_declares(
    atlas: dict[str, Any], mcp: dict[str, Any] | None, problem: str
) -> None:
    text = json.dumps({"name": "echo", "atlas": atlas})
    mcp_text = None if mcp is None else json.dumps({"mcpServers": mcp})
    with pytest.raises(ManifestError, match=problem):
        parse_manifest(text, {"__init__.py"}, folder_name="echo", mcp_text=mcp_text)
    with pytest.raises(ManifestError, match=r"brings no __init__\.py"):
        parse_manifest(
            json.dumps({"name": "echo", "atlas": {"api": 14, "tools": ["t"]}}),
            set(),
            folder_name="echo",
        )


def test_a_secret_a_plugin_s_server_names_is_declared_and_shown(tmp_path: Path) -> None:
    """The MCP client fills a `${NAME}` from the whole vault, so the secret a server names is
    declared by being named (decided with the user on 20/10/2026, for Claude Code's GitHub
    plugin): shown at consent and in the fingerprint, so a plugin never reads a key unseen."""
    mcp = {
        "github": {
            "type": "http",
            "url": "https://api.githubcopilot.com/mcp/",
            "headers": {"Authorization": "Bearer ${GITHUB_PERSONAL_ACCESS_TOKEN}"},
        },
        "other": {"command": "x", "env": {"K": "${ANTHROPIC_API_KEY:-none}"}},
    }
    manifest = parse_manifest(
        json.dumps({"name": "github"}), set(), folder_name="github", mcp_text=json.dumps(mcp)
    )
    assert [s.name for s in manifest.secrets] == [
        "GITHUB_PERSONAL_ACCESS_TOKEN",
        "ANTHROPIC_API_KEY",
    ]
    assert manifest.secrets[0].description == "Read by its MCP server github."
    shown = manifest.declaration()
    assert "Reads the secret GITHUB_PERSONAL_ACCESS_TOKEN from the vault." in shown
    assert "Reads the secret ANTHROPIC_API_KEY from the vault." in shown
    alone = parse_manifest(
        json.dumps({"name": "github"}),
        set(),
        folder_name="github",
        mcp_text=json.dumps({"github": mcp["github"]}),
    )
    assert alone.fingerprint() != manifest.fingerprint(), "a new key asks again"


def test_a_claude_code_plugin_reads_as_claude_code_reads_it(tmp_path: Path) -> None:
    """Two shapes of Claude Code's official plugins (20/10/2026): no manifest, the plugin named
    after its folder (`receipts`), and a `.mcp.json` of servers with no `mcpServers` around them
    (`github`)."""
    bare = tmp_path / "receipts"
    (bare / "skills" / "receipts").mkdir(parents=True)
    (bare / "skills" / "receipts" / "SKILL.md").write_text(SKILL, encoding="utf-8")
    found = read_plugin(bare, bundled=False)
    assert found.manifest is not None, found.problem
    assert (found.manifest.name, found.manifest.skills) == ("receipts", ("receipts",))
    empty = tmp_path / "pyright-lsp"
    empty.mkdir()
    (empty / "README.md").write_text("An LSP server.", encoding="utf-8")
    assert "nothing a plugin brings" in str(read_plugin(empty, bundled=False).problem)

    flat = parse_manifest(
        json.dumps({"name": "docs", "atlas": {"secrets": ["DOCS_TOKEN"]}}),
        {".mcp.json"},
        folder_name="docs",
        mcp_text=json.dumps({"docs": {"type": "http", "url": "https://d/mcp?k=${DOCS_TOKEN}"}}),
    )
    assert list(flat.mcp_servers) == ["docs"]


def test_a_plugin_s_servers_are_named_for_it_with_its_folders_in_place() -> None:
    manifest = parse_manifest(
        json.dumps({"name": "video", "atlas": {"secrets": ["TOKEN"]}}),
        {".mcp.json"},
        folder_name="video",
        mcp_text=json.dumps(
            {
                "mcpServers": {
                    "studio": {
                        "command": "node",
                        "args": ["${CLAUDE_PLUGIN_ROOT}/s.js", "--cache=${CLAUDE_PLUGIN_DATA}"],
                        "env": {"TOKEN": "${TOKEN}"},
                    }
                }
            }
        ),
    )
    assert plugin_servers(manifest, "C:/p/video", "C:/h/plugin-data/video") == {
        "mcpServers": {
            "plugin_video_studio": {
                "command": "node",
                "args": ["C:/p/video/s.js", "--cache=C:/h/plugin-data/video"],
                # The secret stays for the MCP client to fill from the vault.
                "env": {"TOKEN": "${TOKEN}"},
                "trust": "untrusted",
            }
        }
    }


def test_a_plugin_s_server_asks_before_a_call_that_may_write_unless_it_says_full_trust() -> None:
    manifest = parse_manifest(
        json.dumps({"name": "cloud"}),
        {".mcp.json"},
        folder_name="cloud",
        mcp_text=json.dumps(
            {
                "mcpServers": {
                    "api": {"type": "http", "url": "https://mcp.example.com/mcp"},
                    "docs": {
                        "type": "http",
                        "url": "https://docs.example.com/mcp",
                        "trust": "full",
                    },
                }
            }
        ),
    )
    servers = parse_config(json.dumps(plugin_servers(manifest, "/p", "/d")), "a plugin's")
    assert {server.name: server.trusted for server in servers} == {
        "plugin_cloud_api": False,
        "plugin_cloud_docs": True,
    }


def test_claude_code_s_user_config_is_kept_as_settings_and_secrets(
    plugins: Plugins, home: Home, vault: Vault
) -> None:
    """A sensitive option goes to the vault under the plugin's name, the others are the plugin's
    settings; an MCP server reads both by `${user_config.KEY}`, the secret left for the client
    to fill from the vault."""
    _plugin(
        home.plugins_folder,
        "erp",
        mcp={
            "erp": {
                "type": "http",
                "url": "${user_config.endpoint}/mcp",
                "headers": {"Authorization": "Bearer ${user_config.apiToken}"},
            }
        },
        extra={
            "userConfig": {
                "endpoint": {
                    "type": "string",
                    "title": "Endpoint",
                    "description": "The ERP's address",
                    "default": "https://erp.example",
                },
                "apiToken": {
                    "type": "string",
                    "title": "API token",
                    "description": "From the ERP's settings",
                    "sensitive": True,
                },
                "verbose": {
                    "type": "boolean",
                    "title": "Verbose",
                    "description": "",
                    "default": False,
                },
            }
        },
    )
    view = plugins.view("erp")
    manifest = view.manifest
    assert manifest is not None
    assert manifest.options == {"endpoint": "", "apiToken": "ERP_API_TOKEN", "verbose": ""}
    assert [s.name for s in manifest.secrets] == ["ERP_API_TOKEN"]
    assert view.settings == {"endpoint": "https://erp.example", "verbose": "false"}
    assert "Reads the secret ERP_API_TOKEN from the vault." in manifest.declaration()

    _on(plugins, "erp")
    plugins.set_setting("erp", "endpoint", "https://my.erp/${ANTHROPIC_API_KEY}")
    vault.set("ERP_API_TOKEN", "t0k")
    server = plugins.mcp_config()["mcpServers"]["plugin_erp_erp"]
    # A value never names a secret for the MCP client to fill: its `${` is taken out.
    assert server["url"] == "https://my.erp/$ANTHROPIC_API_KEY}/mcp"
    assert server["headers"] == {"Authorization": "Bearer ${ERP_API_TOKEN}"}


@pytest.mark.parametrize(
    ("config", "problem"),
    [
        ({"k": {"type": "string", "title": "K", "description": "", "color": "red"}}, "color"),
        ({"k": {"type": "date", "title": "K", "description": ""}}, "type is one of"),
        ({"1k": {"type": "string", "title": "K", "description": ""}}, "not a digit first"),
        (
            {"k": {"type": "string", "title": "K", "description": "", "default": "${OTHER}"}},
            "may not name another value",
        ),
        ([], "not an object of options"),
    ],
)
def test_a_user_config_claude_code_refuses_is_refused(config: object, problem: str) -> None:
    with pytest.raises(ManifestError, match=problem):
        parse_manifest(json.dumps({"name": "erp", "userConfig": config}), set(), folder_name="erp")


# -- tools and what stops ------------------------------------------------------------------------


def test_an_enabled_plugin_s_tools_load_run_and_stop_with_it(plugins: Plugins, home: Home) -> None:
    _plugin(home.plugins_folder, atlas={"api": 14, "tools": ["echo_upper"]}, code=TOOLS)
    assert asyncio.run(plugins.tools()) == []  # off: never imported
    _on(plugins)

    class Host:
        def __init__(self) -> None:
            self.said: list[str] = []

        def progress(self, text: str) -> None:
            self.said.append(text)

        @property
        def conversation(self) -> str:
            return "c-1"

        async def send_file(self, path: Path) -> str:  # pragma: no cover - this tool sends none
            return str(path)

    async def scenario() -> tuple[str, list[str], str]:
        [tool] = await plugins.tools()
        assert (tool.plugin, tool.name, tool.read_only) == ("echo", "echo_upper", True)
        host = Host()
        result = await tool.run({"text": "hi"}, host)
        await plugins.tools()  # loaded again: what the first load started stops first
        stops = (home.plugin_data_folder / "echo" / "stops").read_text()
        await plugins.stop("echo")
        return result, host.said, stops

    result, said, stops = asyncio.run(scenario())
    assert (result, said) == ("HI", ["working"])
    assert stops == "sync\nasync\n"
    assert (home.plugin_data_folder / "echo" / "stops").read_text() == "sync\nasync\n" * 2


@pytest.mark.parametrize(
    ("call", "problem"),
    [
        ("ctx.register_tool('other', 'x', {'type': 'object'}, f)", "the tool other, which it"),
        ("ctx.register_tool('echo_upper', ' ', {'type': 'object'}, f)", "with no description"),
        ("ctx.register_tool('echo_upper', 'x', {'type': 'string'}, f)", "not a JSON Schema"),
        ("ctx.register_tool('echo_upper', 'x', {'type': 'object'}, 3)", "nothing to run"),
        ("ctx.on_unload(3)", "a stop that cannot be called"),
    ],
)
def test_a_tool_or_a_stop_it_does_not_declare_or_cannot_run_marks_the_plugin(
    plugins: Plugins, home: Home, call: str, problem: str
) -> None:
    code = f"async def f(arguments, host):\n    return ''\n\n\ndef register(ctx):\n    {call}\n"
    _plugin(home.plugins_folder, atlas={"api": 14, "tools": ["echo_upper"]}, code=code)
    _on(plugins)
    assert asyncio.run(plugins.tools()) == []
    assert problem in str(plugins.view("echo").problem)


def test_a_stop_that_fails_is_logged_and_the_others_still_run(
    plugins: Plugins, home: Home, caplog: pytest.LogCaptureFixture
) -> None:
    code = TOOLS.replace(
        "    ctx.on_unload(stopped)\n",
        "    ctx.on_unload(lambda: 1 / 0)\n    ctx.on_unload(stopped)\n",
    )
    _plugin(home.plugins_folder, atlas={"api": 14, "tools": ["echo_upper"]}, code=code)
    _on(plugins)

    async def scenario() -> None:
        await plugins.tools()
        await plugins.stop()

    asyncio.run(scenario())
    assert (home.plugin_data_folder / "echo" / "stops").read_text() == "sync\nasync\n"
    assert "A plugin's stop failed." in caplog.text


# -- skills --------------------------------------------------------------------------------------


def test_a_plugin_s_skills_are_listed_after_its_name_and_read_only(
    plugins: Plugins, home: Home
) -> None:
    _plugin(home.plugins_folder, skills={"shout": SKILL})
    assert plugins.skill_folders() == {}, "off: its skills are not listed"
    _on(plugins)
    folders = plugins.skill_folders()
    assert folders == {"echo": home.plugins_folder / "echo" / "skills"}

    usage = SkillUsage(Database(home.database_file))
    library = SkillLibrary(home.skills_folder, usage)
    library.apply([SkillOperation("create", "shout", content=SKILL)], Origin.USER)
    library.plugin_skills = folders
    names = sorted(skill.name for skill in library.catalog.skills())
    assert names == ["echo:shout", "shout"], "the user's and the plugin's, each by its name"
    loaded = library.catalog.get("echo:shout")
    assert loaded is not None
    assert loaded.folder == home.plugins_folder / "echo" / "skills" / "shout"

    with pytest.raises(SkillChangeError, match="a plugin's skill, which only its plugin"):
        library.apply(
            [SkillOperation("patch", "echo:shout", old_string="loud", new_string="soft")],
            Origin.USER,
        )
    usage.used("echo:shout", datetime.now(UTC))
    assert usage.record("echo:shout") is None, "no row: the curator never archives it"


def test_a_plugin_s_commands_are_skills_named_after_it(plugins: Plugins, home: Home) -> None:
    """Claude Code's `commands/<name>.md`, read as skills as Claude Code reads them now: named by
    the file, the frontmatter optional, a skill of the plugin winning a name both have."""
    folder = _plugin(home.plugins_folder, skills={"shout": SKILL})
    commands = folder / "commands"
    commands.mkdir()
    (commands / "review.md").write_text(
        "---\ndescription: Review the diff.\nargument-hint: <file>\n---\nReview $ARGUMENTS.\n",
        encoding="utf-8",
    )
    (commands / "plain.md").write_text("# Say hello\nSay hello.\n", encoding="utf-8")
    (commands / "shout.md").write_text("A command the skill shout hides.\n", encoding="utf-8")
    (commands / "notes.txt").write_text("not a command", encoding="utf-8")
    manifest = _manifest(folder)
    assert manifest.commands == ("plain", "review", "shout")
    assert manifest.declaration()[-1] == (
        "Adds the commands /echo:plain, /echo:review, /echo:shout: instructions you or the "
        "model may run."
    )
    assert plugins.command_folders() == {}, "off: its commands are not listed"
    _on(plugins)
    library = SkillLibrary(home.skills_folder)
    library.plugin_skills = plugins.skill_folders()
    library.plugin_commands = plugins.command_folders()
    found = {skill.name: skill for skill in library.catalog.skills()}
    assert sorted(found) == ["echo:plain", "echo:review", "echo:shout"]
    assert found["echo:review"].description == "Review the diff."
    assert found["echo:review"].command
    assert found["echo:plain"].description == "Say hello"
    assert not found["echo:shout"].command, "the skill wins the name"
    assert library.catalog.get("echo:review") == found["echo:review"]


def test_a_plugin_s_agents_are_consented_to_and_handed_out_while_it_is_on(
    plugins: Plugins, home: Home
) -> None:
    folder = _plugin(home.plugins_folder)
    (folder / "agents").mkdir()
    agent = folder / "agents" / "editor.md"
    agent.write_text("---\ndescription: Edits.\n---\nEdit.\n", encoding="utf-8")
    manifest = _manifest(folder)
    assert manifest.agents == ("editor",)
    assert manifest.declaration()[-1] == (
        "Adds the subagents echo:editor: helpers with their own instructions, which the model "
        "may hand a task to."
    )
    before = manifest.fingerprint()
    (folder / "agents" / "critic.md").write_text("---\ndescription: C.\n---\nC.\n", "utf-8")
    assert _manifest(folder).fingerprint() != before, "a new agent asks again"
    assert plugins.agent_files() == {}
    _on(plugins)
    assert plugins.agent_files() == {"echo": [folder / "agents" / "critic.md", agent]}


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("Edit.", "no description"),
        ("---\ndescription: d\n---\n", "no instructions"),
        ("---\nname: bad name\ndescription: d\n---\nx", "use letters"),
        ("---\ndescription: d\n", "never closes"),
        ("---\n- a\n---\nx", "not a table"),
    ],
)
def test_an_agent_atlas_cannot_read_says_why(text: str, problem: str) -> None:
    with pytest.raises(DelegationError, match=problem):
        parse_agent(text, "echo", "editor")


def test_an_agent_is_read_as_claude_code_writes_it() -> None:
    agent = parse_agent(
        "---\nname: cutter\ndescription: Cuts\n  scenes.\ntools: [Read, Bash]\n"
        "model: openrouter/anthropic/claude-sonnet-5-5\n---\nCut.\n",
        "video",
        "editor",
    )
    assert (agent.name, agent.description, agent.prompt) == ("video:cutter", "Cuts scenes.", "Cut.")
    assert (agent.tools, agent.model) == (
        ("Read", "Bash"),
        "openrouter/anthropic/claude-sonnet-5-5",
    )
    # Claude Code's aliases name no provider of Atlas's: the parent's model.
    assert parse_agent("---\ndescription: d\nmodel: sonnet\n---\nx", "v", "e").model is None


def test_a_plugin_s_programs_are_on_the_path_of_the_model_s_commands(
    plugins: Plugins, home: Home, tmp_path: Path
) -> None:
    """Claude Code's `bin/`: while the plugin is on, the model runs its programs by name. A
    program for each shell: PowerShell finds `.cmd` by its name, bash a file it may run."""
    folder = _plugin(home.plugins_folder)
    (folder / "bin").mkdir()
    (folder / "bin" / "echo-hello.cmd").write_text("@echo hello from echo\r\n", encoding="utf-8")
    script = folder / "bin" / "echo-hello"
    script.write_text("#!/bin/sh\necho hello from echo\n", encoding="utf-8")
    script.chmod(0o755)
    manifest = _manifest(folder)
    assert manifest.programs == ("echo-hello", "echo-hello.cmd")
    assert manifest.declaration()[-1] == (
        "Puts the programs echo-hello, echo-hello.cmd on the PATH of the commands the model "
        "runs: running one runs the plugin's own code."
    )
    assert plugins.program_folders() == [], "off: not on the PATH"
    _on(plugins)
    assert plugins.program_folders() == [folder / "bin"]

    commands = Commands(Workspace(tmp_path), tmp_path / "commands", path=plugins.program_folders)
    output, code = asyncio.run(commands.execute("echo-hello", 60))
    assert (output.strip(), code) == ("hello from echo", 0)


# -- MCP -----------------------------------------------------------------------------------------


def test_a_plugin_s_mcp_server_connects_when_it_is_on_and_leaves_when_it_is_off(
    plugins: Plugins, home: Home
) -> None:
    folder = _plugin(
        home.plugins_folder,
        mcp={"notes": {"command": sys.executable, "args": ["${CLAUDE_PLUGIN_ROOT}/server.py"]}},
    )
    shutil.copy(MCP_SERVER, folder / "server.py")
    # The user's own server of the same name stays the user's.
    home.mcp_file.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

    async def scenario() -> tuple[bool, bool, bool, str]:
        toolset = Toolset()
        servers: dict[str, Any] = {"mcpServers": {}}
        client = McpClient(home.mcp_file, {}.get, toolset, plugins=lambda: servers)
        await client.start()
        before = toolset.get("mcp__plugin_echo_notes__list_notes") is not None
        _on(plugins)
        servers.update(plugins.mcp_config())
        await client.reload_plugins()
        on = toolset.get("mcp__plugin_echo_notes__list_notes")
        assert on is not None
        said = await on.run({})
        plugins.disable("echo")
        servers.update(plugins.mcp_config())
        await client.reload_plugins()
        after = toolset.get("mcp__plugin_echo_notes__list_notes") is not None
        await client.close()
        return before, on.source == "mcp:plugin_echo_notes", after, str(said)

    before, sourced, after, said = asyncio.run(scenario())
    assert (before, sourced, after) == (False, True, False)
    assert "no notes" in said


# -- in a conversation ---------------------------------------------------------------------------


def test_the_model_finds_and_calls_a_plugin_s_tool_and_lists_its_skills(
    plugins: Plugins,
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    tmp_path: Path,
) -> None:
    _plugin(
        home.plugins_folder,
        atlas={"api": 14, "tools": ["echo_upper"]},
        code=TOOLS,
        skills={"shout": SKILL},
    )
    _on(plugins)
    chosen = ModelSettings(provider="openrouter")
    models = connect(
        fake, "openrouter", settings=chosen, ledger=CallLedger(Database(home.database_file))
    )
    work = tmp_path / "work"
    work.mkdir()
    settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(work))
    assistant = Assistant(home, settings, models, vault, listener=lambda c, e: None)
    fake.reply(FakeReply(calls=[FakeCall(TOOL_SEARCH, '{"query": "capitals"}', id="s1")]))
    fake.reply(FakeReply(calls=[FakeCall("echo_upper", '{"text": "hello"}', id="c1")]))
    fake.reply(FakeReply(text="Done."))

    async def scenario() -> None:
        tools = await plugins.tools()
        await assistant.set_plugins(tools, plugins.skill_folders(), plugins.mcp_config())
        conversation = assistant.conversation(assistant.conversations.create().id)
        conversation.send("Shout hello")
        await conversation.wait()
        await assistant.close()
        await plugins.stop()

    asyncio.run(scenario())
    bodies = fake.bodies(CHAT)
    first = {tool["function"]["name"]: tool for tool in bodies[0]["tools"]}
    assert "echo_upper" not in first, "behind the tool search, as an MCP server's"
    searched = first[TOOL_SEARCH]["function"]["description"]
    assert "echo (1 tools): Tools the plugin echo adds." in searched
    assert "- echo_upper: Repeat a text in capitals." in searched
    assert "echo:shout" in first["use_skill"]["function"]["description"]
    results = [m for m in bodies[2]["messages"] if m.get("role") == "tool"]
    assert results[-1]["content"] == "HELLO"


# -- writing a plugin ----------------------------------------------------------------------------


def test_a_new_plugin_validates_and_its_tool_is_registered(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = new_plugin(tmp_path, "my-plugin")
    found = validate(folder)
    assert found.ok, found.problems
    assert found.registered == ("tool my_plugin_hello",)
    assert found.manifest is not None
    assert found.manifest.skills == ("my-plugin",)
    with pytest.raises(ValueError, match="already there"):
        new_plugin(tmp_path, "my-plugin")
    with pytest.raises(ValueError, match="kebab-case"):
        new_plugin(tmp_path, "My Plugin")

    assert main(["plugins", "validate", str(folder)], {"ATLAS_HOME": str(tmp_path / "h")}) == 0
    out = capsys.readouterr().out
    assert "registered tool my_plugin_hello" in out
    assert out.endswith("It loads as Atlas loads it.\n")


def test_new_makes_the_plugin_in_the_home_where_atlas_finds_it_off(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    assert main(["plugins", "new", "hello"], {"ATLAS_HOME": str(home)}) == 0
    assert "atlas plugins validate" in capsys.readouterr().out
    assert main(["plugins", "list"], {"ATLAS_HOME": str(home)}) == 0
    assert "hello 0.1.0  off" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("code", "problem"),
    [
        ("def register(ctx):\n    pass\n", "does not register them"),
        ("def register(ctx):\n    raise RuntimeError('boom')\n", "RuntimeError: boom"),
        ("x = (", "SyntaxError"),
    ],
)
def test_validate_says_what_is_wrong_with_a_plugin(
    tmp_path: Path, code: str, problem: str, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = _plugin(tmp_path, atlas={"api": 14, "tools": ["echo_upper"]}, code=code)
    found = validate(folder)
    assert not found.ok
    assert problem in " ".join(found.problems)
    assert main(["plugins", "validate", str(folder)], {"ATLAS_HOME": str(tmp_path / "h")}) == 1
    assert capsys.readouterr().out.endswith("It does not load.\n")
    assert not validate(tmp_path / "nowhere").ok
    agented = _plugin(tmp_path, "agented", extra={"agents": ["./a.md"]})
    assert "agents, which Atlas does not load yet" in validate(agented).problems[0]
