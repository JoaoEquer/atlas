---
name: gws-gmail-read
description: "Gmail: open a message, get its body or headers."
metadata:
  version: 0.22.5
  category: "productivity"
  requires:
    bins:
      - gws
  cliHelp: "gws gmail +read --help"
---

# gmail +read

> **Load first:** the `google:gws-shared` skill, which covers signing in, the global flags and the safety rules.

Opens one message and pulls out its body, or its headers.

## Usage

```bash
gws gmail +read --id <ID>
```

## Flags

| Flag | Needed | Default | What it does |
|------|--------|---------|--------------|
| `--id` | ✓ | — | The ID of the Gmail message to open |
| `--headers` | — | — | Adds the From, To, Subject and Date headers to what is printed |
| `--format` | — | text | How it is printed (text, json) |
| `--html` | — | — | Gives the HTML body, not the plain text one |
| `--dry-run` | — | — | Prints the request it would make and sends nothing |

## Examples

```bash
gws gmail +read --id 18f1a2b3c4d
gws gmail +read --id 18f1a2b3c4d --headers
gws gmail +read --id 18f1a2b3c4d --format json | jq '.body'
```

## Good to know

- A message that only has HTML is turned into plain text without being asked.
- multipart/alternative messages and base64 bodies are decoded for you.

## Related skills

- [gws-shared](../gws-shared/SKILL.md): signing in and the global flags
- [gws-gmail](../gws-gmail/SKILL.md): every Gmail command, for sending, reading and managing mail
