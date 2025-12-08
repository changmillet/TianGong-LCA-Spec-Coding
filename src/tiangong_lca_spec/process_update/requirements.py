"""Parsing helpers for the write-process workflow requirements."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Mapping

from tiangong_lca_spec.core.exceptions import SpecCodingError

try:
    import yaml
except ImportError:  # pragma: no cover - dependency missing at runtime
    yaml = None


@dataclass(frozen=True, slots=True)
class LanguageValue:
    """Single language/value pair extracted from the requirements document."""

    language: str
    text: str


@dataclass(frozen=True, slots=True)
class FieldRequirement:
    """Normalised representation of a field update."""

    label: str
    values: str | List[LanguageValue]

    def is_multilang(self) -> bool:
        return isinstance(self.values, list)

    def text_value(self) -> str:
        if self.is_multilang():
            raise SpecCodingError(f"Requirement '{self.label}' expects multi-language values")
        return self.values

    def language_values(self) -> Iterable[LanguageValue]:
        if not self.is_multilang():
            raise SpecCodingError(
                f"Requirement '{self.label}' does not contain multi-language values"
            )
        return self.values  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class ExchangeUpdate:
    match: str
    label: str
    value: str | dict[str, str]


@dataclass(frozen=True, slots=True)
class ProcessRequirement:
    process_name: str
    fields: List[FieldRequirement]
    exchange_updates: List[ExchangeUpdate]
    template_name: str | None = None


@dataclass(frozen=True, slots=True)
class RequirementBundle:
    global_updates: List[FieldRequirement]
    process_updates: List[ProcessRequirement]
    uuid_bindings: Mapping[str, ProcessRequirement]

    def bound_requirement_for(self, json_id: str) -> ProcessRequirement | None:
        return self.uuid_bindings.get(json_id)

    def for_json_id(self, json_id: str) -> "RequirementBundle":
        bound = self.uuid_bindings.get(json_id)
        if not bound:
            return self
        return RequirementBundle(
            global_updates=self.global_updates,
            process_updates=[bound],
            uuid_bindings=self.uuid_bindings,
        )


class RequirementLoader:
    """Load field requirements from a YAML specification."""

    def load(self, path: Path) -> RequirementBundle:
        if not path.exists():
            raise SpecCodingError(f"Requirement file '{path}' does not exist")
        suffix = path.suffix.lower()
        if suffix not in {".yaml", ".yml"}:
            raise SpecCodingError(
                f"Requirement file '{path}' must be provided in YAML format (.yaml/.yml)."
            )
        if yaml is None:
            raise SpecCodingError("PyYAML is required to parse requirement files.")

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise SpecCodingError("Requirement file must deserialize to a mapping.")

        global_updates = self._parse_field_updates(data.get("global_updates", []))
        process_updates = self._parse_process_updates(data.get("process_updates", []))
        templates = self._parse_template_updates(data.get("templates"))
        uuid_bindings = self._parse_process_bindings(data.get("process_bindings"), templates)
        return RequirementBundle(
            global_updates=global_updates,
            process_updates=process_updates,
            uuid_bindings=uuid_bindings,
        )

    def _parse_field_updates(self, entries: list) -> List[FieldRequirement]:
        requirements: List[FieldRequirement] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise SpecCodingError(
                    "Each global update must be a mapping with 'ui_label' and 'value'."
                )
            label = entry.get("ui_label")
            value = entry.get("value")
            requirements.append(self._build_field_requirement(label, value))
        return requirements

    def _parse_template_updates(self, entries: object) -> dict[str, ProcessRequirement]:
        if entries is None:
            return {}
        if not isinstance(entries, dict):
            raise SpecCodingError("Templates must be provided as a mapping of keys to requirements.")

        templates: dict[str, ProcessRequirement] = {}
        for key, entry in entries.items():
            if not isinstance(key, str) or not key.strip():
                raise SpecCodingError("Template keys must be non-empty strings.")
            if not isinstance(entry, dict):
                raise SpecCodingError(f"Template '{key}' must be a mapping.")
            process_name = entry.get("process_name")
            fields = entry.get("fields") or []
            exchange_updates = entry.get("exchange_updates") or []
            field_requirements = [
                self._build_field_requirement(item.get("ui_label"), item.get("value"))
                for item in fields
            ]
            exchanges = [self._build_exchange_update(item) for item in exchange_updates]
            requirement = ProcessRequirement(
                process_name=process_name.strip() if isinstance(process_name, str) and process_name.strip() else "*",
                fields=field_requirements,
                exchange_updates=exchanges,
                template_name=key.strip(),
            )
            templates[key.strip()] = requirement
        return templates

    def _parse_process_bindings(
        self,
        entries: object,
        templates: Mapping[str, ProcessRequirement],
    ) -> dict[str, ProcessRequirement]:
        if entries is None:
            return {}
        if not isinstance(entries, dict):
            raise SpecCodingError("process_bindings must be a mapping of JSON ids to template keys.")

        bindings: dict[str, ProcessRequirement] = {}
        for raw_id, template_key in entries.items():
            if not isinstance(raw_id, str) or not raw_id.strip():
                raise SpecCodingError("process_bindings requires string JSON ids.")
            if not isinstance(template_key, str) or not template_key.strip():
                raise SpecCodingError(
                    f"process_bindings entry for '{raw_id}' must reference a template key."
                )
            template = templates.get(template_key.strip())
            if template is None:
                raise SpecCodingError(
                    f"process_bindings entry for '{raw_id}' references unknown template "
                    f"'{template_key}'."
                )
            bindings[raw_id.strip()] = ProcessRequirement(
                process_name=template.process_name,
                fields=list(template.fields),
                exchange_updates=list(template.exchange_updates),
                template_name=template.template_name,
            )
        return bindings

    def _parse_process_updates(self, entries: list) -> List[ProcessRequirement]:
        process_requirements: List[ProcessRequirement] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise SpecCodingError(
                    "Each process update must be a mapping containing 'process_name' and 'fields'."
                )
            process_name = entry.get("process_name")
            fields = entry.get("fields") or []
            exchange_updates = entry.get("exchange_updates") or []
            if not process_name or not isinstance(process_name, str):
                raise SpecCodingError("Process update is missing a valid 'process_name'.")
            field_requirements = [
                self._build_field_requirement(item.get("ui_label"), item.get("value"))
                for item in fields
            ]
            exchanges = [self._build_exchange_update(item) for item in exchange_updates]
            process_requirements.append(
                ProcessRequirement(
                    process_name=process_name,
                    fields=field_requirements,
                    exchange_updates=exchanges,
                )
            )
        return process_requirements

    def _build_field_requirement(self, label: str | None, value: object) -> FieldRequirement:
        if not label or not isinstance(label, str):
            raise SpecCodingError("Field update requires a string 'ui_label'.")
        if value is None:
            raise SpecCodingError(f"Field '{label}' is missing a 'value'.")
        if isinstance(value, dict):
            language_values = [
                LanguageValue(language=str(lang), text=str(text))
                for lang, text in value.items()
                if text is not None
            ]
            if not language_values:
                raise SpecCodingError(f"Field '{label}' requires at least one language entry.")
            return FieldRequirement(label=label, values=language_values)
        if isinstance(value, list):
            raise SpecCodingError(
                f"Field '{label}' value must be a string or mapping of languages."
            )
        return FieldRequirement(label=label, values=str(value))

    def _build_exchange_update(self, entry: dict) -> ExchangeUpdate:
        if not isinstance(entry, dict):
            raise SpecCodingError(
                "Exchange update must be a mapping with 'match', 'ui_label', 'value'."
            )
        match = entry.get("match", "all")
        label = entry.get("ui_label")
        value = entry.get("value")
        if not label or value is None:
            raise SpecCodingError("Exchange update requires 'ui_label' and 'value'.")
        if isinstance(value, dict):
            value = {str(k): str(v) for k, v in value.items() if v is not None}
        elif not isinstance(value, str):
            value = str(value)
        return ExchangeUpdate(match=str(match), label=str(label), value=value)


__all__ = [
    "ExchangeUpdate",
    "FieldRequirement",
    "LanguageValue",
    "ProcessRequirement",
    "RequirementBundle",
    "RequirementLoader",
]
