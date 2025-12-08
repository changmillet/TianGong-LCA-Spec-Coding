"""Public service layer for flow search."""

from __future__ import annotations

from functools import lru_cache
from typing import Iterable

from tiangong_lca_spec.core.config import Settings, get_settings
from tiangong_lca_spec.core.logging import get_logger
from tiangong_lca_spec.core.models import FlowCandidate, FlowQuery, UnmatchedFlow

from .client import FlowSearchClient
from .validators import hydrate_candidate, passes_similarity

LOGGER = get_logger(__name__)


class FlowSearchService:
    """High-level facade responsible for flow lookup and validation."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: FlowSearchClient | None = None,
        server_name: str | None = None,
        tool_name: str | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client or FlowSearchClient(
            self._settings,
            server_name=server_name,
            tool_name=tool_name,
        )

    def lookup(self, query: FlowQuery) -> tuple[list[FlowCandidate], list[UnmatchedFlow]]:
        LOGGER.info("flow_search.lookup", exchange=query.exchange_name)
        primary_query = FlowQuery(
            exchange_name=query.exchange_name,
            description=query.description,
        )
        raw_candidates = self._client.search(primary_query)
        matches, filtered_out = self._normalize_candidates(query, raw_candidates)
        if not matches:
            if query.exchange_name:
                LOGGER.info(
                    "flow_search.retry_name_only",
                    exchange=query.exchange_name,
                )
                name_only_query = FlowQuery(exchange_name=query.exchange_name)
                fallback_raw = self._client.search(name_only_query)
                fallback_matches, fallback_filtered = self._normalize_candidates(query, fallback_raw)
                if fallback_matches:
                    matches.extend(fallback_matches)
                if fallback_filtered:
                    filtered_out.extend(fallback_filtered)
        if matches:
            return matches, filtered_out
        unmatched = UnmatchedFlow(
            base_name=query.exchange_name,
            general_comment=query.description,
        )
        return [], filtered_out + [unmatched]

    def _normalize_candidates(self, query: FlowQuery, payload: Iterable[dict]) -> tuple[list[FlowCandidate], list[UnmatchedFlow]]:
        items = [item for item in (payload or []) if isinstance(item, dict)]
        candidates = [hydrate_candidate(item) for item in items]
        if candidates:
            return [candidates[0]], []
        return [], []

    def close(self) -> None:
        self._client.close()


@lru_cache(maxsize=512)
def _cached_search(query: FlowQuery) -> tuple[tuple[FlowCandidate, ...], tuple[UnmatchedFlow, ...]]:
    service = FlowSearchService()
    try:
        matches, unmatched = service.lookup(query)
    finally:
        service.close()
    return tuple(matches), tuple(unmatched)


def search_flows(query: FlowQuery) -> tuple[list[FlowCandidate], list[UnmatchedFlow]]:
    """Cached flow search, ready for pipeline usage."""
    matches, unmatched = _cached_search(query)
    return list(matches), list(unmatched)
