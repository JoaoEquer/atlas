"""Stand-ins for `gh` and `gws`, for the first run's Connections (step 18d): what their `auth
status` prints, and `gh`'s login in the browser, the browser played by a file.

Run as `fake_logins.py gh ...` or `fake_logins.py gws ...`, through the launchers `programs`
writes; `FAKE_LOGINS` names the folder their state lives in:

- `gh` is logged in once `gh-login` holds a GitHub login; `gws`, once `gws-login` holds an e-mail.
- `gh auth login --web` prints its code as the real one does away from a terminal, then waits for
  `gh-approve` (the user said yes in the browser), writes its content as the login and exits;
  `gh-refuse` makes it fail instead.
- `gh pr view <branch>` prints `pr-<branch>.json` (its `/` as `-`), or says there is none; `gh pr
  create` writes it, number 7 with the checks `checks.json` holds, and keeps its arguments in
  `pr-created.json` (step 21, A4). `gh issue list` prints `issues.json`, or none (A5).
"""

import json
import os
import sys
import time
from pathlib import Path

CODE = "AB12-CD34"


def _say(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def _gh(state: Path, args: list[str]) -> int:
    login = state / "gh-login"
    if args[:2] == ["auth", "status"]:
        if login.exists():
            account = {"state": "success", "active": True, "host": "github.com"}
            hosts = {"github.com": [{**account, "login": login.read_text("utf-8").strip()}]}
            _say(json.dumps({"hosts": hosts}))
            return 0
        _say(json.dumps({"hosts": {}}))
        return 1
    if args[:2] == ["auth", "login"] and "--web" in args:
        _say("")
        _say(f"! One-time code ({CODE}) copied to clipboard")
        _say("Open this URL to continue in your web browser: https://github.com/login/device")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if (state / "gh-approve").exists():
                login.write_text((state / "gh-approve").read_text("utf-8"), "utf-8")
                _say("Logged in")
                return 0
            if (state / "gh-refuse").exists():
                sys.stderr.write("X Authentication refused\n")
                return 1
            time.sleep(0.1)
        return 1
    if args[:2] == ["pr", "view"]:
        kept = state / f"pr-{args[2].replace('/', '-')}.json"
        if not kept.exists():
            sys.stderr.write(f'no pull requests found for branch "{args[2]}"\n')
            return 1
        _say(kept.read_text("utf-8"))
        return 0
    if args[:2] == ["pr", "create"]:
        head = args[args.index("--head") + 1]
        title = args[args.index("--title") + 1]
        checks = state / "checks.json"
        made = {
            "number": 7,
            "url": "https://github.com/example/shop/pull/7",
            "state": "OPEN",
            "title": title,
            "isDraft": "--draft" in args,
            "statusCheckRollup": json.loads(checks.read_text("utf-8")) if checks.exists() else [],
        }
        (state / f"pr-{head.replace('/', '-')}.json").write_text(json.dumps(made), "utf-8")
        (state / "pr-created.json").write_text(json.dumps(args), "utf-8")
        _say(made["url"])
        return 0
    if args[:2] == ["issue", "list"]:
        issues = state / "issues.json"
        _say(issues.read_text("utf-8") if issues.exists() else "[]")
        return 0
    sys.stderr.write(f"fake gh: {' '.join(args)} is not played\n")
    return 2


def _gws(state: Path, args: list[str]) -> int:
    if args[:2] != ["auth", "status"]:
        sys.stderr.write(f"fake gws: {' '.join(args)} is not played\n")
        return 2
    login = state / "gws-login"
    _say("Using keyring backend: keyring")
    status: dict[str, object] = {"auth_method": "oauth2", "token_valid": login.exists()}
    if login.exists():
        status["user"] = login.read_text("utf-8").strip()
    else:
        status["token_error"] = "Token has been expired or revoked."
    _say(json.dumps(status, indent=2))
    return 0


def programs(folder: Path, state: Path) -> dict[str, str]:
    """Write `gh` and `gws` launchers in `folder` and say the environment that finds them first."""
    folder.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).resolve()
    for name in ("gh", "gws"):
        if sys.platform == "win32":
            launcher = folder / f"{name}.cmd"
            launcher.write_text(f'@"{sys.executable}" "{script}" {name} %*\r\n', "utf-8")
        else:
            launcher = folder / name
            launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" {name} "$@"\n')
            launcher.chmod(0o755)
    return {**os.environ, "PATH": str(folder), "FAKE_LOGINS": str(state)}


def main() -> int:
    state = Path(os.environ["FAKE_LOGINS"])
    program, *args = sys.argv[1:]
    return _gh(state, args) if program == "gh" else _gws(state, args)


if __name__ == "__main__":
    sys.exit(main())
