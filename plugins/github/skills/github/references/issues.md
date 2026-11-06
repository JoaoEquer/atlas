# Working with GitHub issues

Opening, finding, triaging and looking after issues, all with `gh`.

## Before you start

- `gh auth status` passes (see `references/auth.md`)
- You are in a git repository whose remote is on GitHub, or you add `--repo owner/repo` to each command

---

## 1. Reading issues

```bash
gh issue list
gh issue list --state open --label "bug"
gh issue list --assignee @me
gh issue list --search "token expired" --state all
gh issue view 42
gh issue view 42 --comments
```

When a script needs the result, ask `gh` for JSON rather than picking the text apart:

```bash
gh issue list --state open --json number,title,labels --jq '.[] | "#\(.number) \(.title)"'
gh issue view 42 --json title,state,labels,assignees,author,createdAt,body
```

## 2. Opening an issue

Look for a duplicate before anything else: `gh issue list --search "<symptom>" --state all`.

```bash
gh issue create \
  --title "After login, the ?next= page is lost" \
  --body "## Description
Whatever page sent them to log in, users end up on /dashboard.

## Steps to Reproduce
1. Signed out, open /settings
2. The app sends you to /login?next=/settings
3. Sign in
4. You land on /dashboard; /settings was the page to return to

## Expected Behavior
The redirect honors ?next=." \
  --label "bug,backend" \
  --assignee "username"
```

A long body goes in a file: write it with `write_file` and pass `--body-file <path>`, which
behaves the same in bash and in PowerShell. Templates: `templates/bug-report.md`,
`templates/feature-request.md`.

### Short form of a bug report

```
## What goes wrong
<the bug>

## How to make it happen
1. <step>
2. <step>

## What should happen
<the behavior you expected>

## What happens instead
<the behavior you get>

## Where it runs
- OS: <os>
- Version: <version>
```

### Short form of a feature request

```
## The feature
<what should exist>

## Why
<the problem it solves>

## How it could work
<a sketch>

## Other ways considered
<the alternatives>
```

## 3. Looking after issues

### Labels, on and off

```bash
gh issue edit 42 --add-label "priority:high,bug"
gh issue edit 42 --remove-label "needs-triage"

# The labels this repository has
gh label list
```

### Who it is assigned to

```bash
gh issue edit 42 --add-assignee username
gh issue edit 42 --add-assignee @me
```

### Comments

```bash
gh issue comment 42 --body "Traced it to the auth middleware; a fix is on the way."
```

### Closing, and opening again

```bash
gh issue close 42
gh issue close 42 --reason "not planned"
gh issue reopen 42
```

### Tying an issue to a PR

When a PR whose body carries one of these words is merged, GitHub closes the issue by itself:

```
Closes #42
Fixes #42
Resolves #42
```

A branch made from the issue:

```bash
gh issue develop 42 --checkout
```

The same with git alone:

```bash
git checkout main
git pull origin main
git checkout -b fix/issue-42-login-redirect
```

## 4. Triage

When the user asks for issues to be triaged:

1. **Find the ones nobody has triaged:** `gh issue list --label "needs-triage" --state open`
2. **Read each and decide what it is** (`gh issue view N --comments`; make sense of the bug or the feature)
3. **Put on the labels and the priority** (as in "Looking after issues" above)
4. **Assign it** when it is clear whose it is
5. **Leave a comment with your triage notes** when they help

## 5. Many issues at once

Get the numbers with `--json`, then act on each one:

```bash
# bash: close every issue that carries one label
gh issue list --label "wontfix" --json number --jq '.[].number' | \
  xargs -I {} gh issue close {} --reason "not planned"
```

PowerShell takes the same list through
`ForEach-Object { gh issue close $_ --reason "not planned" }`.
Closing many issues changes GitHub: tell the user which ones and how many before you run it,
then let the gate ask.

## At a glance

| To | gh |
|--------|-----|
| See the open issues | `gh issue list` |
| Read one, with its discussion | `gh issue view N --comments` |
| Open one | `gh issue create ...` |
| Label it | `gh issue edit N --add-label ...` |
| Give it to someone | `gh issue edit N --add-assignee ...` |
| Comment on it | `gh issue comment N --body ...` |
| Close it | `gh issue close N` |
| Find one | `gh issue list --search "..."` |
