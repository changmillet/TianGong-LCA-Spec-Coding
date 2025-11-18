"""Aggregation utilities for lifecycle flow prioritisation."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from tiangong_lca_spec.lci_analysis.upstream.models import ActionItem, ExchangeRecord, PrioritySlice


def accumulate_role_totals(
    exchanges: Iterable[ExchangeRecord],
) -> dict[tuple[str, str | None], float]:
    """Sum exchange amounts per (role, unit_family)."""

    totals: defaultdict[tuple[str, str | None], float] = defaultdict(float)
    for record in exchanges:
        if record.amount is None or record.flow_class is None:
            continue
        key = (record.flow_class, record.unit_family)
        totals[key] += record.amount
    return dict(totals)


def build_priority_slices(
    exchanges: Iterable[ExchangeRecord],
    target_role: str,
    totals: dict[tuple[str, str | None], float],
    *,
    cumulative_threshold: float | None = None,
    max_items: int | None = None,
) -> list[PrioritySlice]:
    """Return contribution slices grouped by unit family for the requested class label."""

    relevant = [record for record in exchanges if record.flow_class == target_role and record.amount is not None]
    if not relevant:
        return []

    def _unit_key(record: ExchangeRecord) -> str:
        return record.unit_family or ""

    grouped: dict[str | None, list[ExchangeRecord]] = {}
    for record in relevant:
        grouped.setdefault(record.unit_family, []).append(record)

    ordered_units = sorted(grouped.keys(), key=lambda uf: (uf is None, uf or ""))

    slices: list[PrioritySlice] = []
    for unit_family in ordered_units:
        records_in_unit = grouped[unit_family]
        records_in_unit.sort(key=lambda rec: rec.amount or 0.0, reverse=True)
        total_amount = totals.get((target_role, unit_family))
        running = 0.0
        for idx, record in enumerate(records_in_unit):
            running += record.amount or 0.0
            share = (record.amount or 0.0) / total_amount if total_amount else None
            cumulative = running / total_amount if total_amount else None
            slices.append(
                PrioritySlice(
                    dataset_uuid=record.dataset_uuid,
                    dataset_name=record.dataset_name,
                    exchange_name=record.exchange_name or record.flow_name,
                    exchange_name_zh=record.exchange_name_zh,
                    exchange_name_en=record.exchange_name_en,
                    flow_name_zh=record.flow_name_zh,
                    flow_name_en=record.flow_name_en,
                    flow_uuid=record.flow_uuid,
                    flow_type=record.flow_type,
                    flow_role=target_role,
                    unit_family=unit_family,
                    reference_unit=record.reference_unit,
                    total_amount=record.amount,
                    share=share,
                    cumulative_share=cumulative,
                    classification_confidence=record.classification_confidence,
                    rationale=record.classification_reason,
                    exchanges=[record],
                )
            )
            if cumulative_threshold is not None and cumulative is not None and cumulative >= cumulative_threshold:
                break
            if max_items is not None and idx + 1 >= max_items:
                break
    return slices


def build_downstream_priority_slices(
    exchanges: Iterable[ExchangeRecord],
    totals: dict[tuple[str, str | None], float],
    *,
    roles: tuple[str, ...] = ("product_output", "by_product", "waste", "emission", "resource"),
    cumulative_threshold: float | None = None,
) -> list[PrioritySlice]:
    outputs = [record for record in exchanges if record.direction == "output"]
    if not outputs:
        return []
    slices: list[PrioritySlice] = []
    for role in roles:
        slices.extend(
            build_priority_slices(
                outputs,
                role,
                totals,
                cumulative_threshold=cumulative_threshold,
            )
        )
    for slice_ in slices:
        display_name = slice_.exchange_name or slice_.flow_name_en or slice_.flow_name_zh or ""
        slice_.downstream_path = _infer_downstream_path(display_name)
        slice_.downstream_action = _suggest_downstream_action(display_name, slice_.flow_role)
    return slices


def build_default_actions(
    raw_materials: list[PrioritySlice],
    energy: list[PrioritySlice],
    downstream: list[PrioritySlice],
) -> list[ActionItem]:
    actions: list[ActionItem] = []
    if raw_materials:
        top = raw_materials[0]
        evidence = []
        if top.share is not None:
            evidence.append(f"share≈{top.share:.3f}")
        evidence.append(f"dataset={top.dataset_uuid}")
        actions.append(
            ActionItem(
                priority="high",
                type="upstream",
                summary=f"对{top.exchange_name or '关键原料'}进行上游追踪，补齐来源与损耗假设",
                evidence=evidence,
            )
        )
    if energy:
        labels = []
        for entry in energy[:2]:
            if entry.exchange_name and entry.total_amount is not None:
                labels.append(f"{entry.exchange_name}≈{entry.total_amount:.2f}")
        if labels:
            actions.append(
                ActionItem(
                    priority="medium",
                    type="upstream",
                    summary="校核电力/化石能源购入量及损耗，避免与终端交付重复",
                    evidence=labels,
                )
            )
    wastewater_entry = next(
        (item for item in downstream if item.exchange_name and "废水" in item.exchange_name),
        None,
    )
    if wastewater_entry:
        evidence = []
        if wastewater_entry.share is not None:
            evidence.append(f"share≈{wastewater_entry.share:.3f}")
        actions.append(
            ActionItem(
                priority="medium",
                type="downstream",
                summary="补充废水/尾水去向并校核与 COD/BOD 指标一致",
                evidence=evidence or ["废水流需要明确排放点"],
            )
        )
    if downstream:
        biggest = max(downstream, key=lambda item: item.share or 0.0)
        if biggest.flow_role in {"waste", "emission"} and biggest.exchange_name:
            evidence = []
            if biggest.share is not None:
                evidence.append(f"share≈{biggest.share:.3f}")
            actions.append(
                ActionItem(
                    priority="high",
                    type="downstream",
                    summary=f"明确{biggest.exchange_name}排放控制与许可要求",
                    evidence=evidence or ["大宗废弃物流"],
                )
            )
    return actions


def _infer_downstream_path(name: str) -> str:
    text = (name or "").lower()
    if any(keyword in text for keyword in ("石膏", "gypsum", "副产品", "recycle")):
        return "recycle"
    if any(keyword in text for keyword in ("尾矿", "landfill", "固体", "渣")):
        return "landfill"
    if any(keyword in text for keyword in ("电", "电力", "electric")):
        return "reuse"
    return "unknown"


def _suggest_downstream_action(name: str, flow_class: str | None) -> str:
    text = (name or "").lower()
    if flow_class == "product_output":
        return "追踪终端售电及低压网络损耗"
    if flow_class == "by_product":
        return "确认副产品用途与收益匹配的下游去向"
    if "废水" in text or "wastewater" in text:
        return "补充废水/尾水处理节点并锁定排放口"
    if any(keyword in text for keyword in ("co2", "二氧化碳", "氮氧化物", "so2", "甲烷", "颗粒")):
        return "校对大气排放量并关联许可/减排措施"
    if any(keyword in text for keyword in ("尾矿", "渣", "sludge", "landfill")):
        return "确认固废最终去向并补齐处置记录"
    return "跟踪该流的下游合规去向"
