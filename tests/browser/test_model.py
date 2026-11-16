"""The browser's pure rules: the tool's words, the cloud metadata floor, and how Chrome and the
engine are started."""

import re

import pytest

from atlas.browser.model import (
    DEFAULT_TIMEOUT,
    MAX_TIMEOUT,
    MIN_TIMEOUT,
    BrowserMode,
    HostTab,
    HostTabs,
    app_session_name,
    browser_version,
    cdp_refused,
    chrome_arguments,
    conversation_tabs,
    description,
    devtools_endpoint,
    engine_environment,
    host_of,
    metadata_address,
    metadata_host,
    navigation_url,
    printed_images,
    session_name,
    target_info,
    timeout_of,
    url_refused,
    urls_in,
)


def test_the_description_follows_what_the_model_sees() -> None:
    seeing, blind = description(True), description(False)
    assert "attached to your context automatically" in seeing
    assert "cannot view images" in blind
    for text in (seeing, blind):
        assert "HELPERS (pre-imported)" in text
        assert "never guess or type credentials" in text
        assert "'clean'" in text
        assert "'headless'" in text
        assert "list_tabs() also shows the pages the user opened" in text
        assert "cdp('Emulation.setDeviceMetricsOverride', width=1280" in text
        assert "the browser beside the conversation in the app" in text
        assert "web_search and web_fetch are faster" in text
        assert "only when the user asks for no window" in text


def test_the_timeout_stays_within_bounds() -> None:
    assert timeout_of(None) == DEFAULT_TIMEOUT
    assert timeout_of(True) == DEFAULT_TIMEOUT
    assert timeout_of("soon") == DEFAULT_TIMEOUT
    assert timeout_of(1) == MIN_TIMEOUT
    assert timeout_of(99_999) == MAX_TIMEOUT
    assert timeout_of("60") == 60
    assert timeout_of(42.9) == 42


def test_cloud_metadata_is_found_by_name_and_by_number() -> None:
    assert urls_in("goto_url('http://169.254.169.254/latest')\nx = 'https://a.com/b'") == [
        "http://169.254.169.254/latest",
        "https://a.com/b",
    ]
    assert host_of("http://Metadata.Google.Internal./x") == "metadata.google.internal"
    assert host_of("http://[::1") == ""
    assert metadata_host("metadata.google.internal")
    assert metadata_host("169.254.169.254")
    assert metadata_host("169.254.1.1")  # the whole link-local range
    assert metadata_host("100.100.100.200")
    assert metadata_address("::ffff:169.254.169.254")
    assert metadata_address("fd00:ec2::254")
    assert metadata_address("fe80::1%eth0") is False
    # A private address is a local browser's to reach.
    assert not metadata_host("192.168.0.1")
    assert not metadata_host("localhost")
    assert not metadata_address("not an address")


def test_chrome_opens_on_its_own_profile_with_a_port_it_chooses() -> None:
    windowed = chrome_arguments("chrome", "/p", headless=False)
    assert windowed[:3] == ["chrome", "--user-data-dir=/p", "--remote-debugging-port=0"]
    assert "--headless=new" not in windowed
    assert "--headless=new" in chrome_arguments("chrome", "/p", headless=True)


def test_the_devtools_endpoint_is_read_from_chromes_file() -> None:
    assert devtools_endpoint("54001\n/devtools/browser/abc\n") == (
        "ws://127.0.0.1:54001/devtools/browser/abc"
    )
    assert devtools_endpoint("") is None
    assert devtools_endpoint("port\n/x") is None
    assert devtools_endpoint("54001\n") is None


def test_each_conversation_and_mode_has_its_own_session() -> None:
    first = session_name("c-1", BrowserMode.PROFILE)
    assert first.startswith("atlas-profile-")
    assert len(first) <= 64
    assert first != session_name("c-2", BrowserMode.PROFILE)
    assert first != session_name("c-1", BrowserMode.CLEAN)


def test_the_engine_never_phones_home_nor_reaches_a_cloud_browser() -> None:
    environment = engine_environment(
        {
            "PATH": "/bin",
            "BU_CDP_URL": "http://elsewhere",
            "BU_AUTOSPAWN": "1",
            "BROWSER_USE_API_KEY": "k",
            "PYTHONPATH": "/x",
        },
        home="/h",
        workspace="/w",
        session="s",
        endpoint="ws://127.0.0.1:1/x",
    )
    assert environment["PATH"] == "/bin"
    assert environment["BU_CDP_WS"] == "ws://127.0.0.1:1/x"
    assert environment["BU_NAME"] == "s"
    assert environment["BH_TELEMETRY"] == "0"
    assert environment["BH_UPDATE_CHECK"] == "0"
    for gone in ("BU_CDP_URL", "BU_AUTOSPAWN", "BROWSER_USE_API_KEY", "PYTHONPATH"):
        assert gone not in environment


def test_printed_screenshots_are_found_last_first() -> None:
    output = "saved C:\\tmp\\a.png then /shots/b.jpeg and c.png"
    assert printed_images(output) == ["/shots/b.jpeg", "C:\\tmp\\a.png"]


def test_the_modes() -> None:
    assert BrowserMode.PROFILE.kept
    assert BrowserMode.PROFILE.windowed
    assert not BrowserMode.CLEAN.kept
    assert BrowserMode.CLEAN.windowed
    assert not BrowserMode.HEADLESS.windowed


#: The same table is in the app's `app-browser-rules.test.ts`: the two lists must not drift.
CDP_CASES = [
    ("Page.navigate", True),
    ("Page.captureScreenshot", True),
    ("Runtime.evaluate", True),
    ("DOM.getDocument", True),
    ("Input.dispatchMouseEvent", True),
    ("Network.enable", True),
    ("Accessibility.getFullAXTree", True),
    ("Emulation.setDeviceMetricsOverride", True),
    ("Emulation.clearDeviceMetricsOverride", True),
    ("Emulation.setFocusEmulationEnabled", True),
    ("Inspector.enable", True),
    ("Security.enable", True),
    ("Network.setCacheDisabled", True),
    ("Target.getTargets", False),
    ("Target.createTarget", False),
    ("Browser.close", False),
    ("Browser.getVersion", False),
    ("Page.setDownloadBehavior", False),
    ("Page.close", False),
    ("IO.read", False),
    ("Storage.getCookies", False),
    ("Network.getAllCookies", False),
    ("Network.getCookies", False),
    ("Network.setCookie", False),
    ("Network.deleteCookies", False),
    ("Network.clearBrowserCookies", False),
    ("Network.clearBrowserCache", False),
    ("DOMStorage.getDOMStorageItems", False),
    ("IndexedDB.requestDatabaseNames", False),
    ("CacheStorage.requestCacheNames", False),
    ("Security.setIgnoreCertificateErrors", False),
    ("SystemInfo.getInfo", False),
    ("Tracing.start", False),
    ("page.navigate", False),
    ("Navigate", False),
    ("", False),
]


@pytest.mark.parametrize(("method", "allowed"), CDP_CASES)
def test_a_page_session_reaches_only_the_pages_domains(method: str, allowed: bool) -> None:
    refused = cdp_refused(method)
    assert (refused is None) is allowed
    if refused is not None:
        assert method in refused


def test_the_addresses_the_engine_may_load() -> None:
    assert url_refused("https://example.com/a") is None
    assert url_refused("http://192.168.0.1/") is None
    assert url_refused("about:blank") is None
    assert url_refused("http://169.254.169.254/latest") is not None
    assert url_refused("http://metadata.google.internal/") is not None
    assert url_refused("file:///etc/passwd") is not None
    assert url_refused("chrome://settings") is not None
    assert url_refused("javascript:alert(1)") is not None
    assert url_refused("data:text/html,x") is not None
    assert url_refused("http://[::1") is not None


def test_the_address_a_command_would_load() -> None:
    assert navigation_url("Page.navigate", {"url": "https://a.example"}) == "https://a.example"
    assert navigation_url("Target.createTarget", {"url": "about:blank"}) == "about:blank"
    assert navigation_url("Page.navigate", {}) is None
    assert navigation_url("Page.navigate", {"url": 3}) is None
    assert navigation_url("Runtime.evaluate", {"url": "https://a.example"}) is None


def test_a_conversations_tabs_start_with_the_one_on_the_panel() -> None:
    tabs = HostTabs(
        (
            HostTab("t1", "https://a.example", "A", "c-1"),
            HostTab("t2", "https://b.example", "B", "c-2"),
            HostTab("t3", "https://c.example", "C", "c-1"),
            HostTab("t4", "", "D", "c-1", selected=True),
            HostTab("t5", "https://e.example", "E", ""),
        ),
        "agent/1",
        "Chrome/1",
    )
    assert [tab.id for tab in conversation_tabs(tabs, "c-1")] == ["t4", "t3", "t1"]
    assert [tab.id for tab in conversation_tabs(tabs, "c-2")] == ["t2"]
    assert conversation_tabs(tabs, "c-3") == []
    # The draft's tabs (owner "") are the draft's alone.
    assert [tab.id for tab in conversation_tabs(tabs, "")] == ["t5"]
    info = target_info(tabs.tabs[3], attached=True)
    assert info["url"] == "about:blank"
    assert info["type"] == "page"
    assert info["attached"] is True
    assert info["targetId"] == "t4"
    assert browser_version(tabs)["userAgent"] == "agent/1"
    assert browser_version(tabs)["product"] == "Chrome/1"


def test_the_app_browser_has_a_session_of_its_own() -> None:
    app = app_session_name("c-1")
    assert app.startswith("atlas-app-")
    assert app != session_name("c-1", BrowserMode.PROFILE)
    assert app != app_session_name("c-2")
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", app)
