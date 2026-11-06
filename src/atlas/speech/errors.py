"""What can go wrong writing down a recording."""


class SpeechError(Exception):
    """A recording could not be written down, or the model could not be had; the message says
    why and what to do."""
