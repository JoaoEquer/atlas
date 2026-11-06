---
name: gws-gmail-reply-all
description: "Gmail: answer everyone on a message, in its thread."
metadata:
  version: 0.22.5
  category: "productivity"
  requires:
    bins:
      - gws
  cliHelp: "gws gmail +reply-all --help"
---

# gmail +reply-all

> **Load first:** the `google:gws-shared` skill, which covers signing in, the global flags and the safety rules.

Answers everyone on a message and keeps the answer in the same thread, with no extra work.

## Usage

```bash
gws gmail +reply-all --message-id <ID> --body <TEXT>
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
| `--remove` | — | — | Addresses to leave out of the answer, separated by commas |

## Examples

```bash
gws gmail +reply-all --message-id 18f1a2b3c4d --body 'Sounds good to me!'
gws gmail +reply-all --message-id 18f1a2b3c4d --body 'Updated' --remove bob@example.com
gws gmail +reply-all --message-id 18f1a2b3c4d --body 'Adding Eve' --cc eve@example.com
gws gmail +reply-all --message-id 18f1a2b3c4d --body '<i>Noted</i>' --html
gws gmail +reply-all --message-id 18f1a2b3c4d --body 'Notes attached' -a notes.pdf
gws gmail +reply-all --message-id 18f1a2b3c4d --body 'Draft reply' --draft
```

## Good to know

- The answer goes to the sender and to everyone who was in To or CC on the original.
- --to puts more recipients in the To field.
- --cc adds people to CC who were not there.
- --bcc is for recipients the others must not see.
- --remove leaves addresses out of the answer, the sender or the Reply-To address included.
- When removals and --to leave nobody in To, the command fails.
- -a/--attach adds a file and may be given more than once.
- With --html, the quote is wrapped in Gmail's gmail_quote CSS classes and keeps its HTML formatting. Write fragment tags only (<p>, <b>, <a>, and the like); no <html>/<body> wrapper is needed.
- With --html, images shown inside the quoted message are kept, through cid: references.
- --draft stores the answer as a draft and does not send it now.

## Related skills

- [gws-shared](../gws-shared/SKILL.md): signing in and the global flags
- [gws-gmail](../gws-gmail/SKILL.md): every Gmail command, for sending, reading and managing mail
