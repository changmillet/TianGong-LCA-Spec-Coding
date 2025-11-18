from __future__ import annotations

from pathlib import Path

from tiangong_lca_spec.core.llm import LanguageModelProtocol
from tiangong_lca_spec.lci_analysis.common.classifier_cache import ClassifierCache
from tiangong_lca_spec.lci_analysis.common.flows import FlowMetadata
from tiangong_lca_spec.lci_analysis.upstream.classifiers import DatasetContext, FlowClassifier, LLMFlowClassifier
from tiangong_lca_spec.lci_analysis.upstream.classifiers.rules import classify_with_rules


class _FakeLLM(LanguageModelProtocol):
    def __init__(self, response: str) -> None:
        self._response = response
        self.calls = 0

    def invoke(self, input_data):
        self.calls += 1
        return self._response


def test_flow_classifier_prefers_rules_when_available():
    classifier = FlowClassifier()
    flow_meta = FlowMetadata(uuid="uuid", name="Iron ore", flow_type="Product flow", unit_group_ref=None)
    exchange = {"exchangeDirection": "input"}

    result = classifier.classify(exchange, flow_meta)

    assert result.label == "raw_material"


def test_flow_classifier_falls_back_to_llm_and_uses_cache(tmp_path: Path):
    cache = ClassifierCache(tmp_path / "flow_classifier_cache.json")
    fake_llm = _FakeLLM('{"class_label": "energy", "confidence": 0.9, "rationale": "LLM"}')
    llm_classifier = LLMFlowClassifier(fake_llm, prompt="classify", cache=cache)
    classifier = FlowClassifier(llm_classifier=llm_classifier)
    flow_meta = FlowMetadata(uuid="flow-1", name="Mystery stream", flow_type=None, unit_group_ref=None)
    exchange = {"exchangeDirection": "output"}

    dataset_context = DatasetContext(uuid="dataset", name="Process A")
    result1 = classifier.classify(exchange, flow_meta, dataset_context=dataset_context)
    result2 = classifier.classify(exchange, flow_meta, dataset_context=dataset_context)

    assert result1.label == "energy"
    assert result2.label == "energy"
    assert fake_llm.calls == 1  # second call served from cache


def test_elementary_flow_uses_classification_for_emission():
    flow_meta = FlowMetadata(
        uuid="flow-elem",
        name="CO2",
        flow_type="Elementary flow",
        unit_group_ref=None,
        classifications=("Emissions to air",),
        is_elementary=True,
    )
    exchange = {"exchangeDirection": "output"}

    result = classify_with_rules(exchange, flow_meta)

    assert result.label == "emission"
    assert result.confidence >= 0.9


def test_product_flow_outputs_default_to_product_output():
    classifier = FlowClassifier()
    flow_meta = FlowMetadata(uuid="flow-prod", name="Slag coproduct", flow_type="Product flow", unit_group_ref=None)
    exchange = {"exchangeDirection": "output"}

    result = classifier.classify(exchange, flow_meta)

    assert result.label == "product_output"


def test_flow_classification_emission_overrides_direction():
    classifier = FlowClassifier()
    flow_meta = FlowMetadata(
        uuid="flow-co2",
        name="carbon dioxide (fossil)",
        flow_type="Elementary flow",
        unit_group_ref=None,
        classifications=("Emissions to air",),
        is_elementary=False,
    )
    exchange = {"exchangeDirection": "output"}

    result = classifier.classify(exchange, flow_meta)

    assert result.label == "emission"


def test_output_flow_uses_classification_level_for_emission():
    classifier = FlowClassifier()
    flow_meta = FlowMetadata(
        uuid="flow-stack",
        name="Stack gas",
        flow_type="Product flow",
        unit_group_ref=None,
        classification_levels={"1": "Emissions to air"},
    )
    exchange = {"exchangeDirection": "output"}

    result = classifier.classify(exchange, flow_meta)

    assert result.label == "emission"


def test_flow_classification_resource_overrides_direction():
    classifier = FlowClassifier()
    flow_meta = FlowMetadata(
        uuid="flow-coal",
        name="Hard coal",
        flow_type="Product flow",
        unit_group_ref=None,
        classifications=("Natural resources/fossil",),
    )
    exchange = {"exchangeDirection": "input"}

    result = classifier.classify(exchange, flow_meta)

    assert result.label == "resource"
