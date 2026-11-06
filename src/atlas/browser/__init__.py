"""The browser: Atlas's own Chrome, and the code the model writes to drive it."""

from atlas.browser.errors import BrowserError
from atlas.browser.model import (
    METADATA_REFUSED,
    TOOL_NAME,
    BrowserMode,
    metadata_address,
    metadata_host,
)
from atlas.browser.service import Browser, BrowserResult
from atlas.browser.store import ENGINE, find_chrome

__all__ = [
    "ENGINE",
    "METADATA_REFUSED",
    "TOOL_NAME",
    "Browser",
    "BrowserError",
    "BrowserMode",
    "BrowserResult",
    "find_chrome",
    "metadata_address",
    "metadata_host",
]
