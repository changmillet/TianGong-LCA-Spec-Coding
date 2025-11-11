"""High-level orchestration for the process review workflow."""

from __future__ import annotations

from collections import Counter
from typing import Iterable, Mapping, Sequence

from tiangong_lca_spec.core.config import Settings, get_settings
from tiangong_lca_spec.core.logging import get_logger

from .checks import (
    DEFAULT_BALANCE_TOLERANCE,
    DEFAULT_RELATIVE_TOLERANCE,
    check_exchange_balance,
    check_field_content,
    cross_check_sources,
    structural_validation,
)
from .models import (
    DatasetLabel,
    FieldDefinition,
    ProcessReviewResult,
    ReviewFinding,
    ReviewMetadata,
    ReviewReport,
    SourceRecord,
)
from .schema import normalise_method_names, normalise_scope_name

LOGGER = get_logger(__name__)
INDEPENDENT_REVIEW_TYPE = "Independent external review"


class ProcessReviewService:
    """Coordinates validation, numeric checks, field validation, and reporting."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        balance_tolerance: float = DEFAULT_BALANCE_TOLERANCE,
        source_relative_tolerance: float = DEFAULT_RELATIVE_TOLERANCE,
    ) -> None:
        self._settings = settings or get_settings()
        self._balance_tolerance = balance_tolerance
        self._source_relative_tolerance = source_relative_tolerance

    def review(
        self,
        dataset: Mapping[str, object],
        *,
        scope: str,
        method: str | Sequence[str],
        sources: Sequence[SourceRecord] | None = None,
        field_definitions: Sequence[FieldDefinition] | None = None,
        enabled_checks: Sequence[str] | None = None,
    ) -> ProcessReviewResult:
        scope_name = normalise_scope_name(scope)
        method_names = normalise_method_names(method)

        LOGGER.info(
            "process_review.start",
            scope=scope_name,
            method=method_names,
            enabled_checks=list(enabled_checks or []),
        )
        validation_findings = structural_validation(dataset)
        validation_passed = not any(finding.severity == "error" for finding in validation_findings)
        review_findings: list[ReviewFinding] = []
        if validation_passed:
            review_findings.extend(
                _run_checks(
                    dataset,
                    sources or (),
                    field_definitions or (),
                    enabled_checks,
                    balance_tolerance=self._balance_tolerance,
                    source_tolerance=self._source_relative_tolerance,
                )
            )
        else:
            review_findings.append(
                ReviewFinding(
                    category="review",
                    severity="info",
                    message="Review checks skipped because validation returned errors.",
                    suggestion="Resolve validation findings and rerun the review workflow.",
                )
            )

        metadata = ReviewMetadata(
            review_type=INDEPENDENT_REVIEW_TYPE,
            scope=scope_name,
            method_names=method_names,
        )
        label = _extract_dataset_label(dataset)
        report = _build_report(label, metadata, validation_findings, review_findings)
        LOGGER.info(
            "process_review.complete",
            validation_passed=validation_passed,
            error_count=_count_severity(validation_findings + review_findings, "error"),
            warning_count=_count_severity(validation_findings + review_findings, "warning"),
        )
        return ProcessReviewResult(
            metadata=metadata,
            validation_findings=validation_findings,
            review_findings=review_findings,
            report=report,
            validation_passed=validation_passed,
        )


def _build_report(
    label: DatasetLabel,
    metadata: ReviewMetadata,
    validation_findings: Iterable[ReviewFinding],
    review_findings: Iterable[ReviewFinding],
) -> ReviewReport:
    findings = list(validation_findings) + list(review_findings)
    total_errors = _count_severity(findings, "error")
    total_warnings = _count_severity(findings, "warning")
    total_info = _count_severity(findings, "info")
    dataset_name = label.base_name or label.uuid or "Unknown dataset"

    summary = (
        f"{dataset_name}: {total_errors} error(s), {total_warnings} warning(s), {total_info} info note(s) "
        f"during {metadata.review_type.lower()} (scope: {metadata.scope}; method: {metadata.method_label})."
    )

    lines: list[str] = []
    lines.append(f"# Process Review Report – {dataset_name}")
    if label.uuid:
        lines.append(f"- UUID: `{label.uuid}`")
    if label.reference_year:
        lines.append(f"- Reference year: {label.reference_year}")
    lines.append(f"- Review type: {metadata.review_type}")
    lines.append(f"- Scope: {metadata.scope}")
    lines.append(f"- Method: {metadata.method_label}")
    lines.append("")
    lines.append("## Findings")
    if not findings:
        lines.append("- No findings recorded.")
    else:
        for finding in findings:
            detail = f"- **{finding.severity.upper()}** [{finding.category}] {finding.message}"
            if finding.path:
                detail += f" (path: `{finding.path}`)"
            lines.append(detail)
            if finding.evidence:
                lines.append(f"  - Evidence: {finding.evidence}")
            if finding.suggestion:
                lines.append(f"  - Suggestion: {finding.suggestion}")
    lines.append("")
    lines.append("## Notes")
    lines.append("Ensure all identified issues are addressed before final publication.")

    return ReviewReport(summary=summary, details="\n".join(lines))


def _extract_dataset_label(dataset: Mapping[str, object]) -> DatasetLabel:
    process = dataset.get("processDataSet") if isinstance(dataset, Mapping) else None
    if not isinstance(process, Mapping):
        process = dataset
    if not isinstance(process, Mapping):
        return DatasetLabel()

    process_info = process.get("processInformation") if isinstance(process.get("processInformation"), Mapping) else {}
    data_info = process_info.get("dataSetInformation") if isinstance(process_info, Mapping) else {}
    name_block = data_info.get("name") if isinstance(data_info, Mapping) else {}

    base_name: str | None = None
    base_entries = name_block.get("baseName") if isinstance(name_block, Mapping) else None
    if isinstance(base_entries, list):
        preferred = next((entry for entry in base_entries if isinstance(entry, Mapping) and entry.get("@xml:lang") == "en"), None)
        if not preferred:
            preferred = next((entry for entry in base_entries if isinstance(entry, Mapping) and entry.get("#text")), None)
        if isinstance(preferred, Mapping):
            base_name = preferred.get("#text")
    elif isinstance(base_entries, Mapping):
        base_name = base_entries.get("#text")

    uuid = data_info.get("common:UUID") if isinstance(data_info, Mapping) else None
    time_block = process_info.get("time") if isinstance(process_info, Mapping) else {}
    reference_year = None
    if isinstance(time_block, Mapping):
        reference_year = str(time_block.get("common:referenceYear") or "")
        if not reference_year.strip():
            reference_year = None

    return DatasetLabel(
        base_name=base_name.strip() if isinstance(base_name, str) else None,
        uuid=str(uuid) if uuid else None,
        reference_year=reference_year,
    )


def _count_severity(findings: Iterable[ReviewFinding], severity: str) -> int:
    counter = Counter(f.severity for f in findings)
    return counter.get(severity, 0)


def _run_checks(
    dataset: Mapping[str, object],
    sources: Sequence[SourceRecord],
    field_definitions: Sequence[FieldDefinition],
    enabled_checks: Sequence[str] | None,
    *,
    balance_tolerance: float,
    source_tolerance: float,
) -> list[ReviewFinding]:
    results: list[ReviewFinding] = []
    available = {
        "numeric_balance": lambda: check_exchange_balance(dataset, tolerance=balance_tolerance),
        "source_consistency": lambda: cross_check_sources(dataset, sources, tolerance=source_tolerance),
        "field_content": lambda: check_field_content(dataset, definitions=field_definitions) if field_definitions else [],
    }

    if enabled_checks:
        order = []
        for name in enabled_checks:
            if name in available:
                order.append(name)
            else:
                LOGGER.warning("process_review.unknown_check", check=name)
    else:
        order = list(available.keys())

    for name in order:
        findings = available[name]()
        if findings:
            results.extend(findings)
    return results
