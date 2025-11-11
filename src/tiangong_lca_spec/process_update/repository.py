"""Abstractions for retrieving process JSON datasets via MCP."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from tiangong_lca_spec.core.exceptions import SpecCodingError
from tiangong_lca_spec.core.mcp_client import MCPToolClient


class ProcessRepositoryClient:
    """Thin wrapper around MCP tooling for process dataset retrieval."""

    def __init__(
        self,
        mcp_client: MCPToolClient,
        service_name: str,
        *,
        list_tool_name: str,
        list_table: str = "processes",
        list_limit: int | None = 5000,
        list_extra_filters: Mapping[str, Any] | None = None,
    ) -> None:
        self._mcp = mcp_client
        self._service = service_name
        self._list_tool = list_tool_name
        self._list_table = list_table
        self._list_limit = list_limit
        self._list_extra_filters = dict(list_extra_filters) if list_extra_filters else {}

    def list_json_ids(self, user_id: str) -> list[str]:
        """Return all JSON identifiers belonging to the supplied user."""
        if self._list_tool == "Database_CRUD_Tool":
            filters: dict[str, Any] = dict(self._list_extra_filters)
            filters["user_id"] = user_id
            arguments: dict[str, Any] = {
                "operation": "select",
                "table": self._list_table,
                "filters": filters,
            }
            if self._list_limit and self._list_limit > 0:
                arguments["limit"] = self._list_limit
            payload = self._mcp.invoke_json_tool(
                self._service,
                self._list_tool,
                arguments,
            )
        else:
            payload = self._mcp.invoke_json_tool(
                self._service,
                self._list_tool,
                {"user_id": user_id},
            )
        ids = self._normalise_ids(payload)
        if not ids:
            raise SpecCodingError(
                f"No JSON identifiers returned by tool '{self._list_tool}' for user '{user_id}'"
            )
        return ids

    def fetch_record(
        self,
        table: str,
        record_id: str,
        *,
        preferred_user_id: str | None = None,
    ) -> Mapping[str, Any] | None:
        """Fetch a generic record from the MCP database using Database CRUD."""
        record_limit = 20 if table == "processes" else 1
        payload = self._mcp.invoke_json_tool(
            self._service,
            "Database_CRUD_Tool",
            {
                "operation": "select",
                "table": table,
                "filters": {"id": record_id},
                "limit": record_limit,
            },
        )
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if isinstance(data, list) and data:
            record = self._select_preferred_record(data, preferred_user_id=preferred_user_id)
            if isinstance(record, Mapping):
                return record
        return None

    def detect_current_user_id(self) -> str | None:
        """Infer the authenticated user's id by fetching a writable personal record."""
        payload = self._mcp.invoke_json_tool(
            self._service,
            "Database_CRUD_Tool",
            {
                "operation": "select",
                "table": self._list_table,
                "filters": {"state_code": 0, "team_id": None},
                "fields": ["user_id", "team_id"],
                "limit": 10,
            },
        )
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(data, list):
            return None
        for row in data:
            if not isinstance(row, Mapping):
                continue
            team_id = row.get("team_id")
            if isinstance(team_id, str) and team_id.strip():
                continue
            user_id = row.get("user_id")
            if isinstance(user_id, str) and user_id.strip():
                return user_id.strip()
        return None

    @staticmethod
    def _normalise_ids(payload: Any) -> list[str]:
        if payload is None:
            return []
        if isinstance(payload, list):
            return ProcessRepositoryClient._extract_ids_from_sequence(payload)
        if isinstance(payload, Mapping):
            for candidate_key in ("json_ids", "ids", "items", "data", "results"):
                value = payload.get(candidate_key)
                if value is None:
                    continue
                return ProcessRepositoryClient._extract_ids_from_sequence(value)
        return []

    @staticmethod
    def _extract_ids_from_sequence(sequence: Any) -> list[str]:
        ids: list[str] = []
        if isinstance(sequence, Mapping):
            sequence = sequence.values()
        if isinstance(sequence, Iterable):
            for item in sequence:
                if isinstance(item, str):
                    ids.append(item.strip())
                elif isinstance(item, Mapping):
                    for key in ("id", "json_id", "uuid", "jsonId"):
                        value = item.get(key)
                        if isinstance(value, str):
                            ids.append(value.strip())
                            break
        return [item for item in ids if item]

    @staticmethod
    def _select_preferred_record(
        rows: Sequence[Mapping[str, Any]],
        *,
        preferred_user_id: str | None = None,
    ) -> Mapping[str, Any] | None:
        preferred: Mapping[str, Any] | None = None
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            if preferred is None:
                preferred = row
                continue
            if ProcessRepositoryClient._record_has_priority(
                row,
                preferred,
                preferred_user_id=preferred_user_id,
            ):
                preferred = row
        return preferred

    @staticmethod
    def _record_has_priority(
        candidate: Mapping[str, Any],
        current: Mapping[str, Any],
        *,
        preferred_user_id: str | None = None,
    ) -> bool:
        if preferred_user_id:
            candidate_matches = ProcessRepositoryClient._matches_user(
                candidate.get("user_id"),
                preferred_user_id,
            )
            current_matches = ProcessRepositoryClient._matches_user(
                current.get("user_id"),
                preferred_user_id,
            )
            if candidate_matches != current_matches:
                return candidate_matches

        candidate_priority = 1 if candidate.get("state_code") == 0 else 0
        current_priority = 1 if current.get("state_code") == 0 else 0
        if candidate_priority != current_priority:
            return candidate_priority > current_priority

        candidate_version = ProcessRepositoryClient._parse_version(candidate.get("version"))
        current_version = ProcessRepositoryClient._parse_version(current.get("version"))
        if candidate_version and current_version and candidate_version != current_version:
            return candidate_version > current_version
        if candidate_version and not current_version:
            return True
        if current_version and not candidate_version:
            return False

        return False

    @staticmethod
    def _parse_version(value: Any) -> tuple[int, ...] | None:
        if not isinstance(value, str):
            return None
        parts = value.split(".")
        numbers: list[int] = []
        for part in parts:
            part = part.strip()
            if not part:
                return None
            try:
                numbers.append(int(part))
            except ValueError:
                return None
        return tuple(numbers)

    @staticmethod
    def _matches_user(value: Any, expected: str) -> bool:
        if not isinstance(value, str) or not value:
            return False
        return value.strip() == expected.strip()

__all__ = ["ProcessRepositoryClient"]
