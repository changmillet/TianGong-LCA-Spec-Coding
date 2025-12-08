#!/usr/bin/env python
"""CLI entry point for the Tiangong LCA process review workflow."""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml
from _workflow_common import generate_run_id

from tiangong_lca_spec.core.logging import configure_logging, get_logger
from tiangong_lca_spec.process_review import (
    REVIEW_METHOD_NAMES,
    REVIEW_SCOPE_NAMES,
    FieldDefinition,
    ProcessReviewService,
    SourceRecord,
    generate_docx_report,
    normalise_method_names,
    normalise_scope_name,
)

LOGGER = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        required=True,
        help="Path to the ILCD/TIDAS process JSON exported from Stage 3.",
    )
    parser.add_argument(
        "--sources",
        type=Path,
        help=(
            "Optional JSON/YAML file containing unstructured source records. "
            "Supports either a list of objects or an extract-process style payload."
        ),
    )
    parser.add_argument(
        "--field-definitions",
        type=Path,
        help="Optional JSON/YAML file describing schema expectations for textual fields.",
    )
    parser.add_argument(
        "--scope",
        choices=REVIEW_SCOPE_NAMES,
        help="Override scope name recorded in the review metadata (TIDAS enumeration).",
    )
    parser.add_argument(
        "--method",
        dest="methods",
        action="append",
        choices=REVIEW_METHOD_NAMES,
        help="Validation method name (repeat for multiple methods). Must match the TIDAS enumeration.",
    )
    parser.add_argument(
        "--profile",
        default="default",
        help="Logic profile declared in config/review/logic_profiles.yaml.",
    )
    parser.add_argument(
        "--report-template",
        help="Override DOCX template path (defaults derived from config/review/templates.yaml).",
    )
    parser.add_argument(
        "--report-context",
        dest="report_contexts",
        action="append",
        help="Additional YAML/JSON file providing metadata for populating the report template.",
    )
    parser.add_argument(
        "--run-id",
        help="Identifier for grouping review artifacts (defaults to a UTC timestamp).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory where review artifacts will be written (defaults to artifacts/review_process/<run_id>).",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        help="Path to the structured review log file (defaults to <output-dir>/review.log).",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        help="Path where the Markdown report will be written (defaults to <output-dir>/report.md).",
    )
    parser.add_argument(
        "--docx-report-path",
        type=Path,
        help="Path where the DOCX report will be written (defaults to <output-dir>/report.docx).",
    )
    parser.add_argument(
        "--findings-path",
        type=Path,
        help="Path where structured findings JSON will be written (defaults to <output-dir>/findings.json).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    run_id = args.run_id or generate_run_id()
    output_dir = args.output_dir or Path("artifacts") / "review_process" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    log_path = args.log_file or output_dir / "review.log"
    _configure_logging(log_path)

    dataset = _load_json(args.dataset)
    dataset_uuid = _extract_dataset_uuid(dataset)
    sources = _load_sources(args.sources) if args.sources else []

    profiles = _load_logic_profiles()
    profile_cfg = _resolve_profile_config(args.profile, profiles)

    templates_cfg = _load_template_config()
    template_info = _resolve_template_info(args.profile, dataset_uuid, templates_cfg, args.report_template)

    profile_field_defs = _load_profile_field_definitions(profile_cfg)
    cli_field_defs = _load_field_definitions(args.field_definitions) if args.field_definitions else []
    field_definitions = profile_field_defs + cli_field_defs

    scope_name, method_names = _resolve_scope_method(
        dataset_uuid,
        args.profile,
        args.scope,
        args.methods,
    )

    context_paths = list(template_info.get("context_files", []))
    if args.report_contexts:
        context_paths.extend(args.report_contexts)
    report_context = _load_report_context(context_paths)
    dataset_metadata = _extract_dataset_metadata(dataset)

    LOGGER.info(
        "review.cli.start",
        dataset_path=str(args.dataset),
        source_path=str(args.sources) if args.sources else None,
        field_definition_path=str(args.field_definitions) if args.field_definitions else None,
        scope=scope_name,
        method=method_names,
        profile=args.profile,
        checks=profile_cfg.get("checks"),
        report_template=str(template_info.get("docx_template")) if template_info.get("docx_template") else None,
        run_id=run_id,
    )

    service = ProcessReviewService()
    result = service.review(
        dataset,
        scope=scope_name,
        method=method_names,
        sources=sources,
        field_definitions=field_definitions,
        enabled_checks=profile_cfg.get("checks"),
    )

    markdown_path = args.report_path or output_dir / "report.md"
    markdown_path.write_text(result.report.details, encoding="utf-8")

    docx_output = args.docx_report_path or output_dir / "report.docx"
    generate_docx_report(
        result,
        dataset_metadata=dataset_metadata,
        context=report_context,
        output_path=docx_output,
        template_path=template_info.get("docx_template"),
    )

    findings_path = args.findings_path or output_dir / "findings.json"
    findings_payload = asdict(result)
    findings_path.write_text(json.dumps(findings_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    LOGGER.info(
        "review.cli.complete",
        markdown_report=str(markdown_path),
        docx_report=str(docx_output),
        findings=str(findings_path),
        validation_passed=result.validation_passed,
        errors=_count_severity(result, "error"),
        warnings=_count_severity(result, "warning"),
        infos=_count_severity(result, "info"),
    )
    print(result.report.summary)
    print(f"Markdown report written to {markdown_path}")
    print(f"DOCX report written to {docx_output}")
    print(f"Findings written to {findings_path}")


def _configure_logging(log_path: Path | None) -> None:
    configure_logging()
    if not log_path:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Failed to parse JSON from {path}: {exc}") from exc


def _load_sources(path: Path) -> list[SourceRecord]:
    raw = _load_structured_file(path)

    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        for key in ("records", "result", "segments", "tables", "rows"):
            value = raw.get(key)
            if isinstance(value, list):
                items = value
                break
        else:
            items = [raw]
    else:
        raise SystemExit(f"Unsupported source payload type in {path}: {type(raw)!r}")

    records: list[SourceRecord] = []
    for item in items:
        record = _to_source_record(item)
        if record:
            records.append(record)
    LOGGER.info("review.cli.sources_loaded", count=len(records), source_path=str(path))
    return records


def _to_source_record(item: Any) -> SourceRecord | None:
    if not isinstance(item, dict):
        return None
    identifier = (
        item.get("identifier")
        or item.get("id")
        or item.get("row_id")
        or item.get("rowId")
        or item.get("name")
    )
    text = item.get("text") or item.get("content") or item.get("description")
    if not identifier or not text:
        return None

    quantity = _coerce_float(
        item.get("quantity")
        or item.get("value")
        or item.get("amount")
        or item.get("meanAmount")
    )
    unit = item.get("unit") or item.get("unitName")
    context_keys = (
        "page",
        "page_number",
        "pageNumber",
        "page_index",
        "section",
        "table",
        "table_id",
        "tableId",
        "column",
        "source",
        "figure",
    )
    context = {key: item.get(key) for key in context_keys if key in item}
    return SourceRecord(
        identifier=str(identifier),
        text=str(text),
        quantity=quantity,
        unit=str(unit) if unit is not None else None,
        context=context,
    )


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text.replace(",", ""))
        except ValueError:
            return None
    return None


def _load_field_definitions(path: Path) -> list[FieldDefinition]:
    raw = _load_structured_file(path)
    if isinstance(raw, dict):
        container = raw.get("definitions") or raw.get("items")
        if isinstance(container, list):
            raw = container
        else:
            raise SystemExit(f"Field definition file must contain an array: {path}")
    if not isinstance(raw, list):
        raise SystemExit(f"Field definition file must contain an array: {path}")
    definitions: list[FieldDefinition] = []
    for entry in raw:
        definition = _to_field_definition(entry)
        if definition:
            definitions.append(definition)
    LOGGER.info("review.cli.field_definitions_loaded", count=len(definitions), path=str(path))
    return definitions


def _to_field_definition(entry: Any) -> FieldDefinition | None:
    if not isinstance(entry, dict):
        return None
    path_value = entry.get("path")
    if isinstance(path_value, list):
        path = tuple(str(segment) for segment in path_value)
    elif isinstance(path_value, str):
        path = tuple(segment.strip() for segment in path_value.split(".") if segment.strip())
    else:
        return None

    description = entry.get("description")
    required = bool(entry.get("required", False))
    expected_type = entry.get("expected_type")
    allowed_values = entry.get("allowed_values")
    if isinstance(allowed_values, list):
        allowed_iterable = [str(item) for item in allowed_values]
    else:
        allowed_iterable = None
    pattern = entry.get("pattern")
    return FieldDefinition(
        path=path,
        description=str(description) if description is not None else None,
        required=required,
        expected_type=str(expected_type) if expected_type else None,
        allowed_values=allowed_iterable,
        pattern=str(pattern) if pattern else None,
    )


def _load_structured_file(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix in {".yaml", ".yml"}:
        return yaml.safe_load(text)
    return json.loads(text)


def _count_severity(result: Any, severity: str) -> int:
    findings = result.validation_findings + result.review_findings
    return sum(1 for finding in findings if finding.severity == severity)


def _load_logic_profiles(path: Path | None = None) -> dict[str, Any]:
    config_path = path or Path("config") / "review" / "logic_profiles.yaml"
    if not config_path.exists():
        return {}
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"Invalid logic profile configuration in {config_path}: expected mapping.")
    return data


def _resolve_profile_config(profile: str, profiles: dict[str, Any]) -> dict[str, Any]:
    if profile not in profiles:
        available = ", ".join(sorted(profiles.keys()))
        raise SystemExit(f"Unknown review logic profile '{profile}'. Available profiles: {available}")

    resolved: dict[str, Any] = {}
    visited: set[str] = set()
    current = profile
    while current:
        if current in visited:
            chain = " -> ".join(list(visited) + [current])
            raise SystemExit(f"Circular inheritance detected in logic profile configuration: {chain}")
        visited.add(current)
        cfg = profiles.get(current) or {}
        resolved = _merge_profile(cfg, resolved)
        current = cfg.get("inherits")
    resolved.setdefault("checks", [])
    return resolved


def _merge_profile(source: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    merged = dict(target)
    for key, value in source.items():
        if key == "checks":
            existing = merged.get("checks", [])
            merged["checks"] = list(dict.fromkeys((existing or []) + (value or [])))
        elif key == "field_definitions":
            existing = merged.get("field_definitions", [])
            merged["field_definitions"] = list(dict.fromkeys((existing or []) + (value or [])))
        elif key == "inherits":
            merged.setdefault("inherits", value)
        else:
            merged[key] = value
    return merged


def _load_profile_field_definitions(profile_cfg: dict[str, Any]) -> list[FieldDefinition]:
    paths = profile_cfg.get("field_definitions") or []
    definitions: list[FieldDefinition] = []
    for entry in paths:
        definition_path = Path(entry)
        if not definition_path.exists():
            raise SystemExit(f"Field definition file declared in profile not found: {definition_path}")
        definitions.extend(_load_field_definitions(definition_path))
    return definitions


def _load_scope_method_map(path: Path | None = None) -> dict[str, Any]:
    config_path = path or Path("config") / "review" / "scope_method_map.yaml"
    if not config_path.exists():
        return {}
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"Invalid scope/method configuration in {config_path}: expected mapping.")
    return data


def _resolve_scope_method(
    dataset_uuid: str | None,
    profile: str,
    scope_override: str | None,
    method_override: Sequence[str] | None,
) -> tuple[str, tuple[str, ...]]:
    scope_map = _load_scope_method_map()
    scope = scope_override
    methods = tuple(method_override) if method_override else ()

    if not scope or not methods:
        candidates = [
            dataset_uuid or "",
            profile,
            "default",
        ]
        for key in candidates:
            if not key:
                continue
            entry = scope_map.get(key)
            if not isinstance(entry, dict):
                continue
            if not scope:
                scope_value = entry.get("scope")
                if isinstance(scope_value, str):
                    scope = scope_value
            if not methods:
                method_values = entry.get("methods")
                if isinstance(method_values, list):
                    methods = tuple(str(item) for item in method_values if isinstance(item, (str, int, float)))
            if scope and methods:
                break

    if not scope:
        raise SystemExit("Review scope not provided and no default found in config/review/scope_method_map.yaml.")
    if not methods:
        raise SystemExit("Review methods not provided and no defaults found in config/review/scope_method_map.yaml.")

    scope_name = normalise_scope_name(scope)
    method_names = normalise_method_names(list(methods))
    return scope_name, method_names


def _extract_dataset_uuid(dataset: Any) -> str | None:
    process = dataset.get("processDataSet") if isinstance(dataset, dict) else None
    if not isinstance(process, dict):
        process = dataset
    if not isinstance(process, dict):
        return None
    process_info = process.get("processInformation")
    if not isinstance(process_info, dict):
        return None
    data_info = process_info.get("dataSetInformation")
    if not isinstance(data_info, dict):
        return None
    uuid = data_info.get("common:UUID") or data_info.get("UUID") or data_info.get("uuid")
    if isinstance(uuid, str):
        return uuid
    return None


def _load_template_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or Path("config") / "review" / "templates.yaml"
    if not config_path.exists():
        return {}
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"Invalid template configuration in {config_path}: expected mapping.")
    return data


def _resolve_template_info(
    profile: str,
    dataset_uuid: str | None,
    templates: Mapping[str, Any],
    override_template: str | None,
) -> dict[str, Any]:
    info: dict[str, Any] = {}
    for key in (dataset_uuid or "", profile, "default"):
        if not key:
            continue
        candidate = templates.get(key)
        if isinstance(candidate, Mapping):
            info = dict(candidate)
            break
    if override_template:
        info = dict(info)
        info["docx_template"] = override_template
    info.setdefault("context_files", [])
    return info


def _load_report_context(paths: Sequence[str] | None) -> dict[str, Any]:
    context: dict[str, Any] = {}
    if not paths:
        return context
    for entry in paths:
        if not entry:
            continue
        path = Path(entry)
        if not path.exists():
            raise SystemExit(f"Report context file not found: {path}")
        data = _load_structured_file(path)
        if not isinstance(data, Mapping):
            raise SystemExit(f"Report context file must be a mapping: {path}")
        context = _deep_merge(context, data)
    return context


def _deep_merge(base: dict[str, Any], extra: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in extra.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, Mapping)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _extract_dataset_metadata(dataset: Mapping[str, Any]) -> dict[str, str | None]:
    metadata: dict[str, str | None] = {"name": None, "uuid": None, "version": None, "locator": None}

    process = dataset.get("processDataSet") if isinstance(dataset, Mapping) else None
    if not isinstance(process, Mapping):
        process = dataset
    if not isinstance(process, Mapping):
        return metadata

    process_info = process.get("processInformation")
    if isinstance(process_info, Mapping):
        data_info = process_info.get("dataSetInformation")
        if isinstance(data_info, Mapping):
            metadata["uuid"] = (
                data_info.get("common:UUID")
                or data_info.get("UUID")
                or data_info.get("uuid")
            )
            name_block = data_info.get("name")
            if isinstance(name_block, Mapping):
                base_name = name_block.get("baseName")
                if isinstance(base_name, list):
                    for entry in base_name:
                        if isinstance(entry, Mapping) and entry.get("@xml:lang") == "zh":
                            metadata["name"] = entry.get("#text")
                            break
                    else:
                        for entry in base_name:
                            if isinstance(entry, Mapping) and entry.get("#text"):
                                metadata["name"] = entry.get("#text")
                                break
                elif isinstance(base_name, Mapping):
                    metadata["name"] = base_name.get("#text")
        geography = process_info.get("geography")
        if isinstance(geography, Mapping):
            location = geography.get("locationOfOperationSupplyOrProduction")
            if isinstance(location, Mapping):
                metadata["locator"] = location.get("@location")

    admin = process.get("administrativeInformation")
    if isinstance(admin, Mapping):
        publication = admin.get("publicationAndOwnership")
        if isinstance(publication, Mapping):
            metadata["version"] = publication.get("common:dataSetVersion")

    return {key: (value if value else None) for key, value in metadata.items()}


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    main()
