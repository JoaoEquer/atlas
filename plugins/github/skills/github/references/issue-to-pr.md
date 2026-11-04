# From a GitHub issue to a pull request

The goal: a tested, verified PR. This page holds the habits for the whole trip: check the
issue's premise, look for existing work, fix every place with the same bug, report CI as it
really is. The mechanics of each step live in the other references.

## When it applies

- "Resolve #123 and send a PR for it."
- "Build the feature this GitHub issue asks for."
- "Carry this bug all the way from the issue to a green CI."

Not for reviewing a PR that already exists, nor for a question about code where nothing is to
change.

## The steps

### 1. Read the issue as it stands today: the body AND every comment

With `run_command`, run `gh issue view <N> --comments`. The body was written when the issue was
opened; the latest comments say where things are now: a partial fix already merged, another
root cause found, a maintainer's decision, or a question for you that changes the work. Read the repository's instructions too
(`AGENTS.md`, contribution docs) with `read_file`. Done when you know the behavior asked for
now, what is out of scope, and which questions in the discussion are still open.

### 2. Look for work that already exists

Before any code, run `gh pr list --search "#<N>" --state all`, then at least two more searches
with other words or synonyms for the symptom
(`gh pr list --search "<subsystem> <symptom>" --state open`). A popular issue draws several
independent fixes; a duplicate wastes the work and the credit. Check too whether a recent commit already solved it:
`git log --oneline -20 -- <relevant files>`. Done when you know each open PR and recent commit
that touches the issue, or that there are none.

### 3. Check the premise against today's code, and against why it was written

On the default branch as it is now, make the bug happen, or show the behavior is missing, with
a failing test or fixture; follow the reported path with `find_files` and `read_file`. Then ask
whether the "bug" was meant: on the code the issue wants changed, `git log -p -S "<symbol>"`
finds the commit that wrote it; read its intent, since a missing link or restriction is often
the point. When the issue's text is stale or
wrong, push back instead of building it blindly. Done when the root cause, or the missing
feature, shows in today's code AND the change does not fight a design chosen on purpose.

### 4. Say what done means and what could go wrong

Write down the acceptance criteria, the interfaces, any migration or change of stored state,
compatibility, security and privacy, rollout and rollback. Tie each criterion to a test or a
named check. Done when a reviewer has a contract with an end.

### 5. Make the smallest change that is complete, and fix every case of it

Work on a branch or worktree of its own. When the kind of bug calls for a debugging or
test-first skill and the library has one, load it with `use_skill`. Regression tests first,
then the change. Once the fix works, search with `find_files` for the same bug at the
neighbouring call sites and fix them all in this PR: a fix that leaves known copies of the bug
is worse than none. Every changed line must answer to the issue; no tidying on the way past.
Done when the targeted tests pass, the first failure is gone, and each neighbouring site is
fixed or shown not to need it.

### 6. Show that the regression test catches the bug (break it on purpose)

Briefly put back the old behavior of the exact function the test covers, run the new test, and
see it FAIL; then restore the fix and see it pass. A test that stays green either way is
worthless. Done when the test is seen failing on the pre-fix code.

### 7. Pass the repository's checks, then open the PR at once

On the parts you touched, run the formatter, the linter, the type checker and the repository's
usual test command; read the diff with the pre-push review in `references/code-review.md`.
Then push and open the PR straight away: CI only starts once the PR is up, and it is the slowest
part, so finished work must not sit. `references/pr-workflow.md` covers how: a conventional
branch and commit, and a body that links the issue and gives the problem, the approach, the
tests, the risk and what was left out. Read the PR back: its head SHA, base, title and files
must be the ones you meant. Done when the PR is up with the intended diff and CI is running.

### 8. See CI through honestly, and close the loop

Read the live checks and the failure logs with `gh pr checks` / `gh run view --log-failed`.
Tell failures your diff caused apart from ones already there or from the infrastructure; when
unsure, try the default branch, and rerun once, only for a real infrastructure flake. Never
say "green", "merged" or "released" without seeing that exact state live. When the PR lands,
comment on the issue with its link and one line on what it does, so the reporter can trace the
fix. Done when the CI state, what still blocks, and the issue's discussion all match the truth.

## Where it goes wrong

- Writing code before reading the issue's comments, looking for duplicate PRs, or reading
  today's code.
- "Fixing" behavior that the commit which introduced it shows was intended.
- Fixing the symptom at one call site while its neighbours keep the same bug.
- A regression test that passes without the fix as well.
- A PR whose tests were never run, or that carries unrelated formatting changes.
- Calling the issue delivered because a PR exists.

## Before you say it is done

- [ ] The whole discussion on the issue read; the plan reflects its latest comment.
- [ ] Duplicate PRs searched for by issue number and by 2 other wordings.
- [ ] The premise reproduced on today's code; the design's intent checked in git history.
- [ ] The regression test seen failing without the fix.
- [ ] Neighbouring call sites fixed, or shown not to need it.
- [ ] Every changed line answers to the issue.
- [ ] CI reported only from what is live; the issue commented with the PR's link.
