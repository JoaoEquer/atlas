"""Settings are read once, from defaults, `config.toml` and `ATLAS_*` variables, in that order.

Every test hands `load_settings` its own environment and a temporary home: none reads the
process environment or a live home.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from atlas.home import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    EDITABLE,
    SECRET_VARIABLES,
    Home,
    MemorySettings,
    ModelSettings,
    SecretText,
    Settings,
    SettingsError,
    drop_section,
    edit_settings,
    editable_values,
    forget_secret_variables,
    load_settings,
    open_home,
    resolve_home,
)


def _write_config(home: Path, text: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(text, encoding="utf-8")


def _environ(home: Path, **variables: str) -> dict[str, str]:
    return {"ATLAS_HOME": str(home), **variables}


# -- where the home is ---------------------------------------------------------------------------


def test_without_atlas_home_the_home_is_dot_atlas_in_the_user_folder() -> None:
    assert resolve_home({}) == Path.home() / ".atlas"


def test_a_blank_atlas_home_counts_as_unset() -> None:
    assert resolve_home({"ATLAS_HOME": "   "}) == Path.home() / ".atlas"


def test_atlas_home_wins(tmp_path: Path) -> None:
    assert resolve_home({"ATLAS_HOME": str(tmp_path)}) == tmp_path


def test_a_tilde_is_the_user_folder() -> None:
    assert resolve_home({"ATLAS_HOME": "~/elsewhere"}) == Path.home() / "elsewhere"


def test_a_relative_home_is_refused_rather_than_read_from_the_current_folder() -> None:
    with pytest.raises(SettingsError, match="relative path"):
        resolve_home({"ATLAS_HOME": "data/atlas"})


# -- precedence ----------------------------------------------------------------------------------


def test_with_nothing_declared_the_defaults_hold(tmp_path: Path) -> None:
    settings = load_settings(_environ(tmp_path)).settings

    assert settings.home == tmp_path
    assert (settings.server.host, settings.server.port) == (DEFAULT_HOST, DEFAULT_PORT)
    assert (settings.log.level, settings.log.file) == ("INFO", True)
    assert (settings.vault.key, settings.vault.key_file) == (None, None)
    assert settings.model == ModelSettings()


def test_the_model_comes_from_the_file_and_a_variable_overrides_it(tmp_path: Path) -> None:
    _write_config(tmp_path, '[model]\nprovider = "openai"\nname = "gpt-6-luna"\n')

    settings = load_settings(
        _environ(tmp_path, ATLAS_MODEL_NAME="gpt-6-sol", ATLAS_MODEL_EFFORT="low")
    ).settings

    assert (settings.model.provider, settings.model.name, settings.model.effort) == (
        "openai",
        "gpt-6-sol",
        "low",
    )


def test_the_users_deny_rules_and_the_approval_wait_come_from_the_file_or_a_variable(
    tmp_path: Path,
) -> None:
    _write_config(tmp_path, '[trust]\ndeny = ["git push --force*", " "]\napproval_timeout = 60\n')

    from_file = load_settings(_environ(tmp_path)).settings.trust
    from_variable = load_settings(
        _environ(tmp_path, ATLAS_TRUST_DENY="rm -rf *\ndocker system prune*")
    ).settings.trust

    assert from_file.deny == ("git push --force*",)
    assert from_file.approval_timeout == 60
    assert from_variable.deny == ("rm -rf *", "docker system prune*")
    assert load_settings(_environ(tmp_path / "none")).settings.trust.approval_timeout == 300
    _write_config(tmp_path, '[trust]\ndeny = "git push"\n')
    with pytest.raises(SettingsError, match="must be a list of texts"):
        load_settings(_environ(tmp_path))


def test_the_memorys_limits_come_from_the_file_or_a_variable_within_their_range(
    tmp_path: Path,
) -> None:
    _write_config(tmp_path, "[memory]\nuser_limit = 800\nmemory_limit = 5000\n")

    from_file = load_settings(_environ(tmp_path)).settings.memory
    from_variable = load_settings(_environ(tmp_path, ATLAS_MEMORY_USER_LIMIT="900")).settings

    assert (from_file.user_limit, from_file.memory_limit) == (800, 5_000)
    assert from_variable.memory.user_limit == 900
    assert load_settings(_environ(tmp_path / "none")).settings.memory == MemorySettings()
    assert MemorySettings() == MemorySettings(user_limit=1_375, memory_limit=2_200)
    _write_config(tmp_path, "[memory]\nmemory_limit = 100\n")
    with pytest.raises(SettingsError, match="between 500 and 20000"):
        load_settings(_environ(tmp_path))


def test_the_users_ask_rules_come_from_the_file(tmp_path: Path) -> None:
    _write_config(tmp_path, '[trust]\nask = ["git push*"]\nask_skip = ["gh issue comment*"]\n')

    trust = load_settings(_environ(tmp_path)).settings.trust

    assert trust.ask == ("git push*",)
    assert trust.ask_skip == ("gh issue comment*",)
    assert load_settings(_environ(tmp_path / "none")).settings.trust.ask == ()


def test_the_file_overrides_the_defaults(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        '[server]\nport = 19000\n\n[log]\nlevel = "debug"\nfile = false\n',
    )

    settings = load_settings(_environ(tmp_path)).settings

    assert settings.server.port == 19000
    assert (settings.log.level, settings.log.file) == ("DEBUG", False)


def test_a_variable_overrides_the_file(tmp_path: Path) -> None:
    _write_config(tmp_path, "[server]\nport = 19000\n")

    settings = load_settings(_environ(tmp_path, ATLAS_SERVER_PORT="19001")).settings

    assert settings.server.port == 19001


def test_a_blank_variable_leaves_the_file_in_charge(tmp_path: Path) -> None:
    _write_config(tmp_path, "[server]\nport = 19000\n")

    settings = load_settings(_environ(tmp_path, ATLAS_SERVER_PORT="")).settings

    assert settings.server.port == 19000


@pytest.mark.parametrize(("text", "expected"), [("1", True), ("YES", True), ("off", False)])
def test_switches_read_the_usual_spellings(tmp_path: Path, text: str, expected: bool) -> None:
    assert load_settings(_environ(tmp_path, ATLAS_LOG_FILE=text)).settings.log.file is expected


# -- what is refused, and how the message helps ----------------------------------------------------


@pytest.mark.parametrize(
    ("variables", "message"),
    [
        ({"ATLAS_SERVER_PORT": "abc"}, "ATLAS_SERVER_PORT='abc' must be a whole number"),
        ({"ATLAS_SERVER_PORT": "70000"}, "between 1 and 65535"),
        ({"ATLAS_LOG_FILE": "maybe"}, "must be true or false"),
        ({"ATLAS_LOG_LEVEL": "loud"}, "Use one of: DEBUG, INFO, WARNING, ERROR"),
        ({"ATLAS_VAULT_KEY_FILE": "key.txt"}, "relative path"),
    ],
)
def test_a_malformed_variable_names_itself_and_the_fix(
    tmp_path: Path, variables: dict[str, str], message: str
) -> None:
    with pytest.raises(SettingsError, match=message):
        load_settings(_environ(tmp_path, **variables))


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ('[server]\nport = "19000"\n', "must be a whole number"),
        ("[server]\nport = true\n", "must be a whole number"),
        ("[log]\nfile = 1\n", "must be true or false"),
        ('[server]\nhost = ""\n', "non-empty text"),
        ("server = 1\n", r"\[server\] must be a table"),
    ],
)
def test_a_malformed_file_value_names_the_file_and_the_key(
    tmp_path: Path, config: str, message: str
) -> None:
    _write_config(tmp_path, config)

    with pytest.raises(SettingsError, match=message):
        load_settings(_environ(tmp_path))


def test_a_typo_in_the_file_is_an_error_that_lists_the_known_keys(tmp_path: Path) -> None:
    _write_config(tmp_path, "[server]\nprot = 19000\n")

    with pytest.raises(SettingsError, match=r"unknown setting \[server\] prot.*host, port"):
        load_settings(_environ(tmp_path))


def test_an_unknown_section_is_an_error(tmp_path: Path) -> None:
    _write_config(tmp_path, "[sever]\nport = 19000\n")

    with pytest.raises(SettingsError, match=r"unknown section \[sever\]"):
        load_settings(_environ(tmp_path))


def test_a_section_that_moved_is_read_past_not_refused(tmp_path: Path) -> None:
    """Step 16c moved `[executors] claude_code` into the Claude Code plugin's `program`. Refusing
    it stopped the update's own build, which then went back to the version before (15/10/2026)."""
    _write_config(tmp_path, '[executors]\nclaude_code = ["/opt/claude"]\n\n[server]\nport = 1\n')

    loaded = load_settings(_environ(tmp_path))

    assert loaded.moved == ("executors",)
    assert loaded.settings.server.port == 1
    assert load_settings(_environ(tmp_path / "other")).moved == ()


# -- taking a moved section out of the file ------------------------------------------------------

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
MOVED = """# Atlas, set up by hand
[model]
provider = "openai"  # the cheap one

[executors]
# where my claude is
claude_code = ["C:/tools/claude.exe", "--verbose"]

[log]
level = "INFO"
"""


def _home_with(tmp_path: Path, text: str) -> Home:
    home = open_home(tmp_path / "home")
    home.config_file.write_text(text, encoding="utf-8")
    return home


def test_a_moved_section_is_cut_out_after_it_moved_and_the_file_is_kept_first(
    tmp_path: Path,
) -> None:
    home = _home_with(tmp_path, MOVED)
    moved: list[dict[str, Any]] = []

    kept = drop_section(home, "executors", NOW, moved.append)

    assert moved == [{"claude_code": ["C:/tools/claude.exe", "--verbose"]}]
    assert home.config_file.read_text(encoding="utf-8") == (
        '# Atlas, set up by hand\n[model]\nprovider = "openai"  # the cheap one\n\n'
        '[log]\nlevel = "INFO"\n'
    )
    assert kept == home.backups_folder / "config-20261015T120000Z.toml"
    assert kept.read_text(encoding="utf-8") == MOVED
    assert load_settings(_environ(home.root)).moved == ()


def test_a_section_that_cannot_be_cut_alone_is_left_and_nothing_moves(tmp_path: Path) -> None:
    # A line of a multi-line array that starts with `[` reads like a table's header.
    text = '[server]\nport = 1\n[executors]\nclaude_code = [\n["nested"],\n]\n'
    home = _home_with(tmp_path, text)
    moved: list[dict[str, Any]] = []

    with pytest.raises(SettingsError, match="could not be cut out on its own"):
        drop_section(home, "executors", NOW, moved.append)

    assert moved == []
    assert home.config_file.read_text(encoding="utf-8") == text
    assert not home.backups_folder.exists()


def test_a_move_that_fails_leaves_the_file(tmp_path: Path) -> None:
    home = _home_with(tmp_path, MOVED)

    def refuse(_: dict[str, Any]) -> None:
        raise SettingsError("the plugin's setting could not be written")

    with pytest.raises(SettingsError, match="could not be written"):
        drop_section(home, "executors", NOW, refuse)

    assert home.config_file.read_text(encoding="utf-8") == MOVED
    assert not home.backups_folder.exists()


def test_a_file_that_is_not_toml_says_where_it_broke(tmp_path: Path) -> None:
    _write_config(tmp_path, "[server\nport = 1\n")

    with pytest.raises(SettingsError, match=r"config.toml is not valid TOML.*line 1"):
        load_settings(_environ(tmp_path))


def test_an_unknown_variable_is_reported_and_not_fatal(tmp_path: Path) -> None:
    loaded = load_settings(_environ(tmp_path, ATLAS_SERVR_PORT="1", ATLAS_LOG_LEVEL="ERROR"))

    assert loaded.unknown_variables == ("ATLAS_SERVR_PORT",)
    assert loaded.settings.log.level == "ERROR"


# -- secrets -------------------------------------------------------------------------------------


def test_the_vault_key_in_the_file_is_refused(tmp_path: Path) -> None:
    _write_config(tmp_path, '[vault]\nkey = "AGE-SECRET-KEY-1ABC"\n')

    with pytest.raises(SettingsError, match="ATLAS_VAULT_KEY or ATLAS_VAULT_KEY_FILE"):
        load_settings(_environ(tmp_path))


def test_the_vault_key_comes_from_the_environment_and_never_prints(tmp_path: Path) -> None:
    key = "AGE-SECRET-KEY-1QQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQ"

    settings = load_settings(_environ(tmp_path, ATLAS_VAULT_KEY=key)).settings

    assert settings.vault.key == SecretText(key)
    assert settings.vault.key is not None
    assert settings.vault.key.reveal() == key
    assert key not in repr(settings)
    assert key not in str(settings.vault.key)


def test_secret_variables_are_forgotten_so_child_processes_do_not_inherit_them() -> None:
    environ = {"ATLAS_VAULT_KEY": "AGE-SECRET-KEY-1X", "ATLAS_LOG_LEVEL": "INFO"}

    forget_secret_variables(environ)

    assert environ == {"ATLAS_LOG_LEVEL": "INFO"}
    assert "ATLAS_VAULT_KEY" in SECRET_VARIABLES


def test_the_server_token_comes_from_the_environment_and_is_forgotten(tmp_path: Path) -> None:
    environ = _environ(tmp_path, ATLAS_SERVER_TOKEN="a-long-random-token")

    settings = load_settings(environ).settings
    forget_secret_variables(environ)

    assert settings.server.token == SecretText("a-long-random-token")
    assert "a-long-random-token" not in repr(settings)
    assert "ATLAS_SERVER_TOKEN" not in environ


def test_the_server_token_in_the_file_is_refused(tmp_path: Path) -> None:
    _write_config(tmp_path, '[server]\ntoken = "written-down"\n')

    with pytest.raises(SettingsError, match=r"set ATLAS_SERVER_TOKEN instead"):
        load_settings(_environ(tmp_path))


def test_the_workspace_is_the_users_home_folder_unless_set(tmp_path: Path) -> None:
    work = tmp_path / "work"
    _write_config(tmp_path, f'[workspace]\nfolder = "{work.as_posix()}"\n')

    assert load_settings(_environ(tmp_path)).settings.workspace.folder == work
    assert Settings(home=tmp_path).workspace.folder is None


# -- the settings screen writes the file (step 15b) ----------------------------------------------


def _read_config(home: Path) -> str:
    return (home / "config.toml").read_text(encoding="utf-8")


def test_a_setting_written_keeps_the_comments_and_the_order_of_the_rest(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        "# my settings\n"
        "[model]\n"
        'provider = "openai"  # the one I pay for\n'
        "\n"
        "[log]\n"
        'level = "DEBUG"\n',
    )

    settings = edit_settings(tmp_path, _environ(tmp_path), {("model", "name"): "gpt-6-luna"})

    assert _read_config(tmp_path) == (
        "# my settings\n"
        "[model]\n"
        'provider = "openai"  # the one I pay for\n'
        'name = "gpt-6-luna"\n'
        "\n"
        "[log]\n"
        'level = "DEBUG"\n'
    )
    assert settings.model == ModelSettings(provider="openai", name="gpt-6-luna")
    assert load_settings(_environ(tmp_path)).settings == settings


def test_a_setting_replaced_changes_its_line_only(tmp_path: Path) -> None:
    _write_config(tmp_path, '[model]\nprovider = "openai"  # old\nname = "x"\n')

    edit_settings(tmp_path, _environ(tmp_path), {("model", "provider"): "gemini"})

    assert _read_config(tmp_path) == '[model]\nprovider = "gemini"\nname = "x"\n'


def test_a_new_section_goes_at_the_end_and_a_missing_file_is_made(tmp_path: Path) -> None:
    edit_settings(tmp_path, _environ(tmp_path), {("time", "zone"): "America/Sao_Paulo"})
    edit_settings(tmp_path, _environ(tmp_path), {("trust", "approval_timeout"): "600"})

    assert _read_config(tmp_path) == (
        '[time]\nzone = "America/Sao_Paulo"\n\n[trust]\napproval_timeout = 600\n'
    )


def test_a_list_spanning_lines_is_replaced_whole_and_taken_out_whole(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        '[trust]\ndeny = [\n    "rm -rf /*",\n    "git push --force*",\n]\napproval_timeout = 60\n',
    )

    settings = edit_settings(
        tmp_path, _environ(tmp_path), {("trust", "deny"): "git push --force*\n\n  shutdown*  \n"}
    )

    assert settings.trust.deny == ("git push --force*", "shutdown*")
    assert _read_config(tmp_path) == (
        '[trust]\ndeny = [\n    "git push --force*",\n    "shutdown*",\n]\napproval_timeout = 60\n'
    )
    settings = edit_settings(tmp_path, _environ(tmp_path), {("trust", "deny"): None})
    assert settings.trust.deny == ()
    assert _read_config(tmp_path) == "[trust]\napproval_timeout = 60\n"


def test_a_blank_value_takes_the_setting_out(tmp_path: Path) -> None:
    _write_config(tmp_path, '[workspace]\nfolder = "/somewhere"\n')

    settings = edit_settings(tmp_path, _environ(tmp_path), {("workspace", "folder"): "  "})

    assert settings.workspace.folder is None
    assert _read_config(tmp_path) == "[workspace]\n"


def test_a_windows_path_and_quotes_are_written_as_toml_reads_them(tmp_path: Path) -> None:
    # Backslashes on Windows, quotes everywhere.
    folder = tmp_path / 'My "work"'
    rule = 'echo "hi" \\ there'

    edit_settings(tmp_path, _environ(tmp_path), {("trust", "ask"): rule})
    edit_settings(tmp_path, _environ(tmp_path), {("workspace", "folder"): str(folder)})

    settings = load_settings(_environ(tmp_path)).settings
    assert settings.trust.ask == (rule,)
    assert settings.workspace.folder == folder


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({("time", "zone"): "Mars/Olympus"}, "is not a time zone"),
        ({("workspace", "folder"): "relative/folder"}, "is a relative path"),
        ({("trust", "approval_timeout"): "5"}, "must be between 10 and 86400"),
        ({("trust", "approval_timeout"): "soon"}, "must be a whole number"),
        ({("server", "port"): "9000"}, "not a setting the settings screen writes"),
    ],
)
def test_a_refused_value_leaves_the_file_as_it_was(
    tmp_path: Path, change: dict[tuple[str, str], str], message: str
) -> None:
    before = "# mine\n[model]\nprovider = 'openai'\n"
    _write_config(tmp_path, before)

    with pytest.raises(SettingsError, match=message):
        edit_settings(tmp_path, _environ(tmp_path), change)

    assert _read_config(tmp_path) == before


def test_a_setting_a_variable_sets_is_refused_and_shown_locked(tmp_path: Path) -> None:
    environ = _environ(tmp_path, ATLAS_MODEL_PROVIDER="gemini")
    _write_config(tmp_path, '[model]\nprovider = "openai"\n[trust]\ndeny = ["a", "b"]\n')

    with pytest.raises(SettingsError, match="set by ATLAS_MODEL_PROVIDER"):
        edit_settings(tmp_path, environ, {("model", "provider"): "openrouter"})

    values = {(v.setting.section, v.setting.key): v for v in editable_values(tmp_path, environ)}
    assert [(v.setting.section, v.setting.key) for v in editable_values(tmp_path, environ)] == [
        (setting.section, setting.key) for setting in EDITABLE
    ]
    assert values["model", "provider"].written == "openai"
    assert values["model", "provider"].variable == "ATLAS_MODEL_PROVIDER"
    assert values["trust", "deny"].written == "a\nb"
    assert values["trust", "deny"].variable is None
    assert values["time", "zone"].written is None


def test_a_file_that_cannot_be_cut_line_by_line_is_left_alone(tmp_path: Path) -> None:
    # An inline table writes the section's settings where the screen cannot change one alone.
    before = 'model = { provider = "openai" }\n'
    _write_config(tmp_path, before)

    with pytest.raises(SettingsError, match="change it by hand"):
        edit_settings(tmp_path, _environ(tmp_path), {("model", "name"): "gpt-6-luna"})

    assert _read_config(tmp_path) == before
