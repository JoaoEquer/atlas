---
title: "Settings"
description: "Every setting of `config.toml`: its default, the `ATLAS_*` variable that wins over it, and what it does, written from the code."
sidebar:
  order: 2
---

<!-- Written by scripts/gen_docs.py from the code. Change the code and run it again; an edit here is lost. -->

Atlas reads its settings once, when it starts, from four places, each winning over the one before: the default, the home's `config.toml`, an `ATLAS_*` variable, and a command-line flag. A variable is named after the setting's place in the file: `[server] port` is `ATLAS_SERVER_PORT`, and a blank variable counts as unset.

An unknown key in `config.toml` stops Atlas with an error, so a typo never passes in silence; an unknown `ATLAS_*` variable is only reported. A secret never goes in the file: it comes from its variable, which Atlas removes from its environment once read, so no command the agent runs inherits it. The settings marked *on the settings screen* also change from the app, with no restart. A plugin keeps its own settings, changed with `atlas plugins set`.

## [server]

| Key | Value | Default | Variable | What it does |
|---|---|---|---|---|
| `host` | text | `"127.0.0.1"` | `ATLAS_SERVER_HOST` | The address the server listens on: this machine only, unless told otherwise. `0.0.0.0` belongs only inside a container or behind a firewall. |
| `port` | whole number | `18790` | `ATLAS_SERVER_PORT` | The port the server listens on. `0` lets the system choose one. |
| `token` | secret | unset | `ATLAS_SERVER_TOKEN` | What a client presents to be let in, from `ATLAS_SERVER_TOKEN`, set by whoever starts the server. Unset, the server makes a fresh one nobody knows. |

## [log]

| Key | Value | Default | Variable | What it does |
|---|---|---|---|---|
| `level` | one of `DEBUG`, `INFO`, `WARNING`, `ERROR` | `"INFO"` | `ATLAS_LOG_LEVEL` | The least serious lines the log keeps: `DEBUG`, `INFO`, `WARNING` or `ERROR`. |
| `file` | `true` or `false` | `true` | `ATLAS_LOG_FILE` | Also write `logs/atlas.log`. A container usually turns it off and reads stderr instead. |

## [vault]

| Key | Value | Default | Variable | What it does |
|---|---|---|---|---|
| `key` | secret | unset | `ATLAS_VAULT_KEY` | The vault key itself (`AGE-SECRET-KEY-1...`). Only from the environment, never from a file. |
| `key_file` | path | unset | `ATLAS_VAULT_KEY_FILE` | A file holding the vault key, such as a Docker secret mounted read-only. |

## [model]

| Key | Value | Default | Variable | What it does |
|---|---|---|---|---|
| `provider` | text | unset | `ATLAS_MODEL_PROVIDER` | The provider of the conversation's model, such as `openai` or `openrouter`. *On the settings screen.* |
| `name` | text | unset | `ATLAS_MODEL_NAME` | The model's id at that provider. Unset, the provider's default model answers. *On the settings screen.* |
| `effort` | text | unset | `ATLAS_MODEL_EFFORT` | How hard a reasoning model thinks (`low`, `medium`, `high`...). Unset, the model decides. *On the settings screen.* |

## [trust]

| Key | Value | Default | Variable | What it does |
|---|---|---|---|---|
| `deny` | list of text | `[]` | `ATLAS_TRUST_DENY` | Commands the agent must never run, as shell-style globs (`git push --force*`), matched case-insensitively; blocked even in auto mode. *On the settings screen.* |
| `ask` | list of text | `[]` | `ATLAS_TRUST_ASK` | More commands that always ask the user first, as shell-style globs (`git push*`), on top of the outward actions Atlas asks about on its own. They ask even in auto mode. *On the settings screen.* |
| `ask_skip` | list of text | `[]` | `ATLAS_TRUST_ASK_SKIP` | Globs of commands Atlas's own outward rules leave alone (`gh issue comment*`): the user's way to take an action off the default list. The user's `ask` rules still apply. *On the settings screen.* |
| `approval_timeout` | whole number | `300` | `ATLAS_TRUST_APPROVAL_TIMEOUT` | Seconds an approval waits for the user before it counts as refused (60 was too tight for a phone notification). *On the settings screen.* |

## [memory]

| Key | Value | Default | Variable | What it does |
|---|---|---|---|---|
| `user_limit` | whole number | `1375` | `ATLAS_MEMORY_USER_LIMIT` | The characters `memory/USER.md` may hold, from 500 to 20,000: who the user is. Every model call carries it, and lowered under what is written it cuts nothing. *On the settings screen.* |
| `memory_limit` | whole number | `2200` | `ATLAS_MEMORY_MEMORY_LIMIT` | The characters `memory/MEMORY.md` may hold, from 500 to 20,000: the agent's notes. Every model call carries it, and lowered under what is written it cuts nothing. *On the settings screen.* |

## [workspace]

| Key | Value | Default | Variable | What it does |
|---|---|---|---|---|
| `folder` | path | unset | `ATLAS_WORKSPACE_FOLDER` | Where the file tools and commands start. Unset, the user's home folder. *On the settings screen.* |

## [time]

| Key | Value | Default | Variable | What it does |
|---|---|---|---|---|
| `zone` | text | unset | `ATLAS_TIME_ZONE` | The IANA zone routines run in (`America/Sao_Paulo`). Unset, the system's. *On the settings screen.* |

## [browser]

| Key | Value | Default | Variable | What it does |
|---|---|---|---|---|
| `chrome` | list of text | `[]` | `ATLAS_BROWSER_CHROME` | The program that runs Chrome, Edge or Chromium, and the arguments before its own (step 14). Unset, the first one found where they install. |

## [marketplace]

| Key | Value | Default | Variable | What it does |
|---|---|---|---|---|
| `repository` | text | `"marqueskaio/atlas-marketplace"` | `ATLAS_MARKETPLACE_REPOSITORY` | The marketplace's repository on GitHub, `owner/name`. |
| `branch` | text | `"main"` | `ATLAS_MARKETPLACE_BRANCH` | The branch Atlas reads it from. |
| `archive_url` | text | `"https://codeload.github.com/{repo}/tar.gz/{ref}"` | `ATLAS_MARKETPLACE_ARCHIVE_URL` | Where a repository's archive is fetched, with `{repo}` and `{ref}`: a mirror's address, or a test's. |
| `raw_url` | text | `"https://raw.githubusercontent.com/{repo}/{ref}/{path}"` | `ATLAS_MARKETPLACE_RAW_URL` | Where one file of a repository is read, with `{repo}`, `{ref}` and `{path}`. |
| `outside` | `true` or `false` | `true` | `ATLAS_MARKETPLACE_OUTSIDE` | List what the outside catalogs offer beside the marketplace (step 24): the MCP Registry, Claude Code's plugin directory and skills.sh. |
| `registry_url` | text | `"https://registry.modelcontextprotocol.io/v0/servers"` | `ATLAS_MARKETPLACE_REGISTRY_URL` | Where the MCP Registry's list of servers is read. |
| `skills_url` | text | `"https://skills.sh/api/search"` | `ATLAS_MARKETPLACE_SKILLS_URL` | Where skills.sh is searched. |
| `github_api_url` | text | `"https://api.github.com"` | `ATLAS_MARKETPLACE_GITHUB_API_URL` | Where GitHub's API is reached, to pin an outside item to its repository's commit. |
| `claude_plugins` | text | `"anthropics/claude-plugins-official"` | `ATLAS_MARKETPLACE_CLAUDE_PLUGINS` | Claude Code's plugin directory on GitHub, `owner/name`, whose catalog is read at its branch. |

## [relay]

| Key | Value | Default | Variable | What it does |
|---|---|---|---|---|
| `url` | text | unset | `ATLAS_RELAY_URL` | The relay the home waits at, so the app reaches it from anywhere with no open port (step 20): a `wss://` address. Unset, none: the tailnet and SSH reach it. |

## [backup]

| Key | Value | Default | Variable | What it does |
|---|---|---|---|---|
| `folder` | path | unset | `ATLAS_BACKUP_FOLDER` | Where the server writes the home's backup on its own (step 20): a home in a machine that may be lost (a cloud computer, a VPS) keeps its last few there. Unset, none is written but by hand (`atlas backup`, the app). |
| `every_hours` | whole number | `24` | `ATLAS_BACKUP_EVERY_HOURS` | Hours between two backups. |
| `keep` | whole number | `3` | `ATLAS_BACKUP_KEEP` | The backups of this home kept in the folder, the newest; 0 keeps all. |

## [voice]

| Key | Value | Default | Variable | What it does |
|---|---|---|---|---|
| `transcription` | text | `"local"` | `ATLAS_VOICE_TRANSCRIPTION` | Who writes down a recording sent or dictated: `local` (this computer, with its model downloaded once), `openai`, `gemini` (each with its key), or `off`. *On the settings screen.* |
