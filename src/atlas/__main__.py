"""Command line entry point: `atlas serve`, `atlas server`, `atlas secrets`, `atlas models`,
`atlas usage`, `atlas skills`, `atlas memory`, `atlas backup`, `atlas restore`, `atlas home`,
`atlas routines`, `atlas always-on`, `atlas desktop`, `atlas update`, `atlas docs` and
`atlas version`.

This is a process edge: it reads the environment, passes it to the settings loader once, and hands
the settings to whoever needs them.
"""

import argparse
import asyncio
import getpass
import importlib
import importlib.util
import inspect
import io
import json
import logging
import os
import secrets
import shutil
import socket
import sys
import tempfile
import threading
import time
import webbrowser
from collections.abc import Awaitable, Callable, Generator, Mapping, MutableMapping, Sequence
from contextlib import contextmanager, suppress
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, TextIO, cast

import uvicorn
from starlette.types import ASGIApp

from atlas import __version__
from atlas.bots import BotError, Bots
from atlas.conversations import SCHEMA as CONVERSATIONS_SCHEMA
from atlas.conversations import ConversationStore
from atlas.docs import (
    DocsError,
    find,
    read_pages,
    render_all,
    render_index,
    render_page,
    render_search,
    search,
)
from atlas.home import (
    BACKUP_PREFIX,
    DEFAULT_HOME,
    DEFAULT_HOST,
    DEFAULT_PORT,
    KEEP,
    LOG_FILE,
    Database,
    Home,
    HomeBusyError,
    HomeError,
    HomeNameError,
    LoadedSettings,
    Redactor,
    Schema,
    Settings,
    backup_name,
    check_backup,
    choose_home,
    configure_logging,
    create_home,
    delete_home,
    drop_section,
    forget_secret_variables,
    format_utc,
    has_data,
    home_name,
    home_path,
    home_root,
    is_served,
    list_homes,
    load_settings,
    migrate,
    open_home,
    parse_utc,
    pre_migration_backup,
    prune_backups,
    rename_home,
    resolve_home,
    restore_backup,
    serving,
    time_zone,
    use_home,
    utc_now,
    write_backup,
)
from atlas.installation import (
    COMPOSE_UPDATE_ELSEWHERE,
    Checkout,
    InstallationError,
    Runner,
    Toolchain,
    build_desktop,
    host_channel,
    installation_kind,
    launch,
    update,
    write_launcher,
)
from atlas.instances import (
    AlwaysOnHome,
    AlwaysOnServices,
    InstancesError,
    Tailnet,
    encode_code,
    read_always_on,
    tailnet,
)
from atlas.jobs import SCHEMA as JOBS_SCHEMA
from atlas.jobs import JobsError, Routine, Routines, RoutineStore
from atlas.marketplace import (
    Kind,
    Marketplace,
    MarketplaceError,
    MarketplaceStore,
    Row,
    State,
    shipped_names,
)
from atlas.marketplace import check as check_marketplace
from atlas.memory import SCHEMA as MEMORY_SCHEMA
from atlas.memory import Action, MemoryFiles, Operation, Proposals, ProposalStore
from atlas.models import SCHEMA as MODELS_SCHEMA
from atlas.models import (
    CallLedger,
    CostSource,
    KeyCheck,
    Models,
    ModelsError,
    UsageLine,
    load_catalog,
    usage_report,
)
from atlas.plugins import SCHEMA as PLUGINS_SCHEMA
from atlas.plugins import (
    PluginError,
    Plugins,
    install_plugin,
    new_plugin,
    remove_plugin,
    update_plugin,
    validate,
)
from atlas.secrets import MCP_SIGN_IN_FILE, SecretsError, Vault
from atlas.server import (
    EXIT_CEILING_SECONDS,
    KEPT_TOKEN,
    POLL_SECONDS,
    READY_LINE,
    Gateway,
    Parent,
    create_app,
    is_loopback,
    kept_token,
)
from atlas.skills import SCHEMA as SKILLS_SCHEMA
from atlas.skills import (
    Origin,
    SkillChangeError,
    SkillLibrary,
    SkillUsage,
    changed,
    read_manifest,
    reset_bundled,
    sync_bundled,
)
from atlas.skills import share as share_skill
from atlas.trust import SCHEMA as TRUST_SCHEMA

__all__ = ["DEFAULT_HOST", "DEFAULT_PORT", "SCHEMAS", "build_parser", "listen_address", "main"]

#: Every subject that owns tables, in the order its migrations run. A subject adds its `Schema`
#: here when it gets its first table.
SCHEMAS: tuple[Schema, ...] = (
    MODELS_SCHEMA,
    CONVERSATIONS_SCHEMA,
    SKILLS_SCHEMA,
    TRUST_SCHEMA,
    MEMORY_SCHEMA,
    JOBS_SCHEMA,
    PLUGINS_SCHEMA,
)

_logger = logging.getLogger("atlas.cli")
#: The plugin that drives Claude Code, which took its program from `[executors]` (step 16c).
_CLAUDE_CODE = "claude-code"


def build_parser() -> argparse.ArgumentParser:
    """Describe the commands and their options."""
    parser = argparse.ArgumentParser(prog="atlas", description="Atlas personal agent.")
    parser.add_argument(
        "--home",
        metavar="NAME",
        help="Run in this home: `default`, or one from `atlas home list`. Default: the one "
        "`atlas home use` chose.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("version", help="Print the installed version.")

    docs = commands.add_parser(
        "docs",
        help="Read the documentation of this Atlas: the list of pages, one page, a search, or "
        "every page. It opens no home.",
    )
    docs.add_argument(
        "page",
        nargs="?",
        help="A page from the list, such as install or guides/memory; with --search, the search "
        "stays in it.",
    )
    reading = docs.add_mutually_exclusive_group()
    reading.add_argument(
        "--search", nargs="+", metavar="WORD", help="The sections that mention every word."
    )
    reading.add_argument("--all", action="store_true", help="Every page, one after another.")

    serve = commands.add_parser("serve", help="Run the server that clients connect to.")
    serve.add_argument(
        "--host",
        help=(
            f"Address to listen on (default {DEFAULT_HOST}, or ATLAS_SERVER_HOST). "
            "Use 0.0.0.0 only inside a container or behind a firewall."
        ),
    )
    serve.add_argument(
        "--port",
        type=int,
        help=(
            f"Port to listen on (default {DEFAULT_PORT}, or ATLAS_SERVER_PORT). 0 lets the system "
            f"choose one; the server then writes `{READY_LINE}<port>` on standard output."
        ),
    )
    serve.add_argument(
        "--parent-pid",
        type=int,
        help="Stop when this process is gone: the app that started the server passes its own.",
    )
    serve.add_argument(
        "--always-on",
        metavar="HOME_FOLDER",
        help="Serve this home as its always-on service does: on its kept port, with the token the "
        "vault keeps, and on the tailnet when it is open. Written by `atlas always-on on`.",
    )

    server = commands.add_parser("server", help="What clients need to reach the server.")
    server_actions = server.add_subparsers(dest="action", required=True)
    token = server_actions.add_parser(
        "token",
        help="Print the token a server off loopback (a container, an always-on home) keeps in the "
        "vault, making it the first time. Give it to the app as ATLAS_DESKTOP_TOKEN.",
    )
    token.add_argument(
        "--new",
        action="store_true",
        help="Replace it: every connection code given before stops working. An always-on home's "
        "service starts again on the new one.",
    )
    code = server_actions.add_parser(
        "code",
        help="Print the connection code another machine's app opens this home with. A secret: it "
        "holds the token.",
    )
    code.add_argument(
        "--port",
        type=int,
        help=f"The port clients reach, when the home is not always on (a container: default "
        f"{DEFAULT_PORT}, or ATLAS_SERVER_PORT).",
    )

    always_on = commands.add_parser(
        "always-on",
        help="Run the home's server as this machine's service, outliving the app and starting "
        "with the computer; open it to the user's machines over Tailscale.",
    )
    always_on_actions = always_on.add_subparsers(dest="action", required=True)
    always_on_actions.add_parser("on", help="Write the service and start it.")
    always_on_actions.add_parser("off", help="Stop the service and remove it.")
    status = always_on_actions.add_parser(
        "status", help="Whether the home is always on and open, and where it listens."
    )
    status.add_argument("--json", action="store_true", help="As JSON, for the app.")
    always_on_actions.add_parser(
        "start",
        help="Start the service when its server does not answer, and wait for it: what the app "
        "runs before it attaches.",
    )
    always_on_actions.add_parser(
        "open",
        help="Also listen on this machine's Tailscale address, for the user's other machines.",
    )
    always_on_actions.add_parser("close", help="Listen on this machine only again.")

    secrets = commands.add_parser("secrets", help="Keep API keys and tokens in the vault.")
    actions = secrets.add_subparsers(dest="action", required=True)
    store = actions.add_parser(
        "set", help="Store a secret. The value is read from standard input, never from arguments."
    )
    store.add_argument("name", help="Such as OPENAI_API_KEY.")
    actions.add_parser("list", help="List the secrets' names and when each was set. No values.")
    remove = actions.add_parser("delete", help="Remove a secret.")
    remove.add_argument("name")
    actions.add_parser("check", help="Decrypt every secret and report the ones that fail.")

    mcp = commands.add_parser("mcp", help="Sign in to the MCP servers of mcp.json that use OAuth.")
    mcp_actions = mcp.add_subparsers(dest="action", required=True)
    login = mcp_actions.add_parser(
        "login",
        help="Sign in to a server: its page opens in the browser, where you authorize Atlas, and "
        "the sign-in is kept and renewed.",
    )
    login.add_argument("name", help="The server's name in mcp.json.")
    login.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the page's address without opening it, for a browser on another machine.",
    )
    logout = mcp_actions.add_parser("logout", help="Forget a server's sign-in.")
    logout.add_argument("name", help="The server's name in mcp.json.")

    models = commands.add_parser("models", help="The model providers and their keys.")
    model_actions = models.add_subparsers(dest="action", required=True)
    check = model_actions.add_parser(
        "check", help="Ask each provider whether its key opens it. Spends no tokens."
    )
    check.add_argument(
        "providers",
        nargs="*",
        help="Such as openai or gemini. Default: every provider with a key in the vault, and the "
        "one the settings chose.",
    )

    usage = commands.add_parser(
        "usage", help="What the model calls spent, by day, model and purpose."
    )
    usage.add_argument(
        "--days", type=int, default=30, help="How many days back, today included (default 30)."
    )

    routines = commands.add_parser(
        "routines", help="Scheduled prompts and commands the server runs."
    )
    routine_actions = routines.add_subparsers(dest="action", required=True)
    routine_actions.add_parser("list", help="The routines, their schedules and their last run.")
    make = routine_actions.add_parser(
        "create", help="Make a routine that runs a prompt, or a command, on a schedule."
    )
    make.add_argument("name")
    make.add_argument(
        "--schedule",
        required=True,
        help="every 30m, in 2h, every monday 9am, weekdays at 9am, 0 9 * * *, or a time such as "
        "2026-10-20T14:00.",
    )
    what = make.add_mutually_exclusive_group(required=True)
    what.add_argument("--prompt", help="What the agent does, standing alone.")
    what.add_argument(
        "--command",
        dest="shell_command",  # `command` holds the subcommand's name
        help="A command in the system's shell; its output is what you get.",
    )
    make.add_argument(
        "--model", help="Pin a model, provider/model (openai/gpt-6-sol); unset, the settings'."
    )
    make.add_argument(
        "--bot",
        metavar="NAME",
        help="The bot the prompt runs as; its result enters the bot's fixed conversation.",
    )
    for name, text in (
        ("pause", "Stop a routine firing until resumed."),
        ("resume", "Fire it again; a slot that came due while paused runs once."),
        ("run", "Run it at the server's next minute, ahead of its schedule."),
        ("remove", "Delete a routine and its runs."),
        ("runs", "A routine's runs, the newest first."),
    ):
        acting = routine_actions.add_parser(name, help=text)
        acting.add_argument("routine", help="Its id or name.")

    skills = commands.add_parser(
        "skills", help="The skills and who made each; undo what was done to one."
    )
    skill_actions = skills.add_subparsers(dest="action", required=True)
    skill_actions.add_parser(
        "list", help="Every skill: the library's and each bot's, who made it, and its uses."
    )
    share = skill_actions.add_parser(
        "share", help="Move a bot's skill to the home's library, for every agent."
    )
    share.add_argument("name")
    revert = skill_actions.add_parser(
        "revert", help="Put a skill back as it was before its last change (or a named version)."
    )
    revert.add_argument("name")
    revert.add_argument("--version", help="A version from `atlas skills versions NAME`.")
    versions = skill_actions.add_parser("versions", help="List a skill's kept versions.")
    versions.add_argument("name")
    restore = skill_actions.add_parser("restore", help="Bring an archived skill back.")
    restore.add_argument("name")
    reset = skill_actions.add_parser(
        "reset",
        help="Put a skill Atlas ships back as it ships it, so it is updated again; what the "
        "copy held is kept as a version.",
    )
    reset.add_argument("name")
    for acting in (share, revert, versions, restore):
        acting.add_argument(
            "--bot", metavar="NAME", help="The bot whose skill it is; unset, the library's."
        )

    memory = commands.add_parser(
        "memory", help="The changes to memory the background review proposed."
    )
    memory_actions = memory.add_subparsers(dest="action", required=True)
    memory_actions.add_parser(
        "proposals", help="List them, each with what it would change and whether it still can."
    )
    approve = memory_actions.add_parser(
        "approve", help="Apply one, if the entries it would change are as they were."
    )
    approve.add_argument("id")
    discard = memory_actions.add_parser("discard", help="Drop one without applying it.")
    discard.add_argument("id")

    backup = commands.add_parser(
        "backup", help="Write the home's backup: one zip of the user's data, never the vault."
    )
    backup.add_argument(
        "-o",
        "--output",
        help="A file, a folder, or - for standard output. Default: your home folder, keeping the "
        f"last {KEEP} of this home.",
    )
    backup.add_argument(
        "--keep",
        type=int,
        default=KEEP,
        help=f"Backups of this home kept in the folder (default {KEEP}; 0 keeps all). A file you "
        "named is never removed.",
    )
    restore_command = commands.add_parser(
        "restore", help="Put a backup back over the home, after checking all of it."
    )
    restore_command.add_argument("file", help="The backup's zip, or - for standard input.")
    restore_command.add_argument(
        "-y", "--yes", action="store_true", help="Do not ask before overwriting a home with data."
    )

    homes = commands.add_parser("home", help="The homes of this machine.")
    home_actions = homes.add_subparsers(dest="action", required=True)
    home_actions.add_parser("list", help="List the homes; * marks the one that opens by default.")
    create = home_actions.add_parser("create", help="Make a new home, empty or cloned.")
    create.add_argument("name")
    create.add_argument(
        "--clone",
        action="store_true",
        help="Copy the settings, models, MCP servers, keys, profile, memory and skills of the "
        "current home (or --from). Conversations start empty.",
    )
    create.add_argument("--from", dest="source", metavar="NAME", help="The home to clone.")
    use = home_actions.add_parser("use", help="Open this home when none is named.")
    use.add_argument("name")
    show = home_actions.add_parser("show", help="Where a home is, and what it holds.")
    show.add_argument("name", nargs="?")
    rename = home_actions.add_parser("rename", help="Rename a home no server is serving.")
    rename.add_argument("old")
    rename.add_argument("new")
    delete = home_actions.add_parser("delete", help="Delete a home and everything in it.")
    delete.add_argument("name")
    delete.add_argument("-y", "--yes", action="store_true", help="Do not ask first.")

    bots = commands.add_parser(
        "bot", help="The home's bots: named agents with their own mission, memory and skills."
    )
    bot_actions = bots.add_subparsers(dest="action", required=True)
    bot_actions.add_parser("list", help="List the home's bots.")
    make_bot = bot_actions.add_parser("create", help="Make a bot.")
    make_bot.add_argument("name")
    make_bot.add_argument(
        "--title", default="", help="How the roster calls it; by default, its name."
    )
    make_bot.add_argument("--description", default="", help="What it is for, in the roster.")
    make_bot.add_argument("--model", help="The model it runs on; by default, the home's.")
    make_bot.add_argument(
        "--mission-file",
        metavar="PATH",
        help="A file with its mission and rules, or - to read them from the standard input.",
    )
    show_bot = bot_actions.add_parser("show", help="A bot's definition, and where it is.")
    show_bot.add_argument("name")
    delete_bot = bot_actions.add_parser(
        "delete", help="Delete a bot: its definition, its memory and its skills."
    )
    delete_bot.add_argument("name")
    delete_bot.add_argument("-y", "--yes", action="store_true", help="Do not ask first.")

    plugins = commands.add_parser(
        "plugins", help="The home's plugins: turn them on with consent, or off."
    )
    plugin_actions = plugins.add_subparsers(dest="action", required=True)
    plugin_actions.add_parser("list", help="List the plugins, and what each declares.")
    enable = plugin_actions.add_parser(
        "enable", help="Turn a plugin on, after showing what it declares."
    )
    enable.add_argument("name")
    enable.add_argument(
        "-y", "--yes", action="store_true", help="Consent without asking (what it declares now)."
    )
    disable = plugin_actions.add_parser("disable", help="Turn a plugin off.")
    disable.add_argument("name")
    plugin_secret = plugin_actions.add_parser(
        "secret", help="Store a secret a plugin declares, read from the standard input."
    )
    plugin_secret.add_argument("name")
    plugin_secret.add_argument("secret")
    plugin_set = plugin_actions.add_parser(
        "set", help="Change a setting a plugin declares; with no value, back to its default."
    )
    plugin_set.add_argument("name")
    plugin_set.add_argument("setting")
    plugin_set.add_argument("value", nargs="?")
    plugin_new = plugin_actions.add_parser(
        "new", help="Start a plugin: a folder with a manifest, a tool and a skill to change."
    )
    plugin_new.add_argument("name", help="In kebab-case, such as my-plugin.")
    plugin_new.add_argument(
        "--folder",
        type=Path,
        help="Where to make it; by default the home's plugins folder, where Atlas finds it.",
    )
    plugin_validate = plugin_actions.add_parser(
        "validate",
        help="Check a plugin's folder as Atlas loads it, in a home thrown away after.",
    )
    plugin_validate.add_argument("folder", type=Path)
    plugin_install = plugin_actions.add_parser(
        "install",
        help="Fetch a plugin by its address (a git URL, owner/repo, a folder); it arrives off.",
    )
    plugin_install.add_argument(
        "address", help="https://..., git@..., owner/repo, with #folder for one inside it."
    )
    plugin_install.add_argument("--ref", default="", help="A branch, a tag or a commit.")
    plugin_install.add_argument(
        "--force", action="store_true", help="Replace the one of the same name installed before."
    )
    plugin_update = plugin_actions.add_parser(
        "update", help="Fetch a plugin installed by address again from where it came."
    )
    plugin_update.add_argument("name")
    plugin_remove = plugin_actions.add_parser(
        "remove", help="Turn off and take away a plugin installed by address; its data stays."
    )
    plugin_remove.add_argument("name")

    market = commands.add_parser(
        "marketplace",
        help="The marketplace: skills, MCP servers, plugins and executors made for Atlas, each "
        "reviewed and pinned to a commit.",
    )
    market_actions = market.add_subparsers(dest="action", required=True)
    market_list = market_actions.add_parser(
        "list", help="What the marketplace offers, what Atlas ships, and what came from it."
    )
    market_list.add_argument("--kind", choices=[kind.value for kind in Kind])
    market_list.add_argument("--refresh", action="store_true", help="Read the marketplace now.")
    market_show = market_actions.add_parser(
        "show", help="An item's page: its README, and what it needs here."
    )
    market_show.add_argument("name")
    market_install = market_actions.add_parser(
        "install", help="Install an item, with the MCP servers a skill needs; a plugin arrives off."
    )
    market_install.add_argument("name")
    market_update = market_actions.add_parser(
        "update", help="Bring an installed item to the marketplace's version."
    )
    market_update.add_argument("name")
    market_update.add_argument(
        "--force",
        action="store_true",
        help="Update a skill or a server changed here since it came; a skill keeps what it held.",
    )
    market_remove = market_actions.add_parser(
        "remove", help="Take away an item installed from the marketplace."
    )
    market_remove.add_argument("name")
    market_check = market_actions.add_parser(
        "check",
        help="Check item files as the marketplace's CI does: each by its rules, its code fetched "
        "at its commit.",
    )
    market_check.add_argument(
        "items",
        nargs="*",
        type=Path,
        help="Item files; by default every one in the folder's items.",
    )
    market_check.add_argument(
        "--root", type=Path, default=Path(), help="The marketplace's folder; by default this one."
    )
    market_check.add_argument(
        "--load",
        action="store_true",
        help="Also load each plugin's Python, in a home thrown away after: only where foreign "
        "code may run, as the marketplace's CI.",
    )

    pairing = commands.add_parser("pairing", help="Who may talk to Atlas through a channel.")
    pairing_actions = pairing.add_subparsers(dest="action", required=True)
    pairing_actions.add_parser("list", help="The people let in, and those waiting with a code.")
    approve = pairing_actions.add_parser("approve", help="Let in whoever holds a code.")
    approve.add_argument("code")
    forget = pairing_actions.add_parser("forget", help="Remove someone from a channel.")
    forget.add_argument("channel")
    forget.add_argument("user")

    desktop = commands.add_parser(
        "desktop",
        help="Build the app when its code changed, write its Start Menu shortcut, and open it.",
    )
    desktop.add_argument(
        "--build-only", action="store_true", help="Build it and write the shortcut; do not open it."
    )
    commands.add_parser(
        "update",
        help="Bring this installation to main, then Python and the app; back to where it was if "
        "a step fails. Close the app first.",
    )
    return parser


def listen_address(args: argparse.Namespace, settings: Settings) -> tuple[str, int]:
    """Where the server listens: a command-line flag wins over every setting."""
    host: str | None = args.host
    port: int | None = args.port
    return (
        settings.server.host if host is None else host,
        settings.server.port if port is None else port,
    )


def main(
    argv: Sequence[str] | None = None,
    environ: MutableMapping[str, str] | None = None,
    stdin: TextIO | None = None,
) -> int:
    """Run one command and return its exit code."""
    parser = build_parser()
    given = list(sys.argv[1:] if argv is None else argv)
    home_flag, named, rest = _command_word(given)
    if named is not None and named not in _command_names(parser):
        # Not Atlas's own: a plugin's terminal command (step 16l), or argparse's error.
        found = _plugin_cli(named, rest, home_flag, os.environ if environ is None else environ)
        if found is not None:
            return found
    args = parser.parse_args(given)
    if args.command == "version":
        sys.stdout.write(f"{__version__}\n")
        return 0
    if args.command == "docs":
        return _docs(args)

    environ = os.environ if environ is None else environ
    reader: TextIO = sys.stdin if stdin is None else stdin
    if args.command == "serve" and args.always_on:
        # The service names its home by its folder, so it needs no ATLAS_HOME of its own.
        folder = Path(args.always_on)
        environ["ATLAS_HOME"] = str(folder)
        args.home = home_name(folder)
    try:
        # The home is chosen before the settings are read, which come from it; a process the
        # command starts inherits it.
        environ["ATLAS_HOME"] = str(choose_home(resolve_home(environ), args.home))
        loaded = load_settings(environ)
        forget_secret_variables(environ)
        home = open_home(loaded.settings.home)
    except HomeError as error:
        sys.stderr.write(f"atlas: {error}\n")
        return 1
    redactor = Redactor()
    if args.command == "serve":
        return _serve(args, loaded, home, redactor)
    configure_logging("WARNING", redactor)
    try:
        if args.command == "usage":
            return _usage(args, home)
        if args.command == "routines":
            return _routines(args, home, loaded.settings)
        if args.command == "skills":
            return _skills(args, home)
        if args.command == "memory":
            return _memory(args, home)
        if args.command == "backup":
            return _backup(args, home)
        if args.command == "restore":
            return _restore(args, home, reader)
        if args.command == "home":
            return _homes(args, home, loaded.settings, redactor, reader)
        if args.command == "bot":
            return _bots(args, home, reader)
        if args.command in ("desktop", "update"):
            return _installation(args, home, environ)
        if args.command == "always-on":
            return _always_on(args, home)
    except (HomeError, InstancesError) as error:
        sys.stderr.write(f"atlas: {error}\n")
        return 1
    vault = Vault(home.secrets_folder, loaded.settings.vault, redactor)
    try:
        if args.command == "models":
            return _check_models(args, loaded.settings, home, vault)
        if args.command == "server":
            return _server(args, home, loaded.settings, vault)
        if args.command in ("plugins", "pairing"):
            return _plugins(args, home, vault, reader)
        if args.command == "marketplace":
            return _marketplace(args, home, loaded.settings, vault)
        if args.command == "mcp":
            return _mcp(args, home, vault, reader)
        return _secrets(args, vault, reader)
    except (
        SecretsError,
        HomeError,
        ModelsError,
        PluginError,
        InstancesError,
        MarketplaceError,
    ) as error:
        sys.stderr.write(f"atlas: {error}\n")
        return 1


def _command_word(argv: list[str]) -> tuple[str | None, str | None, list[str]]:
    """`--home NAME`, if it comes first, the command's name, and what follows it."""
    home: str | None = None
    rest = list(argv)
    if rest and rest[0] == "--home" and len(rest) > 1:
        home, rest = rest[1], rest[2:]
    elif rest and rest[0].startswith("--home="):
        home, rest = rest[0].removeprefix("--home="), rest[1:]
    if not rest or rest[0].startswith("-"):
        return home, None, rest
    return home, rest[0], rest[1:]


def _command_names(parser: argparse.ArgumentParser) -> set[str]:
    """Atlas's own commands: a plugin's terminal command never takes one of their names."""
    for action in parser._actions:  # pyright: ignore[reportPrivateUsage]
        if isinstance(action, argparse._SubParsersAction):  # pyright: ignore[reportPrivateUsage]
            choices = cast("dict[str, object]", action.choices)  # pyright: ignore[reportUnknownMemberType]
            return set(choices)
    return set()


def _plugin_cli(
    name: str, rest: list[str], home_flag: str | None, environ: MutableMapping[str, str]
) -> int | None:
    """`atlas <name> ...` when a plugin that is on registered `name` (step 16l): its code runs with
    the arguments, and its exit code is Atlas's. `None` when no plugin did, for argparse to say the
    command is unknown."""
    try:
        environ["ATLAS_HOME"] = str(choose_home(resolve_home(environ), home_flag))
        loaded = load_settings(environ)
        forget_secret_variables(environ)
        home = open_home(loaded.settings.home)
    except HomeError:
        return None
    redactor = Redactor()
    configure_logging("WARNING", redactor)
    vault = Vault(home.secrets_folder, loaded.settings.vault, redactor)
    plugins = Plugins(home, _migrate(home), vault)
    if not any(
        view.running and view.manifest is not None and name in view.manifest.cli_commands
        for view in plugins.all()
    ):
        return None
    found = next((c for c in asyncio.run(plugins.cli_commands()) if c.name == name), None)
    if found is None:
        problem = plugins.view(_owner(plugins, name)).problem
        sys.stderr.write(f"atlas: the plugin that brings `{name}` failed: {problem}\n")
        return 1

    async def run() -> int | None:
        try:
            done = found.run(rest)
            return await done if inspect.isawaitable(done) else done
        finally:
            await plugins.stop()

    try:
        code = asyncio.run(run())
    except KeyboardInterrupt:
        return 130
    except Exception as error:  # noqa: BLE001 - a plugin's own code: its failure is said
        sys.stderr.write(f"atlas {name}: {type(error).__name__}: {error}\n")
        return 1
    return 0 if code is None else int(code)


def _docs(args: argparse.Namespace) -> int:
    """`atlas docs`: this installation's documentation. It opens no home, so it answers on a
    machine that has none, and the agent reads it with a command as any reader would."""
    page: str | None = args.page
    words: list[str] | None = args.search
    try:
        pages = read_pages()
        if words:
            text = render_search(search([find(pages, page)] if page else pages, words), words)
        elif args.all and page:
            raise DocsError("--all prints every page: leave the page out, or drop --all.")
        elif args.all:
            text = render_all(pages)
        elif page:
            text = render_page(find(pages, page))
        else:
            text = render_index(pages)
    except DocsError as error:
        sys.stderr.write(f"atlas: {error}\n")
        return 1
    out: TextIO = sys.stdout
    if isinstance(out, io.TextIOWrapper):
        # Piped or redirected, the pages go out as UTF-8, not in Windows' old code page, which
        # turns their dashes and quotes into garbage; a character a terminal cannot show is
        # replaced, never the end of the command.
        wrapper = cast("io.TextIOWrapper[Any]", out)
        if wrapper.isatty():
            wrapper.reconfigure(errors="replace")
        else:
            wrapper.reconfigure(encoding="utf-8", errors="replace")
    out.write(text if text.endswith("\n") else f"{text}\n")
    return 0


def _owner(plugins: Plugins, name: str) -> str:
    return next(
        view.name
        for view in plugins.all()
        if view.manifest is not None and name in view.manifest.cli_commands
    )


def _server(args: argparse.Namespace, home: Home, settings: Settings, vault: Vault) -> int:
    """`atlas server token` and `atlas server code`."""
    if args.action == "token":
        if args.new:
            vault.set(KEPT_TOKEN, secrets.token_urlsafe(32))
            AlwaysOnHome(home).restart()
        sys.stdout.write(f"{kept_token(vault)}\n")
        return 0
    port: int = settings.server.port if args.port is None else args.port
    code = AlwaysOnHome(home).code(kept_token(vault), port)
    sys.stdout.write(f"{encode_code(code)}\n")
    return 0


def _always_on(args: argparse.Namespace, home: Home) -> int:
    """`atlas always-on on|off|status|open|close`."""
    service = AlwaysOnHome(home)
    if args.action == "on":
        state = service.turn_on()
        if service.wait_ready():
            sys.stdout.write(f"The home {service.name} is always on, on port {state.port}.\n")
        else:
            sys.stdout.write(
                f"The home {service.name} is always on, on port {state.port}, but its server is "
                "not up yet: another one holds the home (close the app), it failed (see the "
                "home's log), or, on Windows, no one is signed in at this computer (it starts "
                "at sign-in, not from an SSH session). The service keeps trying.\n"
            )
        return 0
    if args.action == "off":
        service.turn_off()
        sys.stdout.write(f"The home {service.name} is no longer always on.\n")
        return 0
    if args.action in ("open", "close"):
        state = service.set_open(args.action == "open")
        where = _tailnet_line(tailnet()) if state.open else "this machine only"
        sys.stdout.write(f"The home {service.name} listens on {where}.\n")
        return 0
    if args.action == "start":
        if not service.ensure():
            sys.stderr.write(
                f"atlas: the home {service.name}'s always-on server did not answer; see the "
                "home's log.\n"
            )
            return 1
        return 0
    status = service.status()
    if args.json:
        found = status.tailnet
        sys.stdout.write(
            json.dumps(
                {
                    "home": service.name,
                    "on": status.on,
                    "open": status.open,
                    "port": status.port,
                    "installed": status.installed,
                    "served": status.served,
                    "tailnet": None
                    if found is None
                    else {"address": found.address, "name": found.name},
                    "tailnet_problem": status.tailnet_problem,
                }
            )
            + "\n"
        )
        return 0
    if not status.on:
        sys.stdout.write(f"The home {service.name} is not always on.\n")
        return 0
    lines = [
        f"The home {service.name} is always on, on port {status.port}.",
        "Its server is running." if status.served else "Its server is not running.",
    ]
    if not status.installed:
        lines.append("Its service is missing; `atlas always-on on` writes it again.")
    if status.open:
        lines.append(
            f"Open to your machines on {_tailnet_line(status.tailnet)}."
            if status.tailnet is not None
            else f"Open to your machines, but: {status.tailnet_problem}"
        )
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def _tailnet_line(found: Tailnet) -> str:
    return f"{found.name} ({found.address})" if found.name else found.address


def _serve(args: argparse.Namespace, loaded: LoadedSettings, home: Home, redactor: Redactor) -> int:
    """Open the home's database, bring it up to date, and serve until stopped."""
    settings = loaded.settings
    log_file = home.logs_folder / LOG_FILE if settings.log.file else None
    configure_logging(settings.log.level, redactor, log_file=log_file)
    if loaded.unknown_variables:
        _logger.warning(
            "No setting reads these variables; check their spelling: %s.",
            ", ".join(loaded.unknown_variables),
        )
    database = Database(home.database_file)
    try:
        with serving(home):
            return _serve_held(args, loaded, home, redactor, database)
    except HomeBusyError as error:
        _logger.error("Atlas cannot start: %s", error)
        return 1


def _serve_held(
    args: argparse.Namespace,
    loaded: LoadedSettings,
    home: Home,
    redactor: Redactor,
    database: Database,
) -> int:
    """`_serve` once the home is held for this server."""
    settings = loaded.settings
    try:
        _migrate(home)
        catalog = load_catalog(home.models_file)
    except (HomeError, ModelsError) as error:
        _logger.error("Atlas cannot start: %s", error)
        return 1
    _sync_skills(home)
    # A vault that does not open is said once, here; Atlas still starts, without its secrets.
    vault = Vault(home.secrets_folder, settings.vault, redactor)
    problem = vault.problem()
    if problem is not None:
        _logger.error("The vault does not open: %s", problem)
    if "executors" in loaded.moved:
        _move_executors(home, database, vault)
    always_on = read_always_on(home) if args.always_on else None
    if args.always_on and always_on is None:
        _logger.error("The home is not always on (no always_on.json); `atlas always-on on`.")
        return 1
    if always_on is not None:
        host, port = DEFAULT_HOST, always_on.port
        token = _kept_or_fresh(vault)
        _put_toolchain_first()
    else:
        host, port = listen_address(args, settings)
        token = _server_token(settings, host, vault)
    redactor.add(token)
    try:
        # A Compose host's server reaches its host updater, for the app's update button.
        host_updater = host_channel(loaded.variables)
    except InstallationError as error:
        _logger.error("%s", error)
        return 1
    models = Models(catalog, vault, settings.model, ledger=CallLedger(database))
    gateway = Gateway(
        home,
        settings,
        models,
        vault,
        schemas=SCHEMAS,
        environ=loaded.variables,
        host_updater=host_updater,
    )
    parent = None if args.parent_pid is None else Parent(args.parent_pid)
    _logger.info(
        "Serving.",
        extra={
            "home": str(home.root),
            "host": host,
            "port": port,
            "parent_pid": args.parent_pid,
            "always_on": always_on is not None,
            "open": always_on is not None and always_on.open,
        },
    )
    tailnet_names: set[str] = set()
    app = create_app(gateway, token=token, bound_host=host, tailnet_names=tailnet_names)
    opening = (
        None if always_on is None or not always_on.open else _Tailnet(app, port, tailnet_names)
    )
    server = _Server(uvicorn.Config(app, host=host, port=port, log_config=None), parent, opening)
    try:
        server.run()
    finally:
        if parent is not None:
            parent.close()
    return 0


def _move_executors(home: Home, database: Database, vault: Vault) -> None:
    """Move the `[executors]` an Atlas before step 16c kept in config.toml to the claude-code
    plugin's `program` setting, once the database is up to date, after an update (the file kept
    first). A program the plugin already has stays: it was set since."""
    plugins = Plugins(home, database, vault)

    def move(table: dict[str, Any]) -> None:
        program: object = table.get("claude_code")
        listed = isinstance(program, list) and all(
            isinstance(part, str) for part in cast("list[object]", program)
        )
        if set(table) - {"claude_code"} or not (program is None or listed):
            raise PluginError(f"it holds what Atlas never wrote there: {table!r}")
        if program and not plugins.store.settings(_CLAUDE_CODE).get("program"):
            plugins.set_setting(_CLAUDE_CODE, "program", json.dumps(program))

    try:
        kept = drop_section(home, "executors", utc_now(), move)
    except (HomeError, PluginError, OSError) as error:
        _logger.warning(
            "config.toml's [executors] was not moved (%s). It is read past; remove it, and set "
            "Claude Code's program with `atlas plugins set claude-code program "
            "'[\"<path to claude>\"]'`.",
            error,
        )
        return
    _logger.warning(
        "Moved config.toml's [executors] to the claude-code plugin's program setting (step 16c); "
        "the file as it was is %s.",
        kept,
    )


def _sync_skills(home: Home) -> None:
    """Copy the skills Atlas ships into the library when it starts."""
    result = sync_bundled(SkillLibrary(home.skills_folder))
    if result.copied or result.updated:
        _logger.info(
            "Bundled skills synced.", extra={"copied": result.copied, "updated": result.updated}
        )
    if result.kept:
        _logger.info(
            "Bundled skills changed in this home are kept as they are; `atlas skills reset NAME` "
            "puts one back.",
            extra={"kept": result.kept},
        )
    if result.taken:
        _logger.warning(
            "A skill of the library has the name of one Atlas ships, so Atlas's was not copied.",
            extra={"names": result.taken},
        )


def _server_token(settings: Settings, host: str, vault: Vault) -> str:
    """The token clients present: the one given; off loopback (a container), the one the vault
    keeps; otherwise a fresh one nobody knows (docs/ARCHITECTURE.md, Server and Container)."""
    if settings.server.token is not None:
        return settings.server.token.reveal()
    if not is_loopback(host):
        try:
            token = kept_token(vault)
        except SecretsError as error:
            _logger.error(
                "The server token cannot be kept in the vault, so no client can connect: %s", error
            )
        else:
            _logger.info(
                "The server token is the one kept in the vault; `atlas server token` prints it."
            )
            return token
    else:
        # On loopback, a fresh one at every start. Whoever starts the server sets the
        # variable and gives it to its clients; without it, nobody can connect, and only /health
        # answers.
        _logger.warning(
            "ATLAS_SERVER_TOKEN is not set, so no client can connect. Set it when starting the "
            "server, and give the same value to the clients."
        )
    return secrets.token_urlsafe(32)


def _kept_or_fresh(vault: Vault) -> str:
    """An always-on home's token: the vault's, which `atlas server token` gives the app."""
    try:
        return kept_token(vault)
    except SecretsError as error:
        _logger.error(
            "The server token cannot be kept in the vault, so no client can connect: %s", error
        )
        return secrets.token_urlsafe(32)


def _put_toolchain_first() -> None:
    """An always-on server starts from a service, not from the app, so it puts the installation's
    toolchain first on its PATH itself, as the app does for the server it starts."""
    try:
        environment = Toolchain.of(Checkout.running()).environment(os.environ)
    except InstallationError as error:
        _logger.warning("The installation's toolchain is not on the PATH: %s", error)
        return
    key = next((name for name in environment if name.upper() == "PATH"), "PATH")
    os.environ[key] = environment[key]


class _QuietServer(uvicorn.Server):
    """A second uvicorn on the same app, which leaves the process's signals to the first."""

    @contextmanager
    def capture_signals(self) -> Generator[None]:
        yield


class _Tailnet:
    """The listener on the machine's tailnet address of a home open to the user's machines: a
    second uvicorn on the same app, with no lifespan of its own, bound once Tailscale is up (on
    Windows it starts after the logon too), tried every 30 seconds until then, and started again
    when its address goes away."""

    RETRY_SECONDS = 30.0

    def __init__(self, app: ASGIApp, port: int, names: set[str]) -> None:
        self._app = app
        self._port = port
        self._names = names
        self._server: _QuietServer | None = None
        self.stopping = False

    async def run(self) -> None:
        said: str | None = None
        while not self.stopping:
            try:
                found = await asyncio.to_thread(tailnet)
                listener = socket.create_server((found.address, self._port))
            except (InstancesError, OSError) as error:
                if str(error) != said:
                    _logger.warning("Not listening on the tailnet yet: %s", error)
                    said = str(error)
                await self._pause()
                continue
            said = None
            self._names.clear()
            self._names.update(name for name in (found.address, found.name) if name)
            _logger.info(
                "Listening on the tailnet.",
                extra={"address": found.address, "tailnet_name": found.name},
            )
            config = uvicorn.Config(self._app, lifespan="off", log_config=None)
            self._server = _QuietServer(config)
            await self._server.serve(sockets=[listener])
            self._names.clear()
            listener.close()
            if not self.stopping:
                await self._pause()

    def stop(self) -> None:
        self.stopping = True
        if self._server is not None:
            self._server.should_exit = True

    async def _pause(self) -> None:
        deadline = time.monotonic() + self.RETRY_SECONDS
        while not self.stopping and time.monotonic() < deadline:
            await asyncio.sleep(0.5)


class _Server(uvicorn.Server):
    """uvicorn's server, which says its port once it listens and leaves with the app that started
    it (docs/ARCHITECTURE.md, Desktop app); an open always-on home's
    also listens on the tailnet."""

    def __init__(
        self, config: uvicorn.Config, parent: Parent | None, opening: _Tailnet | None = None
    ) -> None:
        super().__init__(config)
        self._parent = parent
        self._watch: asyncio.Task[None] | None = None
        self._opening = opening
        self._open_task: asyncio.Task[None] | None = None

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets)
        if self.should_exit or not self.servers:
            return
        port: int = self.servers[0].sockets[0].getsockname()[1]
        # On fd 1, never through logging (stderr): the app reads standard output for this line.
        # A service's windowless interpreter (`pythonw`) has no standard output, and nobody reads.
        if sys.stdout is not None:  # pyright: ignore[reportUnnecessaryComparison]
            sys.stdout.write(f"{READY_LINE}{port}\n")
            sys.stdout.flush()
        if self._parent is not None:
            self._watch = asyncio.create_task(self._watch_parent(self._parent))
        if self._opening is not None:
            self._open_task = asyncio.create_task(self._opening.run())

    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        # The tailnet's connections end before the gateway closes under them.
        if self._opening is not None and self._open_task is not None:
            self._opening.stop()
            with suppress(TimeoutError):
                await asyncio.wait_for(self._open_task, EXIT_CEILING_SECONDS)
        await super().shutdown(sockets)

    async def _watch_parent(self, parent: Parent) -> None:
        while parent.alive():
            await asyncio.sleep(POLL_SECONDS)
        _logger.warning("The app that started the server is gone; stopping.")
        # The graceful stop runs the gateway's close; if it stalls, the process ends anyway.
        ceiling = threading.Timer(EXIT_CEILING_SECONDS, os._exit, args=(0,))
        ceiling.daemon = True
        ceiling.start()
        self.should_exit = True


def _migrate(home: Home) -> Database:
    """The home's database brought up to date, backed up first when a migration will change data
    it already holds."""
    database = Database(home.database_file)

    def back_up() -> None:
        written = pre_migration_backup(home, version=__version__, now=utc_now())
        _logger.warning("Backed up the home before migrating its database: %s", written.path)

    migrate(database, SCHEMAS, before=back_up)
    return database


_SOURCES = {
    CostSource.REPORTED: "reported by the provider",
    CostSource.PRICE_LIST: "from the price list",
    CostSource.FREE: "free (a local server)",
    CostSource.UNKNOWN: "unknown",
}


def _routines(args: argparse.Namespace, home: Home, settings: Settings) -> int:
    """The routines' commands: list, create, pause, resume, run, remove, runs."""
    routines = Routines(
        RoutineStore(_migrate(home)), zone=time_zone(settings.time.zone), clock=utc_now
    )
    out = sys.stdout
    try:
        if args.action == "list":
            found = routines.store.all()
            if not found:
                out.write("No routines.\n")
            for routine in found:
                out.write(f"{routine.id}  {routine.name}  {_routine_line(routine, routines)}\n")
            return 0
        if args.action == "create":
            model = None
            if args.model:
                provider, _, name = str(args.model).partition("/")
                if not provider or not name:
                    sys.stderr.write(
                        "atlas: --model is provider/model, such as openai/gpt-6-sol.\n"
                    )
                    return 1
                model = (provider, name, None)
            if args.bot:
                Bots(home.bots_folder).get(args.bot)
            made = routines.create(
                args.name,
                args.schedule,
                prompt=args.prompt,
                command=args.shell_command,
                model=model,
                bot=args.bot or None,
            )
            out.write(f"Made {made.id}  {made.name}  {_routine_line(made, routines)}\n")
            return 0
        if args.action == "runs":
            routine = routines.store.get(args.routine)
            runs = routines.store.runs(routine.id)
            if not runs:
                out.write(f"{routine.name} has not run yet.\n")
            for run in runs:
                when = run.started_at.astimezone(routines.zone).strftime("%Y-%m-%d %H:%M")
                where = f"  conversation {run.conversation_id}" if run.conversation_id else ""
                why = f"  {run.error}" if run.error else ""
                out.write(f"{when}  {run.status.value}  {run.lateness.value}{where}{why}\n")
            return 0
        acted = {
            "pause": routines.pause,
            "resume": routines.resume,
            "run": routines.trigger,
            "remove": routines.remove,
        }[args.action](args.routine)
    except (JobsError, BotError) as error:
        sys.stderr.write(f"atlas: {error}\n")
        return 1
    said = {
        "pause": "Paused",
        "resume": "Resumed",
        "run": "Runs at the server's next minute (open the app if Atlas is not running):",
        "remove": "Removed",
    }[args.action]
    out.write(f"{said} {acted.name}.\n")
    return 0


def _routine_line(routine: Routine, routines: Routines) -> str:
    state = "on" if routine.enabled else "paused"
    upcoming = (
        "no next run"
        if routine.next_run_at is None
        else "next " + routine.next_run_at.astimezone(routines.zone).strftime("%Y-%m-%d %H:%M")
    )
    last = "" if routine.last_status is None else f", last {routine.last_status.value}"
    whose = "" if routine.bot is None else f", as the bot {routine.bot}"
    return f"{routine.schedule.display}, {state}, {upcoming}{last}{whose}"


def _installation(args: argparse.Namespace, home: Home, environ: Mapping[str, str]) -> int:
    """`atlas desktop` and `atlas update`, on the checkout this Atlas runs from, with the
    installation's own tools when the installer left them beside it. In a Compose host's container
    `atlas update` is the host's: the host updater runs it (`scripts/compose-host/atlas`), and a
    command typed here could be the agent's."""
    try:
        if args.command == "update" and installation_kind(environ) == "compose":
            sys.stderr.write(f"atlas: {COMPOSE_UPDATE_ELSEWHERE}\n")
            return 1
        checkout = Checkout.running()
        runner = Runner(Toolchain.of(checkout))
        if args.command == "update":
            # The always-on homes' servers run this code too: stopped for the update, and
            # started again after it, whatever it did.
            services = AlwaysOnServices(home_root(home.root))
            services.stop()
            try:
                served = [found.name for found in list_homes(home_root(home.root)) if found.served]
                updated = update(checkout, runner, served)
            finally:
                services.start()
            if updated.before == updated.after:
                sys.stdout.write(f"Already up to date ({updated.after[:12]}).\n")
            else:
                sys.stdout.write(f"Updated {updated.before[:12]} to {updated.after[:12]}:\n")
                for line in updated.commits:
                    sys.stdout.write(f"  {line}\n")
            return 0
        built = build_desktop(checkout, runner)
        if not (built.installed or built.built):
            sys.stdout.write("The app is up to date.\n")
        _launcher(checkout)
        if not args.build_only:
            launch(checkout)
            sys.stdout.write("Atlas is opening; you can close this terminal.\n")
    except InstallationError as error:
        sys.stderr.write(f"atlas: {error}\n")
        return 1
    return 0


def _launcher(checkout: Checkout) -> None:
    written = write_launcher(checkout, Path.home())
    if written is not None:
        sys.stdout.write(f"Launcher: {written}\n")


def _usage(args: argparse.Namespace, home: Home) -> int:
    """The call ledger summed: in all, by day (the local
    day), by model, by purpose and, in a home with bots, by bot (a bot's turns, its reviews and
    its subagents)."""
    days: int = args.days
    if days < 1:
        sys.stderr.write("atlas: --days takes 1 or more.\n")
        return 1
    today = utc_now().astimezone()
    first = (today - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    database = _migrate(home)
    records = CallLedger(database).since(format_utc(first))
    report = usage_report(
        (parse_utc(record.started_at).astimezone().date().isoformat(), record) for record in records
    )
    out = sys.stdout
    out.write(f"Model calls from {first.date().isoformat()} to {today.date().isoformat()}")
    out.write(" (local days).\n")
    if not records:
        out.write("None.\n")
        return 0
    out.write(f"\nTotal  {_usage_line(report.total)}\n")
    owners = ConversationStore(database).bots()
    by_bot: dict[str, UsageLine] = {}
    for record in records:
        owner = owners.get(record.scope or "")
        if owner is not None:
            by_bot.setdefault(owner, UsageLine()).add(record)
    for title, lines in (
        ("By day", report.days),
        ("By model", report.models),
        ("By purpose", report.purposes),
        ("By bot", dict(sorted(by_bot.items()))),
    ):
        if not lines:
            continue
        width = max(len(key) for key in lines)
        out.write(f"\n{title}\n")
        for key, line in lines.items():
            out.write(f"  {key.ljust(width)}  {_usage_line(line)}\n")
    sources = ", ".join(
        f"{count} {_SOURCES[source]}" for source, count in sorted(report.sources.items())
    )
    out.write(f"\nCosts: {sources}. A free tier's calls carry the price list's figure.\n")
    return 0


def _usage_line(line: UsageLine) -> str:
    cost = f"US$ {line.usd.quantize(Decimal('0.0001'))}"
    if line.unknown:
        cost += f" (+{line.unknown} unknown)"
    tokens = f"in {_tokens(line.input_tokens)} (cached {_tokens(line.cache_read_tokens)})"
    return f"{line.calls:>5} calls  {cost:<26}{tokens}, out {_tokens(line.output_tokens)}"


def _tokens(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def _skills(args: argparse.Namespace, home: Home) -> int:
    """The skills and who made each, a bot's skill shared with the library, and the user's way
    back from what the agent did to one."""
    try:
        usage = SkillUsage(_migrate(home))
        library = SkillLibrary(home.skills_folder, usage)
        bots = Bots(home.bots_folder)
        if args.action == "list":
            return _list_skills(library, usage, bots)
        bot: str | None = getattr(args, "bot", None)
        mine = library
        if bot is not None:
            mine = SkillLibrary(bots.get(bot).skills_folder, usage.owned_by(bot))
        name: str = args.name
        if args.action == "reset":
            reset_bundled(library, name)
            sys.stdout.write(f"Put {name} back as Atlas ships it.\n")
            return 0
        if args.action == "share":
            if bot is None:
                sys.stderr.write("atlas: say whose skill it is, with --bot NAME.\n")
                return 1
            moved = share_skill(name, mine, library)
            sys.stdout.write(f"Moved {name} from the bot {bot} to the library, at {moved}.\n")
            return 0
        if args.action == "versions":
            for version in mine.versions(name):
                sys.stdout.write(f"{version}\n")
            return 0
        if args.action == "restore":
            sys.stdout.write(f"Restored {name} to {mine.restore(name)}.\n")
            return 0
        version = mine.revert(name, args.version)
        sys.stdout.write(f"Put {name} back as version {version} kept it.\n")
        return 0
    except (SkillChangeError, BotError, HomeError) as error:
        sys.stderr.write(f"atlas: {error}\n")
        return 1


def _list_skills(library: SkillLibrary, usage: SkillUsage, bots: Bots) -> int:
    """Every skill, where it is and who made it, so the user sees who fills the library."""
    records = {(record.owner, record.name): record for record in usage.every_record()}
    manifest = read_manifest(library.folder)
    folders = [("", library), *((bot.name, SkillLibrary(bot.skills_folder)) for bot in bots.list())]
    lines: list[str] = []
    for owner, folder in folders:
        for skill in folder.catalog.skills():
            record = records.get((owner, skill.name))
            creator = record.creator if record is not None else None
            made = (
                "the review" if record is not None and record.origin is Origin.REVIEW else "a turn"
            )
            by = "the user" if creator is None else f"{creator}, in {made}"
            shipped = changed(folder, skill.name, manifest) if not owner else None
            if shipped is not None:
                by = "Atlas, changed since" if shipped else "Atlas"
            where = "library" if not owner else f"bot {owner}"
            uses = 0 if record is None else record.use_count
            lines.append(f"{skill.name}  {where}  by {by}  {uses} use{'' if uses == 1 else 's'}\n")
    sys.stdout.write("".join(lines) or "No skills.\n")
    return 0


def _memory(args: argparse.Namespace, home: Home) -> int:
    """The review's memory proposals: list them, approve one or discard one."""
    try:
        database = _migrate(home)
        proposals = Proposals(MemoryFiles(home.memory_folder), ProposalStore(database))
        if args.action == "proposals":
            listed = proposals.checked()
            if not listed:
                sys.stdout.write("No memory proposals.\n")
            for item in listed:
                proposal = item.proposal
                state = "can apply" if item.problem is None else f"cannot apply: {item.problem}"
                sys.stdout.write(
                    f"{proposal.id}  {proposal.target}  {proposal.created_at}  {state}\n"
                )
                for operation in proposal.operations:
                    sys.stdout.write(f"    {_change(operation)}\n")
            return 0
        if args.action == "discard":
            if not proposals.discard(args.id):
                sys.stderr.write(f"atlas: there is no open memory proposal {args.id}.\n")
                return 1
            sys.stdout.write(f"Discarded {args.id}.\n")
            return 0
        approval = proposals.approve(args.id)
        if not approval.applied:
            sys.stderr.write(f"atlas: {approval.error}\n")
            return 1
        sys.stdout.write(f"Approved {args.id}.\n")
        # The approver is the last who can notice a clause went missing.
        for entry in approval.replaced:
            sys.stdout.write(f"Overwrote, whole: {entry}\n")
        for entry in approval.removed:
            sys.stdout.write(f"Removed: {entry}\n")
        return 0
    except HomeError as error:
        sys.stderr.write(f"atlas: {error}\n")
        return 1


def _backup(args: argparse.Namespace, home: Home) -> int:
    """Write the home's backup; with the default name, keep the last few."""
    name = home_name(home.root)
    now = utc_now()
    if args.output == "-":
        # A zip is written seekable, then handed over: the container's way out.
        with tempfile.TemporaryDirectory(prefix="atlas-backup-") as scratch:
            written = write_backup(
                home, Path(scratch) / backup_name(name, now), version=__version__, now=now
            )
            with written.path.open("rb") as source:
                shutil.copyfileobj(source, sys.stdout.buffer)
            sys.stdout.buffer.flush()
        sys.stderr.write(f"Backed up {name}: {written.manifest.files} files.\n")
        return 0
    chosen = Path(args.output).expanduser() if args.output else Path.home()
    named_by_hand = bool(args.output) and (chosen.suffix != "" and not chosen.is_dir())
    target = chosen if named_by_hand else chosen / backup_name(name, now)
    written = write_backup(home, target, version=__version__, now=now)
    sys.stdout.write(
        f"Backed up {name} to {written.path}: {written.manifest.files} files, "
        f"{written.size / 1e6:.1f} MB, checked.\n"
        f"Restore with: atlas{_home_flag(name)} restore {written.path}\n"
    )
    if not named_by_hand:
        for old in prune_backups(target.parent, f"{BACKUP_PREFIX}{name}-", args.keep):
            sys.stdout.write(f"Removed the older {old.name}.\n")
    return 0


def _restore(args: argparse.Namespace, home: Home, stdin: TextIO) -> int:
    """Put a backup back over the home: checked first, then overwritten."""
    with tempfile.TemporaryDirectory(prefix="atlas-restore-") as scratch:
        if args.file == "-":
            source = Path(scratch) / "backup.zip"
            with source.open("wb") as copy:
                shutil.copyfileobj(stdin.buffer, copy)
        else:
            source = Path(args.file).expanduser()
        manifest = check_backup(source, SCHEMAS)
        sys.stdout.write(
            f"{source.name}: the home {manifest.home!r} on {manifest.created_at}, by Atlas "
            f"{manifest.atlas}, {manifest.files} files; checked.\n"
        )
        if has_data(home) and not args.yes:
            if not stdin.isatty():
                sys.stderr.write(
                    f"atlas: {home.root} already holds data, and a restore overwrites it. Run "
                    "again with --yes to go ahead.\n"
                )
                return 1
            sys.stdout.write(f"{home.root} already holds data. Overwrite it? [y/N] ")
            sys.stdout.flush()
            if stdin.readline().strip().lower() not in ("y", "yes"):
                sys.stdout.write("Nothing was restored.\n")
                return 1
        if is_served(home.root):
            sys.stderr.write(
                "atlas: a server is serving this home. It will read the restored database, but "
                "what it holds in memory stays; restart it.\n"
            )
        restored = restore_backup(home, source, SCHEMAS)
    # An older backup comes forward to this Atlas's tables.
    migrate(Database(home.database_file), SCHEMAS)
    sys.stdout.write(f"Restored {restored.files} files into {home.root}.\n")
    if restored.shrunk:
        # What was recorded after the backup is not in it.
        sys.stdout.write("Tables now hold fewer rows than before; the backup is older:\n")
        for table in restored.shrunk:
            sys.stdout.write(f"    {table.table}: {table.before} -> {table.after}\n")
    sys.stdout.write(
        "The vault stays as it was: keys never travel in a backup. On a new machine, store them "
        "with `atlas secrets set`.\n"
    )
    return 0


def _homes(
    args: argparse.Namespace, home: Home, settings: Settings, redactor: Redactor, stdin: TextIO
) -> int:
    """The homes of this machine."""
    root = home_root(home.root)
    action: str = args.action
    if action == "list":
        for found in list_homes(root):
            mark = "*" if found.active else " "
            served = "  (served)" if found.served else ""
            sys.stdout.write(f"{mark} {found.name}  {found.path}{served}\n")
        return 0
    if action == "create":
        clone = None
        if args.clone or args.source:
            clone = home_path(root, args.source) if args.source else home.root
        path = create_home(root, args.name, clone=clone)
        kind = f"cloned from {home_name(clone)}" if clone is not None else "empty"
        sys.stdout.write(f"Created the home {args.name} at {path}, {kind}.\n")
        sys.stdout.write(
            f"Use it with `atlas --home {args.name} ...`, or `atlas home use {args.name}`.\n"
        )
        return 0
    if action == "use":
        use_home(root, args.name)
        sys.stdout.write(f"The home {args.name} opens when none is named.\n")
        return 0
    if action == "show":
        path = home_path(root, args.name) if args.name else home.root
        found = [info for info in list_homes(root) if info.path == path]
        if not found:
            raise HomeNameError(f"There is no home {args.name!r}.")
        shown = Home(path)
        try:
            keys = ", ".join(Vault(shown.secrets_folder, settings.vault, redactor).names())
        except SecretsError as error:
            keys = f"the vault does not open ({error})"
        skills = shown.skills_folder
        count = (
            len([p for p in skills.iterdir() if p.is_dir() and not p.name.startswith(".")])
            if skills.is_dir()
            else 0
        )
        sys.stdout.write(
            f"{found[0].name}  {path}\n"
            f"  opens by default: {'yes' if found[0].active else 'no'}\n"
            f"  served now: {'yes' if found[0].served else 'no'}\n"
            f"  keys: {keys or 'none'}\n"
            f"  skills: {count}\n"
        )
        return 0
    if action == "rename":
        path = rename_home(root, args.old, args.new)
        sys.stdout.write(f"Renamed the home {args.old} to {args.new}: {path}.\n")
        return 0
    if not args.yes:
        if not stdin.isatty():
            sys.stderr.write(
                f"atlas: deleting {args.name} removes everything in it. Run again with --yes.\n"
            )
            return 1
        sys.stdout.write(f"Delete the home {args.name} and everything in it? [y/N] ")
        sys.stdout.flush()
        if stdin.readline().strip().lower() not in ("y", "yes"):
            sys.stdout.write("Nothing was deleted.\n")
            return 1
    delete_home(root, args.name)
    sys.stdout.write(f"Deleted the home {args.name}.\n")
    return 0


def _plugins(args: argparse.Namespace, home: Home, vault: Vault, stdin: TextIO) -> int:
    """The home's plugins and the people their channels let in (docs/ARCHITECTURE.md, Plugins). A
    server serving the home starts or stops a plugin's channels within a minute."""
    action: str = args.action
    if args.command == "plugins" and action == "new":
        return _plugin_new(args.folder or home.plugins_folder, args.name)
    if args.command == "plugins" and action == "validate":
        return _plugin_validate(args.folder)
    plugins = Plugins(home, _migrate(home), vault)
    if args.command == "plugins" and action in ("install", "update", "remove"):
        return _plugin_address(args, plugins)
    if args.command == "pairing":
        if action == "list":
            for pairing in plugins.waiting():
                state = "let in" if pairing.approved else f"waits with {pairing.code}"
                sys.stdout.write(f"{pairing.channel}  {pairing.user}  {pairing.name}  {state}\n")
            return 0
        if action == "approve":
            pairing = plugins.approve(args.code)
            sys.stdout.write(
                f"{pairing.name} may talk through {pairing.channel} now; they send again.\n"
            )
            return 0
        plugins.forget(args.channel, args.user)
        sys.stdout.write(f"{args.user} was removed from {args.channel}.\n")
        return 0
    if action == "list":
        for view in plugins.all():
            state = (
                "on" if view.enabled and view.consented
                else "on, waiting for consent again" if view.enabled
                else "off"
            )  # fmt: skip
            version = f" {view.manifest.version}" if view.manifest and view.manifest.version else ""
            sys.stdout.write(f"{view.name}{version}  {state}\n")
            if view.manifest is not None and view.manifest.description:
                sys.stdout.write(f"  {view.manifest.description}\n")
            if view.problem:
                sys.stdout.write(f"  problem: {view.problem}\n")
            for name, stored in view.secrets.items():
                sys.stdout.write(f"  secret {name}: {'stored' if stored else 'not stored'}\n")
            for name, value in view.settings.items():
                sys.stdout.write(f"  setting {name}: {value}\n")
        return 0
    if action == "enable":
        view = plugins.view(args.name)
        manifest = view.manifest
        if manifest is None:
            raise PluginError(f"{args.name} cannot be turned on: {view.problem}")
        sys.stdout.write(f"{manifest.name} {manifest.version}: {manifest.description}\n")
        for line in manifest.declaration():
            sys.stdout.write(f"  - {line}\n")
        if not args.yes:
            if not stdin.isatty():
                sys.stderr.write("atlas: read what it declares, then run again with --yes.\n")
                return 1
            sys.stdout.write("Turn it on? [y/N] ")
            sys.stdout.flush()
            if stdin.readline().strip().lower() not in ("y", "yes"):
                sys.stdout.write("Left off.\n")
                return 1
        plugins.enable(args.name, manifest.fingerprint())
        sys.stdout.write(f"{args.name} is on.\n")
        return 0
    if action == "disable":
        plugins.disable(args.name)
        sys.stdout.write(f"{args.name} is off.\n")
        return 0
    if action == "secret":
        plugins.set_secret(args.name, args.secret, stdin.read())
        sys.stdout.write(f"{args.secret} is stored in the vault.\n")
        return 0
    plugins.set_setting(args.name, args.setting, args.value)
    sys.stdout.write(
        f"{args.name}'s {args.setting} is {plugins.view(args.name).settings[args.setting]}.\n"
    )
    return 0


def _plugin_address(args: argparse.Namespace, plugins: Plugins) -> int:
    """`atlas plugins install`, `update` and `remove` (step 16n). A server serving the
    home sees the change within a second, and loads it."""
    action: str = args.action
    if action == "remove":
        data = remove_plugin(plugins, args.name)
        sys.stdout.write(f"{args.name} was removed. Its data stays in {data}.\n")
        return 0
    if action == "install":
        found = install_plugin(plugins, args.address, ref=args.ref, force=args.force)
        sys.stdout.write(f"{found.name} is installed in {found.folder}, off.\n")
    else:
        found, changed = update_plugin(plugins, args.name)
        if not changed:
            sys.stdout.write(f"{found.name} is up to date.\n")
            return 0
        sys.stdout.write(f"{found.name} is updated.\n")
    view = plugins.view(found.name)
    if view.enabled and not view.consented:
        sys.stdout.write("It declares something else now, so it waits for your consent again:\n")
    elif view.enabled:
        return 0
    for line in view.manifest.declaration() if view.manifest else ():
        sys.stdout.write(f"  - {line}\n")
    sys.stdout.write(f"Turn it on with `atlas plugins enable {found.name}`, or in the app.\n")
    return 0


def _plugin_new(parent: Path, name: str) -> int:
    """`atlas plugins new` (step 16j): Claude Code's `claude plugin init`."""
    try:
        folder = new_plugin(parent, name)
    except ValueError as error:
        sys.stderr.write(f"atlas: {error}\n")
        return 1
    sys.stdout.write(
        f"{name} is in {folder}. Change its manifest, __init__.py and skill, check it with "
        f"`atlas plugins validate {folder}`, and turn it on in the app's Plugins screen.\n"
    )
    return 0


def _plugin_validate(folder: Path) -> int:
    """`atlas plugins validate` (step 16j): Claude Code's `claude plugin validate`."""
    found = validate(folder)
    manifest = found.manifest
    if manifest is not None:
        version = f" {manifest.version}" if manifest.version else ""
        sys.stdout.write(f"{manifest.name}{version}\n")
        for line in manifest.declaration():
            sys.stdout.write(f"  - {line}\n")
    for item in found.registered:
        sys.stdout.write(f"  registered {item}\n")
    for problem in found.problems:
        sys.stdout.write(f"  problem: {problem}\n")
    sys.stdout.write("It loads as Atlas loads it.\n" if found.ok else "It does not load.\n")
    return 0 if found.ok else 1


_KIND_TITLES = {
    Kind.SKILL: "Skills",
    Kind.MCP: "MCP servers",
    Kind.PLUGIN: "Plugins",
    Kind.EXECUTOR: "Executors",
}


def _marketplace(args: argparse.Namespace, home: Home, settings: Settings, vault: Vault) -> int:
    """`atlas marketplace` (step 16s). A server serving the home sees what changes: a plugin
    within a second, `mcp.json` within a few, a skill at the next turn."""
    action: str = args.action
    store = MarketplaceStore(home.marketplace_folder, settings.marketplace)
    if action == "check":
        root: Path = args.root
        items: list[Path] = args.items or sorted((root / "items").glob("*.yaml"))
        problems = check_marketplace(
            items, store, root=root, shipped=shipped_names(), load=args.load
        )
        lines = [line for problem in problems for line in problem.splitlines()]
        for line in lines:
            sys.stdout.write(f"{line}\n")
        checked = f"{len(items)} {'item' if len(items) == 1 else 'items'} checked"
        found = f"{len(lines)} {'problem' if len(lines) == 1 else 'problems'}"
        if lines:
            sys.stdout.write(f"{checked}: {found}.\n")
        elif items:
            sys.stdout.write(f"{checked}: each may enter.\n")
        else:
            sys.stdout.write("No items yet: the marketplace's own files read well.\n")
        return 1 if lines else 0
    database = _migrate(home)
    market = Marketplace(
        home,
        store,
        Plugins(home, database, vault),
        SkillLibrary(home.skills_folder, SkillUsage(database)),
        stored=vault.names,
    )
    if action == "list":
        listing = market.listing(refresh=args.refresh)
        if listing.problem:
            sys.stdout.write(f"The marketplace could not be read now: {listing.problem}\n")
        for kind in Kind:
            if args.kind and kind.value != args.kind:
                continue
            rows = [row for row in listing.rows if row.kind is kind]
            if rows:
                sys.stdout.write(f"{_KIND_TITLES[kind]}\n")
            for row in rows:
                sys.stdout.write(_market_line(row))
        return 0
    name: str = args.name
    if action == "show":
        page = market.page(name)
        row = page.row
        sys.stdout.write(_market_line(row))
        if row.source_url:
            sys.stdout.write(f"  {row.source_url}\n")
        for need in page.needs:
            sys.stdout.write(f"  needs the {need.kind} {need.name}: {need.state}\n")
        for secret in page.secrets:
            stored = "stored" if secret.stored else "not stored"
            sys.stdout.write(f"  secret {secret.name}: {stored}. {secret.description}\n")
        sys.stdout.write(f"\n{page.readme.strip()}\n")
        return 0
    if action == "remove":
        market.remove(name)
        sys.stdout.write(f"{name} was removed.\n")
        return 0
    done = market.install(name) if action == "install" else market.update(name, force=args.force)
    if done.local_changes:
        sys.stdout.write(
            f"{name} was changed here since it came. Run again with --force to update it; a "
            "skill keeps what it holds now as a version.\n"
        )
        return 1
    if not done.changed:
        sys.stdout.write(f"{name} is up to date.\n")
        return 0
    for brought in done.changed[:-1]:
        sys.stdout.write(f"{brought} came along, since {name} needs it.\n")
    sys.stdout.write(f"{name} is {'installed' if action == 'install' else 'updated'}.\n")
    if done.row.state is State.OFF:
        sys.stdout.write(f"Turn it on with `atlas plugins enable {name}`, or in the app.\n")
    if done.row.problem:
        sys.stdout.write(f"{done.row.problem}\n")
    return 0


def _market_line(row: Row) -> str:
    marks = [row.state.value]
    if row.tier.value == "official":
        marks.append("official")
    if row.update:
        marks.append("update")
    version = f" {row.version}" if row.version else ""
    line = f"  {row.name}{version}  {row.title} by {row.author}  ({', '.join(marks)})\n"
    if row.summary:
        line += f"    {row.summary}\n"
    if row.problem:
        line += f"    {row.problem}\n"
    return line


def _bots(args: argparse.Namespace, home: Home, stdin: TextIO) -> int:
    """The home's bots (docs/ARCHITECTURE.md, Bots)."""
    bots = Bots(home.bots_folder)
    action: str = args.action
    try:
        if action == "list":
            for bot in bots.list():
                about = f"  {bot.description}" if bot.description else ""
                sys.stdout.write(f"{bot.name}  {bot.title}{about}\n")
            return 0
        if action == "create":
            body = ""
            if args.mission_file == "-":
                body = stdin.read()
            elif args.mission_file:
                body = Path(args.mission_file).read_text(encoding="utf-8")
            bot = bots.create(
                args.name,
                title=args.title,
                description=args.description,
                body=body,
                model=args.model,
            )
            sys.stdout.write(f"Created the bot {bot.name} at {bot.folder}.\n")
            return 0
        if action == "show":
            bot = bots.get(args.name)
            sys.stdout.write(
                f"{bot.name}  {bot.folder}\n"
                f"  title: {bot.title}\n"
                f"  description: {bot.description or 'none'}\n"
                f"  model: {bot.model or "the home's"}\n"
                f"  tools: {_allowed(bot.tools)}\n"
                f"  MCP servers: {_allowed(bot.mcp_servers)}\n"
                f"  skills: {_allowed(bot.skills)}\n"
                f"  memory: {'yes' if bot.memory_file.is_file() else 'none yet'}\n"
            )
            return 0
        bots.get(args.name)
        if not args.yes:
            if not stdin.isatty():
                sys.stderr.write(
                    f"atlas: deleting {args.name} removes its definition, its memory and its "
                    "skills. Run again with --yes.\n"
                )
                return 1
            sys.stdout.write(f"Delete the bot {args.name}, its memory and its skills? [y/N] ")
            sys.stdout.flush()
            if stdin.readline().strip().lower() not in ("y", "yes"):
                sys.stdout.write("Nothing was deleted.\n")
                return 1
        bots.delete(args.name)
        # Its conversations are kept, as every conversation is; a bot made later under the
        # same name starts its own.
        database = _migrate(home)
        ConversationStore(database).release(args.name)
        SkillUsage(database, args.name).forget()
        routines = RoutineStore(database)
        for routine in routines.all():
            if routine.bot == args.name:
                routines.remove(routine.id)
        sys.stdout.write(f"Deleted the bot {args.name}.\n")
        return 0
    except (BotError, HomeError, OSError) as error:
        sys.stderr.write(f"atlas: {error}\n")
        return 1


def _allowed(names: tuple[str, ...] | None) -> str:
    if names is None:
        return "all the home has"
    return ", ".join(names) or "none"


def _home_flag(name: str) -> str:
    return "" if name == DEFAULT_HOME else f" --home {name}"


def _change(operation: Operation) -> str:
    match operation.action:
        case Action.ADD:
            return f"add: {operation.content}"
        case Action.REPLACE:
            return f"replace the whole entry: {operation.entry}\n      with: {operation.content}"
        case Action.REMOVE:
            return f"remove: {operation.entry}"


def _secrets(args: argparse.Namespace, vault: Vault, stdin: TextIO) -> int:
    action: str = args.action
    if action == "set":
        name: str = args.name
        vault.set(name, _read_value(name, stdin))
        sys.stdout.write(f"Stored {name}.\n")
        return 0
    if action == "delete":
        if not vault.delete(args.name):
            sys.stderr.write(f"atlas: there is no secret named {args.name}.\n")
            return 1
        sys.stdout.write(f"Deleted {args.name}.\n")
        return 0
    if action == "list":
        for name in vault.names():
            sys.stdout.write(f"{name}\t{vault.updated_at(name)}\n")
        problem = vault.problem()
        if problem is not None:
            sys.stderr.write(f"atlas: the vault does not open: {problem}\n")
            return 1
        return 0
    problems = vault.verify()
    for name, problem in problems.items():
        sys.stderr.write(f"{name}: {problem}\n")
    sys.stdout.write(f"{len(vault.names()) - len(problems)} secrets open, {len(problems)} fail.\n")
    return 1 if problems else 0


def _mcp(args: argparse.Namespace, home: Home, vault: Vault, stdin: TextIO) -> int:
    """`atlas mcp login` and `logout`: the sign-in is the user's, in the browser
    (`atlas.mcp.oauth`); a server serving the home connects the server within a few seconds."""
    name: str = args.name
    sign_ins = vault.beside(MCP_SIGN_IN_FILE)
    if args.action == "logout":
        if not sign_ins.delete(name):
            sys.stderr.write(f"atlas: Atlas is not signed in to {name}.\n")
            return 1
        sys.stdout.write(f"Signed out of {name}.\n")
        return 0
    if importlib.util.find_spec("mcp") is None:
        sys.stderr.write("atlas: MCP servers need the mcp extra, which is not installed.\n")
        return 1
    mcp: Any = importlib.import_module("atlas.mcp")
    try:
        text = home.mcp_file.read_text(encoding="utf-8") if home.mcp_file.exists() else ""
        servers = {server.name: server for server in mcp.parse_config(text, str(home.mcp_file))}
        declared = servers.get(name)
        if declared is None:
            known = ", ".join(sorted(servers)) or "none"
            sys.stderr.write(f"atlas: mcp.json has no server named {name} (it has: {known}).\n")
            return 1
        if declared.transport is mcp.Transport.STDIO:
            sys.stderr.write(f"atlas: {name} runs on this machine and does not sign in.\n")
            return 1
        server = mcp.filled(declared, vault.get)

        def show(page: str) -> None:
            sys.stdout.write(f"Open this page and authorize Atlas:\n\n  {page}\n\n")
            if not args.no_browser:
                with suppress(webbrowser.Error):
                    webbrowser.open(page)
            sys.stdout.write(
                "Waiting for the browser. If it is on another machine, paste here the address "
                "it landed on, and press Enter.\n"
            )
            sys.stdout.flush()

        asyncio.run(mcp.sign_in(name, server.url, sign_ins, show, _pasted(stdin)))
    except mcp.McpError as error:
        sys.stderr.write(f"atlas: {error}\n")
        return 1
    sys.stdout.write(f"Signed in to {name}. A running Atlas connects it within a few seconds.\n")
    return 0


def _pasted(stdin: TextIO) -> Callable[[], Awaitable[str]]:
    """A line the user pastes, read by a thread of its own that never holds the process open."""

    async def wait() -> str:
        loop = asyncio.get_running_loop()
        answer: asyncio.Future[str] = loop.create_future()

        def give(line: str) -> None:
            if not answer.done():
                answer.set_result(line)

        def read() -> None:
            line = stdin.readline()
            if line.strip():
                with suppress(RuntimeError):  # the sign-in ended first, and its loop with it
                    loop.call_soon_threadsafe(give, line)

        threading.Thread(target=read, daemon=True).start()
        return await answer

    return wait


def _check_models(args: argparse.Namespace, settings: Settings, home: Home, vault: Vault) -> int:
    """Ask each provider about its key, where it costs nothing; 1 if one cannot be used."""
    catalog = load_catalog(home.models_file)
    names: list[str] = list(args.providers)
    if not names:
        stored = set(vault.names())
        names = [p.id for p in catalog.providers.values() if p.key is not None and p.key in stored]
        if settings.model.provider is not None and settings.model.provider not in names:
            names.append(settings.model.provider)
    if not names:
        sys.stderr.write(
            "atlas: no provider has a key in the vault. Store one with `atlas secrets set "
            "OPENAI_API_KEY` (or OPENROUTER_API_KEY, GEMINI_API_KEY).\n"
        )
        return 1
    for name in names:
        catalog.provider(name)  # an unknown provider stops here, with the known ones
    models = Models(catalog, vault, settings.model)

    async def check_all() -> list[KeyCheck]:
        try:
            return list(await asyncio.gather(*(models.check_key(name) for name in names)))
        finally:
            await models.close()

    checks = asyncio.run(check_all())
    for found in checks:
        sys.stdout.write(f"{found.provider}\t{found.status.value}\t{found.detail}\n")
    return 0 if all(found.usable for found in checks) else 1


def _read_value(name: str, stdin: TextIO) -> str:
    """The value, typed without echo at a terminal, or piped in. One trailing line break is cut."""
    if stdin.isatty():
        return getpass.getpass(f"Value for {name}: ")
    value = stdin.read()
    return value.removesuffix("\n").removesuffix("\r")


if __name__ == "__main__":
    sys.exit(main())
