"""What can go wrong with a program's login."""


class LoginsError(Exception):
    """A login could not start or be read; the message says what to do."""
