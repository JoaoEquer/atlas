---
name: gws-shared
description: "gws program: signing in, flags every command takes, output."
metadata:
  version: 0.22.5
  category: "productivity"
  requires:
    bins:
      - gws
---

# gws: what every Google Workspace skill relies on

## Getting the program

Atlas runs `gws`, so the program has to be found on `$PATH`. Where Node.js 18 or newer is
installed, `npm install -g @googleworkspace/cli` downloads the binary that fits the machine. Without
Node, the binary comes from the GitHub Releases page of the `gws` project, or, on macOS and Linux,
from `brew install googleworkspace-cli`.

## Signing in

Start by running `gws auth status`. The sign-in itself is done by the user, a single time:

```bash
# Needs gcloud, signed in: makes the Cloud project, turns the APIs on and signs in
gws auth setup

# Later on, or when the OAuth client was made by hand in the Google Cloud console
gws auth login -s gmail,calendar
```

- `gws auth setup` asks its questions in a terminal, and the commands Atlas runs get no input. So
  ask the user to run it in their own terminal and to tell you once it has finished.
- `gws auth login` opens a browser and waits there for the user. You may run this one; tell the
  user beforehand what they will see.

- Request just the services the user will use (`-s gmail,calendar`). An app Google has not
  verified (testing mode) gets roughly 25 scopes at most, so the full preset fails, and on
  `@gmail.com` accounts most of all.
- On the consent screen, "Google hasn't verified this app" is normal when the project is the
  user's own. They click Continue.
- "Error 403: access_denied" ("the app has not completed Google's verification") says the app is
  in testing and the user's account is not a test user of it. They add the account in Audience,
  Test users, at `https://console.cloud.google.com/auth/audience`, then sign in again.
- `gws auth status` shows which APIs the project has turned on. When one the user needs is off,
  `gcloud services enable <api>.googleapis.com --project <id>` turns it on (Atlas asks first).
- A login in testing mode lasts 7 days before Google expires it. Publishing the app for the user
  alone ("In production", left unverified) makes it last.
- Where there is no browser (a container, a server), the user signs in on their own computer and
  runs `gws auth export --unmasked > credentials.json` there; the server is pointed at that file by
  `GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE`. Never print what the file holds.

## Flags any command accepts

| Flag | What it does |
|------|--------------|
| `--format <FORMAT>` | How output is printed: `json` (the default), `table`, `yaml` or `csv` |
| `--dry-run` | Checks the call locally and sends nothing to the API |
| `--sanitize <TEMPLATE>` | Passes responses through a Model Armor screen |

## How a command is built

```bash
gws <service> <resource> [sub-resource] <method> [flags]
```

### Flags of an API method

| Flag | What it does |
|------|--------------|
| `--params '{"key": "val"}'` | Parameters that go in the URL or query string |
| `--json '{"key": "val"}'` | The body of the request |
| `-o, --output <PATH>` | Writes a binary response to a file |
| `--upload <PATH>` | Sends a file's content (multipart) |
| `--page-all` | Follows every page by itself, printing NDJSON |
| `--page-limit <N>` | How many pages --page-all reads at most (10 unless set) |
| `--page-delay <MS>` | Milliseconds to wait between pages (100 unless set) |

## Keeping the user safe

- **Never** print a secret (an API key, a token) as it is.
- **Every command that writes or deletes goes through Atlas's approval** (send, reply, forward,
  insert, update, delete, `gws auth export`). State in one line what you are going to do, then
  run it: the user's approval is the confirmation. Don't ask for it in chat beforehand, and when
  the user refuses, don't look for another way to get the same result.
- For a destructive operation, reach for `--dry-run` first.
- To screen for personal data or unsafe content, use `--sanitize`.

## Quoting in the shell

- **`!` in zsh:** a sheet range such as `Sheet1!A1` holds a `!`, which zsh takes for history
  expansion. Put the value in double quotes (escaping any quotes inside) rather than single quotes:
  ```bash
  # Breaks in zsh: the ! is expanded
  gws sheets +read --spreadsheet ID --range 'Sheet1!A1:D10'

  # Works
  gws sheets +read --spreadsheet ID --range "Sheet1!A1:D10"
  ```
- **JSON values:** put single quotes around what `--params` and `--json` receive, so the double
  quotes inside reach `gws` untouched by the shell:
  ```bash
  gws drive files list --params '{"pageSize": 5}'
  ```

- **Windows PowerShell 5.1** strips the double quotes inside an argument it hands to a program, so
  `--params '{"pageSize": 5}'` arrives at `gws` broken. Escape them (`'{\"pageSize\": 5}'`), or use
  PowerShell 7.3 or newer, which passes them through as written.

## When gws itself is wrong

- A bug in `gws`, or a feature it lacks, is reported at `https://github.com/googleworkspace/cli/issues`: send the user there.
- Before a new issue is opened, **always** look through the issues and feature requests already filed.
- When one already covers it, the user adds what they know as a comment on that thread, and does not open a second one.
