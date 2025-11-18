"""Workflow implementation for lifecycle flow prioritisation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from tiangong_lca_spec.core.config import Settings, get_settings
from tiangong_lca_spec.core.logging import get_logger
from tiangong_lca_spec.lci_analysis.common import FlowRegistry, load_process_datasets
from tiangong_lca_spec.lci_analysis.common.units import normalise_amount
from tiangong_lca_spec.lci_analysis.upstream.calculators import (
    accumulate_role_totals,
    build_default_actions,
    build_downstream_priority_slices,
    build_priority_slices,
)
from tiangong_lca_spec.lci_analysis.upstream.classifiers import ClassificationResult, DatasetContext, FlowClassifier
from tiangong_lca_spec.lci_analysis.upstream.models import (
    ExchangeRecord,
    LifecycleFlowPrioritizationResult,
    PrioritySlice,
    UnknownClassification,
)
from tiangong_lca_spec.lci_analysis.upstream.reference_usage import ReferenceFlowUsage, ReferenceFlowUsageCollector
from tiangong_lca_spec.lci_analysis.upstream.reporters import write_summary_excel, write_summary_json
from tiangong_lca_spec.process_repository import ProcessRepositoryClient

LOGGER = get_logger(__name__)


@dataclass
class WorkflowInputs:
    process_datasets: Path
    flows_dir: Path | None
    flow_properties_dir: Path | None
    unit_groups_dir: Path | None
    output_dir: Path
    run_id: str | None = None
    repository: ProcessRepositoryClient | None = None
    repository_user_id: str | None = None
    reference_flow_stats: bool = False
    reference_process_export_dir: Path | None = None


class LifecycleFlowPrioritizationWorkflow:
    """Coordinates the lifecycle flow prioritisation pipeline."""

    def __init__(self, settings: Settings | None = None, classifier: FlowClassifier | None = None) -> None:
        self._settings = settings or get_settings()
        self._classifier = classifier or FlowClassifier()

    def run(self, inputs: WorkflowInputs, datasets: list[dict[str, Any]] | None = None) -> Path:
        if datasets is None:
            datasets = load_process_datasets(inputs.process_datasets)
        registry = FlowRegistry(
            flows_dir=inputs.flows_dir,
            flow_properties_dir=inputs.flow_properties_dir,
            unit_groups_dir=inputs.unit_groups_dir,
        )

        exchange_records = list(self._build_exchange_records(datasets, registry))
        totals = accumulate_role_totals(exchange_records)

        raw_materials = build_priority_slices(exchange_records, "raw_material", totals)
        energy = build_priority_slices(exchange_records, "energy", totals)
        auxiliaries = build_priority_slices(exchange_records, "auxiliary", totals)
        output_records = [record for record in exchange_records if record.direction == "output"]
        output_totals = accumulate_role_totals(output_records)
        downstream_outputs = build_downstream_priority_slices(output_records, output_totals)

        unknown_entries = self._build_unknown_entries(exchange_records)
        actions = build_default_actions(raw_materials, energy, downstream_outputs)
        notes = self._build_notes(exchange_records)

        metadata: dict[str, Any] = {
            "dataset_count": len(datasets),
            "run_id": inputs.run_id,
            "records": len(exchange_records),
            "schema_version": 2,
        }
        result = LifecycleFlowPrioritizationResult(
            raw_materials=raw_materials,
            energy=energy,
            auxiliaries=auxiliaries,
            downstream_outputs=downstream_outputs,
            unknown_classifications=unknown_entries,
            actions=actions,
            notes=notes,
            metadata=metadata,
        )

        if inputs.reference_flow_stats and inputs.repository:
            self._enrich_with_reference_usage(
                result,
                exchange_records,
                repository=inputs.repository,
                user_id=inputs.repository_user_id,
                export_dir=inputs.reference_process_export_dir or (inputs.output_dir / "reference_processes"),
                stats_path=inputs.output_dir / "reference_flow_stats.json",
            )
        elif inputs.reference_flow_stats and not inputs.repository:
            LOGGER.warning("lci.upstream.reference_usage_disabled", reason="repository_not_configured")

        json_path = write_summary_json(result, inputs.output_dir)
        excel_path = write_summary_excel(result, inputs.output_dir)
        LOGGER.info("lci.upstream.workflow.complete", json=str(json_path), excel=str(excel_path))
        return json_path

    # ------------------------------------------------------------------ helpers

    def _enrich_with_reference_usage(
        self,
        result: LifecycleFlowPrioritizationResult,
        exchanges: Iterable[ExchangeRecord],
        *,
        repository: ProcessRepositoryClient,
        user_id: str | None,
        export_dir: Path,
        stats_path: Path,
    ) -> None:
        flow_uuids = {record.flow_uuid for record in exchanges if record.flow_uuid}
        if not flow_uuids:
            LOGGER.info("lci.upstream.reference_usage.skipped", reason="no_flow_uuids")
            return
        collector = ReferenceFlowUsageCollector(repository, user_id=user_id, export_dir=export_dir)
        stats = collector.collect(flow_uuids)
        if not stats:
            LOGGER.info("lci.upstream.reference_usage.empty")
            return
        self._apply_reference_counts(result, stats)
        total_processes = sum(usage.process_count for usage in stats.values())
        result.metadata["reference_flow_stats"] = {
            "flows_with_dependents": len(stats),
            "process_count": total_processes,
        }
        self._write_reference_stats_file(stats, stats_path)

    def _build_exchange_records(
        self,
        datasets: Iterable[dict[str, Any]],
        registry: FlowRegistry,
    ) -> Iterable[ExchangeRecord]:
        for dataset in datasets:
            pd = dataset.get("processDataSet") or dataset
            process_uuid = _dig(
                pd,
                "processInformation",
                "dataSetInformation",
                "common:UUID",
            ) or pd.get("uuid")
            dataset_name = _resolve_dataset_name(pd)
            dataset_context = _build_dataset_context(pd, process_uuid, dataset_name)
            reference_flow_ids = _extract_reference_flow_ids(pd)

            prepared_entries: list[dict[str, Any]] = []
            exchanges = _extract_exchanges(pd)
            for exchange in exchanges:
                flow_meta = registry.get_flow(exchange.get("referenceToFlowDataSet"))
                unit_group_meta = registry.get_unit_group(flow_meta.unit_group_ref if flow_meta else None)
                unit = exchange.get("unit") or (unit_group_meta.reference_unit if unit_group_meta else None)
                amount = exchange.get("amount") or exchange.get("meanAmount")
                unit_family_hint = flow_meta.unit_family if flow_meta else None
                unit_family = unit_group_meta.unit_family if unit_group_meta else unit_family_hint
                if not unit and unit_family_hint:
                    unit = _default_unit_for_family(unit_family_hint)
                reference_unit = unit_group_meta.reference_unit if unit_group_meta else unit
                normalised_amount, final_unit_family = normalise_amount(amount, unit, unit_family)
                flow_document = registry.get_flow_document(flow_meta.uuid) if flow_meta and flow_meta.uuid else None
                flow_name_zh = _compose_flow_name(flow_document, "zh")
                flow_name_en = _compose_flow_name(flow_document, "en")
                exchange_name_zh = _extract_localised_exchange_name(exchange, "zh")
                exchange_name_en = _extract_localised_exchange_name(exchange, "en")
                exchange_display = _combine_bilingual(exchange_name_zh, exchange_name_en)
                flow_display = _combine_bilingual(flow_name_zh, flow_name_en)
                flow_name = flow_display or _resolve_flow_name(flow_meta, exchange)
                exchange_name = exchange_display or flow_name or _resolve_flow_name(flow_meta, exchange)
                prepared_entries.append(
                    {
                        "dataset_uuid": str(process_uuid) if process_uuid else "unknown",
                        "dataset_name": dataset_name,
                        "dataset_context": dataset_context,
                        "exchange": exchange,
                        "flow_meta": flow_meta,
                        "flow_document": flow_document,
                        "flow_name": flow_name,
                        "flow_name_zh": flow_name_zh,
                        "flow_name_en": flow_name_en,
                        "exchange_name": exchange_name,
                        "exchange_name_zh": exchange_name_zh,
                        "exchange_name_en": exchange_name_en,
                        "amount": normalised_amount,
                        "raw_amount": _coerce_float_value(amount),
                        "unit": unit,
                        "unit_family": final_unit_family,
                        "reference_unit": reference_unit,
                        "flow_uuid": flow_meta.uuid if flow_meta else None,
                        "flow_type": flow_meta.flow_type if flow_meta else None,
                        "direction": (exchange.get("exchangeDirection") or "").lower() or None,
                        "allocation_factor": _extract_allocation_factor(exchange),
                        "is_reference_flow": _is_reference_exchange(exchange, reference_flow_ids),
                    }
                )

            reference_context = _build_reference_context(prepared_entries)
            for entry in prepared_entries:
                classification = self._classify_entry(entry, reference_context)
                yield ExchangeRecord(
                    dataset_uuid=entry["dataset_uuid"],
                    dataset_name=entry["dataset_name"],
                    exchange=entry["exchange"],
                    flow_name=entry["flow_name"],
                    exchange_name=entry["exchange_name"],
                    exchange_name_zh=entry["exchange_name_zh"],
                    exchange_name_en=entry["exchange_name_en"],
                    flow_name_zh=entry["flow_name_zh"],
                    flow_name_en=entry["flow_name_en"],
                    amount=entry["amount"],
                    unit=entry["unit"],
                    unit_family=entry["unit_family"],
                    flow_uuid=entry["flow_uuid"],
                    flow_type=entry["flow_type"],
                    flow_class=classification.label,
                    direction=entry["direction"],
                    reference_unit=entry["reference_unit"],
                    classification_confidence=classification.confidence,
                    classification_reason=classification.rationale,
                )

    def _build_unknown_entries(self, exchanges: Iterable[ExchangeRecord]) -> list[UnknownClassification]:
        entries: list[UnknownClassification] = []
        for record in exchanges:
            if record.flow_class == "unknown":
                entries.append(
                    UnknownClassification(
                        exchange_name=record.flow_name,
                        dataset_uuid=record.dataset_uuid,
                        reason=record.classification_reason or "未匹配到分类规则",
                    )
                )
        return entries

    def _build_notes(self, exchanges: Iterable[ExchangeRecord]) -> list[str]:
        notes: list[str] = []
        if any(record.unit_family is None for record in exchanges):
            notes.append("unit_family_missing: flowProperty/unit 信息缺失，已按原始单位累计")
        return notes

    @staticmethod
    def _apply_reference_counts(
        result: LifecycleFlowPrioritizationResult,
        stats: dict[str, ReferenceFlowUsage],
    ) -> None:
        def _assign_counts(slices: list[PrioritySlice]) -> None:
            for slice_ in slices:
                if slice_.flow_uuid:
                    usage = stats.get(slice_.flow_uuid)
                    if usage:
                        slice_.reference_process_count = usage.process_count

        _assign_counts(result.raw_materials)
        _assign_counts(result.energy)
        _assign_counts(result.auxiliaries)

    @staticmethod
    def _write_reference_stats_file(stats: dict[str, ReferenceFlowUsage], path: Path) -> None:
        if not stats:
            return
        payload = {
            "flows": {
                flow_uuid: {
                    "process_count": usage.process_count,
                    "process_ids": usage.process_ids,
                }
                for flow_uuid, usage in stats.items()
            },
            "flows_with_dependents": len(stats),
            "total_processes": sum(usage.process_count for usage in stats.values()),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _classify_entry(self, entry: dict[str, Any], reference_context: dict[str, Any]) -> ClassificationResult:
        flow_meta = entry["flow_meta"]
        if entry.get("is_reference_flow"):
            return ClassificationResult("product_output", 0.99, "process reference flow")
        by_product_result = _detect_by_product(entry, reference_context)
        if by_product_result:
            return by_product_result
        return self._classifier.classify(
            entry["exchange"],
            flow_meta,
            flow_document=entry["flow_document"],
            dataset_context=entry["dataset_context"],
        )


# --------------------------------------------------------------------------- helpers

def _default_unit_for_family(unit_family: str | None) -> str | None:
    if not unit_family:
        return None
    mapping = {
        "mass": "kg",
        "energy": "MJ",
        "item": "item",
        "volume": "m3",
        "area": "m2",
        "length": "m",
    }
    return mapping.get(unit_family)

def _resolve_dataset_name(process_dataset: dict[str, Any]) -> str | None:
    name_block = _dig(
        process_dataset,
        "processInformation",
        "dataSetInformation",
        "name",
    )
    if name_block is None:
        return None
    zh = _compose_name_from_block(name_block, "zh")
    en = _compose_name_from_block(name_block, "en")
    combined = _combine_bilingual(zh, en)
    if combined:
        return combined
    if isinstance(name_block, dict):
        base = name_block.get("baseName")
        if isinstance(base, list) and base:
            text = base[0].get("#text") if isinstance(base[0], dict) else base[0]
            if isinstance(text, str):
                return text
    if isinstance(name_block, str):
        return name_block
    return None


def _build_dataset_context(
    process_dataset: dict[str, Any],
    process_uuid: Any,
    dataset_name: str | None,
) -> DatasetContext:
    intended = _collect_strings(
        _dig(
            process_dataset,
            "processInformation",
            "dataSetInformation",
            "common:intendedApplications",
        )
    )
    technology = _collect_strings(
        _dig(
            process_dataset,
            "processInformation",
            "technology",
            "technologyDescriptionAndIncludedProcesses",
        )
    )
    process_info = process_dataset.get("processInformation")
    modelling = process_dataset.get("modellingAndValidation")
    return DatasetContext(
        uuid=str(process_uuid) if process_uuid else None,
        name=dataset_name,
        intended_applications=intended or None,
        technology_notes=technology or None,
        process_information=process_info if isinstance(process_info, dict) else None,
        modelling_and_validation=modelling if isinstance(modelling, dict) else None,
    )


def _resolve_flow_name(flow_meta: Any, exchange: dict[str, Any]) -> str | None:
    if flow_meta and getattr(flow_meta, "name", None):
        return flow_meta.name
    reference = exchange.get("referenceToFlowDataSet")
    if isinstance(reference, dict):
        short_desc = reference.get("common:shortDescription")
        text = _coerce_text(short_desc)
        if text:
            return text
    exchange_name = exchange.get("exchangeName")
    if isinstance(exchange_name, str) and exchange_name.strip():
        return exchange_name.strip()
    return None


def _extract_exchanges(process_dataset: dict[str, Any]) -> list[dict[str, Any]]:
    exchanges = _dig(process_dataset, "exchanges", "exchange")
    if isinstance(exchanges, list):
        return [ex for ex in exchanges if isinstance(ex, dict)]
    return []


def _dig(node: Any, *keys: str) -> Any:
    current = node
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _coerce_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        text = value.get("#text") or value.get("text")
        if isinstance(text, str):
            return text
    if isinstance(value, list):
        for item in value:
            text = _coerce_text(item)
            if text:
                return text
    return None


def _collect_strings(node: Any) -> list[str]:
    results: list[str] = []
    if node is None:
        return results
    if isinstance(node, str):
        text = node.strip()
        if text:
            results.append(text)
        return results
    if isinstance(node, dict):
        text = _coerce_text(node)
        if text:
            results.append(text.strip())
        for value in node.values():
            results.extend(_collect_strings(value))
        return _deduplicate(results)
    if isinstance(node, list):
        for item in node:
            results.extend(_collect_strings(item))
        return _deduplicate(results)
    return results


def _deduplicate(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _extract_reference_flow_ids(process_dataset: dict[str, Any]) -> set[str]:
    quantitative_ref = _dig(process_dataset, "processInformation", "quantitativeReference")
    if not isinstance(quantitative_ref, dict):
        return set()
    ref = quantitative_ref.get("referenceToReferenceFlow")
    ids: set[str] = set()
    if isinstance(ref, list):
        for item in ref:
            value = _coerce_text(item)
            if value:
                ids.add(value)
    elif isinstance(ref, dict):
        value = ref.get("@dataSetInternalID") or ref.get("#text") or ref.get("id")
        if isinstance(value, str) and value.strip():
            ids.add(value.strip())
    elif isinstance(ref, str) and ref.strip():
        ids.add(ref.strip())
    return ids


def _is_reference_exchange(exchange: dict[str, Any], reference_ids: set[str]) -> bool:
    if not reference_ids:
        return False
    identifier = exchange.get("@dataSetInternalID") or exchange.get("dataSetInternalID")
    if isinstance(identifier, str) and identifier.strip() in reference_ids:
        return True
    return False


def _build_reference_context(entries: list[dict[str, Any]]) -> dict[str, Any]:
    for entry in entries:
        if entry.get("is_reference_flow"):
            amount = entry.get("amount")
            if amount is None:
                amount = entry.get("raw_amount")
            return {
                "amount": amount,
                "unit_family": entry.get("unit_family"),
            }
    return {"amount": None, "unit_family": None}


def _extract_allocation_factor(exchange: dict[str, Any]) -> float | None:
    allocations = exchange.get("allocations")
    if not isinstance(allocations, dict):
        return None
    allocation = allocations.get("allocation")
    candidates = allocation if isinstance(allocation, list) else [allocation]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in ("fraction", "value", "fractionAmount", "allocationFactor", "@value"):
            if key in candidate:
                value = _coerce_float_value(candidate.get(key))
                if value is not None:
                    return value
        text_value = _coerce_float_value(candidate.get("#text"))
        if text_value is not None:
            return text_value
    return None


def _detect_by_product(entry: dict[str, Any], reference_context: dict[str, Any]) -> ClassificationResult | None:
    if entry.get("direction") != "output":
        return None
    flow_type = (entry.get("flow_type") or "").lower()
    if "product" not in flow_type:
        return None
    allocation = entry.get("allocation_factor")
    if allocation is not None and 0 < allocation < 0.5:
        return ClassificationResult("by_product", 0.9, f"allocation fraction≈{allocation:.3f}")
    reference_amount = reference_context.get("amount")
    reference_family = reference_context.get("unit_family")
    if (
        reference_amount
        and reference_amount > 0
        and entry.get("unit_family")
        and reference_family
        and entry["unit_family"] == reference_family
    ):
        amount = entry.get("amount") or entry.get("raw_amount")
        if amount is not None and amount >= 0:
            ratio = amount / reference_amount
            if ratio < 0.5:
                return ClassificationResult("by_product", 0.75, f"output amount≈{ratio:.3f}× reference product")
    return None


def _coerce_float_value(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    if isinstance(value, dict):
        return _coerce_float_value(value.get("#text") or value.get("text"))
    return None


def _extract_localised_flow_name(flow_document: dict[str, Any] | None, lang: str) -> str | None:
    if not flow_document:
        return None
    dataset = flow_document.get("flowDataSet") if isinstance(flow_document, dict) else None
    source = dataset if isinstance(dataset, dict) else flow_document
    name_block = _dig(source, "flowInformation", "dataSetInformation", "name")
    return _extract_localised_text(name_block, lang)


def _extract_localised_exchange_name(exchange: dict[str, Any], lang: str) -> str | None:
    name_block = exchange.get("name")
    text = _extract_localised_text(name_block, lang)
    if text:
        return text
    reference = exchange.get("referenceToFlowDataSet")
    if isinstance(reference, dict):
        return _extract_localised_text(reference.get("common:shortDescription"), lang)
    fallback = exchange.get("exchangeName")
    if isinstance(fallback, str) and fallback.strip():
        return fallback.strip()
    return None


def _extract_localised_text(node: Any, lang: str) -> str | None:
    if node is None:
        return None
    if isinstance(node, str):
        return node if not lang else None
    if isinstance(node, list):
        for item in node:
            text = _extract_localised_text(item, lang)
            if text:
                return text
        return None
    if isinstance(node, dict):
        lang_tag = node.get("@xml:lang") or node.get("lang")
        text_value = node.get("#text") or node.get("text")
        if lang_tag and text_value and lang_tag.lower().startswith(lang):
            return text_value
        if "baseName" in node:
            return _extract_localised_text(node.get("baseName"), lang)
        for value in node.values():
            if isinstance(value, (dict, list)):
                text = _extract_localised_text(value, lang)
                if text:
                    return text
    return None


def _combine_bilingual(zh: str | None, en: str | None) -> str | None:
    zh = zh.strip() if isinstance(zh, str) else None
    en = en.strip() if isinstance(en, str) else None
    if zh and en and zh.lower() != en.lower():
        return f"{zh}, {en}"
    return zh or en


def _compose_flow_name(flow_document: dict[str, Any] | None, lang: str) -> str | None:
    if not flow_document:
        return None
    dataset = flow_document.get("flowDataSet") if isinstance(flow_document, dict) else None
    source = dataset if isinstance(dataset, dict) else flow_document
    name_block = _dig(source, "flowInformation", "dataSetInformation", "name")
    return _compose_name_from_block(name_block, lang)


def _compose_name_from_block(name_block: Any, lang: str) -> str | None:
    if name_block is None:
        return None
    if not isinstance(name_block, dict):
        return _extract_localised_text(name_block, lang)
    components: list[str] = []
    for key in ("baseName", "treatmentStandardsRoutes", "mixAndLocationTypes", "functionalUnitFlowProperties"):
        value = name_block.get(key)
        text = _extract_localised_text(value, lang)
        if text:
            components.append(text)
    if components:
        return "; ".join(components)
    return _extract_localised_text(name_block, lang)
