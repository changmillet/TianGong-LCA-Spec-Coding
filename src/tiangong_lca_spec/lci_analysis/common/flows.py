"""Flow / flow property / unit group registries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tiangong_lca_spec.core.logging import get_logger

LOGGER = get_logger(__name__)

_FLOW_PROPERTY_FAMILY_HINTS: tuple[tuple[str, str], ...] = (
    ("mass", "mass"),
    ("net calorific value", "energy"),
    ("lower calorific value", "energy"),
    ("gross calorific value", "energy"),
    ("calorific value", "energy"),
    ("energy", "energy"),
    ("number of items", "item"),
    ("items", "item"),
    ("volume", "volume"),
    ("area", "area"),
    ("length", "length"),
    ("distance", "length"),
    ("radioactivity", "radioactivity"),
)

@dataclass(slots=True)
class FlowMetadata:
    uuid: str
    name: str | None
    flow_type: str | None
    unit_group_ref: str | None
    unit_family: str | None = None
    classifications: tuple[str, ...] | None = None
    classification_levels: dict[str, str] | None = None
    is_elementary: bool = False


@dataclass(slots=True)
class UnitGroupMetadata:
    uuid: str
    name: str | None
    reference_unit: str | None
    unit_family: str | None


class FlowRegistry:
    """Loads flow / flowproperty / unitgroup JSON resources for quick lookup."""

    def __init__(
        self,
        flows_dir: Path | str | None = None,
        flow_properties_dir: Path | str | None = None,
        unit_groups_dir: Path | str | None = None,
    ) -> None:
        self._flows = _load_json_dir(flows_dir)
        self._flow_properties = _load_json_dir(flow_properties_dir)
        self._unit_groups = _load_json_dir(unit_groups_dir)

        self._unit_group_meta = self._build_unit_group_meta()
        self._flow_meta = self._build_flow_meta()
        LOGGER.info(
            "lci.flow_registry.loaded",
            flows=len(self._flow_meta),
            flow_properties=len(self._flow_properties),
            unit_groups=len(self._unit_group_meta),
        )

    # ------------------------------------------------------------------ public helpers

    def get_flow(self, reference: dict[str, Any] | str | None) -> FlowMetadata | None:
        uuid = _reference_uuid(reference)
        if not uuid:
            return None
        return self._flow_meta.get(uuid)

    def get_unit_group(self, ref: str | None) -> UnitGroupMetadata | None:
        if not ref:
            return None
        return self._unit_group_meta.get(ref)

    def get_flow_document(self, reference: dict[str, Any] | str | None) -> dict[str, Any] | None:
        """Return the full flow JSON document for the given reference."""

        if isinstance(reference, str):
            uuid = reference.strip()
        else:
            uuid = _reference_uuid(reference)
        if not uuid:
            return None
        document = self._flows.get(uuid)
        if document is None and isinstance(reference, dict):
            ref_uuid = reference.get("@refObjectId") or reference.get("uuid")
            if isinstance(ref_uuid, str):
                document = self._flows.get(ref_uuid.strip())
        return document

    # ------------------------------------------------------------------ internal builders

    def _build_flow_meta(self) -> dict[str, FlowMetadata]:
        meta: dict[str, FlowMetadata] = {}
        for uuid, document in self._flows.items():
            flow_doc = _unwrap_flow_document(document)
            flow_type = _extract_flow_type(flow_doc)
            name = _resolve_name(flow_doc, ("flowInformation", "dataSetInformation", "name"))
            flow_property_ref = _extract_flow_property_ref(flow_doc)
            unit_group_ref = self._resolve_flow_unit_group(flow_property_ref)
            unit_family = None
            if unit_group_ref:
                unit_group = self.get_unit_group(unit_group_ref)
                if unit_group:
                    unit_family = unit_group.unit_family
            if not unit_family:
                unit_family = _infer_unit_family_from_flow_document(flow_doc)
            classifications, classification_levels = _extract_flow_classifications(flow_doc)
            normalized_type = _normalize_flow_type(flow_type)
            meta[uuid] = FlowMetadata(
                uuid=uuid,
                name=name,
                flow_type=normalized_type,
                unit_group_ref=unit_group_ref,
                unit_family=unit_family,
                classifications=classifications,
                classification_levels=classification_levels,
                is_elementary=_is_elementary_flow_type(normalized_type),
            )
        return meta

    def _resolve_flow_unit_group(self, flow_property_ref: str | None) -> str | None:
        if not flow_property_ref:
            return None
        flow_prop_doc = self._flow_properties.get(flow_property_ref)
        if not isinstance(flow_prop_doc, dict):
            return None
        unit_group_ref = _reference_uuid(flow_prop_doc.get("referenceToUnitGroup"))
        if unit_group_ref:
            return unit_group_ref
        unit_group_ref = _reference_uuid(
            _dig(flow_prop_doc, "flowPropertyInformation", "dataSetInformation", "referenceToUnitGroup")
        )
        return unit_group_ref

    def _build_unit_group_meta(self) -> dict[str, UnitGroupMetadata]:
        meta: dict[str, UnitGroupMetadata] = {}
        for uuid, document in self._unit_groups.items():
            name = _resolve_name(document, ("unitGroupInformation", "dataSetInformation", "name"))
            reference_unit = _resolve_reference_unit(document)
            unit_family = _infer_unit_family(name, document)
            meta[uuid] = UnitGroupMetadata(
                uuid=uuid,
                name=name,
                reference_unit=reference_unit,
                unit_family=unit_family,
            )
        return meta


# --------------------------------------------------------------------------- helpers

def _unwrap_flow_document(document: dict[str, Any]) -> dict[str, Any]:
    if isinstance(document, dict):
        flow_data = document.get("flowDataSet")
        if isinstance(flow_data, dict):
            return flow_data
    return document


def _extract_flow_property_node(flow_doc: dict[str, Any]) -> dict[str, Any] | None:
    props = flow_doc.get("flowProperties")
    flow_prop: Any = None
    if isinstance(props, dict):
        flow_prop = props.get("flowProperty")
    elif isinstance(props, list) and props:
        flow_prop = props[0]
    if isinstance(flow_prop, list):
        flow_prop = flow_prop[0] if flow_prop else None
    return flow_prop if isinstance(flow_prop, dict) else None


def _extract_flow_property_ref(flow_doc: dict[str, Any] | None) -> str | None:
    if not flow_doc:
        return None
    dataset = _unwrap_flow_document(flow_doc)
    flow_prop = _extract_flow_property_node(dataset)
    if flow_prop:
        return _reference_uuid(flow_prop.get("referenceToFlowPropertyDataSet"))
    return _reference_uuid(dataset.get("referenceToFlowPropertyDataSet"))


def _infer_unit_family_from_flow_document(flow_doc: dict[str, Any]) -> str | None:
    dataset = _unwrap_flow_document(flow_doc)
    flow_prop = _extract_flow_property_node(dataset)
    if not flow_prop:
        return None
    ref_block = flow_prop.get("referenceToFlowPropertyDataSet")
    label = None
    if isinstance(ref_block, dict):
        label = _extract_short_description(ref_block.get("common:shortDescription"))
    if not label:
        label = _extract_short_description(flow_prop.get("common:shortDescription"))
    if not label:
        return None
    normalized = label.strip().lower()
    for keyword, family in _FLOW_PROPERTY_FAMILY_HINTS:
        if keyword in normalized:
            return family
    return None


def _extract_flow_classifications(flow_doc: dict[str, Any]) -> tuple[tuple[str, ...] | None, dict[str, str] | None]:
    dataset = _unwrap_flow_document(flow_doc)
    class_block = _dig(dataset, "flowInformation", "classificationInformation", "common:classification", "common:class")
    labels = _collect_class_labels(class_block)
    levels = _collect_class_levels(class_block)
    return (tuple(labels) if labels else None, levels or None)


def _collect_class_labels(node: Any) -> list[str]:
    labels: list[str] = []
    if node is None:
        return labels
    if isinstance(node, list):
        for item in node:
            labels.extend(_collect_class_labels(item))
        return labels
    if isinstance(node, dict):
        text = node.get("#text") or node.get("text")
        if isinstance(text, str) and text.strip():
            labels.append(text.strip())
        for value in node.values():
            if isinstance(value, (list, dict)):
                labels.extend(_collect_class_labels(value))
        return labels
    if isinstance(node, str) and node.strip():
        labels.append(node.strip())
    return labels


def _collect_class_levels(node: Any) -> dict[str, str]:
    levels: dict[str, str] = {}
    if node is None:
        return levels
    if isinstance(node, list):
        for item in node:
            levels.update(_collect_class_levels(item))
        return levels
    if isinstance(node, dict):
        text = node.get("#text") or node.get("text")
        if isinstance(text, str):
            text = text.strip()
        else:
            text = None
        level = node.get("@level") or node.get("level")
        if isinstance(level, str):
            key = level.strip()
            if key and text:
                levels.setdefault(key, text)
        for value in node.values():
            if isinstance(value, (list, dict)):
                levels.update(_collect_class_levels(value))
        return levels
    return levels


def _is_elementary_flow_type(flow_type: str | None) -> bool:
    if not flow_type:
        return False
    return "elementary" in flow_type.lower()


def _extract_flow_type(flow_doc: dict[str, Any]) -> str | None:
    """Return `typeOfDataSet` from the common ILCD flow paths."""

    dataset = _unwrap_flow_document(flow_doc)
    candidates = (
        ("flowInformation", "modellingAndValidation", "LCIMethodAndAllocation", "typeOfDataSet"),
        ("flowInformation", "modellingAndValidation", "LCIMethod", "typeOfDataSet"),
        ("modellingAndValidation", "LCIMethodAndAllocation", "typeOfDataSet"),
        ("modellingAndValidation", "LCIMethod", "typeOfDataSet"),
    )
    for path in candidates:
        value = _dig(dataset, *path)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None

def _load_json_dir(directory: Path | str | None) -> dict[str, dict[str, Any]]:
    if directory is None:
        return {}
    path = Path(directory)
    if not path.exists():
        return {}
    items: dict[str, dict[str, Any]] = {}
    for file in path.glob("*.json"):
        try:
            document = json.loads(file.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - defensive
            LOGGER.warning("lci.flow_registry.load_failed", path=str(file), error=str(exc))
            continue
        uuid = _extract_uuid(document)
        if not uuid:
            LOGGER.debug("lci.flow_registry.missing_uuid", path=str(file))
            continue
        items[uuid] = document
    return items


def _extract_uuid(document: dict[str, Any]) -> str | None:
    candidates = [
        ("flowInformation", "dataSetInformation", "common:UUID"),
        ("processInformation", "dataSetInformation", "common:UUID"),
        ("unitGroupInformation", "dataSetInformation", "common:UUID"),
        ("flowPropertyInformation", "dataSetInformation", "common:UUID"),
        ("flowPropertiesInformation", "dataSetInformation", "common:UUID"),
        ("dataSetInformation", "common:UUID"),
        ("@common:UUID",),
    ]
    roots = [document]
    if isinstance(document, dict):
        for container_key in ("flowDataSet", "processDataSet", "unitGroupDataSet", "flowPropertyDataSet"):
            child = document.get(container_key)
            if isinstance(child, dict):
                roots.append(child)
    for root in roots:
        for path in candidates:
            value = _dig(root, *path)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return document.get("uuid") if isinstance(document.get("uuid"), str) else None


def _dig(node: Any, *keys: str) -> Any:
    current = node
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _resolve_name(document: dict[str, Any], path: tuple[str, ...]) -> str | None:
    block = _dig(document, *path)
    if isinstance(block, list) and block:
        return _coerce_text(block[0])
    if isinstance(block, dict):
        base = block.get("baseName")
        if isinstance(base, list) and base:
            return _coerce_text(base[0])
        if isinstance(base, dict):
            return _coerce_text(base.get("#text") or next(iter(base.values()), None))
        text = block.get("#text") or block.get("text")
        if text:
            return _coerce_text(text)
    if isinstance(block, str):
        return block
    return None


def _extract_short_description(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, dict):
        text = value.get("#text") or value.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
        for item in value.values():
            result = _extract_short_description(item)
            if result:
                return result
        return None
    if isinstance(value, list):
        for item in value:
            result = _extract_short_description(item)
            if result:
                return result
    return None


def _coerce_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    return None


def _reference_uuid(reference: Any) -> str | None:
    if isinstance(reference, str):
        return reference.strip() or None
    if isinstance(reference, dict):
        for key in ("@refObjectId", "uuid", "common:UUID"):
            value = reference.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _normalize_flow_type(flow_type: str | None) -> str | None:
    if not flow_type:
        return None
    return flow_type.strip()


def _resolve_reference_unit(document: dict[str, Any]) -> str | None:
    candidates = [
        ("unitGroupInformation", "quantitativeReference", "referenceToReferenceUnit"),
        ("unitGroupInformation", "units", "unit"),
    ]
    for path in candidates:
        node = _dig(document, *path)
        if isinstance(node, list) and node:
            return _extract_unit_name(node[0])
        if isinstance(node, dict):
            return _extract_unit_name(node)
        if isinstance(node, str):
            return node.strip()
    return None


def _extract_unit_name(node: dict[str, Any]) -> str | None:
    if "@name" in node:
        return _coerce_text(node.get("@name"))
    if "#text" in node:
        return _coerce_text(node.get("#text"))
    if "name" in node:
        value = node.get("name")
        if isinstance(value, dict):
            return _coerce_text(value.get("#text"))
        if isinstance(value, str):
            return value
    return None


def _infer_unit_family(name: str | None, document: dict[str, Any]) -> str | None:
    text = (name or json.dumps(document)).lower()
    if any(keyword in text for keyword in ("mass", "kg", "gram", "tonne", "吨")):
        return "mass"
    if any(keyword in text for keyword in ("energy", "kwh", "mj", "joule", "热量")):
        return "energy"
    if any(keyword in text for keyword in ("volume", "liter", "litre", "m3", "立方")):
        return "volume"
    if any(keyword in text for keyword in ("area", "m2")):
        return "area"
    return None
