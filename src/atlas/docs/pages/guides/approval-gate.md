---
title: "The approval gate"
description: "How Atlas checks each command before it runs, what always asks you, what never runs, and what auto mode changes."
sidebar:
  order: 12
---

Atlas runs commands on your machine, so every command its agent wants to run passes the **approval
gate** first. The same gate and the same settings hold for the main agent, its subagents and your
bots. Why it works this way is in
[ARCHITECTURE.md](https://github.com/marqueskaio/atlas/blob/main/docs/ARCHITECTURE.md#trust--decided-2026-10-10)
(Trust).

## What happens to a command

The gate looks at a command in this order, and the first rule that applies decides:

1. **Commands that never run.** A short list is refused in every mode, whatever was approved
   before: deleting the root folder, a system folder or your home folder recursively (`rm -rf ~`),
   formatting a disk (`mkfs`), writing straight to a raw disk, a fork bomb, killing every process,
   and shutting down or rebooting the machine.
2. **Your deny list.** The commands you never want run, as globs in `[trust] deny`. Blocked even in
   auto mode.
3. **Actions outside your machine always ask you.** What changes something elsewhere on your
   behalf asks you first, every time, even in auto mode: merging a pull request or opening an issue
   with `gh`, sending an email or changing your calendar with `gws`, changing Google Cloud with
   `gcloud`, and printing their tokens. Your own globs in `[trust] ask` add to this list, and
   `[trust] ask_skip` takes one of Atlas's off it. With nobody there to answer, the command is
   refused.
4. **Dangerous commands are reviewed.** A command that matches a dangerous pattern, such as
   `git push --force`, `rm -rf` on a folder, or `git clean -f`, goes to a **reviewer**: a short
   request to the conversation's model, which approves it, denies it or passes it to you. What it
   is unsure of comes to you.
5. Anything else runs.

The checks read the whole command line: each part of a compound command, what runs inside `$(...)`,
`sh -c` and `eval`, the program behind variable assignments (`X=1 reboot`) or a wrapper such as
`timeout` or `nice`, and the scripts a command names, so wrapping a command does not slip it past
the gate.

## When it asks you

A question waits where the conversation is: in the app, or in the chat of a channel such as Telegram
when the conversation came from there, with the command and why it asks. You can allow it **once**, **for this conversation**, or **always**, or deny it, with a reason
the agent reads. Silence is not consent: an approval nobody answers in
`[trust] approval_timeout` seconds (5 minutes unless you change it) counts as refused.

In the app, the card asks what the call would do, such as *Run this command?*, over exactly what
would run, and its choices come in that order, the one that goes ahead first and **Deny** last. Click
the card and the number keys pick them: 1 allows once. The card never takes the keyboard from where
you type.

The command shows on the conversation as waiting for your approval, in amber, until you answer: it
has not run. Once allowed it runs; a denied one stays marked as not run, and one nobody answered as
*not answered in time*.

After three denials in a row from the reviewer, the agent is told to stop trying variations of the
command and to report to you.

## Auto mode

Each conversation has an **auto mode**, which hands the wheel to the agent: it skips the reviewer and
the questions about dangerous commands. What it never skips: the commands that never run, your deny list, and the
actions outside your machine, which still ask.

A new conversation with Atlas, a bot's conversation and a conversation from a channel start in auto
mode, and you turn it off on the conversation. A routine and Atlas's own subagents start without
it, since nobody chose it for them.

## Files and MCP servers

The file tools refuse the system's folders and ask before they change your SSH configuration, and
the vault that holds your keys is out of their reach. An MCP server you mark
`"trust": "untrusted"` in `mcp.json` asks before every call to a tool that does not declare itself
read-only.

## Your coding agents

Claude Code, Codex and the other executors keep their own permission rules: what they ask is decided
by them and by your settings for them, and Atlas adds no rules of its own. Atlas decides only who
answers what they ask: you, or its reviewer when you let Atlas answer. See
[Coding agents and accounts](/docs/guides/coding-agents/#questions-and-permissions).

## The settings

All four live in the `[trust]` section of `config.toml`, and change from the app's settings screen
too. The [settings reference](/docs/reference/settings/#trust) lists them.

```toml
[trust]
deny = ["git push --force*"]
ask = ["git push*"]
ask_skip = ["gh issue comment*"]
approval_timeout = 300
```
