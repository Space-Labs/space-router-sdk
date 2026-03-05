"""Routing service for selecting nodes and tracking outcomes."""

import logging
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class ProxyNode:
    """Data class for a proxy node."""

    node_id: str
    endpoint_url: str
    health_score: float


class RoutingService:
    """Selects optimal nodes for routing traffic.

    Selection priority:
    1. Online residential nodes from the DB, weighted by health_score.
    2. ProxyJet fallback (if configured).
    3. None → caller returns 503 to the client.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        settings: Settings,
        db=None,
    ) -> None:
        self._client = http_client
        self._settings = settings
        self._db = db

        # Local cache of nodes for SQLite implementation
        self._nodes_cache: Dict[str, ProxyNode] = {}
        self._node_health: Dict[str, float] = {}

    async def select_node(self) -> Optional[ProxyNode]:
        """Select the best available node for routing traffic."""
        if self._settings.USE_SQLITE:
            return await self._select_node_sqlite()

        # Non-SQLite path (Supabase) – fall back to ProxyJet directly for now
        return self._get_fallback_node()

    async def _select_node_sqlite(self) -> Optional[ProxyNode]:
        """Select a node using SQLite data.

        Queries the DB for online nodes, picks one weighted by health_score,
        and falls back to ProxyJet when none are available.
        """
        # Try to get online nodes from the database
        if self._db is not None:
            try:
                rows = await self._db.select(
                    "nodes",
                    params={"status": "online"},
                )
                if rows:
                    # Refresh the in-memory cache from DB
                    self._nodes_cache.clear()
                    for row in rows:
                        node = ProxyNode(
                            node_id=row["id"],
                            endpoint_url=row["endpoint_url"],
                            health_score=row.get("health_score", 1.0),
                        )
                        self._nodes_cache[node.node_id] = node
            except Exception:
                logger.exception("Failed to query nodes from SQLite")

        # Pick from cached online nodes using weighted random by health_score
        if self._nodes_cache:
            nodes = list(self._nodes_cache.values())
            weights = [n.health_score for n in nodes]
            selected = random.choices(nodes, weights=weights, k=1)[0]
            return selected

        # No residential nodes available – fall back to ProxyJet
        return self._get_fallback_node()

    def _get_fallback_node(self) -> Optional[ProxyNode]:
        """Get a ProxyJet fallback node when no residential nodes are available."""
        if not self._settings.PROXYJET_HOST:
            return None

        auth = None
        if self._settings.PROXYJET_USERNAME and self._settings.PROXYJET_PASSWORD:
            # Username is used as-is from config (e.g. includes session/region params)
            auth = f"{self._settings.PROXYJET_USERNAME}:{self._settings.PROXYJET_PASSWORD}"

        endpoint_url = self._proxyjet_endpoint_url(auth)
        return ProxyNode(
            node_id="proxyjet-fallback",
            endpoint_url=endpoint_url,
            health_score=1.0,
        )

    def _proxyjet_endpoint_url(self, auth: Optional[str]) -> str:
        """Build the ProxyJet endpoint URL with auth if provided."""
        if auth:
            return f"http://{auth}@{self._settings.PROXYJET_HOST}:{self._settings.PROXYJET_PORT}"
        return f"http://{self._settings.PROXYJET_HOST}:{self._settings.PROXYJET_PORT}"

    async def report_outcome(
        self, node_id: str, success: bool, latency_ms: int, bytes_transferred: int
    ) -> None:
        """Record the outcome of a routing decision to track node performance."""
        if self._settings.USE_SQLITE:
            await self._report_outcome_sqlite(node_id, success, latency_ms, bytes_transferred)
            return

    async def _report_outcome_sqlite(
        self, node_id: str, success: bool, latency_ms: int, bytes_transferred: int
    ) -> None:
        """Record a routing outcome using SQLite."""
        # Update the health score in our local cache
        if node_id in self._nodes_cache:
            if success:
                self._node_health[node_id] = min(1.0, self._node_health.get(node_id, 0.9) + 0.1)
            else:
                self._node_health[node_id] = max(0.1, self._node_health.get(node_id, 0.5) - 0.3)

            self._nodes_cache[node_id].health_score = self._node_health[node_id]

        # Persist outcome to DB for analytics
        if self._db is not None:
            try:
                await self._db.insert(
                    "route_outcomes",
                    {
                        "node_id": node_id,
                        "success": 1 if success else 0,
                        "latency_ms": latency_ms,
                        "bytes_transferred": bytes_transferred,
                    },
                    return_rows=False,
                )
            except Exception:
                logger.exception("Failed to persist route outcome")
