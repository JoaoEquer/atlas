"""The rules of talking to more than one machine, with no I/O (docs/ARCHITECTURE.md, Several
machines): who is a tailnet peer, what a home's always-on file holds, the connection code, what
`tailscale status` says of this machine, and the service each system runs.

The service definitions, one server per home:

- **Windows**: a task at the user's logon, `LeastPrivilege` (no elevation), 30 seconds after it,
  with no time limit (the default stops a task after 72 hours), started again every minute when it
  fails, and on battery too. It is written as XML, never built from `schtasks`'s options, whose
  text Windows translates.
- **macOS**: a `launchd` agent with `RunAtLoad` and `KeepAlive`.
- **Linux**: a `systemd --user` unit with `Restart=always`, after the network.
"""

import base64
import binascii
import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

from atlas.instances.errors import CodeError, InstancesError

#: Tailscale's addresses: the carrier-grade NAT range for IPv4 and its own IPv6 prefix. Only a peer
#: from these comes in on the tailnet's socket.
TAILNET_NETWORKS = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),
)
#: What every connection code starts with; the digit is its version.
CODE_PREFIX = "atlas1."
#: How long after the logon the Windows task starts.
LOGON_DELAY = "PT30S"
#: How often the Windows task looks again: a server that died starts within this. Task
#: Scheduler's RestartOnFailure covers a start that failed, never a process that ended later (a
#: server killed on an Azure VM on 21/10/2026 stayed down until the next logon).
WATCH_INTERVAL = "PT1M"


class Transport(StrEnum):
    """How the app reaches a machine: its tailnet, an SSH tunnel, or a relay both dial out to
    (step 20)."""

    TAILSCALE = "tailscale"
    SSH = "ssh"
    RELAY = "relay"


def is_tailnet(address: str) -> bool:
    """Whether an address is a Tailscale one."""
    try:
        ip = ipaddress.ip_address(address.strip().strip("[]").split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip in network for network in TAILNET_NETWORKS)


@dataclass(frozen=True, slots=True)
class AlwaysOn:
    """A home's always-on file: the port its service listens on, fixed once so the codes given stay
    valid, and whether it is open to the user's machines."""

    port: int
    open: bool = False


def parse_always_on(text: str) -> AlwaysOn:
    """The always-on file's content; raises `InstancesError` when it is not one."""
    try:
        data: object = json.loads(text)
    except json.JSONDecodeError as error:
        raise InstancesError(f"always_on.json is not JSON: {error}") from error
    if not isinstance(data, dict):
        raise InstancesError("always_on.json does not hold an object.")
    fields = cast("dict[str, object]", data)
    port, is_open = fields.get("port"), fields.get("open", False)
    if not _is_port(port) or not isinstance(is_open, bool):
        raise InstancesError(f"always_on.json holds what Atlas never wrote there: {fields!r}")
    return AlwaysOn(cast("int", port), is_open)


def dump_always_on(state: AlwaysOn) -> str:
    return json.dumps({"port": state.port, "open": state.open}, indent=2) + "\n"


@dataclass(frozen=True, slots=True)
class RelayAddress:
    """Where a home waits at a relay (step 20): the relay's `wss://` address, the home's room,
    and the key of the tunnel, base64url."""

    url: str
    room: str
    key: str


@dataclass(frozen=True, slots=True)
class ConnectionCode:
    """What another machine's app needs to open a home here: the home's name, the port its server
    listens on, its token, and the machine's tailnet name or address when the home is open (an
    `ssh` machine needs no more than the port, reached through the tunnel)."""

    home: str
    port: int
    token: str
    tailnet: str | None = None
    #: The relay the home waits at (step 20): its address, the home's room, and the key only the
    #: home and its apps hold.
    relay: RelayAddress | None = None

    @property
    def transports(self) -> tuple[Transport, ...]:
        """The transports this code can be used over."""
        found = (Transport.TAILSCALE, Transport.SSH) if self.tailnet else (Transport.SSH,)
        return (*found, Transport.RELAY) if self.relay else found


def encode_code(code: ConnectionCode) -> str:
    """The code as one string: the prefix and the base64url of its JSON, unpadded."""
    data: dict[str, Any] = {"home": code.home, "port": code.port, "token": code.token}
    if code.tailnet:
        data["tailnet"] = code.tailnet
    if code.relay:
        data["relay"] = {"url": code.relay.url, "room": code.relay.room, "key": code.relay.key}
    raw = json.dumps(data, separators=(",", ":")).encode()
    return CODE_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_code(text: str) -> ConnectionCode:
    """A code read back; raises `CodeError` when the text is not one. Never says the text, which
    may hold a token."""
    text = text.strip()
    if not text.startswith(CODE_PREFIX):
        if re.match(r"^atlas\d+\.", text):
            raise CodeError("This code comes from a newer Atlas; update this one first.")
        raise CodeError("This is not an Atlas connection code; copy it again, whole.")
    body = text.removeprefix(CODE_PREFIX)
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        data: object = json.loads(raw)
    except (binascii.Error, ValueError) as error:
        raise CodeError("This code is cut or damaged; copy it again, whole.") from error
    if not isinstance(data, dict):
        raise CodeError("This code is cut or damaged; copy it again, whole.")
    fields = cast("dict[str, object]", data)
    home, port, token = fields.get("home"), fields.get("port"), fields.get("token")
    tailnet = fields.get("tailnet")
    if not (
        isinstance(home, str)
        and home
        and _is_port(port)
        and isinstance(token, str)
        and len(token) >= 16
        and (tailnet is None or (isinstance(tailnet, str) and _is_host(tailnet)))
    ):
        raise CodeError("This code is cut or damaged; copy it again, whole.")
    return ConnectionCode(home, cast("int", port), token, tailnet, _relay(fields.get("relay")))


def _relay(value: object) -> RelayAddress | None:
    """A code's relay, or `None`; raises `CodeError` when it is there but damaged."""
    if value is None:
        return None
    fields = cast("dict[str, object]", value) if isinstance(value, dict) else {}
    url, room, key = fields.get("url"), fields.get("room"), fields.get("key")
    if not (
        isinstance(url, str)
        and url.startswith(("wss://", "ws://"))
        and isinstance(room, str)
        and room
        and isinstance(key, str)
        and len(key) == 43
    ):
        raise CodeError("This code is cut or damaged; copy it again, whole.")
    return RelayAddress(url, room, key)


@dataclass(frozen=True, slots=True)
class Tailnet:
    """This machine on its tailnet: its IPv4 address and its MagicDNS name."""

    address: str
    name: str | None


def parse_tailscale_status(text: str) -> Tailnet:
    """This machine from `tailscale status --json`; raises `InstancesError` saying why the tailnet
    cannot be used (logged out, stopped)."""
    try:
        data: object = json.loads(text)
    except json.JSONDecodeError as error:
        raise InstancesError(f"`tailscale status` said what Atlas cannot read: {error}") from error
    if not isinstance(data, dict):
        raise InstancesError("`tailscale status` said what Atlas cannot read.")
    status = cast("dict[str, object]", data)
    state = status.get("BackendState")
    if state != "Running":
        raise InstancesError(
            f"Tailscale is not connected on this machine (it says {state!r}); open Tailscale and "
            "sign in."
        )
    me = status.get("Self")
    addresses: list[str] = []
    name: str | None = None
    if isinstance(me, dict):
        self_fields = cast("dict[str, object]", me)
        listed = self_fields.get("TailscaleIPs")
        if isinstance(listed, list):
            addresses = [a for a in cast("list[object]", listed) if isinstance(a, str)]
        dns = self_fields.get("DNSName")
        if isinstance(dns, str) and dns.strip("."):
            name = dns.strip(".").lower()
    v4 = [a for a in addresses if ":" not in a and is_tailnet(a)]
    if not v4:
        raise InstancesError("Tailscale is connected but gave this machine no IPv4 address.")
    return Tailnet(v4[0], name)


@dataclass(frozen=True, slots=True)
class ServiceCommand:
    """What a home's service runs: the program, its arguments, the folder it starts in, and the
    PATH it gets (systemd and launchd start with a bare one)."""

    program: str
    args: tuple[str, ...]
    folder: str
    path: str


def service_label(home_name: str, home_root: str) -> str:
    """The service's name: the home's, and a hash of its folder, so two Atlas folders with a home
    of the same name never share one."""
    digest = hashlib.sha256(home_root.encode()).hexdigest()[:8]
    return f"atlas-{home_name}-{digest}"


def systemd_unit(command: ServiceCommand, description: str) -> str:
    """The `systemd --user` unit, cut to what one server needs."""
    exec_start = " ".join(_systemd_quote(part) for part in (command.program, *command.args))
    return (
        "[Unit]\n"
        f"Description={description}\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "StartLimitIntervalSec=0\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={exec_start}\n"
        f"WorkingDirectory={_systemd_quote(command.folder)}\n"
        f"Environment={_systemd_quote('PATH=' + command.path)}\n"
        "Restart=always\n"
        "RestartSec=5\n"
        "KillMode=mixed\n"
        "TimeoutStopSec=20\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def launchd_plist(label: str, command: ServiceCommand) -> str:
    """The `launchd` agent: started at login and kept alive."""
    arguments = "".join(
        f"    <string>{_xml(part)}</string>\n" for part in (command.program, *command.args)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        f"  <key>Label</key>\n  <string>{_xml(label)}</string>\n"
        f"  <key>ProgramArguments</key>\n  <array>\n{arguments}  </array>\n"
        f"  <key>WorkingDirectory</key>\n  <string>{_xml(command.folder)}</string>\n"
        "  <key>EnvironmentVariables</key>\n  <dict>\n"
        f"    <key>PATH</key>\n    <string>{_xml(command.path)}</string>\n  </dict>\n"
        "  <key>RunAtLoad</key>\n  <true/>\n"
        "  <key>KeepAlive</key>\n  <true/>\n"
        "  <key>StandardOutPath</key>\n  <string>/dev/null</string>\n"
        "  <key>StandardErrorPath</key>\n  <string>/dev/null</string>\n"
        "</dict>\n"
        "</plist>\n"
    )


def task_xml(user: str, command: ServiceCommand, description: str, start: str) -> str:
    """The Scheduled Task, as Task Scheduler's own XML (version 1.2, Windows 7 and later).

    It starts at the user's logon, and a time trigger from `start` (local time, as
    `2026-10-21T22:00:00`) fires every `WATCH_INTERVAL` after: `IgnoreNew` drops the firing while
    the server runs, and starts it again once it ended. A repetition on the logon trigger would
    only count from the next logon."""
    arguments = windows_command_line(command.args)
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        f"  <RegistrationInfo><Description>{_xml(description)}</Description></RegistrationInfo>\n"
        "  <Triggers>\n"
        "    <LogonTrigger>\n"
        "      <Enabled>true</Enabled>\n"
        f"      <UserId>{_xml(user)}</UserId>\n"
        f"      <Delay>{LOGON_DELAY}</Delay>\n"
        "    </LogonTrigger>\n"
        "    <TimeTrigger>\n"
        f"      <Repetition><Interval>{WATCH_INTERVAL}</Interval></Repetition>\n"
        f"      <StartBoundary>{_xml(start)}</StartBoundary>\n"
        "      <Enabled>true</Enabled>\n"
        "    </TimeTrigger>\n"
        "  </Triggers>\n"
        "  <Principals>\n"
        '    <Principal id="Author">\n'
        f"      <UserId>{_xml(user)}</UserId>\n"
        "      <LogonType>InteractiveToken</LogonType>\n"
        "      <RunLevel>LeastPrivilege</RunLevel>\n"
        "    </Principal>\n"
        "  </Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <AllowHardTerminate>true</AllowHardTerminate>\n"
        "    <StartWhenAvailable>true</StartWhenAvailable>\n"
        "    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>\n"
        "    <IdleSettings>\n"
        "      <StopOnIdleEnd>false</StopOnIdleEnd>\n"
        "      <RestartOnIdle>false</RestartOnIdle>\n"
        "    </IdleSettings>\n"
        "    <AllowStartOnDemand>true</AllowStartOnDemand>\n"
        "    <Enabled>true</Enabled>\n"
        "    <Hidden>false</Hidden>\n"
        "    <RunOnlyIfIdle>false</RunOnlyIfIdle>\n"
        "    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n"
        "    <Priority>7</Priority>\n"
        "    <RestartOnFailure>\n"
        "      <Interval>PT1M</Interval>\n"
        "      <Count>999</Count>\n"
        "    </RestartOnFailure>\n"
        "  </Settings>\n"
        '  <Actions Context="Author">\n'
        "    <Exec>\n"
        f"      <Command>{_xml(command.program)}</Command>\n"
        f"      <Arguments>{_xml(arguments)}</Arguments>\n"
        f"      <WorkingDirectory>{_xml(command.folder)}</WorkingDirectory>\n"
        "    </Exec>\n"
        "  </Actions>\n"
        "</Task>\n"
    )


def windows_command_line(args: tuple[str, ...]) -> str:
    """Arguments joined as Windows' C runtime splits them back (`subprocess.list2cmdline`'s rules):
    quoted when they hold a space or a tab, backslashes doubled only before a quote."""
    parts: list[str] = []
    for arg in args:
        if arg and not re.search(r'[\s"]', arg):
            parts.append(arg)
            continue
        out: list[str] = ['"']
        backslashes = 0
        for char in arg:
            if char == "\\":
                backslashes += 1
            elif char == '"':
                out.append("\\" * (backslashes * 2 + 1) + '"')
                backslashes = 0
            else:
                out.append("\\" * backslashes + char)
                backslashes = 0
        out.append("\\" * (backslashes * 2) + '"')
        parts.append("".join(out))
    return " ".join(parts)


def _systemd_quote(text: str) -> str:
    """One word of a unit's line: `%` and `$` doubled, quoted when it holds a space."""
    text = text.replace("%", "%%").replace("$", "$$")
    if re.search(r'[\s"\\]', text):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def _xml(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _is_port(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value < 65536


def _is_host(text: str) -> bool:
    """A tailnet name or address, nothing a URL could be bent with."""
    return bool(re.fullmatch(r"[A-Za-z0-9.:-]{1,253}", text))
