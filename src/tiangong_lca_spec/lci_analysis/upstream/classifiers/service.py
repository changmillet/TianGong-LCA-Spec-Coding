"""Coordinator for rule-based and LLM-backed flow classification."""

from __future__ import annotations

from typing import Any

from tiangong_lca_spec.lci_analysis.common.flows import FlowMetadata

from .llm import DatasetContext, LLMFlowClassifier
from .rules import ClassificationResult, classify_with_rules


class FlowClassifier:
    """Apply heuristic rules first, then fall back to an LLM when available."""

    def __init__(self, llm_classifier: LLMFlowClassifier | None = None) -> None:
        self._llm_classifier = llm_classifier

    def classify(
        self,
        exchange: dict[str, Any],
        flow_meta: FlowMetadata | None,
        *,
        flow_document: dict[str, Any] | None = None,
        dataset_context: DatasetContext | None = None,
    ) -> ClassificationResult:
        result = classify_with_rules(exchange, flow_meta)
        if result.label != "unknown" or not self._llm_classifier:
            return result
        llm_result = self._llm_classifier.classify(
            exchange,
            flow_meta,
            flow_document=flow_document,
            dataset=dataset_context,
        )
        return llm_result or result
