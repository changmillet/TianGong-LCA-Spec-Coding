from tiangong_lca_spec.lci_analysis.upstream.workflow import (
    _build_reference_context,
    _detect_by_product,
)
from tiangong_lca_spec.lci_analysis.upstream.classifiers.rules import ClassificationResult


def test_detect_by_product_allocation_fraction():
    entry = {
        "direction": "output",
        "flow_type": "Product flow",
        "allocation_factor": 0.2,
        "unit_family": "mass",
        "amount": 1.0,
        "raw_amount": 1.0,
    }
    reference = {"amount": 10.0, "unit_family": "mass"}

    result = _detect_by_product(entry, reference)

    assert isinstance(result, ClassificationResult)
    assert result.label == "by_product"
    assert "allocation" in result.rationale


def test_detect_by_product_ratio():
    entry = {
        "direction": "output",
        "flow_type": "Product flow",
        "allocation_factor": None,
        "unit_family": "energy",
        "amount": 1.0,
        "raw_amount": 1.0,
    }
    reference = {"amount": 5.0, "unit_family": "energy"}

    result = _detect_by_product(entry, reference)

    assert isinstance(result, ClassificationResult)
    assert result.label == "by_product"
    assert "output amount" in result.rationale


def test_build_reference_context_picks_reference_entry():
    entries = [
        {"is_reference_flow": False, "amount": 2.0, "unit_family": "mass"},
        {"is_reference_flow": True, "amount": 4.0, "unit_family": "energy"},
    ]

    context = _build_reference_context(entries)

    assert context["amount"] == 4.0
    assert context["unit_family"] == "energy"
