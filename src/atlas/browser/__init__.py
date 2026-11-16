"""The browser: Atlas's own Chrome or the app's, and the code the model writes to drive it."""

from atlas.browser.endpoint import AppHost, BrowserEndpoint
from atlas.browser.errors import BrowserError, HostError
from atlas.browser.model import (
    APP_GONE,
    METADATA_REFUSED,
    TOOL_NAME,
    BrowserMode,
    HostTab,
    HostTabs,
    metadata_address,
    metadata_host,
)
from atlas.browser.service import Browser, BrowserResult
from atlas.browser.store import ENGINE, find_chrome

__all__ = [
    "APP_GONE",
    "ENGINE",
    "METADATA_REFUSED",
    "TOOL_NAME",
    "AppHost",
    "Browser",
    "BrowserEndpoint",
    "BrowserError",
    "BrowserMode",
    "BrowserResult",
    "HostError",
    "HostTab",
    "HostTabs",
    "find_chrome",
    "metadata_address",
    "metadata_host",
]
