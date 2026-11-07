---
name: gws-gmail-forward
description: "Gmail: pass a message on to other recipients."
metadata:
  version: 0.22.5
  category: "productivity"
  requires:
    bins:
      - gws
  cliHelp: "gws gmail +forward --help"
---

# gmail +forward

> **Load first:** the `google:gws-shared` skill, which covers signing in, the global flags and the safety rules.

Passes a message on to people who did not get it.

## Usage

```bash
gws gmail +forward --message-id <ID> --to <EMAILS>
```

## Flags

| Flag | Needed | Default | What it does |
|------|--------|---------|--------------|
| `--message-id` | ✓ | — | The ID of the Gmail message being passed on |
| `--to` | ✓ | — | Who receives it: one address or several, separated by commas |
| `--from` | — | — | The address it goes out from (a send-as alias; leave it out for the account's own) |
| `--body` | — | — | A note of your own, placed above the forwarded message (plain, or HTML when --html is given) |
| `--no-original-attachments` | — | — | Leaves out the files attached to the original (with --html, images shown inside it are still kept) |
| `--attach` | — | — | A file to attach (repeat the flag for more) |
| `--cc` | — | — | Addresses copied, separated by commas |
| `--bcc` | — | — | Addresses blind-copied, separated by commas |
| `--html` | — | — | Reads --body as HTML (plain text otherwise) |
| `--dry-run` | — | — | Prints the request it would make and sends nothing |
| `--draft` | — | — | Keeps the forward as a draft rather than sending it |

## Examples

```bash
gws gmail +forward --message-id 18f1a2b3c4d --to dave@example.com
gws gmail +forward --message-id 18f1a2b3c4d --to dave@example.com --body 'FYI see below'
gws gmail +forward --message-id 18f1a2b3c4d --to dave@example.com --cc eve@example.com
gws gmail +forward --message-id 18f1a2b3c4d --to dave@example.com --body '<p>FYI</p>' --html
gws gmail +forward --message-id 18f1a2b3c4d --to dave@example.com -a notes.pdf
gws gmail +forward --message-id 18f1a2b3c4d --to dave@example.com --no-original-attachments
gws gmail +forward --message-id 18f1a2b3c4d --to dave@example.com --draft
```

## Good to know

- The original goes along with its sender, date, subject and recipients.
- The original's attachments go along unless told otherwise, as Gmail on the web does.
- With --html, images shown inside the original are kept too, through cid: references.
- As plain text, images shown inside the original are dropped, as Gmail on the web does.
- --no-original-attachments forwards the message without the original's files.
- -a/--attach adds more files and may be given more than once.
- The original's attachments and the added ones together may not pass 25MB.
- With --html, the forwarded part is wrapped in Gmail's gmail_quote CSS classes and keeps its HTML formatting. Write fragment tags only (<p>, <b>, <a>, and the like); no <html>/<body> wrapper is needed.
- --draft stores the forward as a draft and does not send it now.

## Related skills

- [gws-shared](../gws-shared/SKILL.md): signing in and the global flags
- [gws-gmail](../gws-gmail/SKILL.md): every Gmail command, for sending, reading and managing mail
