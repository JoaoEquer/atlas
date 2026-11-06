# Signing in to GitHub

Getting `gh` ready before any work on repositories, pull requests, issues or
CI. The login belongs to `gh`, which keeps it in the system keyring, and
`gh auth setup-git` lends it to git for HTTPS. You never hold a token
yourself.

## First, find out where things stand

As soon as the user asks for anything on GitHub, run this before the rest:

```bash
git --version
gh --version
gh auth status
```

**What the output tells you:**
1. `gh auth status` names a logged-in account → nothing to set up; do everything with `gh`
2. `gh` answers but is not logged in → go to "Log in" below
3. `gh` is not found → go to "Install gh" below

---

## Install gh

| System | Command |
|---|---|
| Windows | `winget install --id GitHub.cli` |
| macOS | `brew install gh` |
| Debian/Ubuntu | `sudo apt install gh` (or GitHub's apt repo for a newer version: cli.github.com) |

The install can stop for the user's approval (a winget or sudo prompt). On
Windows, winget puts gh in `C:\Program Files\GitHub CLI`, and a shell that
was already open has the old PATH and won't find it: open a new shell, or
run gh by that full path.

## Log in

```bash
gh auth login --hostname github.com --git-protocol https --web
```

With `--web`, gh signs in through the browser: it prints a code that works
once and the address `https://github.com/login/device`. **Only the user can
finish this step.** Give them the code and the address, ask them to type the
code in the browser and approve it, and wait. There is no way for you to do
it in their place.

> **WATCH OUT (when you drive gh yourself):** gh can stop at "Press Enter to
> open github.com in your browser". Run from a background process, it may
> never open a browser on the user's screen; that doesn't matter, because
> the code and the address are enough and any device can open the address.
> In a Windows PTY, Enter is a carriage return: a bare `\n` does not end the
> line, so the prompt never returns, says nothing, and the login hangs.

The scopes it asks for by default (`repo`, `read:org`, `gist`) are enough
for PRs, issues and releases. To push anything under `.github/workflows/`,
it also needs `workflow`:

```bash
gh auth refresh --scopes workflow
```

> **WATCH OUT (Linux with no desktop):** where there is no keyring (a VPS, a
> container, no dbus session), gh can block while saving the login, waiting
> for a secret-service keyring and printing nothing. When the login does not
> come back, say so to the user: `gh auth login --insecure-storage` keeps the
> login in gh's own config file rather than a keyring.

### Let git use gh's login

```bash
gh auth setup-git
```

From then on, `git push`/`git fetch` over HTTPS sign in with gh's login; git
needs no credentials of its own.

### Check it

```bash
gh auth status
gh api user --jq .login
```

### Who the commits are from

git won't commit without it:

```bash
git config --global user.name "Their Name"
git config --global user.email "their-email@example.com"
```

---

## SSH keys (only if wanted)

For a user who wants git to go over SSH. `gh` keeps signing in with its own
login either way.

```bash
# Is there a key already?
ls ~/.ssh/id_*.pub

# No key yet: make an ed25519 one
ssh-keygen -t ed25519 -C "their-email@example.com" -f ~/.ssh/id_ed25519 -N ""

# Send it to GitHub with gh (the user can also add it at https://github.com/settings/keys)
gh ssh-key add ~/.ssh/id_ed25519.pub --title "atlas-<machine-name>"

# Try it
ssh -T git@github.com
# It should answer: "Hi <username>! You've successfully authenticated..."

# From now on gh runs git over SSH
gh config set git_protocol ssh
```

---

## When something goes wrong

| What you see | What to do |
|---------|----------|
| `gh: command not found` | Install gh (see above); on Windows, a new shell or the full path |
| `gh auth status` says not logged in | `gh auth login --web`, and the user finishes it in the browser |
| `git push` asks for a password | `gh auth setup-git`, or move to SSH |
| `remote: Permission to X denied` | Either the account has no access, or the login is missing a scope: `gh auth refresh --scopes repo,workflow` |
| A pushed workflow file is refused | The `workflow` scope is missing: `gh auth refresh --scopes workflow` |
| `ssh: connect to host github.com port 22: Connection refused` | Port 22 is blocked; go through 443 instead. In `~/.ssh/config`, under `Host github.com`, set `Hostname ssh.github.com` and `Port 443` |
| More than one GitHub account | `gh auth login` for each, then `gh auth switch` to move between them |
