from __future__ import annotations

import json
from pathlib import Path

import pytest

from tiangong_lca_spec.process_update import (
    ProcessRepositoryClient,
    ProcessWriteWorkflow,
    RequirementLoader,
)
from tiangong_lca_spec.process_update.translation import PagesProcessTranslationLoader
from tiangong_lca_spec.process_update.updater import ProcessJsonUpdater


class CollectingLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def log(self, message: str) -> None:
        self.messages.append(message)


class FakeMCPClient:
    def __init__(self, payloads: dict[tuple[str, str], object]) -> None:
        self.payloads = payloads
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def invoke_json_tool(self, server_name: str, tool_name: str, arguments: dict | None = None):
        self.calls.append((server_name, tool_name, arguments or {}))
        key = (server_name, tool_name)
        if key not in self.payloads:
            raise AssertionError(f"No payload configured for {key}")
        return self.payloads[key]


@pytest.fixture(scope="module")
def requirement_bundle():
    loader = RequirementLoader()
    return loader.load(Path("test/requirement/write_data.yaml"))


@pytest.fixture(scope="module")
def translation_lookup():
    loader = PagesProcessTranslationLoader()
    return loader.load(Path("test/requirement/pages_process.ts"))


def test_requirement_loader_parses_multilang(requirement_bundle) -> None:
    mapping = {entry.label: entry for entry in requirement_bundle.global_updates}
    target = mapping["建模信息——数据切断和完整性原则"]
    assert target.is_multilang()
    pairs = list(target.language_values())
    assert [p.language for p in pairs] == ["zh", "en"]
    assert "本清单遵循了既定的切断规则" in pairs[0].text
    assert "This inventory follows" in pairs[1].text


def test_process_json_updater_applies_all_fields(translation_lookup, requirement_bundle) -> None:
    logger = CollectingLogger()

    class DummyResolver:
        def resolve(self, ref_id, ref_type=None):
            return None

    updater = ProcessJsonUpdater(translation_lookup, logger, resolver=DummyResolver())

    document = {"processDataSet": {}}
    updated = updater.apply(document, requirement_bundle)
    dataset = updated["processDataSet"]

    mv_section = dataset["modellingAndValidation"]["dataSourcesTreatmentAndRepresentativeness"]
    comment = mv_section["dataCutOffAndCompletenessPrinciples"]
    assert isinstance(comment, list)
    assert {entry["@xml:lang"] for entry in comment} == {"zh", "en"}

    admin = dataset["administrativeInformation"]
    commissioner_ref = admin["common:commissionerAndGoal"]["common:referenceToCommissioner"]
    assert commissioner_ref["@refObjectId"] == "f4b4c314-8c4c-4c83-968f-5b3c7724f6a8"
    assert commissioner_ref["@type"] == "Contact data set"
    assert commissioner_ref["@version"] == "00.00.000"
    assert commissioner_ref["@uri"].endswith("/f4b4c314-8c4c-4c83-968f-5b3c7724f6a8")

    publication = admin["publicationAndOwnership"]
    assert publication["common:copyright"] == "false"
    assert publication["common:licenseType"] == "Free of charge for all users and uses"
    assert logger.messages, "Expected placeholder notes to be logged for reference fields"


def test_updater_normalises_validation_and_compliance(
    translation_lookup, requirement_bundle
) -> None:
    logger = CollectingLogger()

    class DummyResolver:
        def resolve(self, ref_id, ref_type=None):
            return None

    updater = ProcessJsonUpdater(translation_lookup, logger, resolver=DummyResolver())

    document = {
        "processDataSet": {
            "modellingAndValidation": {
                "validation": {"review": {"@type": "Not reviewed"}},
                "complianceDeclarations": {
                    "compliance": [
                        {
                            "common:approvalOfOverallCompliance": "Fully compliant",
                            "common:nomenclatureCompliance": "Fully compliant",
                        },
                        {
                            "common:approvalOfOverallCompliance": "Not defined",
                            "common:nomenclatureCompliance": "Not defined",
                        },
                    ]
                },
            },
            "exchanges": {
                "exchange": [
                    {
                        "@dataSetInternalID": "1",
                        "exchangeDirection": "input",
                        "meanAmount": 1,
                        "resultingAmount": 1,
                        "allocations": {"allocation": {"@allocatedFraction": "100%"}},
                    }
                ]
            },
        }
    }
    updated = updater.apply(document, requirement_bundle)
    dataset = updated["processDataSet"]

    validation = dataset["modellingAndValidation"]["validation"]
    review = validation["review"]
    assert "scope" in review and review["scope"], "Expected review scope to be populated"
    assert validation["reviewDetails"]["#text"], "Review details should contain placeholder text"
    reviewer_ref = validation["common:referenceToNameOfReviewerAndInstitution"]
    assert reviewer_ref["@type"] == "Contact data set"
    report_ref = validation["common:referenceToCompleteReviewReport"]
    assert report_ref["@type"] == "Source data set"

    compliance = dataset["modellingAndValidation"]["complianceDeclarations"]["compliance"]
    assert isinstance(compliance, dict), "Compliance entry should be normalised to an object"
    assert "common:approvalOfOverallCompliance" in compliance

    exchange = dataset["exchanges"]["exchange"][0]
    allocation = exchange["allocations"]["allocation"]
    assert "@allocatedFraction" not in allocation, "Invalid allocated fraction should be removed"

    assert any("Compliance declarations provided as list" in msg for msg in logger.messages)


def test_process_write_workflow_creates_output(tmp_path: Path) -> None:
    repository_payloads = {
        ("service", "list"): {"json_ids": ["demo-process"]},
        ("service", "fetch"): {"processDataSet": {}},
    }
    fake_client = FakeMCPClient(repository_payloads)
    repository = ProcessRepositoryClient(
        fake_client, "service", list_tool_name="list", fetch_tool_name="fetch"
    )

    class DummyResolver:
        def resolve(self, ref_id, ref_type=None):
            return None

    workflow = ProcessWriteWorkflow(repository, resolver=DummyResolver())

    output_dir = tmp_path / "output"
    log_path = tmp_path / "workflow.log"
    written = workflow.run(
        user_id="user",
        requirement_path=Path("test/requirement/write_data.yaml"),
        translation_path=Path("test/requirement/pages_process.ts"),
        output_dir=output_dir,
        log_path=log_path,
        limit=1,
    )

    assert written == [output_dir / "demo-process.json"]
    content = json.loads(written[0].read_text(encoding="utf-8"))
    assert "processDataSet" in content
    assert log_path.exists()
    assert fake_client.calls[0][1] == "list"
    assert fake_client.calls[1][1] == "fetch"
