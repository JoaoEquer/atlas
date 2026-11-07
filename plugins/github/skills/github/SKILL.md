---
name: github
description: "GitHub through the gh CLI: pull requests, issues, code review, repositories and signing in."
version: 2.0.0-atlas.1
license: MIT
---

# GitHub

Everything on GitHub goes through the `gh` CLI: signing in, issues, a pull
request from branch to merge, taking an issue all the way to a PR, reviewing
code, and looking after repositories. This page only points the way; each
task is written out in full in its own reference. ALWAYS open that reference
before you start the task.

## Which reference

| What you are doing | Open first |
|---|---|
| gh not installed or not signed in, a new machine, setting up SSH | `references/auth.md` |
| Opening, triaging, labeling, assigning or closing issues | `references/issues.md` |
| Branch, commit, open the PR, watch CI, merge | `references/pr-workflow.md` |
| Taking an ISSUE to a PR that is checked and verified (the whole loop) | `references/issue-to-pr.md` |
| Reviewing a PR: diffs, comments on lines, a verdict | `references/code-review.md` |
| Cloning, creating or forking repos, remotes, releases | `references/repo-management.md` |

Also here, for support: `references/ci-troubleshooting.md`,
`references/conventional-commits.md`, `references/github-api-cheatsheet.md` and
`references/review-output-template.md`, plus `templates/` with PR bodies, a bug
report and a feature request.

## Rules for every task

- Once per session, before anything else: `gh auth status`. When it fails,
  `references/auth.md` comes first.
- The login is `gh`'s own. Never read, export or paste a GitHub token.
- Use `gh`'s own commands; reach for `gh api` only when no command covers
  the endpoint (the cheatsheet lists those).
- Every `gh` action that changes GitHub (create, comment, merge, edit,
  close, release, a `gh api` write) is put to the user by Atlas's gate.
  Say in one line what you are about to do, run the command, and take the
  gate's approval as the confirmation: don't ask in chat beforehand, and
  after a refusal never look for another way to do it.
- CI is green only once you have read `gh pr checks` yourself; a PR is
  merged only once you have checked `state,mergedAt`.
- Read everything before you write: `gh issue view --comments` /
  `gh pr view --comments`. Decisions live in the discussion, not the title.
- Before creating anything, look for one that already exists:
  `gh pr list --search` / `gh issue list --search`.

## When it is done

- Each task's reference says what done means for it.
- For all of them: anything you say about GitHub's state (CI, merge,
  release, an issue's state) rests on a fresh `gh` read, never on memory.
