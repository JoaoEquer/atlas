"""What can go wrong with talking to more than one machine."""


class InstancesError(Exception):
    """The home cannot be made always on or open, or a code cannot be read; the message says how
    to fix it."""


class CodeError(InstancesError):
    """A connection code that is not one, or comes from a newer Atlas."""


class TunnelError(InstancesError):
    """A connection through the relay that cannot be trusted: the other end lacks the code's key,
    or the relay changed what it carried (step 20)."""


class RelayGoneError(InstancesError):
    """A connection through the relay closed while a frame was sent."""
