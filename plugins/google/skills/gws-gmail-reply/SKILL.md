---
name: gws-gmail-reply
description: "Gmail: answer a message, in its thread."
metadata:
  version: 0.22.5
  category: "productivity"
  requires:
    bins:
      - gws
  cliHelp: "gws gmail +reply --help"
---

# gmail +reply

> **Load first:** the `google:gws-shared` skill, which covers signing in, the global flags and the safety rules.

Answers a message and keeps the answer in the same thread, with no extra work.

## Usage

```bash
gws gmail +reply --message-id <ID> --body <TEXT>
```

## Flags

| Flag | Needed | Default | What it does |
|------|--------|---------|--------------|
| `--message-id` | ✓ | — | The ID of the Gmail message being answered |
| `--body` | ✓ | — | The answer's text (plain, or HTML when --html is given) |
| `--from` | — | — | The address it goes out from (a send-as alias; leave it out for the account's own) |
| `--to` | — | — | More addresses for the To field, separated by commas |
| `--attach` | — | — | A file to attach (repeat the flag for more) |
| `--cc` | — | — | Addresses copied, separated by commas |
| `--bcc` | — | — | Addresses blind-copied, separated by commas |
| `--html` | — | — | Reads --body as HTML (plain text otherwise) |
| `--dry-run` | — | — | Prints the request it would make and sends nothing |
| `--draft` | — | — | Keeps the answer as a draft rather than sending it |

## Examples

```bash
gws gmail +reply --message-id 18f1a2b3c4d --body 'Thanks, got it!'
gws gmail +reply --message-id 18f1a2b3c4d --body 'Looping in Carol' --cc carol@example.com
gws gmail +reply --message-id 18f1a2b3c4d --body 'Adding Dave' --to dave@example.com
gws gmail +reply --message-id 18f1a2b3c4d --body '<b>Bold reply</b>' --html
gws gmail +reply --message-id 18f1a2b3c4d --body 'Updated version' -a updated.docx
gws gmail +reply --message-id 18f1a2b3c4d --body 'Draft reply' --draft
```

## Good to know

- The In-Reply-To, References and threadId headers are filled in for you.
- The original message is quoted under the answer.
- --to puts more recipients in the To field.
- -a/--attach adds a file and may be given more than once.
- With --html, the quote is wrapped in Gmail's gmail_quote CSS classes and keeps its HTML formatting. Write fragment tags only (<p>, <b>, <a>, and the like); no <html>/<body> wrapper is needed.
- With --html, images shown inside the quoted message are kept, through cid: references.
- --draft stores the answer as a draft and does not send it now.
- To answer everyone on the message, use +reply-all, not this command.

## Related skills

- [gws-shared](../gws-shared/SKILL.md): signing in and the global flags
- [gws-gmail](../gws-gmail/SKILL.md): every Gmail command, for sending, reading and managing mail
