"""HTTP and WebSocket server that clients (desktop, mobile) talk to, and the client protocol."""

from atlas.server.access import (
    BAD_ORIGIN,
    BAD_PEER,
    BAD_TOKEN,
    OTHER_PROTOCOL,
    TOKEN_PREFIX,
    is_loopback,
    refusal,
)
from atlas.server.app import READY_LINE, create_app
from atlas.server.connection import Connection, Peer, PeerGoneError
from atlas.server.protocol import (
    EVENTS,
    METHODS,
    NOT_FOUND,
    PROTOCOL,
    SERVER_REQUESTS,
    ProtocolError,
    dump,
    read,
)
from atlas.server.replay import Replay
from atlas.server.service import ASK_SECONDS, Gateway
from atlas.server.tokens import KEPT_TOKEN, RELAY_KEY, RELAY_ROOM, kept_token, relay_room
from atlas.server.watchdog import EXIT_CEILING_SECONDS, POLL_SECONDS, Parent

__all__ = [
    "ASK_SECONDS",
    "BAD_ORIGIN",
    "BAD_PEER",
    "BAD_TOKEN",
    "EVENTS",
    "EXIT_CEILING_SECONDS",
    "KEPT_TOKEN",
    "METHODS",
    "NOT_FOUND",
    "OTHER_PROTOCOL",
    "POLL_SECONDS",
    "PROTOCOL",
    "READY_LINE",
    "RELAY_KEY",
    "RELAY_ROOM",
    "SERVER_REQUESTS",
    "TOKEN_PREFIX",
    "Connection",
    "Gateway",
    "Parent",
    "Peer",
    "PeerGoneError",
    "ProtocolError",
    "Replay",
    "create_app",
    "dump",
    "is_loopback",
    "kept_token",
    "read",
    "refusal",
    "relay_room",
]
