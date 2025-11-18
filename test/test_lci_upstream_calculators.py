"""Unit tests for lifecycle flow prioritisation calculators."""

from __future__ import annotations

from tiangong_lca_spec.lci_analysis.upstream.calculators import (
    accumulate_role_totals,
    build_default_actions,
    build_downstream_priority_slices,
    build_priority_slices,
)
from tiangong_lca_spec.lci_analysis.upstream.models import ExchangeRecord, PrioritySlice


def _record(
    *,
    amount: float,
    flow_class: str,
    flow_name: str,
    direction: str,
) -> ExchangeRecord:
    return ExchangeRecord(
        dataset_uuid="dataset",
        dataset_name="dataset",
        exchange={},
        flow_name=flow_name,
        exchange_name=flow_name,
        exchange_name_zh=flow_name,
        exchange_name_en=flow_name,
        flow_name_zh=flow_name,
        flow_name_en=flow_name,
        amount=amount,
        unit="kg",
        unit_family="mass",
        flow_uuid="flow",
        flow_type="Product flow",
        flow_class=flow_class,
        direction=direction,
        reference_unit="kg",
        classification_confidence=0.8,
        classification_reason="rule",
    )


def test_build_priority_slices_respects_threshold_per_unit():
    records = [
        _record(amount=60, flow_class="raw_material", flow_name="A", direction="input"),
        _record(amount=30, flow_class="raw_material", flow_name="B", direction="input"),
        _record(amount=10, flow_class="raw_material", flow_name="C", direction="input"),
    ]
    totals = accumulate_role_totals(records)

    slices = build_priority_slices(records, "raw_material", totals, cumulative_threshold=0.9)

    assert len(slices) == 2  # third item would exceed 90% threshold
    assert slices[0].exchange_name == "A"
    assert round(slices[0].share or 0, 2) == 0.6
    assert slices[1].cumulative_share and slices[1].cumulative_share > 0.8


def test_build_downstream_priority_slices_orders_all_records():
    records = [
        _record(amount=5, flow_class="waste", flow_name="二氧化碳", direction="output"),
        _record(amount=2, flow_class="product_output", flow_name="交流电", direction="output"),
        _record(amount=1, flow_class="waste", flow_name="废水", direction="output"),
    ]

    totals = accumulate_role_totals(records)
    slices = build_downstream_priority_slices(records, totals)

    assert set(slice_.exchange_name for slice_ in slices) == {"二氧化碳", "交流电", "废水"}
    co2_slice = next(item for item in slices if item.exchange_name == "二氧化碳")
    assert co2_slice.downstream_path == "unknown"
    assert any(item.flow_role == "product_output" for item in slices)
    assert any(item.flow_role == "waste" for item in slices)


def test_build_default_actions_uses_category_context():
    raw_slice = PrioritySlice(
        dataset_uuid="dataset",
        dataset_name="dataset",
        exchange_name="水泥",
        exchange_name_zh="水泥",
        exchange_name_en="cement",
        flow_name_zh="水泥",
        flow_name_en="cement",
        flow_uuid="flow",
        flow_role="raw_material",
        unit_family="mass",
        reference_unit="kg",
        total_amount=100,
        share=0.5,
        cumulative_share=0.5,
        classification_confidence=0.8,
        rationale="rule",
        exchanges=[],
    )
    energy_slice = PrioritySlice(
        dataset_uuid="dataset",
        dataset_name="dataset",
        exchange_name="电力",
        exchange_name_zh="电力",
        exchange_name_en="electricity",
        flow_name_zh="电力",
        flow_name_en="electricity",
        flow_uuid="flow",
        flow_role="energy",
        unit_family="energy",
        reference_unit="MJ",
        total_amount=50,
        share=0.4,
        cumulative_share=0.4,
        classification_confidence=0.8,
        rationale="rule",
        exchanges=[],
    )
    downstream_slice = PrioritySlice(
        dataset_uuid="dataset",
        dataset_name="dataset",
        exchange_name="二氧化碳",
        exchange_name_zh="二氧化碳",
        exchange_name_en="CO2",
        flow_name_zh="二氧化碳",
        flow_name_en="CO2",
        flow_uuid="flow-co2",
        flow_role="waste",
        unit_family="mass",
        reference_unit="kg",
        total_amount=10,
        share=0.6,
        cumulative_share=0.6,
        downstream_path="unknown",
        downstream_action="校对大气排放量并关联许可/减排措施",
        exchanges=[],
    )

    actions = build_default_actions([raw_slice], [energy_slice], [downstream_slice])

    assert any(action.type == "upstream" for action in actions)
    assert any(action.type == "downstream" for action in actions)


def test_build_priority_slices_groups_by_unit_family():
    records = [
        _record(amount=50, flow_class="energy", flow_name="电力A", direction="input"),
        _record(amount=30, flow_class="energy", flow_name="电力B", direction="input"),
        ExchangeRecord(
            dataset_uuid="dataset",
            dataset_name="dataset",
            exchange={},
            flow_name="天然气",
            exchange_name="天然气",
            exchange_name_zh="天然气",
            exchange_name_en="natural gas",
            flow_name_zh="天然气",
            flow_name_en="natural gas",
            amount=5,
            unit="m3",
            unit_family="volume",
            flow_uuid="flow",
            flow_type="Product flow",
            flow_class="energy",
            direction="input",
            reference_unit="m3",
            classification_confidence=0.8,
            classification_reason="rule",
        ),
    ]
    totals = accumulate_role_totals(records)

    slices = build_priority_slices(records, "energy", totals)

    assert len(slices) == 3
    first, second, third = slices
    assert first.unit_family == "mass"
    assert third.unit_family == "volume"
    assert round(first.share or 0, 2) == 0.62  # 50 / (50+30)
    assert round(third.share or 0, 2) == 1.0  # only entry in its unit family
