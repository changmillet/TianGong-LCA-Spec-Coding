"""Rule-based flow classification used before any LLM invocation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tiangong_lca_spec.lci_analysis.common.flows import FlowMetadata

UPSTREAM_CLASSES = (
    "raw_material",
    "energy",
    "auxiliary",
    "waste",
    "product_output",
    "by_product",
    "resource",
    "emission",
    "unknown",
)

ENERGY_TERMS = (
    "electric",
    "electricity",
    "power",
    "steam",
    "heat",
    "fuel",
    "gas",
    "coal",
    "diesel",
    "汽油",
    "柴油",
    "电力",
    "燃料",
    "蒸汽",
)
AUXILIARY_TERMS = (
    "water",
    "nitrogen",
    "oxygen",
    "lubricant",
    "catalyst",
    "solvent",
    "additive",
    "packaging",
    "cooling",
    "洗涤剂",
    "润滑油",
    "氮气",
    "氧气",
)
WASTE_HINT_TERMS = (
    "waste",
    "landfill",
    "emission",
    "排放",
    "尾矿",
    "废水",
    "废渣",
)
PRODUCT_TERMS = ("product", "electricity", "交流电", "供电")


@dataclass(slots=True)
class ClassificationResult:
    label: str
    confidence: float
    rationale: str


def classify_with_rules(
    exchange: dict[str, Any],
    flow_meta: FlowMetadata | None,
) -> ClassificationResult:
    """Return the inferred class label for a single exchange."""

    direction = (exchange.get("exchangeDirection") or "").lower()
    flow_type = (flow_meta.flow_type.lower() if flow_meta and flow_meta.flow_type else "") or ""
    flow_name_source = flow_meta.name if flow_meta and flow_meta.name else exchange.get("exchangeName")
    flow_name = (flow_name_source or "").lower()
    hints_value = exchange.get("generalComment") or ""
    hints = _normalise_text(hints_value)
    classifications = tuple(flow_meta.classifications or ()) if flow_meta else ()
    unit_family = getattr(flow_meta, "unit_family", None) if flow_meta else None

    classification_levels = flow_meta.classification_levels if flow_meta else None
    level1_label = _get_level_label(classification_levels, "1")
    if direction == "output" and level1_label and "emission" in level1_label.lower():
        return ClassificationResult("emission", 0.95, f"flow 分类 level1 标记为 {level1_label}")

    if flow_meta and flow_meta.is_elementary:
        label, rationale = _classify_elementary_flow(classifications)
        confidence = 0.95 if rationale != "elementary_flow_default" else 0.6
        final_rationale = (
            "Elemental flow 分类标记为排放/资源" if rationale != "elementary_flow_default" else "Elemental flow 缺少分类标签，默认视为 emission"
        )
        if rationale not in {"elementary_flow_default"}:
            final_rationale = f"Elemental flow 分类命中 {rationale}"
        return ClassificationResult(label, confidence, final_rationale)

    classification_result = _classify_by_flow_classifications(classifications)
    if classification_result:
        return classification_result

    if unit_family == "energy":
        return ClassificationResult("energy", 0.85, "单位族为 energy，判定为能源流")

    # Waste detection first to avoid leaking into other categories
    if "waste" in flow_type:
        return ClassificationResult("waste", 0.95, "flowType 标记为 Waste flow")
    if any(term in flow_name for term in WASTE_HINT_TERMS):
        return ClassificationResult("waste", 0.9, "名称包含废弃/排放相关关键词")

    if direction == "output" and flow_type == "product flow":
        if any(term in flow_name for term in PRODUCT_TERMS):
            return ClassificationResult("product_output", 0.95, "输出方向且 flowType=Product flow")
        return ClassificationResult("product_output", 0.85, "输出方向且 flowType=Product flow，默认作为可售产品处理")

    if direction == "output" and any(term in hints for term in WASTE_HINT_TERMS):
        return ClassificationResult("waste", 0.8, "输出流备注包含排放/废弃提示")

    if any(term in flow_name for term in ENERGY_TERMS) or any(term in hints for term in ("energy", "电力", "燃料")):
        return ClassificationResult("energy", 0.85, "名称/备注匹配能源相关关键词")

    if any(term in flow_name for term in AUXILIARY_TERMS) or any(term in hints for term in ("cooling", "maintenance")):
        return ClassificationResult("auxiliary", 0.75, "名称包含公用工程/辅料关键词")

    if direction == "input":
        return ClassificationResult("raw_material", 0.7, "输入方向默认判定为原材料")

    return ClassificationResult("unknown", 0.4, "未匹配到任何启发式规则")


def _classify_elementary_flow(classifications: tuple[str, ...]) -> tuple[str, str]:
    lowered = [(label, label.lower()) for label in classifications]
    for original, label in lowered:
        if any(term in label for term in ("emission", "air", "water", "soil")):
            return "emission", original
    for original, label in lowered:
        if "resource" in label or "natural resource" in label:
            return "resource", original
    return "emission", "elementary_flow_default"


def _classify_by_flow_classifications(classifications: tuple[str, ...]) -> ClassificationResult | None:
    lowered = [(label, label.lower()) for label in classifications]
    for original, label in lowered:
        if any(term in label for term in ("emission", "air emission", "water emission", "soil emission")):
            return ClassificationResult("emission", 0.9, f"flow classification包含 {original}")
    for original, label in lowered:
        if "resource" in label or "natural resource" in label:
            return ClassificationResult("resource", 0.8, f"flow classification包含 {original}")
    return None


def _normalise_text(value: Any) -> str:
    if isinstance(value, str):
        return value.lower()
    if isinstance(value, dict):
        text = value.get("#text") or value.get("text") or ""
        return text.lower()
    if isinstance(value, list) and value:
        first = value[0]
        return _normalise_text(first)
    return ""


def _get_level_label(levels: dict[str, str] | None, level: str) -> str | None:
    if not levels:
        return None
    key = str(level).strip()
    if not key:
        return None
    value = levels.get(key)
    return value
