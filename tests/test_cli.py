"""The `atlas` command: what it prints, what it refuses, where it serves, and its vault."""

import dataclasses
import io
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pyrage
import pytest

from atlas import __version__
from atlas.__main__ import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    SCHEMAS,
    _server_token,  # pyright: ignore[reportPrivateUsage]
    build_parser,
    listen_address,
    main,
)
from atlas.conversations import BOT, ConversationStore
from atlas.home import (
    Database,
    Redactor,
    VaultSettings,
    format_utc,
    load_settings,
    migrate,
    open_home,
    utc_now,
)
from atlas.memory import SCHEMA as MEMORY_SCHEMA
from atlas.memory import (
    Action,
    MemoryFiles,
    Operation,
    Proposal,
    Proposals,
    ProposalStore,
    Target,
)
from atlas.models import SCHEMA as MODELS_SCHEMA
from atlas.models import CallLedger, CallOutcome, CallRecord, Cost, CostSource, Usage
from atlas.secrets import Vault
from atlas.server import KEPT_TOKEN
from atlas.skills import SCHEMA, Origin, SkillLibrary, SkillOperation, SkillUsage, sync_bundled


def test_version_prints_the_installed_version(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["version"])

    assert exit_code == 0
    assert capsys.readouterr().out == f"{__version__}\n"


def test_serve_listens_only_on_this_machine_by_default(tmp_path: Path) -> None:
    args = build_parser().parse_args(["serve"])
    settings = load_settings({"ATLAS_HOME": str(tmp_path)}).settings

    assert listen_address(args, settings) == (DEFAULT_HOST, DEFAULT_PORT)
    assert DEFAULT_HOST == "127.0.0.1"


def test_a_flag_wins_over_the_variable(tmp_path: Path) -> None:
    args = build_parser().parse_args(["serve", "--port", "19100"])
    settings = load_settings(
        {"ATLAS_HOME": str(tmp_path), "ATLAS_SERVER_PORT": "19000", "ATLAS_SERVER_HOST": "0.0.0.0"}  # noqa: S104
    ).settings

    assert listen_address(args, settings) == ("0.0.0.0", 19100)  # noqa: S104


def test_a_bad_setting_stops_before_serving_and_says_why(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    environ = {"ATLAS_HOME": str(tmp_path), "ATLAS_SERVER_PORT": "not-a-port"}

    exit_code = main(["serve"], environ)

    assert exit_code == 1
    assert "ATLAS_SERVER_PORT='not-a-port' must be a whole number" in capsys.readouterr().err


def test_a_command_is_required() -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([])

    assert exit_info.value.code == 2


# -- atlas secrets -------------------------------------------------------------------------------


def _run(home: Path, *argv: str, stdin: str = "", environ: dict[str, str] | None = None) -> int:
    return main(list(argv), environ or {"ATLAS_HOME": str(home)}, io.StringIO(stdin))


def test_a_secret_set_from_standard_input_is_listed_by_name_never_by_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(tmp_path, "secrets", "set", "OPENAI_API_KEY", stdin="sk-proj-from-stdin\n") == 0
    capsys.readouterr()

    assert _run(tmp_path, "secrets", "list") == 0  # a new call, a new vault object

    out = capsys.readouterr().out
    assert out.startswith("OPENAI_API_KEY\t")
    assert "sk-proj" not in out


def test_check_reports_every_secret_open(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(tmp_path, "secrets", "set", "A", stdin="value-a")
    _run(tmp_path, "secrets", "set", "B", stdin="value-b\r\n")
    capsys.readouterr()

    assert _run(tmp_path, "secrets", "check") == 0
    assert capsys.readouterr().out == "2 secrets open, 0 fail.\n"


def test_delete_removes_and_a_second_delete_says_there_is_none(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _run(tmp_path, "secrets", "set", "A", stdin="value-a")

    assert _run(tmp_path, "secrets", "delete", "A") == 0
    assert _run(tmp_path, "secrets", "delete", "A") == 1
    assert "there is no secret named A" in capsys.readouterr().err


def test_an_empty_value_is_refused_with_the_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(tmp_path, "secrets", "set", "A", stdin="\n") == 1
    assert "empty" in capsys.readouterr().err


def test_a_vault_without_its_key_is_reported_by_list(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    key = str(pyrage.x25519.Identity.generate())
    with_key = {"ATLAS_HOME": str(tmp_path), "ATLAS_VAULT_KEY": key}
    _run(tmp_path, "secrets", "set", "A", stdin="value-a", environ=with_key)
    capsys.readouterr()

    assert _run(tmp_path, "secrets", "list") == 1

    captured = capsys.readouterr()
    assert captured.out.startswith("A\t")
    assert "ATLAS_VAULT_KEY_FILE" in captured.err


def test_the_vault_key_variable_is_gone_after_the_settings_are_read(tmp_path: Path) -> None:
    environ = {
        "ATLAS_HOME": str(tmp_path),
        "ATLAS_VAULT_KEY": str(pyrage.x25519.Identity.generate()),
    }

    _run(tmp_path, "secrets", "set", "A", stdin="value-a", environ=environ)

    assert "ATLAS_VAULT_KEY" not in environ


# -- the server token ----------------------------------------------------------------------------


def _vault_of(home: Path) -> Vault:
    return Vault(open_home(home).secrets_folder, VaultSettings(), Redactor())


def test_server_token_prints_the_kept_token_and_keeps_printing_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(tmp_path, "server", "token") == 0
    first = capsys.readouterr().out
    assert _run(tmp_path, "server", "token") == 0

    assert capsys.readouterr().out == first
    assert first == f"{_vault_of(tmp_path).get(KEPT_TOKEN)}\n"
    assert len(first.strip()) >= 40


def test_off_loopback_the_server_uses_the_kept_token_and_on_loopback_a_fresh_one(
    tmp_path: Path,
) -> None:
    settings = load_settings({"ATLAS_HOME": str(tmp_path)}).settings
    vault = _vault_of(tmp_path)

    in_container = _server_token(settings, "0.0.0.0", vault)  # noqa: S104
    assert in_container == _server_token(settings, "0.0.0.0", vault)  # noqa: S104
    assert in_container == vault.get(KEPT_TOKEN)
    assert _server_token(settings, "127.0.0.1", vault) not in {
        in_container,
        _server_token(settings, "127.0.0.1", vault),
    }


def test_a_given_token_wins_everywhere(tmp_path: Path) -> None:
    environ = {"ATLAS_HOME": str(tmp_path), "ATLAS_SERVER_TOKEN": "given-token-0123456789"}
    settings = load_settings(environ).settings
    vault = _vault_of(tmp_path)

    assert _server_token(settings, "0.0.0.0", vault) == "given-token-0123456789"  # noqa: S104
    assert _server_token(settings, "127.0.0.1", vault) == "given-token-0123456789"
    assert vault.names() == []


# -- homes, backup and restore -------------------------------------------------------------------


def test_homes_are_made_listed_used_and_shown_from_the_command_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "atlas"
    assert _run(root, "secrets", "set", "OPENAI_API_KEY", stdin="sk-root\n") == 0

    assert _run(root, "home", "create", "work", "--clone") == 0
    assert _run(root, "home", "use", "work") == 0
    capsys.readouterr()
    assert _run(root, "home", "list") == 0
    listed = capsys.readouterr().out.splitlines()
    assert [line.split()[:2] for line in listed] == [["default", str(root)], ["*", "work"]]
    # The active home is where a command runs; --home names another.
    assert _run(root, "home", "show") == 0
    shown = capsys.readouterr().out
    assert shown.startswith("work  ")
    assert "keys: OPENAI_API_KEY" in shown
    assert _run(root, "--home", "default", "secrets", "list") == 0
    assert "OPENAI_API_KEY" in capsys.readouterr().out
    assert _run(root, "--home", "nope", "secrets", "list") == 1
    assert "There is no home 'nope'" in capsys.readouterr().err


def test_deleting_a_home_asks_unless_told_not_to(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "atlas"
    assert _run(root, "home", "create", "work") == 0

    assert _run(root, "home", "delete", "work") == 1
    assert "Run again with --yes" in capsys.readouterr().err
    assert (root / "homes" / "work").is_dir()
    assert _run(root, "home", "delete", "work", "--yes") == 0
    assert not (root / "homes" / "work").exists()


def test_a_backup_restores_into_another_home_and_never_carries_the_keys(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "atlas"
    assert _run(root, "secrets", "set", "OPENAI_API_KEY", stdin="sk-only-here\n") == 0
    (root / "memory").mkdir()
    (root / "memory" / "USER.md").write_text("Ana, designer.", encoding="utf-8")
    assert _run(root, "memory", "proposals") == 0  # makes the database
    assert _run(root, "home", "create", "copy") == 0
    capsys.readouterr()

    assert _run(root, "backup", "-o", str(tmp_path / "out")) == 0
    said = capsys.readouterr().out
    (written,) = (tmp_path / "out").iterdir()
    assert written.name.startswith("atlas-backup-default-")
    assert f"Restore with: atlas restore {written}" in said
    assert b"sk-only-here" not in written.read_bytes()

    assert _run(root, "--home", "copy", "restore", str(written)) == 0
    assert "The vault stays as it was" in capsys.readouterr().out
    copy = root / "homes" / "copy"
    assert (copy / "memory" / "USER.md").read_text(encoding="utf-8") == "Ana, designer."
    assert not (copy / "secrets").exists()
    # Restoring over data asks first; without a terminal, --yes is the way.
    assert _run(root, "--home", "copy", "restore", str(written)) == 1
    assert "already holds data" in capsys.readouterr().err
    assert _run(root, "--home", "copy", "restore", "--yes", str(written)) == 0


def test_a_backup_on_standard_output_restores_from_standard_input(
    tmp_path: Path, capfdbinary: pytest.CaptureFixture[bytes]
) -> None:
    # The container's way: `docker compose exec -T atlas atlas backup -o - > atlas.zip`.
    root = tmp_path / "atlas"
    (root / "memory").mkdir(parents=True)
    (root / "memory" / "USER.md").write_text("Ana.", encoding="utf-8")
    assert _run(root, "backup", "-o", "-") == 0
    zipped = capfdbinary.readouterr().out
    assert zipped.startswith(b"PK")

    other = tmp_path / "other"
    reader = io.TextIOWrapper(io.BytesIO(zipped))
    assert main(["restore", "-"], {"ATLAS_HOME": str(other)}, reader) == 0
    assert (other / "memory" / "USER.md").read_text(encoding="utf-8") == "Ana."


def test_a_damaged_backup_is_refused_before_the_home_is_touched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"not a zip at all")

    assert _run(tmp_path / "atlas", "restore", "--yes", str(broken)) == 1

    assert "is not a backup Atlas can read" in capsys.readouterr().err
    assert sorted(p.name for p in (tmp_path / "atlas").iterdir()) == []


# -- atlas bot -----------------------------------------------------------------------------------


def test_bots_are_made_listed_shown_and_deleted_from_the_command_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mission = tmp_path / "mission.md"
    mission.write_text("Keep the bills paid on time.\n", encoding="utf-8")
    root = tmp_path / "atlas"

    assert (
        _run(root, "bot", "create", "finance", "--title", "Finance", "--description", "Bills.") == 0
    )
    assert _run(root, "bot", "create", "work", "--mission-file", str(mission)) == 0
    assert _run(root, "bot", "create", "notes", "--mission-file", "-", stdin="Take notes.") == 0
    assert _run(root, "bot", "list") == 0
    assert _run(root, "bot", "show", "work") == 0
    out = capsys.readouterr().out

    assert f"Created the bot finance at {root / 'bots' / 'finance'}." in out
    assert "finance  Finance  Bills.\nnotes  notes\nwork  work\n" in out
    assert "  model: the home's\n  tools: all the home has\n" in out
    assert "  memory: none yet\n" in out
    assert "Keep the bills paid" in (root / "bots" / "work" / "BOT.md").read_text(encoding="utf-8")
    assert "Take notes." in (root / "bots" / "notes" / "BOT.md").read_text(encoding="utf-8")

    # Each home has its own bots.
    assert _run(root, "home", "create", "other") == 0
    capsys.readouterr()
    assert _run(root, "--home", "other", "bot", "list") == 0
    assert capsys.readouterr().out == ""


def test_a_bot_command_that_cannot_be_done_says_why(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(tmp_path, "bot", "create", "finance") == 0

    assert _run(tmp_path, "bot", "create", "finance") == 1
    assert _run(tmp_path, "bot", "create", "Atlas Bot") == 1
    assert _run(tmp_path, "bot", "create", "atlas") == 1
    assert _run(tmp_path, "bot", "show", "ghost") == 1
    assert _run(tmp_path, "bot", "create", "late", "--mission-file", str(tmp_path / "no.md")) == 1
    err = capsys.readouterr().err

    assert "already a bot 'finance'" in err
    assert "'Atlas Bot' is not a bot name" in err
    assert "'atlas' is taken" in err
    assert "There is no bot 'ghost'" in err
    assert "no.md" in err
    assert not (tmp_path / "bots" / "late").exists()


def test_deleting_a_bot_asks_unless_told_not_to(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(tmp_path, "bot", "create", "finance") == 0

    assert _run(tmp_path, "bot", "delete", "finance") == 1
    assert "Run again with --yes" in capsys.readouterr().err
    assert (tmp_path / "bots" / "finance").is_dir()
    assert _run(tmp_path, "bot", "delete", "ghost", "--yes") == 1
    conversations = ConversationStore(Database(tmp_path / "atlas.db"))
    migrate(Database(tmp_path / "atlas.db"), SCHEMAS)
    old = conversations.create(source=BOT, bot="finance").id
    environ = {"ATLAS_HOME": str(tmp_path), "ATLAS_TIME_ZONE": "America/Sao_Paulo"}
    made = ("routines", "create", "bills", "--schedule", "every 1h", "--prompt", "Bills.")
    assert _run(tmp_path, *made, "--bot", "ghost", environ=environ) == 1
    assert _run(tmp_path, *made, "--bot", "finance", environ=environ) == 0
    assert _run(tmp_path, "routines", "list", environ=environ) == 0
    said = capsys.readouterr()
    assert "There is no bot 'ghost'" in said.err
    assert ", as the bot finance" in said.out
    assert _run(tmp_path, "bot", "delete", "finance", "--yes") == 0
    assert not (tmp_path / "bots" / "finance").exists()
    # Its routines go with it: nobody is left to run as.
    assert _run(tmp_path, "routines", "list", environ=environ) == 0
    assert capsys.readouterr().out.endswith("No routines.\n")
    # Its conversation is kept, and a bot made later under the same name starts its own.
    assert conversations.info(old).bot is None
    assert conversations.fixed("finance") is None


# -- atlas skills --------------------------------------------------------------------------------


def test_skills_revert_and_restore_undo_what_was_done_to_a_skill(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    database = Database(home / "atlas.db")
    home.mkdir()
    migrate(database, (SCHEMA,))
    library = SkillLibrary(home / "skills", SkillUsage(database))
    text = "---\nname: notes\ndescription: Use when writing notes.\n---\nWrite in bullets.\n"
    library.apply([SkillOperation("create", "notes", content=text)], Origin.USER)
    library.apply(
        [SkillOperation("patch", "notes", old_string="bullets", new_string="prose")], Origin.REVIEW
    )

    assert _run(home, "skills", "versions", "notes") == 0
    assert _run(home, "skills", "revert", "notes") == 0
    assert "bullets" in (home / "skills" / "notes" / "SKILL.md").read_text(encoding="utf-8")
    library.archive("notes")
    assert _run(home, "skills", "restore", "notes") == 0
    assert (home / "skills" / "notes" / "SKILL.md").is_file()
    assert _run(home, "skills", "restore", "notes") == 1

    out, err = capsys.readouterr()
    assert "Put notes back" in out
    assert "Restored notes" in out
    assert "No archived skill is named 'notes'" in err


def test_the_skills_atlas_ships_are_listed_as_atlass_and_reset_puts_one_back(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path
    sync_bundled(SkillLibrary(home / "skills"))
    send = home / "skills" / "write-plugin" / "SKILL.md"
    shipped = send.read_text(encoding="utf-8")
    send.write_text(shipped + "\nAlways sign as Atlas.\n", encoding="utf-8")

    assert _run(home, "skills", "list") == 0
    listed = capsys.readouterr().out
    assert _run(home, "skills", "reset", "write-plugin") == 0
    assert _run(home, "skills", "reset", "nope") == 1
    said = capsys.readouterr()

    assert "atlas  library  by Atlas  0 uses\n" in listed
    assert "write-plugin  library  by Atlas, changed since  0 uses\n" in listed
    assert send.read_text(encoding="utf-8") == shipped
    assert "Put write-plugin back as Atlas ships it." in said.out
    assert "'nope' is not a skill Atlas ships" in said.err
    assert _run(home, "skills", "revert", "write-plugin") == 0  # the change is kept
    assert "Always sign as Atlas." in send.read_text(encoding="utf-8")


def test_the_skills_are_listed_with_who_made_each_and_a_bots_skill_is_shared(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path
    assert _run(home, "skills", "list") == 0
    assert capsys.readouterr().out == "No skills.\n"
    assert _run(home, "bot", "create", "finance") == 0
    database = Database(home / "atlas.db")
    usage = SkillUsage(database)
    text = "---\nname: {}\ndescription: Use when testing.\n---\nDo the thing.\n"

    def create(library: SkillLibrary, name: str, origin: Origin) -> None:
        library.apply([SkillOperation("create", name, content=text.format(name))], origin)

    create(SkillLibrary(home / "skills", usage, creator="atlas"), "deploy", Origin.USER)
    own = SkillLibrary(
        home / "bots" / "finance" / "skills", usage.owned_by("finance"), creator="finance"
    )
    create(own, "bills", Origin.REVIEW)
    (home / "skills" / "by-hand").mkdir()
    (home / "skills" / "by-hand" / "SKILL.md").write_text(text.format("by-hand"), "utf-8")
    usage.used("deploy", utc_now())
    capsys.readouterr()

    assert _run(home, "skills", "list") == 0
    listed = capsys.readouterr().out
    assert _run(home, "skills", "share", "bills") == 1
    assert _run(home, "skills", "share", "bills", "--bot", "ghost") == 1
    assert _run(home, "skills", "share", "bills", "--bot", "finance") == 0
    said = capsys.readouterr()
    assert _run(home, "skills", "list") == 0
    after = capsys.readouterr().out

    assert listed == (
        "by-hand  library  by the user  0 uses\n"
        "deploy  library  by atlas, in a turn  1 use\n"
        "bills  bot finance  by finance, in the review  0 uses\n"
    )
    assert "say whose skill it is, with --bot NAME" in said.err
    assert "There is no bot 'ghost'" in said.err
    assert "Moved bills from the bot finance to the library" in said.out
    # In the library now, and still the bot's making: who fills the library stays in sight.
    assert "bills  library  by finance, in the review  0 uses\n" in after
    assert (home / "skills" / "bills" / "SKILL.md").is_file()


# -- atlas memory --------------------------------------------------------------------------------


def test_memory_proposals_are_listed_approved_and_discarded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    database = Database(home / "atlas.db")
    migrate(database, (MEMORY_SCHEMA,))
    files = MemoryFiles(home / "memory")
    files.change(
        Target.USER, [Operation(Action.ADD, "Lives in Porto."), Operation(Action.ADD, "Likes tea.")]
    )
    proposals = Proposals(files, ProposalStore(database))
    moved = proposals.propose(
        Target.USER, [Operation(Action.REPLACE, "Lives in Lisbon.", "Porto")], "conv-1"
    )
    dropped = proposals.propose(Target.USER, [Operation(Action.REMOVE, old_text="tea")], None)
    assert isinstance(moved, Proposal)
    assert isinstance(dropped, Proposal)

    assert _run(home, "memory", "proposals") == 0
    assert _run(home, "memory", "approve", moved.id) == 0
    assert _run(home, "memory", "approve", moved.id) == 1
    assert _run(home, "memory", "discard", dropped.id) == 0
    assert _run(home, "memory", "discard", dropped.id) == 1
    assert _run(home, "memory", "proposals") == 0

    out, err = capsys.readouterr()
    assert f"{moved.id}  user  " in out
    assert "  can apply\n" in out
    assert "replace the whole entry: Lives in Porto.\n      with: Lives in Lisbon." in out
    assert "remove: Likes tea." in out
    assert f"Approved {moved.id}.\nOverwrote, whole: Lives in Porto.\n" in out
    assert f"Discarded {dropped.id}." in out
    assert out.endswith("No memory proposals.\n")
    assert "it was approved or discarded already" in err
    assert f"there is no open memory proposal {dropped.id}" in err
    assert files.entries(Target.USER) == ["Lives in Lisbon.", "Likes tea."]


# -- atlas usage ---------------------------------------------------------------------------------


def _call(
    started: datetime, provider: str, model: str, purpose: str, usd: str | None
) -> CallRecord:
    return CallRecord(
        started_at=format_utc(started),
        purpose=purpose,
        scope=None,
        provider=provider,
        model=model,
        served_model=None,
        outcome=CallOutcome.OK,
        duration_ms=900,
        usage=Usage(input_tokens=12_000, output_tokens=300, cache_read_tokens=9_000),
        cost=Cost(None, CostSource.UNKNOWN)
        if usd is None
        else Cost(Decimal(usd), CostSource.PRICE_LIST),
    )


def test_usage_sums_the_ledger_by_day_model_and_purpose(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Usage, read from the call ledger: costs never invented, an unknown one counted."""
    home = tmp_path / "home"
    home.mkdir()
    database = Database(home / "atlas.db")
    migrate(database, (MODELS_SCHEMA,))
    ledger = CallLedger(database)
    now = utc_now()
    for record in (
        _call(now, "openai", "gpt-6-luna", "turn", "0.0021"),
        _call(now, "openai", "gpt-6-luna", "background_review", "0.0009"),
        _call(now, "gemini", "gemini-3.8-flash", "turn", None),
        _call(now - timedelta(days=40), "openai", "gpt-6-sol", "turn", "9.0000"),
    ):
        assert ledger.record(record)

    assert _run(home, "usage") == 0
    assert _run(home, "usage", "--days", "0") == 1

    out, err = capsys.readouterr()
    lines = [" ".join(line.split()) for line in out.splitlines()]
    today = now.astimezone().date().isoformat()
    assert "Total 3 calls US$ 0.0030 (+1 unknown) in 36.0k (cached 27.0k), out 900" in lines
    assert any(line.startswith(f"{today} 3 calls US$ 0.0030 (+1 unknown)") for line in lines)
    assert any(line.startswith("openai gpt-6-luna 2 calls US$ 0.0030 in") for line in lines)
    assert any(line.startswith("gemini gemini-3.8-flash 1 calls US$ 0.0000 (+1") for line in lines)
    assert "gpt-6-sol" not in out  # older than the 30 days
    assert any(line.startswith("turn 2 calls US$ 0.0021 (+1 unknown)") for line in lines)
    assert "Costs: 2 from the price list, 1 unknown." in out
    assert "--days takes 1 or more" in err


def test_usage_says_what_each_bot_cost(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    home.mkdir()
    database = Database(home / "atlas.db")
    migrate(database, SCHEMAS)
    conversations = ConversationStore(database)
    fixed = conversations.create(source=BOT, bot="finance").id
    child = conversations.create(source="subagent", parent_id=fixed, bot="finance").id
    mine = conversations.create().id
    ledger = CallLedger(database)
    now = utc_now()
    for scope, purpose, usd in (
        (fixed, "turn", "0.0020"),
        (fixed, "background_review", "0.0010"),
        (child, "subagent", "0.0005"),
        (mine, "turn", "0.0100"),
    ):
        call = _call(now, "openai", "gpt-6-luna", purpose, usd)
        assert ledger.record(dataclasses.replace(call, scope=scope))

    assert _run(home, "usage") == 0

    lines = [" ".join(line.split()) for line in capsys.readouterr().out.splitlines()]
    # A bot's turns, its reviews and its subagents; the main agent's are not a bot's.
    assert "By bot" in lines
    assert any(line.startswith("finance 3 calls US$ 0.0035 in") for line in lines)
    assert "Total 4 calls US$ 0.0135 in 48.0k (cached 36.0k), out 1.2k" in lines


def test_usage_of_an_empty_ledger_says_none(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(tmp_path, "usage", "--days", "7") == 0
    assert capsys.readouterr().out.endswith("(local days).\nNone.\n")


# -- atlas routines --------------------------------------------------------------------------------


def test_routines_are_made_listed_paused_run_and_removed_from_the_command_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    environ = {"ATLAS_HOME": str(tmp_path), "ATLAS_TIME_ZONE": "America/Sao_Paulo"}

    def atlas(*argv: str) -> int:
        return main(list(argv), dict(environ), io.StringIO(""))

    assert atlas("routines", "list") == 0
    assert (
        atlas("routines", "create", "news", "--schedule", "weekdays at 9am", "--prompt", "News.")
        == 0
    )
    assert (
        atlas(
            "routines",
            "create",
            "backup",
            "--schedule",
            "every day at 3am",
            "--command",
            "atlas backup",
        )
        == 0
    )
    assert (
        atlas(
            "routines",
            "create",
            "pinned",
            "--schedule",
            "every 2h",
            "--prompt",
            "Hi.",
            "--model",
            "openai/gpt-6-sol",
        )
        == 0
    )
    assert atlas("routines", "pause", "news") == 0
    assert atlas("routines", "resume", "news") == 0
    assert atlas("routines", "run", "backup") == 0
    assert atlas("routines", "runs", "news") == 0
    assert atlas("routines", "list") == 0
    assert atlas("routines", "remove", "pinned") == 0
    assert atlas("routines", "create", "x", "--schedule", "soon", "--prompt", "Hi.") == 1
    assert (
        atlas(
            "routines", "create", "y", "--schedule", "every 1h", "--prompt", "Hi.", "--model", "gpt"
        )
        == 1
    )
    assert atlas("routines", "pause", "nope") == 1

    out, err = capsys.readouterr()
    assert out.startswith("No routines.\n")
    assert "news  weekdays at 9am, on, next " in out
    assert "backup  every day at 3am, on, next " in out
    assert "Paused news." in out
    assert "Resumed news." in out
    assert "Runs at the server's next minute" in out
    assert "news has not run yet." in out
    assert "Removed pinned." in out
    assert "not a schedule" in err
    assert "--model is provider/model" in err
    assert "There is no routine 'nope'" in err
