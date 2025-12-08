"""LLM-assisted refinement for flow search candidates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from tiangong_lca_spec.core.json_utils import parse_json_response
from tiangong_lca_spec.core.logging import get_logger
from tiangong_lca_spec.core.models import FlowCandidate, FlowQuery

LOGGER = get_logger(__name__)


class LanguageModelProtocol(Protocol):
    """Minimal protocol required from language models used in this module."""

    def invoke(self, input_data: dict[str, Any]) -> Any: ...


@dataclass(slots=True)
class LLMSelectionResult:
    """Outcome returned by the LLM selector."""

    candidate: FlowCandidate | None
    confidence: float | None
    reasoning: str | None
    source_indices: list[int]


class FlowSearchLLMSelector:
    """Select or re-rank candidates using an LLM."""

    PROMPT = (
        "You are helping map ecoinvent flows to Tiangong reference flows. \n"
        "We already ran a similarity search that produced up to 10 candidates. \n"
        "Choose the best candidate index (0-based), or return null if none match. \n"
        "Respond with strict JSON: {\n"
        "  \"best_index\": integer|null,\n"
        "  \"confidence\": number between 0 and 1 (optional),\n"
        "  \"reason\": short justification.\n"
        "}\n"
        "Use the exchange name, classification, synonyms, flow property, and usage summary as context."
    )

    def __init__(self, llm: LanguageModelProtocol) -> None:
        self._llm = llm

    def select(self, query: FlowQuery, flow_metadata: dict[str, Any], candidates: Sequence[FlowCandidate]) -> LLMSelectionResult:
        if not candidates:
            return LLMSelectionResult(candidate=None, confidence=None, reasoning=None, source_indices=[])
        try:
            payload = {
                "prompt": self.PROMPT,
                "context": self._build_context(query, flow_metadata, candidates),
            }
            raw = self._llm.invoke(payload)
            parsed = self._parse_response(raw)
            best_index = parsed.get("best_index")
            if best_index is None:
                return LLMSelectionResult(candidate=None, confidence=self._coerce_float(parsed.get("confidence")), reasoning=parsed.get("reason"), source_indices=[])
            if not isinstance(best_index, int) or not 0 <= best_index < len(candidates):
                LOGGER.warning(
                    "flow_search.llm_selector.invalid_index",
                    index=best_index,
                    candidate_count=len(candidates),
                )
                return LLMSelectionResult(candidate=None, confidence=None, reasoning="Invalid index returned by LLM", source_indices=[])
            return LLMSelectionResult(
                candidate=candidates[best_index],
                confidence=self._coerce_float(parsed.get("confidence")),
                reasoning=parsed.get("reason"),
                source_indices=[best_index],
            )
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.warning("flow_search.llm_selector.failed", error=str(exc))
            return LLMSelectionResult(candidate=None, confidence=None, reasoning=str(exc), source_indices=[])

    def _build_context(self, query: FlowQuery, flow_metadata: dict[str, Any], candidates: Sequence[FlowCandidate]) -> str:
        payload = {
            "exchange": {
                "exchange_name": query.exchange_name,
                "description": query.description,
                "flow_uuid": flow_metadata.get("flow_uuid"),
                "classification": flow_metadata.get("classification"),
                "flow_property": flow_metadata.get("flow_property"),
                "synonyms": flow_metadata.get("synonyms"),
                "usage_count": flow_metadata.get("usage_count"),
            },
            "candidates": [
                {
                    "index": idx,
                    "uuid": candidate.uuid,
                    "base_name": candidate.base_name,
                    "geography": candidate.geography,
                    "classification": candidate.classification,
                    "flow_properties": candidate.flow_properties,
                    "general_comment": candidate.general_comment,
                }
                for idx, candidate in enumerate(candidates[:10])
            ],
        }
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _parse_response(raw: Any) -> dict[str, Any]:
        if raw is None:
            return {}
        if isinstance(raw, (dict, list)):
            if isinstance(raw, list):
                return raw[0] if raw else {}
            return raw
        if isinstance(raw, str):
            return parse_json_response(raw)
        return {}

    @staticmethod
    def _coerce_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
