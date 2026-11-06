"""The server token a container keeps (docs/ARCHITECTURE.md, Container).

A server Atlas runs off loopback, which today means in a container, keeps its token in the vault,
in the data volume, made on the first boot and never written over: whoever reaches the port needs
it, and `atlas server token` hands it to the user (Atlas V3's `atlas instancia token`). A token
given in `ATLAS_SERVER_TOKEN` still wins, and a loopback server given none makes a fresh one that
nobody knows, as before.
"""

import secrets

from atlas.instances import RelayRoom, new_key, new_room
from atlas.secrets import Vault

#: The token's name in the vault, the same as the variable that gives it.
KEPT_TOKEN = "ATLAS_SERVER_TOKEN"  # noqa: S105 - a secret's name, not a secret


def kept_token(vault: Vault) -> str:
    """The token kept in the vault, made the first time it is asked for."""
    return vault.keep(KEPT_TOKEN, secrets.token_urlsafe(32))


#: The home's room at the relay and the key of its tunnel (step 20), in the vault: the room is
#: where it waits, the key what only it and its apps hold.
RELAY_ROOM = "ATLAS_RELAY_ROOM"
RELAY_KEY = "ATLAS_RELAY_KEY"


def relay_room(vault: Vault, url: str) -> RelayRoom:
    """The home's room at the relay at `url`, made the first time it is asked for, so the codes
    given stay valid."""
    return RelayRoom(url, vault.keep(RELAY_ROOM, new_room()), vault.keep(RELAY_KEY, new_key()))
