# Reviewing code

Two kinds of review: changes still on this machine, before they are pushed, and pull requests
already open on GitHub. Plain `git` does most of the work; `gh` comes in for what happens on
the PR itself.

## Before you start

- `gh auth status` passes (see `references/auth.md`)
- The working folder is a git repository

`gh api` fills `{owner}` and `{repo}` in its paths from the remote of the repository you are in.

---

## 1. Local changes, before the push

Only `git`; no API call.

### The diff

```bash
# What the next commit would hold: the staged part
git diff --staged

# Everything since main, which is what a PR would carry
git diff main...HEAD

# Just the paths that changed
git diff main...HEAD --name-only

# Lines added and removed, file by file
git diff main...HEAD --stat
```

### How to go through it

1. **Start with the size and shape of the change:**

```bash
git diff main...HEAD --stat
git log main..HEAD --oneline
```

2. **Then one file at a time**: the diff says what changed, and `read_file` on the changed
   file shows what surrounds it:

```bash
git diff main...HEAD -- src/auth/login.py
```

3. **Sweep for the usual slips** (bash; in PowerShell pipe into `Select-String` with the same
   patterns):

```bash
# Leftover prints, console.log calls, debugger lines and TODO-style notes
git diff main...HEAD | grep -n "print(\|console\.log\|TODO\|FIXME\|HACK\|XXX\|debugger"

# The ten biggest files in the change (a stray binary shows here)
git diff main...HEAD --stat | sort -t'|' -k2 -rn | head -10

# Anything that looks like a password, key or token
git diff main...HEAD | grep -in "password\|secret\|api_key\|token.*=\|private_key"

# Conflict markers a merge left in place
git diff main...HEAD | grep -n "<<<<<<\|>>>>>>\|======="
```

4. **Give the user what you found, in the shape below.**

### The shape of a local review

Group what you found under these headings:

```
## Code Review Summary

### Critical
- **src/auth.py:45** — the query is built from user input as is: SQL injection.
  Suggestion: bind it as a parameter instead.

### Warnings
- **src/models/user.py:23** — the password is kept in clear text; hash it with bcrypt or argon2.
- **src/api/routes.py:112** — the login endpoint has no rate limit.

### Suggestions
- **src/utils/helpers.py:8** — the same logic as `src/core/utils.py:34`; keep one copy.
- **tests/test_auth.py** — no test for a token that has expired.

### Looks Good
- The middleware layer keeps each concern apart
- The happy path is well tested
```

---

## 2. A pull request on GitHub

### Reading the PR

```bash
gh pr view 123
gh pr view 123 --comments
gh pr diff 123
gh pr diff 123 --name-only
gh pr view 123 --json files --jq '.files[] | "\(.additions) +/\(.deletions) -  \(.path)"'
```

### Checking it out to review it whole

```bash
gh pr checkout 123

# The same without gh
git fetch origin pull/123/head:pr-123
git checkout pr-123

# The branch is now on disk: read_file, find_files and the tests all work on it

# Compare it with the branch it targets
git diff main...HEAD
```

### Commenting

**On the PR as a whole:**

```bash
gh pr comment 123 --body "Mostly fine; some notes follow."
```

**On one line (through the API):**

```bash
gh pr view 123 --json headRefOid --jq .headRefOid   # the head SHA

gh api repos/{owner}/{repo}/pulls/123/comments \
  --method POST \
  -f body="A list comprehension would make this shorter." \
  -f path="src/auth/login.py" \
  -f commit_id="<HEAD_SHA>" \
  -F line=45 \
  -f side="RIGHT"
```

### A formal review: approve, request changes, or comment

```bash
gh pr review 123 --approve --body "Ready to merge."
gh pr review 123 --request-changes --body "Details are on the lines."
gh pr review 123 --comment --body "A few ideas; none of them block the merge."
```

**Several comments in one review, posted together**: put the review in a JSON file with
`write_file` and send it with `--input` (the same in bash and PowerShell):

```json
{
  "commit_id": "<HEAD_SHA>",
  "event": "COMMENT",
  "body": "Code review",
  "comments": [
    {"path": "src/auth.py", "line": 45, "body": "Bind the input as a query parameter; as written it allows SQL injection."},
    {"path": "src/models/user.py", "line": 23, "body": "Hash the password with bcrypt before it is saved."},
    {"path": "tests/test_auth.py", "line": 1, "body": "A test for an expired token is missing."}
  ]
}
```

```bash
gh api repos/{owner}/{repo}/pulls/123/reviews --method POST --input review.json
```

`event` takes `"APPROVE"`, `"REQUEST_CHANGES"` or `"COMMENT"`.

`line` counts lines in the file as the PR leaves it (the *new* side). A comment on a line the
PR deletes needs `"side": "LEFT"`.

---

## 3. What to check

Every review, local or on a PR, goes through all of these:

### Correctness
- Does the code do what it says it does?
- What about the edges: empty input, nulls, a lot of data, access from several places at once?
- When something fails, does it fail cleanly?

### Security
- No secret, credential or API key written into the code
- Input that comes from users is validated
- No SQL injection, XSS or path traversal
- Authentication and authorization checked where they are needed

### Code quality
- Names that say what a variable, function or class is
- Nothing more complex or more abstract than the problem needs yet
- DRY: no logic repeated that belongs in one place
- Each function does one thing

### Tests
- Are the new code paths tested?
- Both the happy path and the errors?
- Can the tests be read and kept up?

### Performance
- No N+1 queries, no loops that need not be there
- A cache where one pays off
- Nothing that blocks inside async code

### Documentation
- Public APIs are documented
- Logic that is not obvious has a comment saying why
- The README changed if the behavior did

---

## 4. The local review, step by step

When the user asks you to "review the code" or to "check before pushing":

1. `git diff main...HEAD --stat` — how far the change reaches
2. `git diff main...HEAD` — the whole diff, read through
3. `read_file` on any changed file whose diff is not enough on its own
4. The checklist of section 3
5. The findings, under Critical / Warnings / Suggestions / Looks Good
6. Anything Critical: offer to fix it before the user pushes

---

## 5. The PR review, start to finish

When the user asks you to "review PR #N" or to "look at this PR", or hands you a PR's URL, do
this:

### Step 1: Preflight

`gh auth status`; when it fails, go to `references/auth.md`.

### Step 2: The PR's context

Before the code, learn the scope: the metadata, the description, the discussion and which files
changed.

```bash
gh pr view 123 --comments
gh pr diff 123 --name-only
gh pr checks 123
```

### Step 3: Check it out

With the branch on disk you have `read_file`, `find_files`, and you can run the tests.

```bash
gh pr checkout 123
```

### Step 4: The diff

```bash
# The whole change against the branch it targets
git diff main...HEAD

# A big PR reads better one file at a time: list the paths
git diff main...HEAD --name-only
# and take them one by one
git diff main...HEAD -- path/to/file.py
```

Open each changed file with `read_file` too: some problems only show next to the code around
the change, never in the diff.

### Step 5: The automated checks, run here (when the repository has them)

Run the repository's tests and linter (`pytest`, `npm test`, `cargo test`, `go test ./...`,
`ruff check .`, `eslint`, `clippy`, ...) and read the end of the output for failures.

### Step 6: The checklist of section 3

Each heading in turn: Correctness, Security, Code Quality, Testing, Performance, Documentation.

### Step 7: The review, posted on GitHub

Gather the findings into one formal review with comments on the lines.

```bash
# Nothing to fix: approve
gh pr review 123 --approve --body "Clean and well tested; no security problems found."

# Something to fix: request changes
gh pr review 123 --request-changes --body "Some problems need fixing; each is noted on its line."
```

Comments on lines go in a single review sent with `--input` (section 2), with
`"event": "REQUEST_CHANGES"` and each comment opening with its severity:

```json
{
  "commit_id": "<HEAD_SHA>",
  "event": "REQUEST_CHANGES",
  "body": "## Code Review\n\n2 issues and 1 suggestion, each on its line.",
  "comments": [
    {"path": "src/auth.py", "line": 45, "body": "🔴 **Critical:** the SQL query is built from user input as is; bind it as a parameter."},
    {"path": "src/models.py", "line": 23, "body": "⚠️ **Warning:** the password is saved unhashed."},
    {"path": "src/utils.py", "line": 8, "body": "💡 **Suggestion:** the same logic already lives in core/utils.py:34."}
  ]
}
```

### Step 8: A summary comment as well

Beside the comments on lines, leave one comment on the PR itself, so its author sees the whole
review in one place. It follows `references/review-output-template.md`: write it to a file with
`write_file`, then `gh pr comment 123 --body-file <path>`. For example:

```markdown
## Code Review Summary

**Verdict: Changes Requested** (2 issues, 1 suggestion)

### 🔴 Critical
- **src/auth.py:45** — user input reaches the SQL query unbound

### ⚠️ Warnings
- **src/models.py:23** — the password is stored in clear text

### 💡 Suggestions
- **src/utils.py:8** — logic repeated from elsewhere; keep one copy

### ✅ Looks Good
- A clean API
- The middleware layer handles errors well

---
*Reviewed by Atlas*
```

### Step 9: Clean up

```bash
git checkout main
git branch -D <pr-branch>
```

### Which verdict

- **Approve**: nothing Critical and nothing at Warning level; at most small suggestions
- **Request Changes**: at least one Critical or Warning finding that has to be fixed before the
  merge
- **Comment**: remarks and suggestions with nothing blocking; also when you are not sure, or the
  PR is a draft
