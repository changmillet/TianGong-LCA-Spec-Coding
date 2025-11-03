"""Helpers for resolving reference metadata from the remote repository."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from tiangong_lca_spec.core.logging import get_logger

from .repository import ProcessRepositoryClient

LOGGER = get_logger(__name__)


@dataclass(slots=True)
class ReferenceMetadata:
    """Structured metadata for a remote reference target."""

    ref_type: str
    ref_id: str
    version: str
    uri: str
    descriptions: list[tuple[str, str]]

    def to_global_reference(self) -> dict[str, Any]:
        """Convert metadata into the ILCD-compatible reference structure."""
        description_nodes = [
            {"@xml:lang": lang or "en", "#text": text}
            for lang, text in self.descriptions
            if text
        ]
        reference: dict[str, Any] = {
            "@type": self.ref_type,
            "@refObjectId": self.ref_id,
            "@version": self.version or "00.00.000",
            "@uri": self.uri,
        }
        if description_nodes:
            if len(description_nodes) == 1:
                reference["common:shortDescription"] = description_nodes[0]
            else:
                reference["common:shortDescription"] = description_nodes
        return reference


class ReferenceMetadataResolver:
    """Lookup helper that enriches references with metadata fetched from MCP."""

    TYPE_TABLE_MAP: Mapping[str, str] = {
        "contact data set": "contacts",
        "source data set": "sources",
        "flow data set": "flows",
        "process data set": "processes",
    }

    def __init__(self, repository: ProcessRepositoryClient) -> None:
        self._repository = repository

    def resolve(self, ref_id: str, ref_type: str | None) -> ReferenceMetadata | None:
        table = self._table_for_type(ref_type)
        if not table:
            LOGGER.debug(
                "reference_resolver.table_unknown", ref_id=ref_id, ref_type=ref_type or "unknown"
            )
            return None
        record = self._repository.fetch_record(table, ref_id)
        if not record:
            LOGGER.debug(
                "reference_resolver.record_missing",
                ref_id=ref_id,
                ref_type=ref_type or "unknown",
                table=table,
            )
            return None
        version = (record.get("version") or "00.00.000").strip()
        payload = record.get("json") or {}
        descriptions = self._extract_descriptions(payload)
        if not descriptions:
            descriptions = [("en", ref_id)]
        uri = self._build_uri(table, ref_id)
        resolved_type = self._normalise_type(ref_type, payload) or (ref_type or "Source data set")
        return ReferenceMetadata(
            ref_type=resolved_type,
            ref_id=ref_id,
            version=version,
            uri=uri,
            descriptions=descriptions,
        )

    def _table_for_type(self, ref_type: str | None) -> str | None:
        if not ref_type:
            return None
        return self.TYPE_TABLE_MAP.get(ref_type.lower())

    @staticmethod
    def _build_uri(table: str, ref_id: str) -> str:
        return f"https://tiangong.earth/datasets/{ref_id}"

    @staticmethod
    def _normalise_type(ref_type: str | None, payload: Mapping[str, Any]) -> str | None:
        if ref_type:
            return ref_type
        for key in payload.keys():
            if key.endswith("DataSet"):
                base = key.removesuffix("DataSet")
                return f"{base.capitalize()} data set"
        return None

    def _extract_descriptions(self, payload: Mapping[str, Any]) -> list[tuple[str, str]]:
        for key in ("sourceDataSet", "contactDataSet", "processDataSet", "flowDataSet"):
            section = payload.get(key)
            if isinstance(section, Mapping):
                extractor = getattr(self, f"_extract_{key}_descriptions", None)
                if extractor:
                    entries = extractor(section)
                    if entries:
                        return entries
        return self._extract_common_text_entries(payload)

    def _extract_sourceDataSet_descriptions(
        self, section: Mapping[str, Any]
    ) -> list[tuple[str, str]]:
        info = section.get("sourceInformation", {}).get("dataSetInformation", {})
        entries = self._extract_multilang_entries(info.get("common:shortName"))
        if entries:
            return entries
        return self._extract_multilang_entries(info.get("sourceCitation"))

    def _extract_contactDataSet_descriptions(
        self, section: Mapping[str, Any]
    ) -> list[tuple[str, str]]:
        info = section.get("contactInformation", {}).get("dataSetInformation", {})
        entries = self._extract_multilang_entries(info.get("common:shortName"))
        if entries:
            return entries
        return self._extract_multilang_entries(info.get("common:name"))

    def _extract_flowDataSet_descriptions(
        self, section: Mapping[str, Any]
    ) -> list[tuple[str, str]]:
        info = section.get("flowInformation", {}).get("dataSetInformation", {})
        entries = self._extract_multilang_entries(info.get("name"))
        if entries:
            return entries
        return self._extract_multilang_entries(info.get("common:shortName"))

    def _extract_processDataSet_descriptions(
        self, section: Mapping[str, Any]
    ) -> list[tuple[str, str]]:
        info = section.get("processInformation", {}).get("dataSetInformation", {})
        name = info.get("name")
        if isinstance(name, Mapping):
            entries = self._extract_multilang_entries(name.get("baseName"))
            if entries:
                return entries
        return self._extract_multilang_entries(info.get("common:shortName"))

    def _extract_multilang_entries(self, value: Any) -> list[tuple[str, str]]:
        results: list[tuple[str, str]] = []

        def collect(item: Any) -> None:
            if isinstance(item, list):
                for sub in item:
                    collect(sub)
                return
            if isinstance(item, Mapping):
                if "@xml:lang" in item and "#text" in item and isinstance(item["#text"], str):
                    lang = item.get("@xml:lang") or "en"
                    results.append((str(lang), item["#text"]))
                    return
                for sub in item.values():
                    collect(sub)
                return
            if isinstance(item, str) and item.strip():
                results.append(("en", item.strip()))

        collect(value)
        deduped: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for lang, text in results:
            key = (lang or "en", text)
            if key not in seen:
                seen.add(key)
                deduped.append(key)
        return deduped

    def _extract_common_text_entries(self, payload: Mapping[str, Any]) -> list[tuple[str, str]]:
        if not isinstance(payload, Mapping):
            return []
        direct = payload.get("common:shortDescription")
        entries = self._extract_multilang_entries(direct)
        if entries:
            return entries
        for value in payload.values():
            if isinstance(value, Mapping):
                nested = self._extract_common_text_entries(value)
                if nested:
                    return nested
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, Mapping):
                        nested = self._extract_common_text_entries(item)
                        if nested:
                            return nested
        return []


__all__ = ["ReferenceMetadata", "ReferenceMetadataResolver"]
