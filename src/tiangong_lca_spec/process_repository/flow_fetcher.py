"""Helpers for retrieving flow + flow property + unit group records via MCP."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from tiangong_lca_spec.core.exceptions import SpecCodingError
from tiangong_lca_spec.core.logging import get_logger

from .repository import ProcessRepositoryClient

LOGGER = get_logger(__name__)


@dataclass(slots=True)
class FlowBundle:
    """Container holding a flow, its flow property, and unit group records."""

    flow_uuid: str | None
    flow: Mapping[str, Any] | None
    flow_property_uuid: str | None
    flow_property: Mapping[str, Any] | None
    unit_group_uuid: str | None
    unit_group: Mapping[str, Any] | None


class FlowBundleFetcher:
    """Fetches flow/flow property/unit group records with simple caching."""

    def __init__(self, repository: ProcessRepositoryClient) -> None:
        self._repository = repository
        self._flow_cache: dict[str, Mapping[str, Any]] = {}
        self._flow_property_cache: dict[str, Mapping[str, Any]] = {}
        self._unit_group_cache: dict[str, Mapping[str, Any]] = {}
        self._flow_property_supported = True
        self._unit_group_supported = True

    # ------------------------------------------------------------------ public API

    def fetch_bundle(self, flow_reference: Mapping[str, Any] | str | None) -> FlowBundle:
        flow_uuid = _extract_ref_uuid(flow_reference)
        flow_doc = self._get_flow(flow_uuid) if flow_uuid else None
        flow_property_uuid = self._extract_flow_property_uuid(flow_doc)
        flow_property_doc = self._get_flow_property(flow_property_uuid) if flow_property_uuid else None
        unit_group_uuid = self._extract_unit_group_uuid(flow_property_doc)
        unit_group_doc = self._get_unit_group(unit_group_uuid) if unit_group_uuid else None

        return FlowBundle(
            flow_uuid=flow_uuid,
            flow=flow_doc,
            flow_property_uuid=flow_property_uuid,
            flow_property=flow_property_doc,
            unit_group_uuid=unit_group_uuid,
            unit_group=unit_group_doc,
        )

    def persist_bundle(self, bundle: FlowBundle, export_root: Path | str) -> None:
        """Write the bundle components to exports/{flows,flowproperties,unitgroups}."""

        root = Path(export_root)
        if bundle.flow_uuid and bundle.flow:
            _write_json(root / "flows" / f"{bundle.flow_uuid}.json", bundle.flow)
        if bundle.flow_property_uuid and bundle.flow_property:
            _write_json(root / "flowproperties" / f"{bundle.flow_property_uuid}.json", bundle.flow_property)
        if bundle.unit_group_uuid and bundle.unit_group:
            _write_json(root / "unitgroups" / f"{bundle.unit_group_uuid}.json", bundle.unit_group)

    # ------------------------------------------------------------------ caching getters

    def _get_flow(self, flow_uuid: str | None) -> Mapping[str, Any] | None:
        if not flow_uuid:
            return None
        if flow_uuid not in self._flow_cache:
            record = self._repository.fetch_record("flows", flow_uuid)
            document = _extract_document(record) if record else None
            if document:
                self._flow_cache[flow_uuid] = document
        return self._flow_cache.get(flow_uuid)

    def _get_flow_property(self, flow_property_uuid: str | None) -> Mapping[str, Any] | None:
        if not flow_property_uuid:
            return None
        if not self._flow_property_supported:
            return None
        if flow_property_uuid not in self._flow_property_cache:
            try:
                record = self._repository.fetch_record("flowproperties", flow_property_uuid)
            except SpecCodingError as exc:
                self._flow_property_supported = False
                LOGGER.warning(
                    "flow_bundle.flow_property_fetch_unsupported",
                    uuid=flow_property_uuid,
                    error=str(exc),
                )
                return None
            document = _extract_document(record) if record else None
            if document:
                self._flow_property_cache[flow_property_uuid] = document
        return self._flow_property_cache.get(flow_property_uuid)

    def _get_unit_group(self, unit_group_uuid: str | None) -> Mapping[str, Any] | None:
        if not unit_group_uuid:
            return None
        if not self._unit_group_supported:
            return None
        if unit_group_uuid not in self._unit_group_cache:
            try:
                record = self._repository.fetch_record("unitgroups", unit_group_uuid)
            except SpecCodingError as exc:
                self._unit_group_supported = False
                LOGGER.warning(
                    "flow_bundle.unit_group_fetch_unsupported",
                    uuid=unit_group_uuid,
                    error=str(exc),
                )
                return None
            document = _extract_document(record) if record else None
            if document:
                self._unit_group_cache[unit_group_uuid] = document
        return self._unit_group_cache.get(unit_group_uuid)

    # ------------------------------------------------------------------ extraction helpers

    @staticmethod
    def _extract_flow_property_uuid(flow_doc: Mapping[str, Any] | None) -> str | None:
        if not flow_doc:
            return None
        document = _unwrap_container(flow_doc, ("flowDataSet",))
        chains = [
            ("flowInformation", "quantitativeReference", "referenceToFlowPropertyDataSet"),
            ("flowInformation", "generalInformation", "referenceToFlowPropertyDataSet"),
            ("flowInformation", "flowProperties", "flowProperty", "referenceToFlowPropertyDataSet"),
            ("flowInformation", "flowPropertyList", "flowProperty", "referenceToFlowPropertyDataSet"),
            ("flowProperties", "flowProperty", "referenceToFlowPropertyDataSet"),
        ]
        for chain in chains:
            uuid = _extract_nested_ref(document, *chain)
            if uuid:
                return uuid
        return _extract_ref_uuid(document.get("referenceToFlowPropertyDataSet"))

    @staticmethod
    def _extract_unit_group_uuid(flow_property_doc: Mapping[str, Any] | None) -> str | None:
        if not flow_property_doc:
            return None
        document = _unwrap_container(flow_property_doc, ("flowPropertyDataSet",))
        chains = [
            ("flowPropertyInformation", "dataSetInformation", "referenceToUnitGroup"),
            ("flowPropertyInformation", "quantitativeReference", "referenceToUnitGroup"),
        ]
        for chain in chains:
            uuid = _extract_nested_ref(document, *chain)
            if uuid:
                return uuid
        return _extract_ref_uuid(document.get("referenceToUnitGroup"))


# --------------------------------------------------------------------------- helpers

def _extract_ref_uuid(reference: Mapping[str, Any] | str | None) -> str | None:
    if isinstance(reference, str):
        return reference.strip() or None
    if isinstance(reference, Mapping):
        for key in ("@refObjectId", "uuid", "id", "common:UUID"):
            value = reference.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _extract_nested_ref(document: Mapping[str, Any], *keys: str) -> str | None:
    def _walk(node: Any, idx: int) -> str | None:
        if idx == len(keys):
            return _extract_ref_uuid(node)
        key = keys[idx]
        if isinstance(node, Mapping):
            return _walk(node.get(key), idx + 1)
        if isinstance(node, list):
            for item in node:
                result = _walk(item, idx)
                if result:
                    return result
            return None
        return None

    return _walk(document, 0)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    LOGGER.info("flow_bundle.persist", path=str(path))


def _extract_document(record: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if not record:
        return None
    payload = record.get("json_ordered") or record.get("json")
    if payload is None:
        return None
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return None
    if isinstance(payload, Mapping):
        return payload
    return None


def _unwrap_container(document: Mapping[str, Any], keys: tuple[str, ...]) -> Mapping[str, Any]:
    if not isinstance(document, Mapping):
        return document
    for key in keys:
        child = document.get(key)
        if isinstance(child, Mapping):
            return child
    return document
