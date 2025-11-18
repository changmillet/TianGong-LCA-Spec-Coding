"""LLM-backed flow classification helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from tiangong_lca_spec.core.json_utils import parse_json_response
from tiangong_lca_spec.core.llm import LanguageModelProtocol
from tiangong_lca_spec.core.logging import get_logger
from tiangong_lca_spec.lci_analysis.common.classifier_cache import CacheEntry, ClassifierCache
from tiangong_lca_spec.lci_analysis.common.flows import FlowMetadata

from .rules import ClassificationResult

LOGGER = get_logger(__name__)

ALLOWED_LABELS = {"raw_material", "energy", "auxiliary", "product_output", "waste", "unknown"}


@dataclass(slots=True)
class DatasetContext:
    uuid: str | None = None
    name: str | None = None
    intended_applications: list[str] | None = None
    technology_notes: list[str] | None = None
    process_information: dict[str, Any] | None = None
    modelling_and_validation: dict[str, Any] | None = None


class LLMFlowClassifier:
    """Invoke an LLM to classify flows when heuristic rules are inconclusive."""

    def __init__(
        self,
        llm: LanguageModelProtocol,
        prompt: str,
        *,
        cache: ClassifierCache | None = None,
    ) -> None:
        self._llm = llm
        self._prompt = prompt.strip()
        self._cache = cache

    def classify(
        self,
        exchange: dict[str, Any],
        flow_meta: FlowMetadata | None,
        *,
        flow_document: dict[str, Any] | None = None,
        dataset: DatasetContext | None = None,
    ) -> ClassificationResult | None:
        cache_key = self._build_cache_key(exchange, flow_meta, dataset)
        cached = self._cache.get(cache_key) if self._cache else None
        if cached:
            return ClassificationResult(
                label=cached.label or "unknown",
                confidence=cached.confidence or 0.5,
                rationale=cached.rationale or "cached_llm_result",
            )

        context = self._build_context(exchange, flow_meta, flow_document, dataset)
        payload = {
            "prompt": self._prompt,
            "context": context,
            "response_format": {"type": "json_object"},
        }
        try:
            raw = self._llm.invoke(payload)
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.warning("lci.upstream.llm_classification_failed", error=str(exc))
            return None
        try:
            data = parse_json_response(raw) if isinstance(raw, str) else raw
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.warning("lci.upstream.llm_parse_failed", error=str(exc))
            return None
        if not isinstance(data, dict):
            LOGGER.warning("lci.upstream.llm_invalid_payload", payload=data)
            return None
        label = self._normalise_label(data.get("class_label"))
        if label not in ALLOWED_LABELS:
            LOGGER.warning("lci.upstream.llm_invalid_label", label=label, payload=data)
            return None
        confidence = self._coerce_confidence(data.get("confidence"))
        rationale = self._stringify(data.get("rationale")) or "LLM classification"
        result = ClassificationResult(label=label, confidence=confidence, rationale=rationale)
        if self._cache:
            self._cache.set(CacheEntry(flow_uuid=cache_key, label=label, confidence=confidence, rationale=rationale))
        return result

    def _build_cache_key(
        self,
        exchange: dict[str, Any],
        flow_meta: FlowMetadata | None,
        dataset: DatasetContext | None,
    ) -> str:
        uuid = flow_meta.uuid if flow_meta else "unknown_flow"
        direction = (exchange.get("exchangeDirection") or "").lower() or "unknown_direction"
        dataset_key = ""
        if dataset:
            dataset_key = dataset.uuid or dataset.name or ""
            if dataset.intended_applications:
                dataset_key += "|" + "|".join(dataset.intended_applications)
        return f"{uuid}|{direction}|{dataset_key}"

    def _build_context(
        self,
        exchange: dict[str, Any],
        flow_meta: FlowMetadata | None,
        flow_document: dict[str, Any] | None,
        dataset: DatasetContext | None,
    ) -> dict[str, Any]:
        dataset_block = {}
        if dataset:
            if dataset.uuid:
                dataset_block["uuid"] = dataset.uuid
            if dataset.name:
                dataset_block["name"] = dataset.name
            if dataset.intended_applications:
                dataset_block["intended_applications"] = dataset.intended_applications
            if dataset.technology_notes:
                dataset_block["technology_notes"] = dataset.technology_notes
            if dataset.process_information:
                dataset_block["process_information"] = dataset.process_information
            if dataset.modelling_and_validation:
                dataset_block["modelling_and_validation"] = dataset.modelling_and_validation
        flow_block: dict[str, Any] = {}
        if flow_document:
            flow_block = flow_document
        else:
            flow_block = {
                "uuid": flow_meta.uuid if flow_meta else None,
                "name": flow_meta.name if flow_meta else None,
                "flowType": flow_meta.flow_type if flow_meta else None,
            }
        exchange_block = {
            "direction": exchange.get("exchangeDirection") or exchange.get("direction"),
            "unit": exchange.get("unit") or exchange.get("resultingAmountUnit"),
            "amount": exchange.get("meanAmount") or exchange.get("resultingAmount"),
            "comment": self._stringify(exchange.get("generalComment")),
            "allocation": exchange.get("allocations"),
            "original": exchange,
        }
        return {
            "dataset": dataset_block,
            "exchange": exchange_block,
            "flow": flow_block,
        }

    @staticmethod
    def _stringify(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            text = value.get("#text") or value.get("text") or value.get("@value")
            if isinstance(text, str):
                return text
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _normalise_label(label: Any) -> str:
        if not isinstance(label, str):
            return "unknown"
        cleaned = label.strip().lower().replace("-", "_")
        mapping = {
            "product": "product_output",
            "product_output": "product_output",
            "product output": "product_output",
            "wastes": "waste",
            "waste_flow": "waste",
            "raw": "raw_material",
            "rawmaterial": "raw_material",
        }
        return mapping.get(cleaned, cleaned)

    @staticmethod
    def _coerce_confidence(value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.6
        if number < 0.0:
            return 0.0
        if number > 1.0:
            return 1.0
        return number
