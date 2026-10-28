---
title: "Privacy and your data"
description: "What Atlas keeps on your machine, what leaves it and to whom, how your keys are protected, and what the agent can and cannot reach."
sidebar:
  order: 13
---

Atlas has no account, no telemetry and no cloud of its own. There is no server of Atlas's that
your conversations, memory or keys could reach: what leaves your machine goes to the services you
set up, and only when a task needs them.

## What stays on your machine

Everything Atlas keeps is in its home folder, `~/.atlas` unless you choose another: your settings,
profile and memory, the skills, the conversations and their attachments, the routines, the plugins'
data and your dictations. It is plain files and one SQLite database, which you can open, copy and
delete. [Backups, updates and homes](/docs/guides/backups/#where-the-home-is) lists them.

## What leaves it, and to whom

| When | What goes | To |
|---|---|---|
| You talk to Atlas | The conversation, your memory and the tools' results | Your model provider (OpenAI, Anthropic, Google, OpenRouter), or nowhere with a local model |
| A new conversation starts | Its first message, to name it | Anthropic or OpenAI, when you have a key there |
| The background review runs | The conversation it rereads | Your model provider |
| The agent searches the web | The search words | Tavily or Brave with your key, or DuckDuckGo |
| The agent reads a page or uses the browser | The request for that page | That website |
| You use a coding agent | What that coding agent sends, as it always does | Its own provider, on its own login |
| You use Telegram or Voice | Your messages, or your audio | Telegram, or OpenAI |
| You use an MCP server | What the agent passes to its tools | Wherever that server sends it |
| Atlas looks for an update, or reads the marketplace | A request for the latest version or list | GitHub |
| You run the install line | The request for the installer, which the site counts by system and country, never by your address | The Atlas site, on Cloudflare |

Each provider's own terms say what it does with what it receives.

## Your keys

- **Your API keys and tokens live in the vault**, `ATLAS_HOME/secrets/vault.json`, each value
  encrypted on its own (age, X25519). On Windows the vault's key is encrypted by your Windows
  account; elsewhere it is a file only you can open.
- **A key never travels**: not in a backup, not in a log (every secret is masked there), and never
  to the model. When the agent needs a key, it asks for it in a card whose value goes straight to
  the vault.
- **The commands the agent runs never see the vault.** A key reaches only what you set up to use
  it: your model provider, the web search, an MCP server that names it, a plugin that declares it.

## Your coding agents' logins

Claude Code, Codex, the Gemini CLI and DeepSeek keep their own logins, in their own folders. You
sign in through each one's own login; Atlas never sees, keeps or passes a credential, never reads
Claude Code's login token, and never calls Anthropic's usage API with it. What it shows of each
session comes from the coding agent itself, through its official protocol
([where Claude Code's data comes from](/docs/guides/coding-agents/#where-claude-codes-data-comes-from)).

## What the agent can reach

Atlas's agent works on your machine with your rights: it reads and writes files and runs commands,
starting in your home folder. Two kinds of protection stand around it:

- **The [approval gate](/docs/guides/approval-gate/)** checks every command before it runs: some
  never run, dangerous ones are reviewed, and anything that acts outside your machine asks you.
- **The file tools refuse** the vault and the system's folders, and ask before changing your SSH
  configuration.

These are guards, not a sandbox: a determined program run by the agent could still reach what you
can. Keep auto mode for work you trust, and give a remote machine's Atlas only what it needs.

## Other machines

A machine you open from your laptop is reached over Tailscale, your private network, or an SSH
tunnel with your own keys, and asks for that home's password, which its connection code holds. The
Atlas over there keeps that machine's files and keys. **Change the password** makes every code
given before stop working ([Several machines](/docs/guides/machines/)).

## Delete everything

Turn off **Always on** if you turned it on, close the app, then delete the home folder. Your data is
nowhere else. What remains is the program: the installation (`%LOCALAPPDATA%\atlas` on Windows,
`~/.local/share/atlas` elsewhere), its `atlas` command on your PATH, its shortcut, and the app's
own folder, which remembers the window and the last home opened.
