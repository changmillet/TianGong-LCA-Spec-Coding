"""Typed primitives used by the process review tooling."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence, Tuple

ReviewSeverity = Literal["info", "warning", "error"]


@dataclass(slots=True)
class ReviewFinding:
    """Structured record describing a single review observation."""

    category: str
    message: str
    severity: ReviewSeverity
    path: str | None = None
    evidence: str | None = None
    suggestion: str | None = None


@dataclass(slots=True)
class ReviewMetadata:
    """Final metadata fields that must be written back to the dataset."""

    review_type: str
    scope: str
    method_names: Tuple[str, ...]

    @property
    def method_label(self) -> str:
        return ", ".join(self.method_names)


@dataclass(slots=True)
class ReviewReport:
    """Lightweight representation of the generated review report."""

    summary: str
    details: str


@dataclass(slots=True)
class SourceRecord:
    """Structured snippet extracted from an unstructured data source."""

    identifier: str
    text: str
    quantity: float | None = None
    unit: str | None = None
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FieldDefinition:
    """Schema rule used to verify textual fields."""

    path: tuple[str, ...]
    description: str | None = None
    required: bool = False
    expected_type: Literal["string", "multilang", "number", "mapping", "list", "bool"] | None = None
    allowed_values: Sequence[str] | None = None
    pattern: str | None = None


@dataclass(slots=True)
class ProcessReviewResult:
    """Aggregated outcome of running validation and review."""

    metadata: ReviewMetadata
    validation_findings: list[ReviewFinding]
    review_findings: list[ReviewFinding]
    report: ReviewReport
    validation_passed: bool


@dataclass(slots=True)
class DatasetLabel:
    """Identifiers extracted from a process dataset for reporting."""

    base_name: str | None = None
    uuid: str | None = None
    reference_year: str | None = None
