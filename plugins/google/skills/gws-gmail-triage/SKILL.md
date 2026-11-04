---
name: gws-gmail-triage
description: "Gmail: the unread inbox in brief (sender, subject, date)."
metadata:
  version: 0.22.5
  category: "productivity"
  requires:
    bins:
      - gws
  cliHelp: "gws gmail +triage --help"
---

# gmail +triage

> **Load first:** the `google:gws-shared` skill, which covers signing in, the global flags and the safety rules.

Lists the unread inbox in brief: who sent each message, its subject and its date.

## Usage

```bash
gws gmail +triage
```

## Flags

| Flag | Needed | Default | What it does |
|------|--------|---------|--------------|
| `--max` | — | 20 | How many messages it lists at most (20 unless set) |
| `--query` | — | — | A Gmail search to use instead (is:unread unless set) |
| `--labels` | — | — | Adds each message's label names to the list |

## Examples

```bash
gws gmail +triage
gws gmail +triage --max 5 --query 'from:boss'
gws gmail +triage --format json | jq '.[].subject'
gws gmail +triage --labels
```

## Good to know

- It only reads: the mailbox is never changed.
- It prints a table unless another format is asked for.

## Related skills

- [gws-shared](../gws-shared/SKILL.md): signing in and the global flags
- [gws-gmail](../gws-gmail/SKILL.md): every Gmail command, for sending, reading and managing mail
