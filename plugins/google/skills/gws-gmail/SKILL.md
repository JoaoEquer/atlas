---
name: gws-gmail
description: "Gmail: send email, read it and keep the mailbox in order."
metadata:
  version: 0.22.5
  category: "productivity"
  requires:
    bins:
      - gws
  cliHelp: "gws gmail --help"
---

# Gmail through gws (API v1)

> **Load first:** the `google:gws-shared` skill, which covers signing in, the global flags and the safety rules.

```bash
gws gmail <resource> <method> [flags]
```

## Shortcut commands

| Command | What it does |
|---------|--------------|
| [`+send`](../gws-gmail-send/SKILL.md) | Sends a new email |
| [`+triage`](../gws-gmail-triage/SKILL.md) | Lists the unread inbox in brief (sender, subject, date) |
| [`+reply`](../gws-gmail-reply/SKILL.md) | Answers a message, keeping it in its thread |
| [`+reply-all`](../gws-gmail-reply-all/SKILL.md) | Answers everyone on a message, keeping it in its thread |
| [`+forward`](../gws-gmail-forward/SKILL.md) | Passes a message on to other recipients |
| [`+read`](../gws-gmail-read/SKILL.md) | Opens a message and pulls out its body or headers |
| [`+watch`](../gws-gmail-watch/SKILL.md) | Waits for incoming mail and streams each one as NDJSON |

## What the API exposes

### users

  - `getProfile`: the signed-in user's Gmail profile.
  - `stop`: ends push notifications for the user's mailbox.
  - `watch`: starts, or changes, a push notification watch on the user's mailbox.
  - `drafts`: methods of the 'drafts' resource
  - `history`: methods of the 'history' resource
  - `labels`: methods of the 'labels' resource
  - `messages`: methods of the 'messages' resource
  - `settings`: methods of the 'settings' resource
  - `threads`: methods of the 'threads' resource

## Finding the right call

Look a method up before you call it:

```bash
# List the resources and their methods
gws gmail --help

# One method: which params it needs, their types and defaults
gws schema gmail.<resource>.<method>
```

What `gws schema` prints is what you build the `--params` and `--json` flags from.
