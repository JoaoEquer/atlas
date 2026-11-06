# A pull request from start to finish

Everything a PR goes through, from the first branch to the merge, with `git` and `gh`.

## Before you start

- `gh auth status` passes (see `references/auth.md`)
- You are in a git repository whose remote is on GitHub

`gh` works out owner/repo from the remote; outside a checkout, add `--repo owner/repo`. Inside
`gh api` paths, `{owner}` and `{repo}` are replaced with the current repository's.

---

## 1. A new branch

```bash
# Bring main up to the remote's state first
git fetch origin
git checkout main
git pull origin main

# The branch for the work, created and checked out at once
git checkout -b feat/session-login
```

How branches are named:
- `feat/description` — something new
- `fix/description` — a bug fixed
- `refactor/description` — code reshaped
- `docs/description` — documentation
- `ci/description` — the CI/CD setup

## 2. Commits

Change the files with the file tools (`write_file`, `edit_file`), then commit:

```bash
# Name the files to stage, one by one
git add src/auth.py src/models/user.py tests/test_auth.py

# The message in Conventional Commits form
git commit -m "feat: let users sign in with a JWT

- Endpoints to register and to log in
- A User model that stores a hashed password
- Middleware that guards the private routes
- Tests that cover the sign-in flow"
```

The message follows Conventional Commits (`references/conventional-commits.md` has the details):
```
type(scope): short description

A body when the subject is not enough, wrapped at 72 characters.
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `ci`, `chore`, `perf`

## 3. Push, then open the PR

First look for one that exists: `gh pr list --search "<topic>" --state all`.

```bash
git push -u origin HEAD

gh pr create \
  --title "feat: let users sign in with a JWT" \
  --body "## Summary
- Register and log-in endpoints in the API
- Tokens issued and checked as JWTs

## Test Plan
- [ ] The unit tests are green

Closes #42"
```

More options: `--draft`, `--reviewer user1,user2`, `--label "enhancement"`, `--base develop`.
A long body is written to a file with `write_file` and passed as `--body-file <path>`
(templates: `templates/pr-body-feature.md`, `templates/pr-body-bugfix.md`).

Then read the PR back: `gh pr view --json number,url,headRefOid,baseRefName,title,files`.

## 4. Watching CI

```bash
# The state of the checks right now
gh pr checks

# Keep asking, every 10 seconds, until every check is done
gh pr checks --watch
```

## 5. Fixing a red CI yourself

### Step 1: Find out what failed

```bash
# The last five runs of the branch's workflows
gh run list --branch <branch> --limit 5

# Only the log lines of the steps that failed
gh run view <RUN_ID> --log-failed
```

The branch's name comes from `git branch --show-current`. The failures seen most often are in
`references/ci-troubleshooting.md`.

### Step 2: Fix it and push

Once you know what is wrong, fix it with `read_file` and `edit_file`/`write_file`:

```bash
git add <fixed_files>
git commit -m "fix: make <check_name> pass in CI"
git push
```

### Step 3: Check again

Read the CI status once more with `gh pr checks` (section 4).

### The loop, when asked to fix CI on your own

1. Read CI's status → find what failed
2. Read the logs of the failure → understand the error
3. `read_file` + `edit_file`/`write_file` → fix the code
4. `git add <files>`, `git commit -m "fix: ..."`, `git push`
5. Wait for CI → read its status again
6. Still failing: go round again, 3 times at most, then ask the user

## 6. The merge

```bash
# One squashed commit, and the branch removed: the tidiest end for a feature branch
gh pr merge --squash --delete-branch

# Let GitHub merge once every check is green (only if the repository allows auto-merge)
gh pr merge --auto --squash --delete-branch
```

The ways to merge: `--merge` (a merge commit), `--squash`, `--rebase`.

Check it, and never say merged before you have:

```bash
gh pr view <N> --json state,mergedAt
```

Then return to main on this machine:

```bash
git checkout main
git pull origin main
git branch -d <branch>
```

## 7. The whole thing, in one go

```bash
# 1. main, current and clean
git checkout main
git pull origin main

# 2. A branch for the fix
git checkout -b fix/login-redirect-bug

# 3. (the code is changed with the file tools)

# 4. The commit
git add src/auth/login.py tests/test_login.py
git commit -m "fix: send the user to the right page after login

Keeps the ?next= parameter; the redirect no longer always goes to /dashboard."

# 5. Push, then the PR (section 3)
git push -u origin HEAD
gh pr create --title "fix: send the user to the right page after login" --body-file <path>

# 6. Wait on CI (section 4)
gh pr checks --watch

# 7. Merge once it is green, and confirm it (section 6)
gh pr merge --squash --delete-branch
gh pr view --json state,mergedAt
```

## Other PR commands

| To | gh |
|--------|-----|
| See the PRs you opened | `gh pr list --author @me` |
| Read a PR and its discussion | `gh pr view N --comments` |
| See a PR's diff | `gh pr diff N` (on a checkout, `git diff main...HEAD` also works) |
| Comment on it | `gh pr comment N --body "..."` |
| Ask someone to review | `gh pr edit N --add-reviewer user` |
| Close it | `gh pr close N` |
| Check out another person's PR | `gh pr checkout N` (or `git fetch origin pull/N/head:pr-N`, then `git checkout pr-N`) |
