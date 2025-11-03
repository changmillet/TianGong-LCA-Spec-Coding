from __future__ import annotations

import json
from pathlib import Path

import pytest

from tiangong_lca_spec.process_update import ProcessWriteWorkflow, RequirementLoader
from tiangong_lca_spec.process_update.translation import PagesProcessTranslationLoader
from tiangong_lca_spec.process_update.updater import ProcessJsonUpdater
from tiangong_lca_spec.process_update.requirements import ProcessRequirement, RequirementBundle


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


class StubRepository:
    """In-memory repository stub for workflow tests."""

    def __init__(
        self,
        *,
        current_user_id: str,
        ids: list[str],
        records: dict[str, dict],
        documents: dict[str, dict],
    ) -> None:
        self._current_user_id = current_user_id
        self._ids = ids
        self._records = records
        self._documents = documents

    def detect_current_user_id(self) -> str | None:
        return self._current_user_id

    def list_json_ids(self, user_id: str) -> list[str]:
        return list(self._ids)

    def fetch_record(self, table: str, record_id: str):
        return json.loads(json.dumps(self._records.get(record_id)))

    def fetch_process_json(self, json_id: str) -> dict:
        return json.loads(json.dumps(self._documents[json_id]))


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
    assert review["@type"] == "Not reviewed"
    assert "scope" not in review
    assert "reviewDetails" not in validation
    assert "common:reviewDetails" not in validation
    assert "common:referenceToNameOfReviewerAndInstitution" not in validation
    assert "common:referenceToCompleteReviewReport" not in validation

    compliance = dataset["modellingAndValidation"]["complianceDeclarations"]["compliance"]
    assert isinstance(compliance, dict), "Compliance entry should be normalised to an object"
    assert "common:approvalOfOverallCompliance" in compliance

    exchange = dataset["exchanges"]["exchange"][0]
    allocation = exchange["allocations"]["allocation"]
    assert "@allocatedFraction" not in allocation, "Invalid allocated fraction should be removed"

    assert any("Compliance declarations provided as list" in msg for msg in logger.messages)


def test_process_write_workflow_creates_output(tmp_path: Path) -> None:
    class DummyResolver:
        def resolve(self, ref_id, ref_type=None):
            return None

    record = {
        "state_code": 0,
        "user_id": "user",
        "json": {"processDataSet": {}},
    }
    repository = StubRepository(
        current_user_id="user",
        ids=["demo-process"],
        records={"demo-process": record},
        documents={"demo-process": {"processDataSet": {}}},
    )
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


def test_updater_analyse_detects_missing_fields(
    translation_lookup, requirement_bundle
) -> None:
    logger = CollectingLogger()

    class DummyResolver:
        def resolve(self, ref_id, ref_type=None):
            return None

    updater = ProcessJsonUpdater(translation_lookup, logger, resolver=DummyResolver())
    analysis = updater.analyse({"processDataSet": {}}, requirement_bundle)

    assert analysis.needs_update()
    assert "建模信息——数据切断和完整性原则" in analysis.missing_global_fields
    assert analysis.describe_scope().startswith("global")


def test_updater_analyse_detects_satisfied_dataset(
    translation_lookup, requirement_bundle
) -> None:
    logger = CollectingLogger()

    class DummyResolver:
        def resolve(self, ref_id, ref_type=None):
            return None

    updater = ProcessJsonUpdater(translation_lookup, logger, resolver=DummyResolver())
    base_document = {"processDataSet": {}}
    populated = updater.apply(json.loads(json.dumps(base_document)), requirement_bundle)
    analysis = updater.analyse(json.loads(json.dumps(populated)), requirement_bundle)

    assert not analysis.needs_update()
    assert "global" in analysis.describe_scope()
    assert analysis.describe_scope().count("process") >= 1


def test_process_write_workflow_skips_when_satisfied(
    translation_lookup, requirement_bundle, tmp_path: Path
) -> None:
    logger = CollectingLogger()

    class DummyResolver:
        def resolve(self, ref_id, ref_type=None):
            return None

    updater = ProcessJsonUpdater(translation_lookup, logger, resolver=DummyResolver())
    populated = updater.apply({"processDataSet": {}}, requirement_bundle)

    record = {
        "state_code": 0,
        "user_id": "user",
        "json": json.loads(json.dumps(populated)),
    }
    repository = StubRepository(
        current_user_id="user",
        ids=["demo-process"],
        records={"demo-process": record},
        documents={"demo-process": json.loads(json.dumps(populated))},
    )
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

    assert written == []
    assert log_path.exists()
    content = log_path.read_text(encoding="utf-8")
    assert "requirements satisfied" in content


def test_requirement_loader_supports_templates_and_bindings() -> None:
    loader = RequirementLoader()
    bundle = loader.load(Path("test/requirement/write_data_xx.yaml"))

    assert bundle.global_updates == []
    assert bundle.process_updates == []
    expected_ids = {
        "d7b7144f-4fcd-4c6d-9c52-16824edc008b",
        "012c079b-e14f-421b-94af-7b2d962721a4",
        "aef876a0-dd1f-4e00-97c5-3e59a0cfb6ac",
        "6a51981c-26bf-49c6-bebc-be1c1b934df6",
        "7b12c46d-8070-4b1c-8325-dd4143fccbb2",
        "4ac49ee2-b0fc-43a7-97fe-22aaa1750738",
    }
    assert set(bundle.uuid_bindings) == expected_ids

    first = bundle.uuid_bindings["d7b7144f-4fcd-4c6d-9c52-16824edc008b"]
    second = bundle.uuid_bindings["012c079b-e14f-421b-94af-7b2d962721a4"]
    assert first is not second
    assert first.template_name == "process_1_updates"
    assert second.template_name == "process_1_updates"
    assert any(field.label == "过程信息——混合和位置类型" for field in bundle.uuid_bindings["aef876a0-dd1f-4e00-97c5-3e59a0cfb6ac"].fields)

    narrowed = bundle.for_json_id("d7b7144f-4fcd-4c6d-9c52-16824edc008b")
    assert len(narrowed.process_updates) == 1
    assert narrowed.process_updates[0].template_name == "process_1_updates"


def test_process_write_workflow_skips_read_only(tmp_path: Path) -> None:
    repository = StubRepository(
        current_user_id="user",
        ids=["demo-process"],
        records={
            "demo-process": {
                "state_code": 100,
                "user_id": "user",
                "json": {"processDataSet": {}},
            }
        },
        documents={"demo-process": {"processDataSet": {}}},
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
    assert written == []
    assert log_path.exists()
    assert "state_code=100" in log_path.read_text(encoding="utf-8")


def test_process_name_matching_supports_partial_segments(translation_lookup) -> None:
    logger = CollectingLogger()

    class DummyResolver:
        def resolve(self, ref_id, ref_type=None):
            return None

    updater = ProcessJsonUpdater(translation_lookup, logger, resolver=DummyResolver())
    document = {
        "processDataSet": {
            "processInformation": {
                "dataSetInformation": {
                    "name": {
                        "baseName": [
                            {"@xml:lang": "zh", "#text": "风电场运维"},
                            {"@xml:lang": "en", "#text": "wind farm operation"},
                        ],
                        "treatmentStandardsRoutes": [
                            {"@xml:lang": "zh", "#text": "陆上/海上"},
                            {"@xml:lang": "en", "#text": "onshore/offshore"},
                        ],
                        "mixAndLocationTypes": [
                            {"@xml:lang": "zh", "#text": "陆/海上风电"},
                            {"@xml:lang": "en", "#text": "onshore/offshore wind"},
                        ],
                        "functionalUnitFlowProperties": [
                            {"@xml:lang": "zh", "#text": "5 MW"},
                            {"@xml:lang": "en", "#text": "5 MW"},
                        ],
                    }
                }
            }
        }
    }
    requirements = RequirementBundle(
        global_updates=[],
        process_updates=[
            ProcessRequirement(
                process_name="风电场运维; 陆/海上风电",
                fields=[],
                exchange_updates=[],
            )
        ],
        uuid_bindings={},
    )

    analysis = updater.analyse(document, requirements)
    assert analysis.matched_process_name == "风电场运维; 陆/海上风电"
    assert not analysis.needs_update()


def test_process_name_matching_supports_wildcards(translation_lookup) -> None:
    logger = CollectingLogger()

    class DummyResolver:
        def resolve(self, ref_id, ref_type=None):
            return None

    updater = ProcessJsonUpdater(translation_lookup, logger, resolver=DummyResolver())
    document = {
        "processDataSet": {
            "processInformation": {
                "dataSetInformation": {
                    "name": {
                        "baseName": {"@xml:lang": "zh", "#text": "风力发电机组制造"},
                        "treatmentStandardsRoutes": {"@xml:lang": "zh", "#text": "陆上风电"},
                        "mixAndLocationTypes": {"@xml:lang": "zh", "#text": "生产组合"},
                        "functionalUnitFlowProperties": {"@xml:lang": "zh", "#text": "8 MW"},
                    }
                }
            }
        }
    }
    requirements = RequirementBundle(
        global_updates=[],
        process_updates=[
            ProcessRequirement(
                process_name="风力发电机组制造; *; 生产组合",
                fields=[],
                exchange_updates=[],
            )
        ],
        uuid_bindings={},
    )

    analysis = updater.analyse(document, requirements)
    assert analysis.matched_process_name == "风力发电机组制造; *; 生产组合"


def test_process_write_workflow_uses_template_bindings(tmp_path: Path) -> None:
    class DummyResolver:
        def resolve(self, ref_id, ref_type=None):
            return None

    def make_document(base_name: str, treatment: str) -> dict:
        return {
            "processDataSet": {
                "processInformation": {
                    "dataSetInformation": {
                        "name": {
                            "baseName": {"@xml:lang": "zh", "#text": base_name},
                            "treatmentStandardsRoutes": {"@xml:lang": "zh", "#text": treatment},
                            "mixAndLocationTypes": {"@xml:lang": "zh", "#text": "技术组合"},
                            "functionalUnitFlowProperties": {
                                "@xml:lang": "zh",
                                "#text": "35-330kV",
                            },
                        }
                    }
                }
            }
        }

    ids = [
        "d7b7144f-4fcd-4c6d-9c52-16824edc008b",
        "012c079b-e14f-421b-94af-7b2d962721a4",
        "unused-process",
    ]
    documents = {
        "d7b7144f-4fcd-4c6d-9c52-16824edc008b": make_document("流程一", "上游"),
        "012c079b-e14f-421b-94af-7b2d962721a4": make_document("流程二", "上游"),
        "unused-process": make_document("未绑定流程", "其他"),
    }
    records = {
        json_id: {
            "state_code": 0,
            "user_id": "user",
            "json": json.loads(json.dumps(documents[json_id])),
        }
        for json_id in ids
    }

    repository = StubRepository(
        current_user_id="user",
        ids=ids,
        records=records,
        documents=documents,
    )
    workflow = ProcessWriteWorkflow(repository, resolver=DummyResolver())

    output_dir = tmp_path / "output"
    log_path = tmp_path / "workflow.log"
    written = workflow.run(
        user_id="user",
        requirement_path=Path("test/requirement/write_data_xx.yaml"),
        translation_path=Path("test/requirement/pages_process.ts"),
        output_dir=output_dir,
        log_path=log_path,
        limit=0,
    )

    expected_outputs = {
        output_dir / "d7b7144f-4fcd-4c6d-9c52-16824edc008b.json",
        output_dir / "012c079b-e14f-421b-94af-7b2d962721a4.json",
    }
    assert set(written) == expected_outputs
    for path in expected_outputs:
        content = json.loads(path.read_text(encoding="utf-8"))
        publication = content["processDataSet"]["administrativeInformation"]["publicationAndOwnership"]
        assert publication["common:licenseType"] == "Free of charge for all users and uses"

    assert "unused-process" not in {item.stem for item in written}
    log_text = log_path.read_text(encoding="utf-8")
    assert "using bound requirement template 'process_1_updates'" in log_text
    assert "[aef876a0-dd1f-4e00-97c5-3e59a0cfb6ac] bound requirement defined but JSON id not found" in log_text
