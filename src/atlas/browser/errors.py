"""What can go wrong with the browser."""


class BrowserError(Exception):
    """The browser or its engine could not start or go on; the message says why."""


class HostError(BrowserError):
    """The app browser would not do what it was asked, or is gone; the message says why, in the
    words of the DevTools command's own error when it was one."""
