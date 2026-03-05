"""Tests for the routing service (node selection and health scoring)."""

import httpx
import pytest

from app.config import Settings
from app.services.routing_service import RoutingService
from app.sqlite_db import SQLiteClient


class TestProxyjetEndpointUrl:
    def test_builds_correct_url(self):
        settings = Settings(
            USE_SQLITE=False,
            PROXYJET_HOST="ca.proxy-jet.io",
            PROXYJET_PORT=1010,
            PROXYJET_USERNAME="user",
            PROXYJET_PASSWORD="pass",
        )
        http_client = httpx.AsyncClient()
        service = RoutingService(http_client, settings)
        url = service._proxyjet_endpoint_url("user:pass")
        assert url == "http://user:pass@ca.proxy-jet.io:1010"

    def test_url_without_auth(self):
        settings = Settings(
            USE_SQLITE=False,
            PROXYJET_HOST="ca.proxy-jet.io",
            PROXYJET_PORT=1010,
        )
        http_client = httpx.AsyncClient()
        service = RoutingService(http_client, settings)
        url = service._proxyjet_endpoint_url(None)
        assert url == "http://ca.proxy-jet.io:1010"


class TestGetFallbackNode:
    def test_returns_proxyjet_node_with_credentials(self):
        settings = Settings(
            USE_SQLITE=False,
            PROXYJET_HOST="ca.proxy-jet.io",
            PROXYJET_PORT=1010,
            PROXYJET_USERNAME="user123",
            PROXYJET_PASSWORD="pass456",
        )
        http_client = httpx.AsyncClient()
        service = RoutingService(http_client, settings)
        node = service._get_fallback_node()

        assert node is not None
        assert node.node_id == "proxyjet-fallback"
        assert node.endpoint_url == "http://user123:pass456@ca.proxy-jet.io:1010"

    def test_username_used_as_is(self):
        """ProxyJet username is used as-is from config (includes session/region params)."""
        settings = Settings(
            USE_SQLITE=False,
            PROXYJET_HOST="ca.proxy-jet.io",
            PROXYJET_PORT=1010,
            PROXYJET_USERNAME="260302CJd90-static_region-US_California_Sanfrancisco-ip-725842936",
            PROXYJET_PASSWORD="NwUqzgu38X",
        )
        http_client = httpx.AsyncClient()
        service = RoutingService(http_client, settings)
        node = service._get_fallback_node()

        assert node is not None
        # Username is used exactly as configured, no suffix appended
        assert node.endpoint_url == "http://260302CJd90-static_region-US_California_Sanfrancisco-ip-725842936:NwUqzgu38X@ca.proxy-jet.io:1010"

    def test_returns_none_when_no_host(self):
        settings = Settings(
            USE_SQLITE=False,
            PROXYJET_HOST="",
        )
        http_client = httpx.AsyncClient()
        service = RoutingService(http_client, settings)
        assert service._get_fallback_node() is None

    def test_returns_node_without_auth_when_no_creds(self):
        """Host configured but no username/password → URL has no auth."""
        settings = Settings(
            USE_SQLITE=False,
            PROXYJET_HOST="ca.proxy-jet.io",
            PROXYJET_PORT=1010,
            PROXYJET_USERNAME="",
            PROXYJET_PASSWORD="",
        )
        http_client = httpx.AsyncClient()
        service = RoutingService(http_client, settings)
        node = service._get_fallback_node()

        assert node is not None
        assert node.endpoint_url == "http://ca.proxy-jet.io:1010"
        assert "@" not in node.endpoint_url

    def test_health_score_is_one(self):
        """Fallback node always has health_score 1.0."""
        settings = Settings(
            USE_SQLITE=False,
            PROXYJET_HOST="proxy-jet.io",
            PROXYJET_PORT=1010,
            PROXYJET_USERNAME="u",
            PROXYJET_PASSWORD="p",
        )
        http_client = httpx.AsyncClient()
        service = RoutingService(http_client, settings)
        node = service._get_fallback_node()
        assert node.health_score == 1.0


class TestSelectNode:
    @pytest.mark.asyncio
    async def test_selects_db_node_when_online(self, tmp_path):
        """Online nodes in the DB are returned instead of ProxyJet."""
        db_path = str(tmp_path / "test.db")
        settings = Settings(
            USE_SQLITE=True,
            SQLITE_DB_PATH=db_path,
            PROXYJET_HOST="proxy.proxyjet.io",
            PROXYJET_PORT=1010,
            PROXYJET_USERNAME="user",
            PROXYJET_PASSWORD="pass",
        )
        db = SQLiteClient(db_path)

        # Register an online node in the DB
        await db.insert("nodes", {
            "endpoint_url": "http://127.0.0.1:9090",
            "node_type": "residential",
            "status": "online",
            "health_score": 1.0,
        })

        http_client = httpx.AsyncClient()
        service = RoutingService(http_client, settings, db=db)
        result = await service.select_node()

        assert result is not None
        assert result.node_id != "proxyjet-fallback"
        assert result.endpoint_url == "http://127.0.0.1:9090"

    @pytest.mark.asyncio
    async def test_falls_back_to_proxyjet_when_no_online_nodes(self, tmp_path):
        """Empty DB → falls back to ProxyJet."""
        db_path = str(tmp_path / "test.db")
        settings = Settings(
            USE_SQLITE=True,
            SQLITE_DB_PATH=db_path,
            PROXYJET_HOST="proxy.proxyjet.io",
            PROXYJET_PORT=1010,
            PROXYJET_USERNAME="user",
            PROXYJET_PASSWORD="pass",
        )
        db = SQLiteClient(db_path)

        http_client = httpx.AsyncClient()
        service = RoutingService(http_client, settings, db=db)
        result = await service.select_node()

        assert result is not None
        assert result.node_id == "proxyjet-fallback"
        assert "proxy.proxyjet.io" in result.endpoint_url

    @pytest.mark.asyncio
    async def test_falls_back_to_proxyjet_when_only_offline_nodes(self, tmp_path):
        """All nodes offline → falls back to ProxyJet."""
        db_path = str(tmp_path / "test.db")
        settings = Settings(
            USE_SQLITE=True,
            SQLITE_DB_PATH=db_path,
            PROXYJET_HOST="proxy.proxyjet.io",
            PROXYJET_PORT=1010,
            PROXYJET_USERNAME="user",
            PROXYJET_PASSWORD="pass",
        )
        db = SQLiteClient(db_path)

        # Register an offline node
        await db.insert("nodes", {
            "endpoint_url": "http://127.0.0.1:9090",
            "node_type": "residential",
            "status": "offline",
            "health_score": 0.5,
        })

        http_client = httpx.AsyncClient()
        service = RoutingService(http_client, settings, db=db)
        result = await service.select_node()

        assert result is not None
        assert result.node_id == "proxyjet-fallback"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_nodes_and_no_proxyjet(self, tmp_path):
        """No nodes, no ProxyJet config → returns None (503)."""
        db_path = str(tmp_path / "test.db")
        settings = Settings(
            USE_SQLITE=True,
            SQLITE_DB_PATH=db_path,
            PROXYJET_HOST="",
        )
        db = SQLiteClient(db_path)

        http_client = httpx.AsyncClient()
        service = RoutingService(http_client, settings, db=db)
        result = await service.select_node()

        assert result is None

    @pytest.mark.asyncio
    async def test_fallback_to_proxyjet_when_not_sqlite(self):
        """Non-SQLite mode falls back to ProxyJet."""
        settings = Settings(
            USE_SQLITE=False,
            PROXYJET_HOST="proxy.proxyjet.io",
            PROXYJET_PORT=1010,
            PROXYJET_USERNAME="user",
            PROXYJET_PASSWORD="pass",
        )
        http_client = httpx.AsyncClient()
        service = RoutingService(http_client, settings)
        result = await service.select_node()

        assert result is not None
        assert result.node_id == "proxyjet-fallback"
        assert "proxy.proxyjet.io" in result.endpoint_url

    @pytest.mark.asyncio
    async def test_works_without_db(self, tmp_path):
        """RoutingService still works when db=None (backwards compat)."""
        settings = Settings(
            USE_SQLITE=True,
            SQLITE_DB_PATH=str(tmp_path / "test.db"),
            PROXYJET_HOST="proxy.proxyjet.io",
            PROXYJET_PORT=1010,
            PROXYJET_USERNAME="user",
            PROXYJET_PASSWORD="pass",
        )
        http_client = httpx.AsyncClient()
        service = RoutingService(http_client, settings)  # no db
        result = await service.select_node()

        # No DB → no cached nodes → falls back to ProxyJet
        assert result is not None
        assert result.node_id == "proxyjet-fallback"


class TestProxyJetFallbackPriority:
    """Verify the routing priority: DB nodes > ProxyJet > None."""

    @pytest.mark.asyncio
    async def test_db_node_preferred_over_proxyjet(self, tmp_path):
        """When online DB nodes exist, ProxyJet is never returned."""
        db_path = str(tmp_path / "test.db")
        settings = Settings(
            USE_SQLITE=True,
            SQLITE_DB_PATH=db_path,
            PROXYJET_HOST="proxy-jet.io",
            PROXYJET_PORT=1010,
            PROXYJET_USERNAME="user",
            PROXYJET_PASSWORD="pass",
        )
        db = SQLiteClient(db_path)
        await db.insert("nodes", {
            "endpoint_url": "http://192.168.1.10:9090",
            "node_type": "residential",
            "status": "online",
            "health_score": 0.5,
        })

        http_client = httpx.AsyncClient()
        service = RoutingService(http_client, settings, db=db)

        # Select many times – should always pick the residential node
        for _ in range(20):
            result = await service.select_node()
            assert result is not None
            assert result.node_id != "proxyjet-fallback"

    @pytest.mark.asyncio
    async def test_draining_nodes_not_selected(self, tmp_path):
        """Nodes with status 'draining' are not online and trigger fallback."""
        db_path = str(tmp_path / "test.db")
        settings = Settings(
            USE_SQLITE=True,
            SQLITE_DB_PATH=db_path,
            PROXYJET_HOST="proxy-jet.io",
            PROXYJET_PORT=1010,
            PROXYJET_USERNAME="user",
            PROXYJET_PASSWORD="pass",
        )
        db = SQLiteClient(db_path)
        await db.insert("nodes", {
            "endpoint_url": "http://192.168.1.10:9090",
            "node_type": "residential",
            "status": "draining",
            "health_score": 1.0,
        })

        http_client = httpx.AsyncClient()
        service = RoutingService(http_client, settings, db=db)
        result = await service.select_node()

        assert result is not None
        assert result.node_id == "proxyjet-fallback"

    @pytest.mark.asyncio
    async def test_mixed_online_and_offline_only_selects_online(self, tmp_path):
        """Mix of online and offline nodes – only online nodes are selected."""
        db_path = str(tmp_path / "test.db")
        settings = Settings(
            USE_SQLITE=True,
            SQLITE_DB_PATH=db_path,
            PROXYJET_HOST="proxy-jet.io",
            PROXYJET_PORT=1010,
            PROXYJET_USERNAME="user",
            PROXYJET_PASSWORD="pass",
        )
        db = SQLiteClient(db_path)
        await db.insert("nodes", {
            "endpoint_url": "http://10.0.0.1:9090",
            "node_type": "residential",
            "status": "offline",
            "health_score": 1.0,
        })
        await db.insert("nodes", {
            "endpoint_url": "http://10.0.0.2:9090",
            "node_type": "residential",
            "status": "online",
            "health_score": 0.8,
        })

        http_client = httpx.AsyncClient()
        service = RoutingService(http_client, settings, db=db)

        for _ in range(20):
            result = await service.select_node()
            assert result is not None
            assert result.node_id != "proxyjet-fallback"
            assert result.endpoint_url == "http://10.0.0.2:9090"


class TestReportOutcome:
    @pytest.mark.asyncio
    async def test_records_outcome_sqlite(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        settings = Settings(
            USE_SQLITE=True,
            SQLITE_DB_PATH=db_path,
            PROXYJET_HOST="proxy.proxyjet.io",
            PROXYJET_PORT=1010,
            PROXYJET_USERNAME="user",
            PROXYJET_PASSWORD="pass",
        )
        db = SQLiteClient(db_path)

        # Register a node so select_node returns it
        await db.insert("nodes", {
            "endpoint_url": "http://127.0.0.1:9090",
            "node_type": "residential",
            "status": "online",
            "health_score": 1.0,
        })

        http_client = httpx.AsyncClient()
        service = RoutingService(http_client, settings, db=db)

        node = await service.select_node()
        assert node is not None

        # Report a successful outcome – should not raise
        await service.report_outcome(node.node_id, True, 100, 1024)
