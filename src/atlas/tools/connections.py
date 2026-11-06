"""Offering the user a connection in the conversation (05/11/2026, asked by the user): GitHub,
Gmail and Calendar are where much of a person's work is, and someone who never opened a
terminal does not know they can connect them. So Atlas offers one when what the user asked
needs it, or once early on, the way an assistant mentions what it can reach.

The tool only offers: the app draws its call as a card with the plugin's declaration, where the
user turns the plugin on (the consent, theirs) and logs in. Nothing is turned on or reached by
the model. It is given only in the user's own conversations, while a connection is not ready.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from atlas.agent import Tool

NAME = "offer_connection"


@dataclass(frozen=True, slots=True)
class Connection:
    """One of the connections Atlas offers, and whether it is ready."""

    #: The plugin that brings it (`github`, `google`).
    name: str
    #: What the user calls it (`GitHub`, `Gmail and Calendar`).
    label: str
    #: What it lets Atlas do, for the model to judge when it helps.
    use: str
    #: On, consented to and logged in: nothing to offer.
    ready: bool


def offer_connection(
    connections: Callable[[], Sequence[Connection]], conversations: int
) -> Tool | None:
    """The tool, while a connection is not ready; `None` when every one is. `conversations` is how
    many the user has, so the model knows whether they are just starting."""
    offered = [found for found in connections() if not found.ready]
    if not offered:
        return None
    listed = "; ".join(f"{found.name}: {found.label}, {found.use}" for found in offered)
    early = (
        " The user is just starting with Atlas: in this conversation, at a natural moment and in a "
        "sentence, offer the one that would help them most."
        if conversations <= 3
        else ""
    )

    async def run(arguments: dict[str, Any]) -> str:
        name = arguments.get("connection")
        found = next((each for each in connections() if each.name == name), None)
        if found is None:
            return f"[Atlas: connection must be one of: {', '.join(c.name for c in offered)}.]"
        if found.ready:
            return f"[Atlas: {found.label} is connected already; use it.]"
        return (
            f"[Atlas: The user sees a card to connect {found.label} here, with what it may do. "
            "Turning it on and logging in are theirs, in the app. Go on once they say it is done, "
            "or without it if they decline; do not offer it again in this conversation.]"
        )

    return Tool(
        name=NAME,
        description=(
            "Offer the user one of their connections that is not set up yet, as a card they click "
            f"to connect: {listed}. Offer one when what they ask needs it (their email, their "
            "calendar, their repositories), saying in a sentence what it would let you do; never "
            "more than once per connection in a conversation, and never to push." + early
        ),
        parameters={
            "type": "object",
            "properties": {
                "connection": {"type": "string", "enum": [found.name for found in offered]},
            },
            "required": ["connection"],
            "additionalProperties": False,
        },
        run=run,
    )
