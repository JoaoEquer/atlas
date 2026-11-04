# Commit messages in the Conventional Commits style

The subject line: `type(scope): description`

## The types

| Type | For | Example |
|------|------------|---------|
| `feat` | Something new the code can do | `feat(auth): add OAuth2 login flow` |
| `fix` | A bug, fixed | `fix(api): handle null response from /users endpoint` |
| `refactor` | Code moved or reshaped, behavior unchanged | `refactor(db): extract query builder into separate module` |
| `docs` | Documentation and nothing else | `docs: update API usage examples in README` |
| `test` | Tests added or changed | `test(auth): add integration tests for token refresh` |
| `ci` | The CI/CD setup | `ci: add Python 3.12 to test matrix` |
| `chore` | Upkeep: dependencies, tooling | `chore: upgrade pytest to 8.x` |
| `perf` | Faster or lighter | `perf(search): add index on users.email column` |
| `style` | Formatting only: whitespace, semicolons | `style: run black formatter on src/` |
| `build` | The build system or outside dependencies | `build: switch from setuptools to hatch` |
| `revert` | Undoing an earlier commit | `revert: revert "feat(auth): add OAuth2 login flow"` |

## The scope (you may leave it out)

A short name for the part of the code the commit touches: `auth`, `api`, `db`, `ui`, `cli`, and so on.

## A change that breaks callers

Mark it with `!` right after the type, or with a `BREAKING CHANGE:` footer:

```
feat(api)!: sign API requests with bearer tokens

BREAKING CHANGE: the API-key header is gone; every endpoint wants a Bearer token.
How to move over: https://docs.example.com/migrate-auth
```

## A body over several lines

Lines no longer than 72 characters. Several changes read best as bullets:

```
feat(auth): let users sign in with a JWT

- Register and log-in endpoints that validate their input
- A User model whose passwords are hashed with argon2
- Middleware in front of the private routes
- A refresh endpoint that rotates the token

Closes #42
```

## Pointing at issues

These go in the body or the footer:

```
Closes #42          ← the merge closes the issue
Fixes #42           ← does the same
Refs #42            ← links to it, leaves it open
Co-authored-by: Name <email>
```

## Picking the type

- The commit adds something new → `feat`
- Something was broken and now works → `fix`
- The code is laid out differently but does the same → `refactor`
- Only tests changed → `test`
- Only docs changed → `docs`
- The CI/CD pipelines changed → `ci`
- Dependencies or tooling were updated → `chore`
- Something got faster → `perf`
