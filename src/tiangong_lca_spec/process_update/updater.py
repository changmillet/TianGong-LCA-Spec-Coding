"""Apply requirement-driven updates to process datasets."""

from __future__ import annotations

import itertools
from copy import deepcopy
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Protocol, Sequence

from tiangong_lca_spec.core.exceptions import SpecCodingError

from .reference_resolver import ReferenceMetadata, ReferenceMetadataResolver
from .requirements import ExchangeUpdate, FieldRequirement, ProcessRequirement, RequirementBundle
from .translation import PagesProcessTranslation


class MessageLogger(Protocol):
    """Minimal logger protocol used by the updater."""

    def log(self, message: str) -> None: ...


@dataclass(frozen=True, slots=True)
class FieldMapping:
    """Static mapping between requirement entries and dataset targets."""

    label: str
    schema_path: tuple[str, ...]
    value_type: str
    ui_key: str
    reference_type: str | None = None


FIELD_MAPPINGS: Mapping[str, FieldMapping] = {
    "数据切断和完整性原则": FieldMapping(
        label="建模信息——数据切断和完整性原则",
        schema_path=(
            "modellingAndValidation",
            "dataSourcesTreatmentAndRepresentativeness",
            "dataCutOffAndCompletenessPrinciples",
        ),
        value_type="multilang",
        ui_key="pages.process.view.modellingAndValidation.dataCutOffAndCompletenessPrinciples",
    ),
    "数据集委托方": FieldMapping(
        label="管理信息——数据集委托方",
        schema_path=(
            "administrativeInformation",
            "common:commissionerAndGoal",
            "common:referenceToCommissioner",
        ),
        value_type="reference",
        ui_key="pages.process.view.administrativeInformation.referenceToCommissioner",
        reference_type="Contact data set",
    ),
    "数据集生成者/建模者": FieldMapping(
        label="管理信息——数据集生成者/建模者",
        schema_path=(
            "administrativeInformation",
            "dataGenerator",
            "common:referenceToPersonOrEntityGeneratingTheDataSet",
        ),
        value_type="reference",
        ui_key="pages.process.view.administrativeInformation.RreferenceToPersonOrEntityGeneratingTheDataSet",
        reference_type="Contact data set",
    ),
    "数据录入人": FieldMapping(
        label="管理信息——数据录入人",
        schema_path=(
            "administrativeInformation",
            "dataEntryBy",
            "common:referenceToPersonOrEntityEnteringTheData",
        ),
        value_type="reference",
        ui_key="pages.process.view.administrativeInformation.referenceToPersonOrEntityEnteringTheData",
        reference_type="Contact data set",
    ),
    "数据集拥有者": FieldMapping(
        label="管理信息——数据集拥有者",
        schema_path=(
            "administrativeInformation",
            "publicationAndOwnership",
            "common:referenceToOwnershipOfDataSet",
        ),
        value_type="reference",
        ui_key="pages.process.view.administrativeInformation.referenceToOwnershipOfDataSet",
        reference_type="Contact data set",
    ),
    "版权？": FieldMapping(
        label="管理信息——版权？",
        schema_path=(
            "administrativeInformation",
            "publicationAndOwnership",
            "common:copyright",
        ),
        value_type="bool",
        ui_key="pages.process.view.administrativeInformation.copyright",
    ),
    "许可类型": FieldMapping(
        label="管理信息——许可类型",
        schema_path=(
            "administrativeInformation",
            "publicationAndOwnership",
            "common:licenseType",
        ),
        value_type="enum",
        ui_key="pages.process.view.administrativeInformation.licenseType",
    ),
    "混合和位置类型": FieldMapping(
        label="过程信息——混合和位置类型",
        schema_path=(
            "processInformation",
            "dataSetInformation",
            "name",
            "mixAndLocationTypes",
        ),
        value_type="multilang",
        ui_key="pages.process.view.processInformation.mixAndLocationTypes",
    ),
    "定量产品或过程属性": FieldMapping(
        label="过程信息——定量产品或过程属性",
        schema_path=(
            "processInformation",
            "dataSetInformation",
            "name",
            "functionalUnitFlowProperties",
        ),
        value_type="multilang",
        ui_key="pages.process.view.processInformation.functionalUnitFlowProperties",
    ),
    "使用的数据来源": FieldMapping(
        label="建模信息——使用的数据来源",
        schema_path=(
            "modellingAndValidation",
            "dataSourcesTreatmentAndRepresentativeness",
            "referenceToDataSource",
        ),
        value_type="reference",
        ui_key="pages.process.view.modellingAndValidation.referenceToDataSource",
        reference_type="Source data set",
    ),
    "技术描述及背景系统": FieldMapping(
        label="过程信息——技术描述及背景系统",
        schema_path=(
            "processInformation",
            "technology",
            "technologyDescriptionAndIncludedProcesses",
        ),
        value_type="multilang",
        ui_key="pages.process.view.processInformation.technologyDescriptionAndIncludedProcesses",
    ),
    "数据集格式": FieldMapping(
        label="管理信息——数据集格式",
        schema_path=(
            "administrativeInformation",
            "dataEntryBy",
            "common:referenceToDataSetFormat",
        ),
        value_type="reference",
        ui_key="pages.process.view.administrativeInformation.referenceToDataSetFormat",
        reference_type="Source data set",
    ),
    "预期应用": FieldMapping(
        label="管理信息——预期应用",
        schema_path=(
            "administrativeInformation",
            "common:commissionerAndGoal",
            "common:intendedApplications",
        ),
        value_type="multilang",
        ui_key="pages.process.view.administrativeInformation.intendedApplications",
    ),
}


@dataclass(frozen=True, slots=True)
class RequirementAnalysis:
    """Summarises whether a dataset already satisfies the YAML requirements."""

    has_global_requirements: bool
    missing_global_fields: list[str]
    matched_process_name: str | None
    matched_process_index: int | None
    process_fields_defined: bool
    process_exchanges_defined: bool
    missing_process_fields: list[str]
    missing_process_exchanges: list[str]
    available_process_names: list[str]
    unsupported_labels: list[str]

    def needs_update(self) -> bool:
        return bool(
            self.missing_global_fields
            or self.missing_process_fields
            or self.missing_process_exchanges
        )

    def describe_scope(self) -> str:
        parts: list[str] = []
        if self.has_global_requirements:
            parts.append("global")
        if self.matched_process_name:
            if self.matched_process_index:
                parts.append(
                    f"process[{self.matched_process_index}] {self.matched_process_name}"
                )
            else:
                parts.append(f"process {self.matched_process_name}")
        elif self.available_process_names:
            parts.append("process (no match)")
        return " + ".join(parts) if parts else "none"

    def describe_missing(self) -> str:
        segments: list[str] = []
        if self.missing_global_fields:
            segments.append(
                "global fields: " + ", ".join(sorted(set(self.missing_global_fields)))
            )
        if self.missing_process_fields:
            label = self.matched_process_name or "process"
            segments.append(
                f"{label} fields: "
                + ", ".join(sorted(set(self.missing_process_fields)))
            )
        if self.missing_process_exchanges:
            label = self.matched_process_name or "process"
            segments.append(
                f"{label} exchanges: "
                + ", ".join(sorted(set(self.missing_process_exchanges)))
            )
        if not segments:
            segments.append("no missing fields")
        if self.unsupported_labels:
            segments.append(
                "unsupported labels: " + ", ".join(sorted(set(self.unsupported_labels)))
            )
        return "; ".join(segments)


class ProcessJsonUpdater:
    """Update a process dataset based on parsed requirements."""

    EXCHANGE_FIELD_KEYS: Mapping[str, str] = {
        "数据推导类型/状态": "dataDerivationTypeStatus",
    }

    def __init__(
        self,
        translations: PagesProcessTranslation,
        logger: MessageLogger,
        *,
        resolver: ReferenceMetadataResolver | None = None,
    ) -> None:
        self._translations = translations
        self._logger = logger
        self._resolver = resolver

    def analyse(self, dataset: dict, requirements: RequirementBundle) -> RequirementAnalysis:
        process_dataset = self._get_process_dataset_view(dataset)

        missing_global: list[str] = []
        missing_process_fields: list[str] = []
        missing_process_exchanges: list[str] = []
        unsupported: list[str] = []

        has_global = bool(requirements.global_updates)
        for requirement in requirements.global_updates:
            base_label = self._normalise_label(requirement.label)
            mapping = FIELD_MAPPINGS.get(base_label)
            if not mapping:
                unsupported.append(requirement.label)
                continue
            if self._field_value_differs(process_dataset, mapping, requirement):
                missing_global.append(requirement.label)

        available_process_names = [item.process_name.strip() for item in requirements.process_updates]
        matched_requirement, matched_index = self._match_process_requirement(
            process_dataset, requirements.process_updates
        )

        process_fields_defined = False
        process_exchanges_defined = False
        if matched_requirement:
            process_fields_defined = bool(matched_requirement.fields)
            process_exchanges_defined = bool(matched_requirement.exchange_updates)

            for field_requirement in matched_requirement.fields:
                base_label = self._normalise_label(field_requirement.label)
                mapping = FIELD_MAPPINGS.get(base_label)
                if not mapping:
                    unsupported.append(field_requirement.label)
                    continue
                if self._field_value_differs(process_dataset, mapping, field_requirement):
                    missing_process_fields.append(field_requirement.label)

            for exchange_requirement in matched_requirement.exchange_updates:
                if exchange_requirement.label not in self.EXCHANGE_FIELD_KEYS:
                    unsupported.append(exchange_requirement.label)
                    continue
                if self._exchange_value_differs(process_dataset, exchange_requirement):
                    missing_process_exchanges.append(exchange_requirement.label)
        else:
            process_fields_defined = any(req.fields for req in requirements.process_updates)
            process_exchanges_defined = any(
                req.exchange_updates for req in requirements.process_updates
            )

        matched_name = matched_requirement.process_name.strip() if matched_requirement else None

        return RequirementAnalysis(
            has_global_requirements=has_global,
            missing_global_fields=missing_global,
            matched_process_name=matched_name,
            matched_process_index=matched_index,
            process_fields_defined=process_fields_defined,
            process_exchanges_defined=process_exchanges_defined,
            missing_process_fields=missing_process_fields,
            missing_process_exchanges=missing_process_exchanges,
            available_process_names=available_process_names,
            unsupported_labels=unsupported,
        )

    def apply(self, dataset: dict, requirements: RequirementBundle) -> dict:
        process_dataset = self._resolve_process_dataset(dataset)
        self._apply_field_requirements(process_dataset, requirements.global_updates)

        matched = self._locate_process_requirement(process_dataset, requirements.process_updates)
        if matched:
            self._apply_field_requirements(process_dataset, matched.fields)
            self._apply_exchange_updates(process_dataset, matched.exchange_updates)

        self._post_update_cleanup(dataset)
        return dataset

    def _get_process_dataset_view(self, dataset: dict) -> dict:
        if "processDataSet" in dataset and isinstance(dataset.get("processDataSet"), dict):
            return dataset["processDataSet"]
        if "processInformation" in dataset and isinstance(dataset.get("processInformation"), dict):
            return dataset
        return {}

    def _field_value_differs(
        self, process_dataset: dict, mapping: FieldMapping, requirement: FieldRequirement
    ) -> bool:
        existing_value = self._get_nested_value(process_dataset, mapping.schema_path)
        if mapping.value_type == "multilang":
            expected = self._expected_multilang_map(requirement)
            actual = self._extract_multilang_map(existing_value)
            return any(actual.get(lang) != text for lang, text in expected.items())
        if mapping.value_type == "reference":
            expected_id = self._expected_reference_id(requirement)
            existing_ids = self._extract_reference_ids(existing_value)
            return expected_id not in existing_ids
        if mapping.value_type == "enum":
            expected_value = self._build_enum(mapping, requirement)
            existing = self._normalise_enum_value(existing_value)
            return existing != expected_value
        if mapping.value_type == "bool":
            expected_value = self._build_bool(requirement)
            existing = self._normalise_bool_value(existing_value)
            return existing != expected_value
        raise SpecCodingError(
            f"Unsupported value type '{mapping.value_type}' for '{mapping.label}'"
        )

    def _get_nested_value(self, dataset: dict, path: tuple[str, ...]) -> object:
        cursor: object = dataset
        for segment in path:
            if not isinstance(cursor, dict):
                return None
            cursor = cursor.get(segment)
        return cursor

    def _expected_multilang_map(self, requirement: FieldRequirement) -> dict[str, str]:
        pairs: dict[str, str] = {}
        for entry in requirement.language_values():
            if entry.text is None:
                continue
            pairs.setdefault(entry.language, entry.text)
        if not pairs:
            raise SpecCodingError(
                f"No multi-language values provided for '{requirement.label}'"
            )
        return pairs

    def _extract_multilang_map(self, value: object) -> dict[str, str]:
        result: dict[str, str] = {}
        if isinstance(value, list):
            for item in value:
                inner = self._extract_multilang_map(item)
                for lang, text in inner.items():
                    result.setdefault(lang, text)
            return result
        if isinstance(value, dict):
            text = value.get("#text") or value.get("text")
            lang = value.get("@xml:lang")
            if isinstance(text, str):
                key = str(lang) if isinstance(lang, str) else ""
                result.setdefault(key, text)
            return result
        if isinstance(value, str):
            result.setdefault("", value)
        return result

    def _expected_reference_id(self, requirement: FieldRequirement) -> str:
        raw_value = requirement.text_value().strip()
        try:
            return str(uuid.UUID(raw_value))
        except ValueError as exc:
            raise SpecCodingError(
                f"Requirement '{requirement.label}' expected a UUID, received '{raw_value}'"
            ) from exc

    def _extract_reference_ids(self, value: object) -> set[str]:
        result: set[str] = set()
        if isinstance(value, list):
            for item in value:
                result.update(self._extract_reference_ids(item))
            return result
        if isinstance(value, dict):
            ref = value.get("@refObjectId")
            if isinstance(ref, str) and ref.strip():
                result.add(ref.strip())
            return result
        if isinstance(value, str) and value.strip():
            result.add(value.strip())
        return result

    def _normalise_bool_value(self, value: object) -> str | None:
        if isinstance(value, str):
            candidate = value.strip().lower()
        elif isinstance(value, bool):
            candidate = "true" if value else "false"
        else:
            candidate = str(value).strip().lower() if value is not None else ""
        if candidate in {"true", "false"}:
            return candidate
        return None

    def _normalise_enum_value(self, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value.strip()
        return str(value).strip()

    def _exchange_value_differs(self, process_dataset: dict, update: ExchangeUpdate) -> bool:
        field_key = self.EXCHANGE_FIELD_KEYS[update.label]
        exchanges_section = process_dataset.get("exchanges")
        if not isinstance(exchanges_section, dict):
            return True
        exchange_items = exchanges_section.get("exchange")
        if isinstance(exchange_items, dict):
            exchange_items = [exchange_items]
        if not isinstance(exchange_items, list) or not exchange_items:
            return True
        expected_value = self._normalise_exchange_value(update.value)
        if update.match != "all":
            return True
        for exchange in exchange_items:
            if not isinstance(exchange, dict):
                return True
            existing_value = self._normalise_exchange_value(exchange.get(field_key))
            if existing_value != expected_value:
                return True
        return False

    def _apply_field_requirements(
        self, process_dataset: dict, requirements: Sequence[FieldRequirement]
    ) -> None:
        for requirement in requirements:
            base_label = self._normalise_label(requirement.label)
            mapping = FIELD_MAPPINGS.get(base_label)
            if not mapping:
                self._logger.log(
                    f"Skipped requirement '{requirement.label}': no mapping available."
                )
                continue
            value = self._convert_value(mapping, requirement)
            self._assign(process_dataset, mapping.schema_path, value, requirement.label)

    def _apply_exchange_updates(
        self, process_dataset: dict, updates: Sequence[ExchangeUpdate]
    ) -> None:
        if not updates:
            return
        exchanges_section = process_dataset.get("exchanges")
        if not isinstance(exchanges_section, dict):
            self._logger.log(
                "Process dataset missing 'exchanges' section; skipping exchange updates."
            )
            return
        exchange_items = exchanges_section.get("exchange")
        if isinstance(exchange_items, dict):
            exchange_items = [exchange_items]
            exchanges_section["exchange"] = exchange_items
        if not isinstance(exchange_items, list):
            self._logger.log(
                "Process dataset 'exchange' field is not a list; skipping exchange updates."
            )
            return

        for update in updates:
            field_key = self.EXCHANGE_FIELD_KEYS.get(update.label)
            if not field_key:
                self._logger.log(
                    f"Exchange update label '{update.label}' is not supported; skipped."
                )
                continue
            targets = exchange_items
            if update.match != "all":
                self._logger.log(
                    "Exchange match rule '%s' is not implemented; defaulting to all exchanges."
                    % update.match
                )
            value = self._normalise_exchange_value(update.value)
            for exchange in targets:
                if isinstance(exchange, dict):
                    exchange[field_key] = value

    @staticmethod
    def _normalise_exchange_value(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, dict):
            if not value:
                return ""
            if "zh" in value and value["zh"]:
                return str(value["zh"])
            if "en" in value and value["en"]:
                return str(value["en"])
            first = next(iter(value.values()), "")
            return str(first) if first is not None else ""
        return str(value)

    def _locate_process_requirement(
        self, process_dataset: dict, requirements: Sequence[ProcessRequirement]
    ) -> ProcessRequirement | None:
        matched, _ = self._match_process_requirement(process_dataset, requirements)
        return matched

    def _match_process_requirement(
        self, process_dataset: dict, requirements: Sequence[ProcessRequirement]
    ) -> tuple[ProcessRequirement | None, int | None]:
        if not requirements:
            return None, None
        candidates = self._build_process_name_candidates(process_dataset)
        for index, requirement in enumerate(requirements, start=1):
            provided = requirement.process_name.strip()
            if any(self._compare_names(provided, candidate) for candidate in candidates):
                return requirement, index
        return None, None

    def _build_process_name_candidates(self, process_dataset: dict) -> set[str]:
        info = process_dataset.get("processInformation", {})
        if not isinstance(info, dict):
            return set()
        data_info = info.get("dataSetInformation", {})
        if not isinstance(data_info, dict):
            return set()
        name_block = data_info.get("name", {}) if isinstance(data_info.get("name"), dict) else {}
        base_names = self._extract_multilang_list(name_block.get("baseName"))
        treatment = self._extract_multilang_list(name_block.get("treatmentStandardsRoutes"))
        mix = self._extract_multilang_list(name_block.get("mixAndLocationTypes"))
        functional = self._extract_multilang_list(name_block.get("functionalUnitFlowProperties"))

        components = [base_names, treatment, mix, functional]
        components = [component for component in components if component]
        candidates: set[str] = set(base_names + treatment + mix + functional)
        if components:
            for combo in itertools.product(*components):
                candidate = "; ".join(part for part in combo if part)
                if candidate:
                    candidates.add(candidate)
        return {candidate.strip() for candidate in candidates if candidate}

    @staticmethod
    def _extract_multilang_list(value: object) -> list[str]:
        results: list[str] = []
        if isinstance(value, list):
            for item in value:
                text = ProcessJsonUpdater._extract_multilang_text(item)
                if text:
                    results.append(text)
        elif value:
            text = ProcessJsonUpdater._extract_multilang_text(value)
            if text:
                results.append(text)
        return results

    @staticmethod
    def _extract_multilang_text(value: object) -> str | None:
        if isinstance(value, dict):
            text = value.get("#text") or value.get("text")
            if text:
                return str(text)
        elif isinstance(value, str):
            return value
        return None

    @staticmethod
    def _compare_names(provided: str, candidate: str) -> bool:
        provided_name = ProcessJsonUpdater._normalise_name(provided)
        candidate_name = ProcessJsonUpdater._normalise_name(candidate)
        if not provided_name or not candidate_name:
            return False
        if provided_name == candidate_name:
            return True

        if "*" in provided_name:
            wildcard_pattern = ProcessJsonUpdater._compile_wildcard_pattern(provided_name)
            if wildcard_pattern.fullmatch(candidate_name):
                return True

        if provided_name in candidate_name or candidate_name in provided_name:
            return True

        provided_segments = [
            segment for segment in (s.strip() for s in provided_name.split(";")) if segment
        ]
        if provided_segments and all(segment in candidate_name for segment in provided_segments):
            return True

        candidate_segments = [
            segment for segment in (s.strip() for s in candidate_name.split(";")) if segment
        ]
        if candidate_segments and all(segment in provided_name for segment in candidate_segments):
            return True

        return False

    @staticmethod
    def _normalise_name(value: str) -> str:
        parts = []
        for segment in value.split(";"):
            cleaned = re.sub(r"\s+", " ", segment).strip().lower()
            if cleaned:
                parts.append(cleaned)
        return "; ".join(parts)

    @staticmethod
    def _compile_wildcard_pattern(pattern: str) -> re.Pattern[str]:
        escaped = re.escape(pattern)
        return re.compile("^" + escaped.replace(r"\*", ".*") + "$")

    def _convert_value(self, mapping: FieldMapping, requirement: FieldRequirement) -> object:
        if mapping.value_type == "multilang":
            return self._build_multilang(requirement)
        if mapping.value_type == "reference":
            return self._build_reference(mapping, requirement)
        if mapping.value_type == "enum":
            return self._build_enum(mapping, requirement)
        if mapping.value_type == "bool":
            return self._build_bool(requirement)
        raise SpecCodingError(
            f"Unsupported value type '{mapping.value_type}' for '{mapping.label}'"
        )

    def _build_multilang(self, requirement: FieldRequirement) -> object:
        pairs = [
            {"@xml:lang": entry.language, "#text": entry.text}
            for entry in requirement.language_values()
            if entry.text
        ]
        if not pairs:
            raise SpecCodingError(f"No multi-language values provided for '{requirement.label}'")
        if len(pairs) == 1:
            return pairs[0]
        return pairs

    def _build_reference(self, mapping: FieldMapping, requirement: FieldRequirement) -> dict:
        raw_value = requirement.text_value().strip()
        try:
            uuid_obj = uuid.UUID(raw_value)
        except ValueError as exc:
            raise SpecCodingError(
                f"Requirement '{requirement.label}' expected a UUID, received '{raw_value}'"
            ) from exc
        metadata = self._resolve_reference(str(uuid_obj), mapping.reference_type)
        if metadata:
            return metadata.to_global_reference()
        suffix = mapping.ui_key.split(".")[-1]
        description = f"Auto-filled {self._camel_to_sentence(suffix).lower()} (review required)."
        self._logger.log(
            f"Field '{requirement.label}' populated with placeholder metadata; confirm short "
            "description, URI, and version before publishing."
        )
        return {
            "@type": mapping.reference_type or "Contact data set",
            "@refObjectId": str(uuid_obj),
            "@version": "00.00.000",
            "@uri": f"https://tiangong.earth/datasets/{uuid_obj}",
            "common:shortDescription": {
                "@xml:lang": "en",
                "#text": description,
            },
        }

    def _build_enum(self, mapping: FieldMapping, requirement: FieldRequirement) -> str:
        raw_value = requirement.text_value().strip()
        translation_key = self._translations.key_for_value(raw_value)
        if not translation_key or not translation_key.startswith(mapping.ui_key):
            raise SpecCodingError(
                f"Unable to resolve enumeration value for '{requirement.label}' ({raw_value})"
            )
        suffix = translation_key.split(".")[-1]
        return self._format_enumeration_label(suffix)

    def _build_bool(self, requirement: FieldRequirement) -> str:
        raw_value = requirement.text_value().strip().lower()
        if raw_value in {"true", "yes", "y", "1", "是"}:
            return "true"
        if raw_value in {"false", "no", "n", "0", "否"}:
            return "false"
        raise SpecCodingError(
            f"Unable to convert '{requirement.label}' value '{raw_value}' to bool"
        )

    def _resolve_reference(self, ref_id: str, ref_type: str | None) -> ReferenceMetadata | None:
        if not self._resolver:
            return None
        metadata = self._resolver.resolve(ref_id, ref_type)
        if not metadata:
            self._logger.log(
                f"Reference '{ref_id}' ({ref_type or 'unknown type'}) missing metadata; "
                "placeholder shortDescription used."
            )
        return metadata

    def _format_enumeration_label(self, suffix: str) -> str:
        sentence = self._camel_to_sentence(suffix)
        if not sentence:
            raise SpecCodingError("Failed to derive enumeration label from suffix")
        return sentence

    def _resolve_process_dataset(self, dataset: dict) -> dict:
        if "processDataSet" in dataset and isinstance(dataset.get("processDataSet"), dict):
            return dataset["processDataSet"]
        if "processInformation" in dataset:
            return dataset
        dataset["processDataSet"] = {}
        self._logger.log("Input document missing 'processDataSet'; created an empty placeholder.")
        return dataset["processDataSet"]

    def _assign(self, dataset: dict, path: tuple[str, ...], value: object, label: str) -> None:
        cursor = dataset
        for segment in path[:-1]:
            next_cursor = cursor.get(segment)
            if not isinstance(next_cursor, dict):
                next_cursor = {}
                cursor[segment] = next_cursor
            cursor = next_cursor
        leaf_key = path[-1]
        previous = cursor.get(leaf_key)
        cursor[leaf_key] = value
        if previous not in (None, value):
            self._logger.log(
                f"Field '{label}' replaced existing value during update; original preserved in log."
            )

    def _post_update_cleanup(self, dataset: dict) -> None:
        process_data_set = dataset.get("processDataSet")
        if not isinstance(process_data_set, dict):
            return

        self._normalise_time(process_data_set)
        self._normalise_administrative_section(process_data_set)
        self._normalise_modelling_section(process_data_set)
        self._normalise_exchanges(process_data_set)

    def _normalise_time(self, process_data_set: dict) -> None:
        info = process_data_set.get("processInformation")
        if not isinstance(info, dict):
            return
        time_block = info.get("time")
        if isinstance(time_block, dict):
            reference_year = time_block.get("common:referenceYear")
            if isinstance(reference_year, str) and reference_year.isdigit():
                time_block["common:referenceYear"] = int(reference_year)

    def _normalise_administrative_section(self, process_data_set: dict) -> None:
        admin = process_data_set.get("administrativeInformation")
        if not isinstance(admin, dict):
            return
        data_entry = admin.get("dataEntryBy")
        if isinstance(data_entry, dict):
            timestamp = (
                datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            )
            data_entry["common:timeStamp"] = timestamp
            for stale_key in (
                "common:referenceToConvertedOriginalDataSetFrom",
                "common:referenceToDataSetUseApproval",
            ):
                candidate = data_entry.get(stale_key)
                if self._is_empty_reference(candidate):
                    data_entry.pop(stale_key, None)

        commissioner = admin.get("common:commissionerAndGoal")
        if isinstance(commissioner, dict):
            intended = commissioner.get("common:intendedApplications")
            if self._is_empty_multilang(intended):
                commissioner.pop("common:intendedApplications", None)

    def _normalise_modelling_section(self, process_data_set: dict) -> None:
        modelling = process_data_set.get("modellingAndValidation")
        if not isinstance(modelling, dict):
            modelling = {}
            process_data_set["modellingAndValidation"] = modelling

        validation = modelling.get("validation")
        if not isinstance(validation, dict):
            validation = {}
            modelling["validation"] = validation
            self._logger.log("Validation block missing; inserted placeholder structure.")
        self._ensure_validation_block(validation)

        compliance = modelling.get("complianceDeclarations")
        if not isinstance(compliance, dict):
            compliance = {}
            modelling["complianceDeclarations"] = compliance
            self._logger.log("Compliance declarations missing; inserted placeholder structure.")
        self._ensure_compliance_block(compliance)

        data_sources = modelling.get("dataSourcesTreatmentAndRepresentativeness")
        if isinstance(data_sources, dict):
            reference = data_sources.get("referenceToDataSource")
            if self._is_empty_reference(reference):
                data_sources.pop("referenceToDataSource", None)

    def _normalise_exchanges(self, process_data_set: dict) -> None:
        exchanges = process_data_set.get("exchanges")
        if not isinstance(exchanges, dict):
            return
        exchange_items = exchanges.get("exchange")
        if isinstance(exchange_items, dict):
            exchange_items = [exchange_items]
            exchanges["exchange"] = exchange_items
        if not isinstance(exchange_items, list):
            return
        for exchange in exchange_items:
            if not isinstance(exchange, dict):
                continue
            direction = exchange.get("exchangeDirection")
            if isinstance(direction, str):
                exchange["exchangeDirection"] = direction.capitalize()
            for numeric_key in ("meanAmount", "resultingAmount"):
                if numeric_key in exchange and not isinstance(exchange[numeric_key], str):
                    exchange[numeric_key] = str(exchange[numeric_key]).strip()
                elif isinstance(exchange.get(numeric_key), str):
                    exchange[numeric_key] = exchange[numeric_key].strip()
            refs = exchange.get("referencesToDataSource")
            if isinstance(refs, dict):
                reference = refs.get("referenceToDataSource")
                if self._is_empty_reference(reference):
                    exchange.pop("referencesToDataSource", None)

            allocations = exchange.get("allocations")
            if not isinstance(allocations, dict):
                continue
            allocation_entries = allocations.get("allocation")
            if isinstance(allocation_entries, list):
                targets = [item for item in allocation_entries if isinstance(item, dict)]
            elif isinstance(allocation_entries, dict):
                targets = [allocation_entries]
            else:
                targets = []
            for allocation in targets:
                value = allocation.get("@allocatedFraction")
                if not isinstance(value, str):
                    continue
                normalised = self._normalise_allocation_fraction(value)
                exchange_id = exchange.get("@dataSetInternalID", "unknown")
                if normalised is None:
                    allocation.pop("@allocatedFraction", None)
                    self._logger.log(
                        f"Removed invalid allocation fraction '{value}' (exchange {exchange_id})."
                    )
                elif normalised != value:
                    allocation["@allocatedFraction"] = normalised
                    self._logger.log(
                        f"Normalised fraction for exchange {exchange_id} to '{normalised}'."
                    )

    def _ensure_validation_block(self, validation: dict) -> None:
        review = validation.get("review")
        if not isinstance(review, dict):
            review = {}
            validation["review"] = review
            self._logger.log("Validation review missing; inserted placeholder entry.")

        raw_review_type = review.get("@type")
        review_type = raw_review_type.strip() if isinstance(raw_review_type, str) else ""
        if not review_type:
            review_type = "Not reviewed"
        review["@type"] = review_type

        scope_value = review.get("common:scope") or review.get("scope")
        scope = self._normalise_review_scope(scope_value)
        if scope is None:
            scope = {
                "@name": "Documentation",
                "common:method": {"@name": "Documentation"},
            }
            log_message = (
                "Validation review scope missing; inserted default scope"
                if review_type != "Not reviewed"
                else "Validation review scope missing; inserted default scope for logging"
            )
            self._logger.log(log_message + ".")
        if review_type == "Not reviewed":
            review.pop("common:scope", None)
        else:
            review["common:scope"] = scope
        review["scope"] = scope

        details = validation.get("common:reviewDetails") or validation.get("reviewDetails")
        if self._is_empty_multilang(details):
            placeholder_details = {
                "@xml:lang": "en",
                "#text": "Review summary pending confirmation.",
            }
            validation["common:reviewDetails"] = placeholder_details
            validation["reviewDetails"] = placeholder_details
            self._logger.log("Review details missing; inserted placeholder text.")
        else:
            if isinstance(details, dict):
                validation["reviewDetails"] = details

        reviewer_ref = validation.get("common:referenceToNameOfReviewerAndInstitution")
        if self._is_empty_reference(reviewer_ref):
            placeholder_ref = {
                "@type": "Contact data set",
                "@refObjectId": "00000000-0000-0000-0000-000000000002",
                "@version": "1.0",
                "@uri": "https://placeholder.example/reviewer",
                "common:shortDescription": {
                    "@xml:lang": "en",
                    "#text": "Review contact pending confirmation.",
                },
            }
            validation["common:referenceToNameOfReviewerAndInstitution"] = placeholder_ref
            validation["referenceToNameOfReviewerAndInstitution"] = placeholder_ref
            self._logger.log("Review contact reference missing; inserted placeholder reference.")
        else:
            if isinstance(reviewer_ref, dict):
                validation["referenceToNameOfReviewerAndInstitution"] = reviewer_ref

        report_ref = validation.get("common:referenceToCompleteReviewReport")
        if self._is_empty_reference(report_ref):
            placeholder_report = {
                "@type": "Source data set",
                "@refObjectId": "00000000-0000-0000-0000-000000000003",
                "@version": "1.0",
                "@uri": "https://placeholder.example/review-report",
                "common:shortDescription": {
                    "@xml:lang": "en",
                    "#text": "Review report reference pending confirmation.",
                },
            }
            validation["common:referenceToCompleteReviewReport"] = placeholder_report
            validation["referenceToCompleteReviewReport"] = placeholder_report
            self._logger.log("Review report reference missing; inserted placeholder reference.")
        else:
            if isinstance(report_ref, dict):
                validation["referenceToCompleteReviewReport"] = report_ref

    def _normalise_review_scope(self, scope: object) -> object | None:
        if isinstance(scope, list):
            items = []
            for entry in scope:
                normalised = self._normalise_scope_entry(entry)
                if normalised is not None:
                    items.append(normalised)
            return items or None
        return self._normalise_scope_entry(scope)

    def _normalise_scope_entry(self, entry: object) -> dict | None:
        if not isinstance(entry, dict):
            return None
        name = entry.get("@name")
        if not isinstance(name, str) or not name.strip():
            name = "Documentation"
        else:
            name = name.strip()

        method_block = entry.get("common:method") or entry.get("method")
        if isinstance(method_block, dict):
            method_name = method_block.get("@name")
            if not isinstance(method_name, str) or not method_name.strip():
                method_block = {"@name": "Documentation"}
            else:
                method_block = {"@name": method_name.strip()}
        else:
            method_block = {"@name": "Documentation"}
        return {"@name": name, "common:method": method_block}

    def _ensure_compliance_block(self, compliance_section: dict) -> None:
        compliance = compliance_section.get("compliance")
        if isinstance(compliance, list):
            if compliance:
                compliance_section["compliance"] = compliance[0]
                compliance = compliance_section["compliance"]
                self._logger.log(
                    "Compliance declarations provided as list; kept the first entry for MCP update."
                )
            else:
                compliance = None

        default_compliance_reference = {
            "@type": "Compliance system",
            "@refObjectId": "c84c4185-d1b0-44fc-823e-d2ec630c7906",
            "@version": "01.00.000",
            "@uri": "https://tiangong.earth/datasets/c84c4185-d1b0-44fc-823e-d2ec630c7906",
            "common:shortDescription": {
                "@xml:lang": "en",
                "#text": "Environmental Footprint (EF) 3.1",
            },
        }
        placeholder_compliance = {
            "common:referenceToComplianceSystem": deepcopy(default_compliance_reference),
            "common:approvalOfOverallCompliance": "Fully compliant",
            "common:nomenclatureCompliance": "Fully compliant",
            "common:methodologicalCompliance": "Not defined",
            "common:reviewCompliance": "Not defined",
            "common:documentationCompliance": "Not defined",
            "common:qualityCompliance": "Not defined",
        }

        if not isinstance(compliance, dict):
            compliance_section["compliance"] = deepcopy(placeholder_compliance)
            self._logger.log(
                "Compliance declaration missing; inserted default Environmental Footprint entry."
            )
            return

        reference = compliance.get("common:referenceToComplianceSystem")
        if self._is_empty_reference(reference):
            compliance["common:referenceToComplianceSystem"] = deepcopy(
                default_compliance_reference
            )
            self._logger.log("Compliance reference missing; inserted default EF 3.1 reference.")

        for key, default in placeholder_compliance.items():
            if key == "common:referenceToComplianceSystem":
                continue
            value = compliance.get(key)
            if not isinstance(value, str) or not value.strip():
                compliance[key] = deepcopy(default) if isinstance(default, dict) else default

    def _normalise_allocation_fraction(self, value: str) -> str | None:
        stripped = value.strip()
        if not stripped:
            return None
        if re.fullmatch(r"0\.\d+", stripped):
            return stripped
        if stripped.endswith("%"):
            number = stripped[:-1].strip()
            try:
                percent_value = float(number)
            except ValueError:
                return None
            fraction = percent_value / 100.0
            if 0 <= fraction < 1:
                return f"{fraction:.4f}".rstrip("0").rstrip(".")
            return None
        try:
            numeric = float(stripped)
        except ValueError:
            return None
        if 0 <= numeric < 1:
            normalised = f"{numeric:.4f}".rstrip("0").rstrip(".")
            if normalised.startswith("0."):
                return normalised or "0.0"
        return None

    @staticmethod
    def _is_empty_reference(value: object) -> bool:
        if isinstance(value, dict):
            return not any(
                key in value and isinstance(value.get(key), str) and value.get(key).strip()
                for key in ("@refObjectId", "@uri")
            )
        if isinstance(value, list):
            return all(ProcessJsonUpdater._is_empty_reference(item) for item in value)
        return value in (None, "", [])

    @staticmethod
    def _is_empty_multilang(value: object) -> bool:
        if isinstance(value, dict):
            return not value.get("#text")
        if isinstance(value, list):
            return all(ProcessJsonUpdater._is_empty_multilang(item) for item in value)
        return True

    def _normalise_label(self, label: str) -> str:
        if "——" in label:
            label = label.split("——", 1)[-1]
        return label.strip()

    def _camel_to_sentence(self, value: str) -> str:
        if not value:
            return value
        chars: list[str] = []
        current = value[0]
        for char in value[1:]:
            if char.isupper():
                chars.append(current)
                current = char.lower()
            else:
                current += char
        chars.append(current)
        words = [segment.lower() for segment in chars if segment]
        if not words:
            return value
        words[0] = words[0].capitalize()
        return " ".join(words)


__all__ = ["FieldMapping", "ProcessJsonUpdater"]
