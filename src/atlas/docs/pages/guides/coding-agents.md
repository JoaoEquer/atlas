---
title: "Coding agents and accounts"
description: "Run Claude Code, Codex, the Gemini CLI and DeepSeek inside Atlas on all your accounts, switch accounts mid-conversation, and give each session a route for when a plan runs out."
sidebar:
  order: 1
---

Atlas runs the coding agents you already use, Claude Code, Codex, the Gemini CLI and DeepSeek's harness, as **executors**. It runs the copy you installed, with your settings, your project's instructions and your own login, and it shows every session they have, including the ones you started in a terminal or an editor.

## Turn them on

Open **Settings**, then **Extensions**, and on its **Executors** tab turn on the harnesses you use; one that is not installed shows the command that installs it. Each one you turn on appears in the **Executors** tab of the sidebar with its project folders, its sessions and its plan's usage: the five-hour window, the week and each model's quota, read the way the harness's own `/usage` shows them, without spending a token.

A session you started elsewhere shows up in the list too. If it is still open there, Atlas warns you and offers to go on in a copy. To start a new one, choose **New session** at the top of the tab, then the executor and the project folder.

## Several accounts

Each account is its own executor, such as **Claude Code · personal** and **Claude Code · company B**, and keeps its own world: its own folder, login, settings, MCP servers, plugins and history. Accounts run at the same time, and each project folder starts its new sessions on the account you chose for it.

To add one, choose **Add an account** in the Executors tab and give it a nickname. Atlas makes a folder for it beside your main one and opens the harness's own login in your browser: sign in there with that account. Atlas never sees, keeps or passes a credential. Removing an account leaves its folder as it is, with its login and its sessions.

## Switch accounts in a conversation

Out of credit on one account? Switch the session to another. The conversation goes on in a copy on that account, and the first account keeps it as it was.

Before the switch, the app tells you what changes: the tools the other account lacks, and that the first message reads the whole conversation again on that account's plan. Compacting first (`/compact`) reads less.

## A route for each session

A session's **route** says where it goes on when its plan's limit is reached: which accounts and harnesses it may use, in which order. For example:

```
Claude Code · personal  →  Claude Code · company  →  Codex · main
```

- **Within one harness**, the session itself moves: it goes on in a copy on the next account. It compacts there first (`/compact`), so the copy reads less on the new account's plan, wherever the harness has that command; you can turn it off.
- **Across harnesses**, a new session starts on the next one with a handoff: the task, your latest instruction, the steps not done, the files changed and what happened so far. Atlas puts it together from the session itself, with no model call.
- **The model and the effort go on with it.** The session keeps the model and the effort it ran on, the effort fitted to what the next model takes; on another harness, it runs on that harness's default model. A stop can name its own model and effort instead. If the next account does not offer the model, the session goes on on its default and says so.

The route is drawn as a path above the session's box, from its pill: each stop in order, the one it runs on marked, whether each can take over (installed, logged in, not at its own limit) and how much of its plan is used. With no route, the harness's own accounts take over at a plan's limit. Going on at a limit is on by default, and you can turn it off.

## Questions and permissions

When an executor asks a question or asks for permission, the request waits for you with no deadline, in the app or on your phone through Telegram. You can also let Atlas answer: its safety reviewer takes the permissions and the conversation's model takes the questions, and what either is unsure of still comes to you.

Each harness keeps its own permission rules. Atlas decides only who answers what the harness asks.

## Let Atlas hand out the work

Ask Atlas in plain words, and it delegates to the right executor on the account you name, with a route of its own when you give one, for example `claude-code@company` and then `codex`. It follows along and brings back the result.

## How Atlas talks to them

Atlas brings no copy of these programs. Each one is driven by a plugin Atlas ships, which runs the program you installed and speaks the protocol its own makers use for their apps:

| Harness | How Atlas drives it |
|---|---|
| Claude Code | Its control protocol over standard input and output, the one Anthropic's Agent SDK speaks |
| Codex | `codex app-server`, the protocol Codex's own app and its editor extension use |
| Gemini CLI | The Agent Client Protocol (ACP), `gemini --acp` |
| DeepSeek | The Agent Client Protocol, through its harness `dsh` |

Because the program is yours, it keeps its own login, system prompt, settings, permission rules and the project's instructions. The core of Atlas knows what every executor has in common (sessions, turns, questions, accounts, usage), and each plugin translates its harness into that, so a new harness comes as a plugin. [Writing a plugin](/docs/extending/plugins/) shows how.

### Where Claude Code's data comes from

Most of what Atlas shows of Claude Code comes from Claude Code itself, through its official protocol and commands. A few things do not, and they are marked, so you know what a new version of Claude Code could change.

| What you see | Where it comes from | Kind |
|---|---|---|
| The conversation: messages, tools, approvals and questions | Claude Code's control protocol | Official |
| The models, effort levels and slash commands it offers | Its answer to `initialize`, in the same protocol | Official |
| Its MCP servers, and whether each one connected | The protocol's `mcp_status` request | Official |
| A plan's limit reached during a turn | The `rate_limit_event` it writes as the turn runs | Official |
| The plan's usage bars: the five-hour window, the week | The protocol's `get_usage` request | Official, but Anthropic marks it experimental: it may change |
| Which account is logged in | `claude auth status` | Official command |
| The list of past sessions and their messages | The session files Claude Code writes, read the way Anthropic's Agent SDK reads them | Official way of reading files |
| A `/command` you typed, shown again as you typed it | The tags Claude Code writes around it in those files | Atlas's own reading |
| A warning that a session is open in another program | Claude Code's list of running sessions, `~/.claude/sessions/` | Not official: used only to warn you |

Atlas never reads Claude Code's login token, and never calls Anthropic's usage API with it.
