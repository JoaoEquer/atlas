---
title: "Commands"
description: "Every `atlas` command and what it takes, written from the command line's own parser."
sidebar:
  order: 1
---

<!-- Written by scripts/gen_docs.py from the code. Change the code and run it again; an edit here is lost. -->

Every command runs in the home `atlas home use` chose, unless `--home NAME` comes before it: `atlas --home work usage`. A plugin that is on may add commands of its own, which `atlas plugins list` shows.

## `atlas version`

Print the installed version.

```
atlas version
```

## `atlas docs`

Read the documentation of this Atlas: the list of pages, one page, a search, or every page. It opens no home.

```
atlas docs [page] [--search WORD ...] [--all]
```

| Argument | What it does |
|---|---|
| `[page]` | A page from the list, such as install or guides/memory; with --search, the search stays in it. |
| `--search WORD ...` | The sections that mention every word. |
| `--all` | Every page, one after another. |

`--search` and `--all` do not go together.

## `atlas serve`

Run the server that clients connect to.

```
atlas serve [--host HOST] [--port PORT] [--parent-pid PARENT_PID] [--always-on HOME_FOLDER]
```

| Argument | What it does |
|---|---|
| `--host HOST` | Address to listen on (default 127.0.0.1, or ATLAS_SERVER_HOST). Use 0.0.0.0 only inside a container or behind a firewall. |
| `--port PORT` | Port to listen on (default 18790, or ATLAS_SERVER_PORT). 0 lets the system choose one; the server then writes `ATLAS_SERVER_READY port=<port>` on standard output. |
| `--parent-pid PARENT_PID` | Stop when this process is gone: the app that started the server passes its own. |
| `--always-on HOME_FOLDER` | Serve this home as its always-on service does: on its kept port, with the token the vault keeps, and on the tailnet when it is open. Written by `atlas always-on on`. |

## `atlas server`

What clients need to reach the server.

### `atlas server token`

Print the token a server off loopback (a container, an always-on home) keeps in the vault, making it the first time. Give it to the app as ATLAS_DESKTOP_TOKEN.

```
atlas server token [--new]
```

| Argument | What it does |
|---|---|
| `--new` | Replace it: every connection code given before stops working. An always-on home's service starts again on the new one. |

### `atlas server code`

Print the connection code another machine's app opens this home with. A secret: it holds the token.

```
atlas server code [--port PORT]
```

| Argument | What it does |
|---|---|
| `--port PORT` | The port clients reach, when the home is not always on (a container: default 18790, or ATLAS_SERVER_PORT). |

## `atlas always-on`

Run the home's server as this machine's service, outliving the app and starting with the computer; open it to the user's machines over Tailscale.

### `atlas always-on on`

Write the service and start it.

```
atlas always-on on
```

### `atlas always-on off`

Stop the service and remove it.

```
atlas always-on off
```

### `atlas always-on status`

Whether the home is always on and open, and where it listens.

```
atlas always-on status [--json]
```

| Argument | What it does |
|---|---|
| `--json` | As JSON, for the app. |

### `atlas always-on start`

Start the service when its server does not answer, and wait for it: what the app runs before it attaches.

```
atlas always-on start
```

### `atlas always-on open`

Also listen on this machine's Tailscale address, for the user's other machines.

```
atlas always-on open
```

### `atlas always-on close`

Listen on this machine only again.

```
atlas always-on close
```

## `atlas secrets`

Keep API keys and tokens in the vault.

### `atlas secrets set`

Store a secret. The value is read from standard input, never from arguments.

```
atlas secrets set name
```

| Argument | What it does |
|---|---|
| `name` | Such as OPENAI_API_KEY. |

### `atlas secrets list`

List the secrets' names and when each was set. No values.

```
atlas secrets list
```

### `atlas secrets delete`

Remove a secret.

```
atlas secrets delete name
```

| Argument | What it does |
|---|---|
| `name` |  |

### `atlas secrets check`

Decrypt every secret and report the ones that fail.

```
atlas secrets check
```

## `atlas mcp`

Sign in to the MCP servers of mcp.json that use OAuth.

### `atlas mcp login`

Sign in to a server: its page opens in the browser, where you authorize Atlas, and the sign-in is kept and renewed.

```
atlas mcp login name [--no-browser]
```

| Argument | What it does |
|---|---|
| `name` | The server's name in mcp.json. |
| `--no-browser` | Print the page's address without opening it, for a browser on another machine. |

### `atlas mcp logout`

Forget a server's sign-in.

```
atlas mcp logout name
```

| Argument | What it does |
|---|---|
| `name` | The server's name in mcp.json. |

## `atlas models`

The model providers and their keys.

### `atlas models check`

Ask each provider whether its key opens it. Spends no tokens.

```
atlas models check [providers ...]
```

| Argument | What it does |
|---|---|
| `[providers ...]` | Such as openai or gemini. Default: every provider with a key in the vault, and the one the settings chose. |

## `atlas usage`

What the model calls spent, by day, model and purpose.

```
atlas usage [--days DAYS]
```

| Argument | What it does |
|---|---|
| `--days DAYS` | How many days back, today included (default 30). |

## `atlas routines`

Scheduled prompts and commands the server runs.

### `atlas routines list`

The routines, their schedules and their last run.

```
atlas routines list
```

### `atlas routines create`

Make a routine that runs a prompt, or a command, on a schedule.

```
atlas routines create name --schedule SCHEDULE [--prompt PROMPT] [--command SHELL_COMMAND] [--model MODEL] [--bot NAME]
```

| Argument | What it does |
|---|---|
| `name` |  |
| `--schedule SCHEDULE` | Required. every 30m, in 2h, every monday 9am, weekdays at 9am, 0 9 * * *, or a time such as 2026-10-20T14:00. |
| `--prompt PROMPT` | What the agent does, standing alone. |
| `--command SHELL_COMMAND` | A command in the system's shell; its output is what you get. |
| `--model MODEL` | Pin a model, provider/model (openai/gpt-6-sol); unset, the settings'. |
| `--bot NAME` | The bot the prompt runs as; its result enters the bot's fixed conversation. |

One of `--prompt` or `--command` is required.

### `atlas routines pause`

Stop a routine firing until resumed.

```
atlas routines pause routine
```

| Argument | What it does |
|---|---|
| `routine` | Its id or name. |

### `atlas routines resume`

Fire it again; a slot that came due while paused runs once.

```
atlas routines resume routine
```

| Argument | What it does |
|---|---|
| `routine` | Its id or name. |

### `atlas routines run`

Run it at the server's next minute, ahead of its schedule.

```
atlas routines run routine
```

| Argument | What it does |
|---|---|
| `routine` | Its id or name. |

### `atlas routines remove`

Delete a routine and its runs.

```
atlas routines remove routine
```

| Argument | What it does |
|---|---|
| `routine` | Its id or name. |

### `atlas routines runs`

A routine's runs, the newest first.

```
atlas routines runs routine
```

| Argument | What it does |
|---|---|
| `routine` | Its id or name. |

## `atlas skills`

The skills and who made each; undo what was done to one.

### `atlas skills list`

Every skill: the library's and each bot's, who made it, and its uses.

```
atlas skills list
```

### `atlas skills share`

Move a bot's skill to the home's library, for every agent.

```
atlas skills share name [--bot NAME]
```

| Argument | What it does |
|---|---|
| `name` |  |
| `--bot NAME` | The bot whose skill it is; unset, the library's. |

### `atlas skills revert`

Put a skill back as it was before its last change (or a named version).

```
atlas skills revert name [--version VERSION] [--bot NAME]
```

| Argument | What it does |
|---|---|
| `name` |  |
| `--version VERSION` | A version from `atlas skills versions NAME`. |
| `--bot NAME` | The bot whose skill it is; unset, the library's. |

### `atlas skills versions`

List a skill's kept versions.

```
atlas skills versions name [--bot NAME]
```

| Argument | What it does |
|---|---|
| `name` |  |
| `--bot NAME` | The bot whose skill it is; unset, the library's. |

### `atlas skills restore`

Bring an archived skill back.

```
atlas skills restore name [--bot NAME]
```

| Argument | What it does |
|---|---|
| `name` |  |
| `--bot NAME` | The bot whose skill it is; unset, the library's. |

### `atlas skills reset`

Put a skill Atlas ships back as it ships it, so it is updated again; what the copy held is kept as a version.

```
atlas skills reset name
```

| Argument | What it does |
|---|---|
| `name` |  |

## `atlas memory`

The changes to memory the background review proposed.

### `atlas memory proposals`

List them, each with what it would change and whether it still can.

```
atlas memory proposals
```

### `atlas memory approve`

Apply one, if the entries it would change are as they were.

```
atlas memory approve id
```

| Argument | What it does |
|---|---|
| `id` |  |

### `atlas memory discard`

Drop one without applying it.

```
atlas memory discard id
```

| Argument | What it does |
|---|---|
| `id` |  |

## `atlas backup`

Write the home's backup: one zip of the user's data, never the vault.

```
atlas backup [--output OUTPUT] [--keep KEEP]
```

| Argument | What it does |
|---|---|
| `-o, --output OUTPUT` | A file, a folder, or - for standard output. Default: your home folder, keeping the last 3 of this home. |
| `--keep KEEP` | Backups of this home kept in the folder (default 3; 0 keeps all). A file you named is never removed. |

## `atlas restore`

Put a backup back over the home, after checking all of it.

```
atlas restore file [--yes]
```

| Argument | What it does |
|---|---|
| `file` | The backup's zip, or - for standard input. |
| `-y, --yes` | Do not ask before overwriting a home with data. |

## `atlas home`

The homes of this machine.

### `atlas home list`

List the homes; * marks the one that opens by default.

```
atlas home list
```

### `atlas home create`

Make a new home, empty or cloned.

```
atlas home create name [--clone] [--from NAME]
```

| Argument | What it does |
|---|---|
| `name` |  |
| `--clone` | Copy the settings, models, MCP servers, keys, profile, memory and skills of the current home (or --from). Conversations start empty. |
| `--from NAME` | The home to clone. |

### `atlas home use`

Open this home when none is named.

```
atlas home use name
```

| Argument | What it does |
|---|---|
| `name` |  |

### `atlas home show`

Where a home is, and what it holds.

```
atlas home show [name]
```

| Argument | What it does |
|---|---|
| `[name]` |  |

### `atlas home rename`

Rename a home no server is serving.

```
atlas home rename old new
```

| Argument | What it does |
|---|---|
| `old` |  |
| `new` |  |

### `atlas home delete`

Delete a home and everything in it.

```
atlas home delete name [--yes]
```

| Argument | What it does |
|---|---|
| `name` |  |
| `-y, --yes` | Do not ask first. |

## `atlas bot`

The home's bots: named agents with their own mission, memory and skills.

### `atlas bot list`

List the home's bots.

```
atlas bot list
```

### `atlas bot create`

Make a bot.

```
atlas bot create name [--title TITLE] [--description DESCRIPTION] [--model MODEL] [--mission-file PATH]
```

| Argument | What it does |
|---|---|
| `name` |  |
| `--title TITLE` | How the roster calls it; by default, its name. |
| `--description DESCRIPTION` | What it is for, in the roster. |
| `--model MODEL` | The model it runs on; by default, the home's. |
| `--mission-file PATH` | A file with its mission and rules, or - to read them from the standard input. |

### `atlas bot show`

A bot's definition, and where it is.

```
atlas bot show name
```

| Argument | What it does |
|---|---|
| `name` |  |

### `atlas bot delete`

Delete a bot: its definition, its memory and its skills.

```
atlas bot delete name [--yes]
```

| Argument | What it does |
|---|---|
| `name` |  |
| `-y, --yes` | Do not ask first. |

## `atlas plugins`

The home's plugins: turn them on with consent, or off.

### `atlas plugins list`

List the plugins, and what each declares.

```
atlas plugins list
```

### `atlas plugins enable`

Turn a plugin on, after showing what it declares.

```
atlas plugins enable name [--yes]
```

| Argument | What it does |
|---|---|
| `name` |  |
| `-y, --yes` | Consent without asking (what it declares now). |

### `atlas plugins disable`

Turn a plugin off.

```
atlas plugins disable name
```

| Argument | What it does |
|---|---|
| `name` |  |

### `atlas plugins secret`

Store a secret a plugin declares, read from the standard input.

```
atlas plugins secret name secret
```

| Argument | What it does |
|---|---|
| `name` |  |
| `secret` |  |

### `atlas plugins set`

Change a setting a plugin declares; with no value, back to its default.

```
atlas plugins set name setting [value]
```

| Argument | What it does |
|---|---|
| `name` |  |
| `setting` |  |
| `[value]` |  |

### `atlas plugins new`

Start a plugin: a folder with a manifest, a tool and a skill to change.

```
atlas plugins new name [--folder FOLDER]
```

| Argument | What it does |
|---|---|
| `name` | In kebab-case, such as my-plugin. |
| `--folder FOLDER` | Where to make it; by default the home's plugins folder, where Atlas finds it. |

### `atlas plugins validate`

Check a plugin's folder as Atlas loads it, in a home thrown away after.

```
atlas plugins validate folder
```

| Argument | What it does |
|---|---|
| `folder` |  |

### `atlas plugins install`

Fetch a plugin by its address (a git URL, owner/repo, a folder); it arrives off.

```
atlas plugins install address [--ref REF] [--force]
```

| Argument | What it does |
|---|---|
| `address` | https://..., git@..., owner/repo, with #folder for one inside it. |
| `--ref REF` | A branch, a tag or a commit. |
| `--force` | Replace the one of the same name installed before. |

### `atlas plugins update`

Fetch a plugin installed by address again from where it came.

```
atlas plugins update name
```

| Argument | What it does |
|---|---|
| `name` |  |

### `atlas plugins remove`

Turn off and take away a plugin installed by address; its data stays.

```
atlas plugins remove name
```

| Argument | What it does |
|---|---|
| `name` |  |

## `atlas marketplace`

The marketplace: skills, MCP servers, plugins and executors made for Atlas, each reviewed and pinned to a commit.

### `atlas marketplace list`

What the marketplace offers, what Atlas ships, and what came from it.

```
atlas marketplace list [--kind KIND] [--refresh]
```

| Argument | What it does |
|---|---|
| `--kind KIND` |  |
| `--refresh` | Read the marketplace now. |

### `atlas marketplace show`

An item's page: its README, and what it needs here.

```
atlas marketplace show name
```

| Argument | What it does |
|---|---|
| `name` |  |

### `atlas marketplace install`

Install an item, with the MCP servers a skill needs; a plugin arrives off.

```
atlas marketplace install name
```

| Argument | What it does |
|---|---|
| `name` |  |

### `atlas marketplace update`

Bring an installed item to the marketplace's version.

```
atlas marketplace update name [--force]
```

| Argument | What it does |
|---|---|
| `name` |  |
| `--force` | Update a skill or a server changed here since it came; a skill keeps what it held. |

### `atlas marketplace remove`

Take away an item installed from the marketplace.

```
atlas marketplace remove name
```

| Argument | What it does |
|---|---|
| `name` |  |

### `atlas marketplace check`

Check item files as the marketplace's CI does: each by its rules, its code fetched at its commit.

```
atlas marketplace check [items ...] [--root ROOT] [--load]
```

| Argument | What it does |
|---|---|
| `[items ...]` | Item files; by default every one in the folder's items. |
| `--root ROOT` | The marketplace's folder; by default this one. |
| `--load` | Also load each plugin's Python, in a home thrown away after: only where foreign code may run, as the marketplace's CI. |

## `atlas pairing`

Who may talk to Atlas through a channel.

### `atlas pairing list`

The people let in, and those waiting with a code.

```
atlas pairing list
```

### `atlas pairing approve`

Let in whoever holds a code.

```
atlas pairing approve code
```

| Argument | What it does |
|---|---|
| `code` |  |

### `atlas pairing forget`

Remove someone from a channel.

```
atlas pairing forget channel user
```

| Argument | What it does |
|---|---|
| `channel` |  |
| `user` |  |

## `atlas desktop`

Build the app when its code changed, write its Start Menu shortcut, and open it.

```
atlas desktop [--build-only]
```

| Argument | What it does |
|---|---|
| `--build-only` | Build it and write the shortcut; do not open it. |

## `atlas update`

Bring this installation to main, then Python and the app; back to where it was if a step fails. Close the app first.

```
atlas update
```
