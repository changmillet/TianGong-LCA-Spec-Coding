"""Abstractions for retrieving process JSON datasets via MCP."""

from __future__ import annotations

import json
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
        fetch_tool_name: str,
        list_table: str = "processes",
        list_limit: int | None = 5000,
        list_extra_filters: Mapping[str, Any] | None = None,
    ) -> None:
        self._mcp = mcp_client
        self._service = service_name
        self._list_tool = list_tool_name
        self._fetch_tool = fetch_tool_name
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

    def fetch_process_json(self, json_id: str) -> dict[str, Any]:
        """Fetch a single process JSON document."""
        payload = self._mcp.invoke_json_tool(
            self._service,
            self._fetch_tool,
            {"json_id": json_id},
        )
        document = self._normalise_document(payload)
        if not isinstance(document, dict):
            raise SpecCodingError(
                f"Unexpected payload format for JSON id '{json_id}': {type(document)!r}"
            )
        return document

    def fetch_record(self, table: str, record_id: str) -> Mapping[str, Any] | None:
        """Fetch a generic record from the MCP database using Database CRUD."""
        payload = self._mcp.invoke_json_tool(
            self._service,
            "Database_CRUD_Tool",
            {
                "operation": "select",
                "table": table,
                "filters": {"id": record_id},
                "limit": 1,
            },
        )
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if isinstance(data, list) and data:
            record = data[0]
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
    def _normalise_document(payload: Any) -> Any:
        if payload is None:
            raise SpecCodingError("MCP tool returned an empty payload")
        if isinstance(payload, str):
            try:
                return json.loads(payload)
            except json.JSONDecodeError as exc:  # pragma: no cover - defensive
                raise SpecCodingError("Failed to parse process JSON payload") from exc
        if isinstance(payload, Sequence) and not isinstance(payload, (bytes, bytearray)):
            # Some tools might wrap the JSON in a single-item list.
            if len(payload) == 1:
                return ProcessRepositoryClient._normalise_document(payload[0])
        if isinstance(payload, Mapping):
            for candidate_key in ("json", "process", "data", "document"):
                value = payload.get(candidate_key)
                if value is not None:
                    return ProcessRepositoryClient._normalise_document(value)
        return payload


__all__ = ["ProcessRepositoryClient"]
