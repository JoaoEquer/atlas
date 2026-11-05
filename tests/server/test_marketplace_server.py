"""The marketplace through the server (step 16s): the app lists it, opens a page, installs a skill
that brings its MCP server along, stores the server's secret and removes the skill, and every
client hears what changed. GitHub is the fake one, over real HTTP.
"""

import json
from pathlib import Path

import pytest

from atlas.__main__ import SCHEMAS
from atlas.home import (
    Database,
    Home,
    MarketplaceSettings,
    ModelSettings,
    Settings,
    WorkspaceSettings,
    migrate,
)
from atlas.models import CallLedger
from atlas.secrets import Vault
from atlas.server import NOT_FOUND
from atlas.server.protocol import INVALID_PARAMS
from fake_github import GIT, FakeGitHub
from fake_model import FakeModelServer
from marketplace_support import MARKETPLACE, Repository, entry, marketplace, skill_text
from model_support import Connect
from server_support import AtlasServer

pytestmark = pytest.mark.skipif(GIT is None, reason="the fake GitHub serves git repositories")

#: Two secrets, so storing one leaves the server waiting for the other: no server process starts.
DOCS = {
    "command": "npx",
    "args": ["-y", "docs-mcp@1.2.3"],
    "env": {"KEY": "${DOCS_KEY}", "REGION": "${DOCS_REGION}"},
}


def test_the_app_installs_from_the_marketplace_and_every_client_hears_it(
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    work: Path,
    tmp_path: Path,
) -> None:
    database = Database(home.database_file)
    migrate(database, SCHEMAS)
    chosen = ModelSettings(provider="openrouter")
    models = connect(fake, "openrouter", settings=chosen, ledger=CallLedger(database))
    with FakeGitHub() as github:
        github.registry = [
            {
                "server": {
                    "name": "com.example/mcp",
                    "description": "Example's own server.",
                    "version": "1.0.0",
                    "remotes": [{"type": "streamable-http", "url": "https://mcp.example.com"}],
                },
                "_meta": {
                    "io.modelcontextprotocol.registry/official": {
                        "status": "active",
                        "updatedAt": "2026-11-01T10:00:00Z",
                    }
                },
            }
        ]
        tools = Repository(tmp_path, "someone/tools", github)
        sha = tools.write(
            {"pdf/SKILL.md": skill_text("pdf", requires={"mcp": ["docs"]}, body="Read PDFs.")}
        ).commit()
        marketplace(
            tmp_path,
            github,
            {
                "pdf": entry("pdf", "skill", repo=tools, sha=sha, path="pdf"),
                "docs": {
                    **entry("docs", "mcp"),
                    "server": DOCS,
                    "secrets": [{"name": "DOCS_KEY"}, {"name": "DOCS_REGION"}],
                },
            },
        )
        settings = Settings(
            home=home.root,
            model=chosen,
            workspace=WorkspaceSettings(work),
            marketplace=MarketplaceSettings(
                repository=MARKETPLACE,
                archive_url=github.archive_url,
                raw_url=github.raw_url,
                registry_url=github.registry_url,
                skills_url=github.skills_url,
                github_api_url=github.github_api_url,
            ),
        )
        with (
            AtlasServer(home, settings, models, vault) as server,
            server.client() as app,
            server.client() as other,
        ):
            listing = app.result("marketplace.list", {"refresh": True})
            items = {item["name"]: item for item in listing["items"]}
            assert listing["name"] == "Test marketplace"
            assert listing["read_at"] is not None
            assert items["pdf"]["state"] == "available"
            assert items["docs"]["kind"] == "mcp"
            assert items["claude-code"]["shipped"] is True
            assert items["claude-code"]["tier"] == "official"
            # The MCP Registry, read directly (step 24), searched with what the user typed.
            outside = items["mcp-registry:com.example/mcp"]
            assert (outside["source"], outside["updated"], outside["installs"]) == (
                "mcp-registry",
                "2026-11-01",
                None,
            )
            found = app.result("marketplace.list", {"query": "nothing like it"})
            assert not [i for i in found["items"] if i["source"] != "atlas"]
            assert found["source_problems"] == []

            page = app.result("marketplace.page", {"name": "pdf"})
            assert page["readme"] == "Read PDFs."
            assert page["needs"] == [{"kind": "server", "name": "docs", "state": "brought"}]

            done = app.result("marketplace.install", {"name": "pdf"})
            assert done["changed"] == ["docs", "pdf"]
            assert done["item"]["state"] == "installed"
            assert other.event("marketplace.changed")["payload"] == {"name": "pdf"}
            assert (home.skills_folder / "pdf" / "SKILL.md").is_file()
            servers = json.loads(home.mcp_file.read_text("utf-8"))["mcpServers"]
            assert servers["docs"] == DOCS

            stored = app.result(
                "marketplace.secret", {"name": "docs", "secret": "DOCS_KEY", "value": "k-123"}
            )
            assert [(s["name"], s["stored"]) for s in stored["secrets"]] == [
                ("DOCS_KEY", True),
                ("DOCS_REGION", False),
            ]
            assert vault.get("DOCS_KEY") == "k-123"
            assert "k-123" not in json.dumps(stored)
            refused = app.call(
                "marketplace.secret", {"name": "docs", "secret": "OTHER", "value": "x"}
            )
            assert refused["error"]["code"] == INVALID_PARAMS
            missing = app.call("marketplace.install", {"name": "nowhere"})
            assert missing["error"]["code"] == NOT_FOUND

            removed = app.result("marketplace.remove", {"name": "pdf"})
            assert removed["state"] == "available"
            assert other.event("marketplace.changed")["payload"]["name"] in ("docs", "pdf")
            assert not (home.skills_folder / "pdf").exists()
