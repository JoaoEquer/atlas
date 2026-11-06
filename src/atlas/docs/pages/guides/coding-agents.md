---
title: "Coding agents and accounts"
description: "Run Claude Code, Codex, the Gemini CLI and DeepSeek inside Atlas on all your accounts, switch accounts mid-conversation, and give each session a route for when a plan runs out."
sidebar:
  order: 1
---

Atlas runs the coding agents you already use, Claude Code, Codex, the Gemini CLI and DeepSeek's harness, as **executors**. It runs the copy you installed, with your settings, your project's instructions and your own login, and it shows every session they have, including the ones you started in a terminal or an editor.

## Turn them on

Open **Extensions**, in the sidebar, and on its **Executors** tab turn on the harnesses you use; one that is not installed shows the command that installs it. Each one you turn on appears in the **Executors** tab of the sidebar with its project folders, its sessions and its plan's usage: the five-hour window, the week and each model's quota, read the way the harness's own `/usage` shows them, without spending a token.

A folder or a session you archive leaves the list, and comes back by itself as soon as it changes again, in Atlas or in a terminal. To keep only what you have been working on, run **Bring your work** again (**Settings**, then **General**, then **Open the first run again**): what has not changed within the days you choose is archived, and nothing is copied or touched.

A session you started elsewhere shows up in the list too. If it is still open there, Atlas warns you and offers to go on in a copy. To start a new one, choose **New session** at the top of the tab, then the executor and the project folder.

When an update of Atlas changes what an executor's plugin may do, the plugin waits for your consent again before it runs, and so does the executor. Its place in the sidebar says so ("Claude Code waits for your consent again after an update"), and so does a tab of one of its sessions; **Review and turn on** opens the plugin in **Extensions**, on the **Plugins** tab, with what it now declares. Once you turn it on, its sessions come back as they were. One that is not logged in, never or no longer, says **not logged in** on its row, with a **Log in** button that opens the harness's own login in your browser.

## Updates

When a newer version of a coding agent is out, its row says **Update available** with both versions, and **Update** installs it the way it was installed: `claude update`, `winget upgrade`, `brew upgrade` or your npm. Hover the button to see the command it runs. Atlas looks at most once a day, in the background. Updating needs that agent's sessions stopped, since a program in use cannot be replaced; when it ends, Atlas looks for the agent again and shows the version it finds. One installed some other way, such as from the Microsoft Store, updates there as before.

An agent older than the oldest version Atlas drives stays in the list, saying it is too old, with the same **Update** button when Atlas knows how it updates, and does not run until it is updated.

## When a plan runs out

A session that stops mid-task at its plan's limit, with no other account to take it, goes on by itself, once, a minute after the harness says the limit resets, with "Continue the task from where you stopped." The session says when ("The plan's limit came: it goes on by itself at 15:05"). If the computer is asleep then, it goes on when it wakes. It does not if you wrote to it in the meantime, switched its account or stopped it. A harness that does not say when its limit resets leaves the session to you, and the session says so.

## Several accounts

Atlas runs each coding agent on your own login. To use more than one account, turn on the **Accounts** plugin, in **Extensions**, on the **Plugins** tab; the first run offers it too. Everything below in this section, switching accounts and routes, comes with it.

Each account is its own executor, such as **Claude Code · personal** and **Claude Code · company B**, and keeps its own world: its own folder, login, settings, MCP servers, plugins and history. Accounts run at the same time, and each project folder starts its new sessions on the account you chose for it.

To add one, open the harness in the Executors tab, choose **Add an account** at the top of its list and give it a nickname. Atlas makes a folder for it beside your main one; its row's menu then opens the harness's own login in your browser: sign in there with that account. Atlas never sees, keeps or passes a credential. Opened, an account's list shows the projects that start on it, where you add or take one back, and **Remove**, which leaves its folder as it is, with its login and its sessions.

If you used several accounts before this plugin existed, the update turned it on for you, with your accounts, their order and your routes; no folder was touched.

## Switch accounts in a conversation

Out of credit on one account? Switch the session to another. The conversation goes on in a copy on that account, and the first account keeps it as it was: Atlas deletes no session.

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

## Under the session's box

- **Where it works**: the project and the branch checked out there. Its menu shows the whole folder and, in a git repository, offers **New worktree**: a workspace in Atlas's home (`worktrees/`), on a new branch `atlas/<date-time>` from the one checked out, where a new session works apart from it. Nothing in the project's folder changes.
- **The mode**, for a harness that lists its permission modes: Claude Code's **Manual** (it asks before it changes anything), **Accept edits**, **Plan** and **Automatic**. Press 1 to 4 in its menu to pick one. Automatic is read about before it turns on. A harness with only an automatic mode keeps the **Auto** switch.
- **The plan ring** fills with the fullest of the account's plan limits, and turns amber near the limit. Hover over it to see each limit and when it starts again.

On the right of the title bar, a session's folder opens in the file manager, in VS Code, or in a terminal.

## Workspaces

A workspace is one task of a project: a copy of the repository (a git worktree) in Atlas's home, on a branch of its own, `atlas/<name>`, where sessions of any coding agent work without touching the project's folder or each other. Several can run side by side.

- **Where they are**: atop the **Executors** tab, each under its project. A click starts a session in it. A workspace says when its setup script is still running, or failed, with the script's last words.
- **A new one**: **New worktree** under a session's box, or **New workspace** in its project's menu (the three dots), by a name of your own.
- **How a new one is prepared**: **Prepare new workspaces**, in the project's menu. The files listed in the project's `.worktreeinclude`, one path per line (a `.env`, for example), are copied in first; then the setup script runs in its folder (`npm ci`, for example). Give a first port, and each workspace gets a block of its own, in `ATLAS_WORKSPACE_PORT` and `ATLAS_WORKSPACE_PORTS`, so two dev servers never clash.
- **Archive** keeps its work in a commit on its branch and removes its folder; bringing it back makes the folder again with the work as it was, nothing committed. **Delete** removes the folder and the branch after a yes, and asks again when it holds work not delivered.
- **Changes**: with a session in a workspace on screen, the side panel's **Changes** tab shows every file it changed since it left its branch, committed or not, line by line. Click a line to comment on it; the comments gather below, and **Send to the agent** gives them to the session as one message, each with its file, its line and the line itself, so you never have to describe where.
- **Deliver**: atop **Changes**, commit the workspace's changes with a message of yours (your own commit, with the project's hooks). Then **Merge into** the branch it came from, in the project's folder where that branch is checked out (it must hold no changes of its own), or **Send the branch** to the project's remote, `origin`; each asks first. A merge that would conflict changes nothing: ask the agent to merge the branch of origin into its own and settle the conflicts, then deliver again. With the GitHub plugin on, **Open a pull request** sends the branch and opens one into the branch it came from, with your title (as a draft if you like); then the pull request and how each of its checks stands show there.
- **From an issue**: with the GitHub plugin on, **Issues on GitHub** under a project lists its open issues; **Start** makes a workspace named after the issue and opens a session there whose first message is the issue.
- **Checkpoints**: before each message you send to a session in a workspace, Atlas keeps its files as they are, in git but on no branch. Under **Changes**, **Go back here** puts the files back as they were before that message; how they were just before is kept as a checkpoint too, so going back can itself be undone. The session then goes on in a copy, and the original keeps what was said since. Ignored files, such as a `.env`, are left as they are.
- When Atlas delegates a task to a coding agent, it can ask for a new workspace for it. The result then says the branch and what changed.

If a harness asks before every tool in a workspace because it does not trust its folder, trust the `worktrees` folder of Atlas's home once: that covers every workspace.

The side panel, with the session's executor open, shows its **Environment** first: the machine, the folder and its branch. Below it, **Context** says how much of the model's window the session holds, when the harness says.

## Questions and permissions

When an executor asks a question or asks for permission, the request waits for you with no deadline, in the app or on your phone through Telegram. You can also let Atlas answer: its safety reviewer takes the permissions and the conversation's model takes the questions, and what either is unsure of still comes to you.

Each harness keeps its own permission rules. Atlas decides only who answers what the harness asks.

## Claude Code's browser

Claude Code can drive your own Chrome through Claude's Chrome extension (Claude in Chrome): open tabs, read pages, click, see what is on them. In a terminal you turn it on with `/chrome`; in Atlas that command is not there, because the browser is turned on when the session starts.

To have it, install the extension in Chrome, then open **Extensions**, the **Plugins** tab, and set Claude Code's **chrome** setting to **on** (or run `atlas plugins set claude-code chrome on`). Each Claude Code session started after that, on every account, comes with the browser; one already open takes it when it starts again. The tabs open in your Chrome, in its own window, not inside Atlas.

This is Claude Code's browser. Atlas's own, for its conversations, is another one: [The browser](/docs/guides/browser/).

## Let Atlas hand out the work

Ask Atlas in plain words, and it delegates to the right executor on the account you name, with a route of its own when you give one, for example `claude-code@company` and then `codex`. It follows along and brings back the result. Ask for the work to be done apart, in a new workspace, and it runs in a copy of the project on a branch of its own, made in Atlas's home and prepared as the project says; the result names the branch and what changed. Otherwise it works in the folder itself, and Atlas is told when another agent is working there too.

## How Atlas talks to them

Atlas brings no copy of these programs. Each one is driven by a plugin Atlas makes, installed when you turn the agent on, which runs the program you installed and speaks the protocol its own makers use for their apps:

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
