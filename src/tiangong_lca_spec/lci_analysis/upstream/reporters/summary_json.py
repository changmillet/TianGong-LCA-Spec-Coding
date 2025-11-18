"""Utility for writing lifecycle flow prioritisation output."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tiangong_lca_spec.lci_analysis.upstream.models import (
    ActionItem,
    LifecycleFlowPrioritizationResult,
    PrioritySlice,
    UnknownClassification,
)


def write_summary_json(result: LifecycleFlowPrioritizationResult, output_dir: Path | str) -> Path:
    path = Path(output_dir) / "upstream_priority.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "upstream_priority": _group_slices_by_unit_family(
            {
                "raw_materials": result.raw_materials,
                "energy": result.energy,
                "auxiliaries": result.auxiliaries,
            }
        ),
        "downstream_priority": _group_slices_by_unit_family(
            {
                "outputs": result.downstream_outputs,
            },
            include_downstream_fields=True,
        ),
        "unknown_classification": [_unknown_to_dict(item) for item in result.unknown_classifications],
        "actions": [_action_to_dict(item) for item in result.actions],
        "notes": result.notes,
        "metadata": result.metadata,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _unknown_to_dict(entry: UnknownClassification) -> dict[str, Any]:
    return {
        "exchange_name": entry.exchange_name,
        "dataset_uuid": entry.dataset_uuid,
        "reason": entry.reason,
    }


def _action_to_dict(action: ActionItem) -> dict[str, Any]:
    return {
        "priority": action.priority,
        "type": action.type,
        "summary": action.summary,
        "evidence": action.evidence,
    }


def _group_slices_by_unit_family(
    role_buckets: dict[str, list[PrioritySlice]],
    *,
    include_downstream_fields: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for role_name, slices in role_buckets.items():
        for slice_ in slices:
            family = slice_.unit_family or "unknown_unit"
            grouped.setdefault(family, []).append(
                _format_priority_entry(role_name, slice_, include_downstream_fields=include_downstream_fields)
            )
    return dict(sorted(grouped.items(), key=lambda item: (item[0] == "unknown_unit", item[0])))


def _format_priority_entry(role_name: str, slice_: PrioritySlice, *, include_downstream_fields: bool = False) -> dict[str, Any]:
    payload = {
        "exchange_name_zh": slice_.exchange_name_zh,
        "flow_name_zh": slice_.flow_name_zh,
        "exchange_name_en": slice_.exchange_name_en,
        "flow_name_en": slice_.flow_name_en,
        "flow_uuid": slice_.flow_uuid,
        "flow_type": slice_.flow_type,
        "flow_role": slice_.flow_role or role_name,
        "unit_family": slice_.unit_family,
        "reference_unit": slice_.reference_unit,
        "total_amount": slice_.total_amount,
        "share": _format_percent(slice_.share),
        "cumulative_share": _format_percent(slice_.cumulative_share),
        "reference_process_count": slice_.reference_process_count,
        "classification_confidence": slice_.classification_confidence,
        "rationale": slice_.rationale,
        "dataset_uuid": slice_.dataset_uuid,
        "dataset_name": slice_.dataset_name,
    }
    if include_downstream_fields:
        payload["downstream_path"] = slice_.downstream_path
        payload["downstream_action"] = slice_.downstream_action
    return payload


def _format_percent(value: float | None) -> str | None:
    if value is None:
        return None
    percent = value * 100
    text = f"{percent:.4g}"
    if "e" not in text and "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text}%"
