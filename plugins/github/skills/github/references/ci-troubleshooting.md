# When CI fails

What a failed run usually looks like in its log, where to look, and what tends to fix it.

## Getting the log

```bash
# Just the steps that failed
gh run view <RUN_ID> --log-failed

# Every line, for the whole run or for a single job
gh run view <RUN_ID> --log
gh run view --job <JOB_ID> --log
```

For a long log, redirect it to a file in a temporary folder (`$(mktemp -d)`
in bash, `$env:TEMP` in PowerShell) and search it with `find_files`.

## What each kind of failure looks like

### A test failed

**In the log:**
```
FAILED tests/test_foo.py::test_bar - AssertionError
E       assert 42 == 43
ERROR tests/test_foo.py - ModuleNotFoundError
```

**Finding the cause:**
1. Take the test file and the line from the traceback
2. Open the failing test with `read_file`
3. Decide which side is wrong: the code's logic, or an assertion the change made outdated
4. A `ModuleNotFoundError` almost always means CI lacks a dependency

**What usually fixes it:**
- The assertion, brought up to the behavior that is now expected
- The dependency, added to requirements.txt / pyproject.toml
- A flaky test made stable: a retry, a mock for the outside service, the race removed

---

### Lint or formatting

**In the log:**
```
src/auth.py:45:1: E302 expected 2 blank lines, got 1
src/models.py:12:80: E501 line too long (95 > 88 characters)
error: would reformat src/utils.py
```

**Finding the cause:**
1. Go to each file:line the log names
2. Note which tool raised it (flake8, ruff, black, isort, mypy)

**What usually fixes it:**
- The formatter, run on your machine: `black .`, `isort .`, `ruff check --fix .`
- An edit to the file for the one rule it breaks
- With `edit_file`, the file's own indentation, matched exactly

---

### Types (mypy / pyright)

**In the log:**
```
src/api.py:23: error: Argument 1 to "process" has incompatible type "str"; expected "int"
src/models.py:45: error: Missing return statement
```

**Finding the cause:**
1. Open the file at the line given
2. Compare the function's signature with what the caller hands it

**What usually fixes it:**
- A cast or a conversion
- A corrected signature
- Only when nothing else works: a `# type: ignore` comment that says why

---

### Build or compilation

**In the log:**
```
ModuleNotFoundError: No module named 'some_package'
ERROR: Could not find a version that satisfies the requirement foo==1.2.3
npm ERR! Could not resolve dependency
```

**Finding the cause:**
1. Look in requirements.txt / package.json for the dependency that is missing or does not fit
2. Compare the Python/Node version CI runs with yours

**What usually fixes it:**
- The missing dependency, added to the requirements file
- A version pin that fits
- A refreshed lockfile (`pip freeze`, `npm install`)

---

### Permissions or authentication

**In the log:**
```
fatal: could not read Username for 'https://github.com': No such device or address
Error: Resource not accessible by integration
403 Forbidden
```

**Finding the cause:**
1. See whether the workflow needs permissions it was not given (token scopes)
2. See whether its secrets are set up (what the built-in `GITHUB_TOKEN` of the workflow may do, or the repository's own secrets)

**What usually fixes it:**
- A `permissions:` block in the workflow's YAML
- Secrets confirmed to exist: `gh secret list`, or the repository's settings
- On a PR from a fork, some secrets are withheld on purpose

---

### Timeouts

**In the log:**
```
Error: The operation was canceled.
The job running on runner ... has exceeded the maximum execution time
```

**Finding the cause:**
1. Find the step that ran out of time
2. Look for a loop that never ends, a process that hangs, or a slow network call

**What usually fixes it:**
- A limit on that step: `timeout-minutes: 10`
- The slowness itself, fixed
- The work spread over jobs that run in parallel

---

### Docker or containers

**In the log:**
```
docker: Error response from daemon
failed to solve: ... not found
COPY failed: file not found in build context
```

**Finding the cause:**
1. Find the Dockerfile step that fails
2. Make sure the files it names are in the repository

**What usually fixes it:**
- The path in the COPY/ADD command, corrected
- A newer tag for the base image
- `.dockerignore` corrected for the missing file: an exclusion added for it there, or the file taken off the list

---

## Choosing the fix

```
The run failed
├── A test
│   ├── The assertion no longer matches → fix the logic, or update the test
│   └── An import or module error → add the dependency
├── Lint → run the formatter, fix the style
├── Types → fix the types
├── The build
│   ├── A dependency is missing → add it to the requirements
│   └── Versions conflict → update the pins
├── Permissions → change the workflow's permissions (the user has to)
└── A timeout → look into the slowness (the user may have to weigh in)
```

## Pushing the fix and watching again

```bash
git add <fixed_files>
git commit -m "fix: make CI pass again"
git push

# and follow the new run
gh pr checks --watch
```
