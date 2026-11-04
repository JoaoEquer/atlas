---
name: gws-gmail-send
description: "Gmail: send a new email."
metadata:
  version: 0.22.5
  category: "productivity"
  requires:
    bins:
      - gws
  cliHelp: "gws gmail +send --help"
---

# gmail +send

> **Load first:** the `google:gws-shared` skill, which covers signing in, the global flags and the safety rules.

Sends a new email.

## Usage

```bash
gws gmail +send --to <EMAILS> --subject <SUBJECT> --body <TEXT>
```

## Flags

| Flag | Needed | Default | What it does |
|------|--------|---------|--------------|
| `--to` | ✓ | — | Who receives it: one address or several, separated by commas |
| `--subject` | ✓ | — | The subject line |
| `--body` | ✓ | — | The message text (plain, or HTML when --html is given) |
| `--from` | — | — | The address it goes out from (a send-as alias; leave it out for the account's own) |
| `--attach` | — | — | A file to attach (repeat the flag for more) |
| `--cc` | — | — | Addresses copied, separated by commas |
| `--bcc` | — | — | Addresses blind-copied, separated by commas |
| `--html` | — | — | Reads --body as HTML (plain text otherwise) |
| `--dry-run` | — | — | Prints the request it would make and sends nothing |
| `--draft` | — | — | Keeps the message as a draft rather than sending it |

## Examples

```bash
gws gmail +send --to alice@example.com --subject 'Hello' --body 'Hi Alice!'
gws gmail +send --to alice@example.com --subject 'Hello' --body 'Hi!' --cc bob@example.com
gws gmail +send --to alice@example.com --subject 'Hello' --body '<b>Bold</b> text' --html
gws gmail +send --to alice@example.com --subject 'Hello' --body 'Hi!' --from alias@example.com
gws gmail +send --to alice@example.com --subject 'Report' --body 'See attached' -a report.pdf
gws gmail +send --to alice@example.com --subject 'Files' --body 'Two files' -a a.pdf -a b.csv
gws gmail +send --to alice@example.com --subject 'Hello' --body 'Hi!' --draft
```

## Good to know

- The command takes care of the RFC 5322 format, the MIME encoding and the base64 on its own.
- --from sends through a send-as alias already set up on the account, not the main address.
- -a/--attach adds a file and may be given more than once. All attachments together may not pass 25MB.
- With --html, write fragment tags only (<p>, <b>, <a>, <br>, and the like); no <html>/<body> wrapper is needed.
- --draft stores the message as a draft and does not send it now.

> [!CAUTION]
> This command **writes**: Atlas asks the user before running it. Say in one line what you are
> going to do and run it; don't ask a second time in chat.

## Related skills

- [gws-shared](../gws-shared/SKILL.md): signing in and the global flags
- [gws-gmail](../gws-gmail/SKILL.md): every Gmail command, for sending, reading and managing mail
