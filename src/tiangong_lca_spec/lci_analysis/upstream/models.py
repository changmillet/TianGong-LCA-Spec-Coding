"""Data models used by the lifecycle flow prioritisation workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ExchangeRecord:
    dataset_uuid: str
    dataset_name: str | None
    exchange: dict[str, Any]
    flow_name: str | None
    exchange_name: str | None
    exchange_name_zh: str | None
    exchange_name_en: str | None
    flow_name_zh: str | None
    flow_name_en: str | None
    amount: float | None
    unit: str | None
    unit_family: str | None
    flow_uuid: str | None
    flow_type: str | None
    flow_class: str | None
    direction: str | None
    reference_unit: str | None
    classification_confidence: float | None = None
    classification_reason: str | None = None


@dataclass(slots=True)
class PrioritySlice:
    dataset_uuid: str
    dataset_name: str | None
    exchange_name: str | None
    exchange_name_zh: str | None
    exchange_name_en: str | None
    flow_name_zh: str | None
    flow_name_en: str | None
    flow_uuid: str | None
    flow_role: str
    unit_family: str | None
    reference_unit: str | None
    total_amount: float | None
    share: float | None
    cumulative_share: float | None
    reference_process_count: int | None = None
    flow_type: str | None = None
    classification_confidence: float | None = None
    rationale: str | None = None
    downstream_path: str | None = None
    downstream_action: str | None = None
    exchanges: list[ExchangeRecord] = field(default_factory=list)


@dataclass(slots=True)
class UnknownClassification:
    exchange_name: str | None
    dataset_uuid: str
    reason: str


@dataclass(slots=True)
class ActionItem:
    priority: str
    type: str
    summary: str
    evidence: list[str] = field(default_factory=list)


@dataclass(slots=True)
class LifecycleFlowPrioritizationResult:
    raw_materials: list[PrioritySlice]
    energy: list[PrioritySlice]
    auxiliaries: list[PrioritySlice]
    downstream_outputs: list[PrioritySlice]
    unknown_classifications: list[UnknownClassification]
    actions: list[ActionItem]
    notes: list[str]
    metadata: dict[str, Any]
