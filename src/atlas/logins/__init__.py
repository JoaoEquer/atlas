"""The logins of the programs Atlas reaches GitHub and Google Workspace through (`gh`, `gws`):
read, and GitHub's started in the browser."""

from atlas.logins.errors import LoginsError
from atlas.logins.model import (
    PROGRAMS,
    DeviceCode,
    ProgramLogin,
    Service,
    parse_device_code,
    parse_gh_status,
    parse_gws_status,
)
from atlas.logins.service import ProgramLogins

__all__ = [
    "PROGRAMS",
    "DeviceCode",
    "LoginsError",
    "ProgramLogin",
    "ProgramLogins",
    "Service",
    "parse_device_code",
    "parse_gh_status",
    "parse_gws_status",
]
