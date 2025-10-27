"""Helpers for resolving reference metadata from the remote repository."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from tiangong_lca_spec.core.logging import get_logger

from .repository import ProcessRepositoryClient

LOGGER = get_logger(__name__)


@dataclass(slots=True)
class ReferenceMetadata:
    ref_type: str
    ref_id: str
    version: str
    uri: str
    description: str
    language: str = "en"

    def to_global_reference(self) -> dict[str, Any]:
        return {
            "@type": self.ref_type,
            "@refObjectId": self.ref_id,
            "@version": self.version or "00.00.000",
            "@uri": self.uri,
            "common:shortDescription": {
                "@xml:lang": self.language,
                "#text": self.description,
            },
        }


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
        description = self._extract_description(payload) or ref_id
        language = "en"
        uri = self._build_uri(table, ref_id)
        resolved_type = self._normalise_type(ref_type, payload) or (ref_type or "Source data set")
        return ReferenceMetadata(
            ref_type=resolved_type,
            ref_id=ref_id,
            version=version,
            uri=uri,
            description=description,
            language=language,
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

    def _extract_description(self, payload: Mapping[str, Any]) -> str | None:
        for key in ("sourceDataSet", "contactDataSet", "processDataSet", "flowDataSet"):
            section = payload.get(key)
            if isinstance(section, Mapping):
                extractor = getattr(self, f"_extract_{key}_description", None)
                if extractor:
                    result = extractor(section)
                    if result:
                        return result
        return self._extract_common_text(payload)

    def _extract_sourceDataSet_description(self, section: Mapping[str, Any]) -> str | None:
        info = section.get("sourceInformation", {}).get("dataSetInformation", {})
        return self._extract_multilang(info.get("common:shortName")) or info.get("sourceCitation")

    def _extract_contactDataSet_description(self, section: Mapping[str, Any]) -> str | None:
        info = section.get("contactInformation", {}).get("dataSetInformation", {})
        return self._extract_multilang(info.get("common:shortName")) or self._extract_multilang(
            info.get("common:name")
        )

    def _extract_flowDataSet_description(self, section: Mapping[str, Any]) -> str | None:
        info = section.get("flowInformation", {}).get("dataSetInformation", {})
        return self._extract_multilang(info.get("name")) or self._extract_multilang(
            info.get("common:shortName")
        )

    def _extract_processDataSet_description(self, section: Mapping[str, Any]) -> str | None:
        info = section.get("processInformation", {}).get("dataSetInformation", {})
        name = info.get("name")
        if isinstance(name, Mapping):
            return self._extract_multilang(name.get("baseName"))
        return self._extract_multilang(info.get("common:shortName"))

    def _extract_multilang(self, value: Any) -> str | None:
        if isinstance(value, list):
            for item in value:
                if isinstance(item, Mapping) and item.get("@xml:lang") == "en":
                    text = item.get("#text")
                    if text:
                        return text
            for item in value:
                text = self._extract_multilang(item)
                if text:
                    return text
        if isinstance(value, Mapping):
            lang = value.get("@xml:lang")
            text = value.get("#text")
            if text:
                if not lang or lang == "en":
                    return text
                return text
        if isinstance(value, str):
            return value
        return None

    def _extract_common_text(self, payload: Mapping[str, Any]) -> str | None:
        if isinstance(payload, Mapping):
            if "common:shortDescription" in payload:
                return self._extract_multilang(payload["common:shortDescription"])
            for item in payload.values():
                result = self._extract_common_text(item) if isinstance(item, Mapping) else None
                if result:
                    return result
                if isinstance(item, list):
                    for sub in item:
                        result = self._extract_common_text(sub)
                        if result:
                            return result
        return None


__all__ = ["ReferenceMetadata", "ReferenceMetadataResolver"]
