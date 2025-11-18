"""Utilities for collecting reference flow usage statistics from MCP repositories."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from tiangong_lca_spec.core.exceptions import SpecCodingError
from tiangong_lca_spec.core.logging import get_logger
from tiangong_lca_spec.process_repository import ProcessRepositoryClient

LOGGER = get_logger(__name__)


@dataclass(slots=True)
class ReferenceFlowUsage:
    """Holds process usage information for a single flow."""

    flow_uuid: str
    process_ids: list[str] = field(default_factory=list)

    @property
    def process_count(self) -> int:
        return len(self.process_ids)


class ReferenceFlowUsageCollector:
    """Scans repository processes to find which ones reference target flows."""

    def __init__(
        self,
        repository: ProcessRepositoryClient,
        *,
        user_id: str | None = None,
        export_dir: Path | None = None,
    ) -> None:
        self._repository = repository
        self._user_id = user_id
        self._export_dir = Path(export_dir) if export_dir else None

    def collect(self, flow_uuids: Iterable[str]) -> dict[str, ReferenceFlowUsage]:
        """Return reference flow usage for the supplied UUIDs."""

        targets = {item.strip() for item in flow_uuids if isinstance(item, str) and item.strip()}
        if not targets:
            return {}
        user_id = self._user_id or self._repository.detect_current_user_id()
        if not user_id:
            LOGGER.warning("reference_usage.missing_user_id")
            return {}
        try:
            json_ids = self._repository.list_json_ids(user_id)
        except SpecCodingError as exc:
            LOGGER.error("reference_usage.list_failed", error=str(exc))
            return {}
        usages: dict[str, ReferenceFlowUsage] = {uuid: ReferenceFlowUsage(flow_uuid=uuid) for uuid in targets}
        seen: set[str] = set()
        matches = 0
        for process_id in json_ids:
            if not process_id or process_id in seen:
                continue
            seen.add(process_id)
            record = self._repository.fetch_record("processes", process_id, preferred_user_id=user_id)
            dataset, raw_payload = _extract_process_dataset(record, process_id=process_id)
            if not dataset:
                continue
            reference_flows = _extract_reference_flow_uuids(dataset)
            overlapping = reference_flows & targets
            if not overlapping:
                continue
            process_uuid = _extract_process_uuid(dataset) or process_id
            matches += 1
            for flow_uuid in overlapping:
                usage = usages.setdefault(flow_uuid, ReferenceFlowUsage(flow_uuid=flow_uuid))
                usage.process_ids.append(process_uuid)
                if self._export_dir and raw_payload:
                    _persist_process_payload(raw_payload, self._export_dir, flow_uuid, process_uuid)
        LOGGER.info(
            "reference_usage.summary",
            scanned=len(seen),
            matched_processes=matches,
            flow_matches=sum(usage.process_count for usage in usages.values()),
        )
        return {uuid: usage for uuid, usage in usages.items() if usage.process_ids}


# --------------------------------------------------------------------------- helpers

def _extract_process_dataset(
    record: Mapping[str, Any] | None,
    *,
    process_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not isinstance(record, Mapping):
        return None, None
    payload = record.get("json_ordered") or record.get("json")
    if payload is None:
        return None, None
    document: dict[str, Any] | None = None
    if isinstance(payload, str):
        try:
            document = json.loads(payload)
        except json.JSONDecodeError as exc:
            LOGGER.warning("reference_usage.json_decode_failed", process_id=process_id, error=str(exc))
            return None, None
    elif isinstance(payload, Mapping):
        document = dict(payload)
    if not isinstance(document, dict):
        return None, None
    dataset = document.get("processDataSet")
    if isinstance(dataset, Mapping):
        return dict(dataset), document
    if "processInformation" in document:
        return dict(document), {"processDataSet": document}
    return None, document


def _extract_process_uuid(process_dataset: Mapping[str, Any]) -> str | None:
    info = process_dataset.get("processInformation")
    if not isinstance(info, Mapping):
        return None
    data_info = info.get("dataSetInformation")
    if isinstance(data_info, Mapping):
        value = data_info.get("common:UUID")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_reference_flow_uuids(process_dataset: Mapping[str, Any]) -> set[str]:
    reference_ids = _extract_reference_flow_ids(process_dataset)
    if not reference_ids:
        return set()
    exchanges_block = process_dataset.get("exchanges")
    if isinstance(exchanges_block, Mapping):
        exchanges = exchanges_block.get("exchange")
    else:
        exchanges = exchanges_block
    candidates: list[Any]
    if isinstance(exchanges, list):
        candidates = exchanges
    elif isinstance(exchanges, Mapping):
        candidates = [exchanges]
    else:
        return set()
    flow_uuids: set[str] = set()
    for exchange in candidates:
        if not isinstance(exchange, Mapping):
            continue
        identifier = exchange.get("@dataSetInternalID") or exchange.get("dataSetInternalID")
        if isinstance(identifier, str) and identifier.strip() in reference_ids:
            uuid = _reference_uuid(exchange.get("referenceToFlowDataSet"))
            if uuid:
                flow_uuids.add(uuid)
    return flow_uuids


def _extract_reference_flow_ids(process_dataset: Mapping[str, Any]) -> set[str]:
    quantitative_ref = process_dataset.get("processInformation", {}).get("quantitativeReference")
    if not isinstance(quantitative_ref, Mapping):
        return set()
    ref = quantitative_ref.get("referenceToReferenceFlow")
    ids: set[str] = set()
    if isinstance(ref, list):
        for item in ref:
            value = _coerce_text(item)
            if value:
                ids.add(value)
    elif isinstance(ref, Mapping):
        value = ref.get("@dataSetInternalID") or ref.get("#text") or ref.get("id")
        if isinstance(value, str) and value.strip():
            ids.add(value.strip())
    elif isinstance(ref, str) and ref.strip():
        ids.add(ref.strip())
    return ids


def _reference_uuid(reference: Any) -> str | None:
    if isinstance(reference, str):
        return reference.strip() or None
    if isinstance(reference, Mapping):
        for key in ("@refObjectId", "uuid", "id", "common:UUID"):
            value = reference.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _coerce_text(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, Mapping):
        candidate = value.get("#text") or value.get("text")
        if isinstance(candidate, str):
            text = candidate.strip()
            if text:
                return text
    return None


def _persist_process_payload(payload: Mapping[str, Any], export_dir: Path, flow_uuid: str, process_uuid: str) -> None:
    path = export_dir / flow_uuid / f"{process_uuid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        LOGGER.warning("reference_usage.persist_failed", path=str(path), error=str(exc))
