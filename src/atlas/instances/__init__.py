"""Talking to more than one machine: a home always on and open to the user's machines, its
connection code, and who may come in over the tailnet or through a relay.

The relay's names load on first use: they bring the WebSocket client and `cryptography`, which the
installation's hand-off, importing this package, must not load."""

import importlib
from typing import TYPE_CHECKING

from atlas.instances.errors import CodeError, InstancesError, RelayGoneError, TunnelError
from atlas.instances.model import (
    CODE_PREFIX,
    TAILNET_NETWORKS,
    AlwaysOn,
    ConnectionCode,
    RelayAddress,
    ServiceCommand,
    Tailnet,
    Transport,
    decode_code,
    encode_code,
    is_tailnet,
    launchd_plist,
    parse_always_on,
    parse_tailscale_status,
    service_label,
    systemd_unit,
    task_xml,
    windows_command_line,
)
from atlas.instances.service import (
    START_SECONDS,
    AlwaysOnHome,
    AlwaysOnServices,
    Status,
    serve_command,
)
from atlas.instances.store import (
    LaunchAgents,
    ScheduledTasks,
    ServiceManager,
    SystemdUser,
    answers,
    read_always_on,
    service_manager,
    tailnet,
    tailscale_program,
)

if TYPE_CHECKING:
    from atlas.instances.relay import (
        PAIRED,
        RelayClient,
        RelayLink,
        RelayRoom,
        RelaySession,
        new_room,
        relay_client,
    )
    from atlas.instances.tunnel import new_key

__all__ = [
    "CODE_PREFIX",
    "PAIRED",
    "START_SECONDS",
    "TAILNET_NETWORKS",
    "AlwaysOn",
    "AlwaysOnHome",
    "AlwaysOnServices",
    "CodeError",
    "ConnectionCode",
    "InstancesError",
    "LaunchAgents",
    "RelayAddress",
    "RelayClient",
    "RelayGoneError",
    "RelayLink",
    "RelayRoom",
    "RelaySession",
    "ScheduledTasks",
    "ServiceCommand",
    "ServiceManager",
    "Status",
    "SystemdUser",
    "Tailnet",
    "Transport",
    "TunnelError",
    "answers",
    "decode_code",
    "encode_code",
    "is_tailnet",
    "launchd_plist",
    "new_key",
    "new_room",
    "parse_always_on",
    "parse_tailscale_status",
    "read_always_on",
    "relay_client",
    "serve_command",
    "service_label",
    "service_manager",
    "systemd_unit",
    "tailnet",
    "tailscale_program",
    "task_xml",
    "windows_command_line",
]


#: The relay's names, from the module each lives in, imported when first asked for.
_LAZY = {
    "PAIRED": "relay",
    "RelayClient": "relay",
    "RelayLink": "relay",
    "RelayRoom": "relay",
    "RelaySession": "relay",
    "new_room": "relay",
    "relay_client": "relay",
    "new_key": "tunnel",
}


def __getattr__(name: str) -> object:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(f"atlas.instances.{module}"), name)
