"""What the programs Atlas reaches GitHub and Google Workspace through say of their login.

`gh` and `gws` keep their own login on the machine (step 14b); Atlas only reads what their
`auth status` prints, and starts `gh`'s login in the browser for the first run's Connections.
"""

import json
import re
from dataclasses import dataclass
from typing import Literal, cast

#: The services, each with the program that reaches it.
Service = Literal["github", "google"]

PROGRAMS: dict[Service, str] = {"github": "gh", "google": "gws"}

#: What reads a login, without asking for anything.
GH_STATUS = ("auth", "status", "--hostname", "github.com", "--json", "hosts")
GWS_STATUS = ("auth", "status")

#: `gh`'s login in the browser: away from a terminal it prints a one-time code and the page to
#: type it on, then waits until the browser said yes. Git over HTTPS, as the github skill says.
GH_LOGIN = ("auth", "login", "--hostname", "github.com", "--git-protocol", "https", "--web")

#: How long a status may take, and how long a login waits for the browser.
STATUS_SECONDS = 20
LOGIN_SECONDS = 15 * 60
#: How long `gh` may take to print its code.
CODE_SECONDS = 30


@dataclass(frozen=True, slots=True)
class ProgramLogin:
    """A service, whether its program is on this machine, and who it is logged in as."""

    service: Service
    program: str
    installed: bool
    logged_in: bool
    #: Who: the GitHub login, or Google's e-mail when `gws` says it; empty when it does not.
    account: str = ""


@dataclass(frozen=True, slots=True)
class DeviceCode:
    """What the user types in the browser, and where."""

    code: str
    url: str


def _object(text: str) -> dict[str, object] | None:
    try:
        found: object = json.loads(text)
    except ValueError:
        return None
    return cast("dict[str, object]", found) if isinstance(found, dict) else None


def parse_gh_status(text: str) -> str | None:
    """The account `gh auth status --json hosts` says is active on github.com, or `None`."""
    status = _object(text)
    hosts = status.get("hosts") if status is not None else None
    if not isinstance(hosts, dict):
        return None
    accounts = cast("dict[str, object]", hosts).get("github.com")
    if not isinstance(accounts, list):
        return None
    for each in cast("list[object]", accounts):
        if not isinstance(each, dict):
            continue
        account = cast("dict[str, object]", each)
        if account.get("state") == "success" and account.get("active") is True:
            login = account.get("login")
            return login if isinstance(login, str) else ""
    return None


def parse_gws_status(text: str) -> tuple[bool, str]:
    """Whether `gws auth status` holds a token Google takes, and the e-mail when it says one.

    It prints a line or two before its JSON. A token Google refused (`token_valid` false, with
    "expired or revoked") is no login: the user signs in again.
    """
    start = text.find("{")
    status = _object(text[start:]) if start >= 0 else None
    if status is None or status.get("token_valid") is not True:
        return False, ""
    for key in ("user", "email", "account"):
        value = status.get(key)
        if isinstance(value, str) and value:
            return True, value
    return True, ""


_CODE = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b")
_URL = re.compile(r"https://\S+")


def parse_device_code(text: str) -> DeviceCode | None:
    """The one-time code and its page in what `gh auth login --web` printed so far."""
    code = _CODE.search(text)
    url = _URL.search(text)
    if code is None or url is None:
        return None
    return DeviceCode(code.group(1), url.group(0).rstrip(".,"))
