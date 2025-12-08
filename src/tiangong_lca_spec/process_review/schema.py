"""Schema-driven enumerations and normalisation helpers for review metadata."""

from __future__ import annotations

from typing import Sequence, Tuple

REVIEW_SCOPE_NAMES: Tuple[str, ...] = (
    "Raw data",
    "Unit process(es), single operation",
    "Unit process(es), black box",
    "LCI results or Partly terminated system",
    "LCIA results",
    "Documentation",
    "Life cycle inventory methods",
    "LCIA results calculation",
    "Goal and scope definition",
)

REVIEW_METHOD_NAMES: Tuple[str, ...] = (
    "Validation of data sources",
    "Sample tests on calculations",
    "Energy balance",
    "Element balance",
    "Cross-check with other source",
    "Cross-check with other data set",
    "Expert judgement",
    "Mass balance",
    "Compliance with legal limits",
    "Compliance with ISO 14040 to 14044",
    "Documentation",
    "Evidence collection by means of plant visits and/or interviews",
)


def normalise_scope_name(value: str) -> str:
    """Return the canonical scope name if it matches the TIDAS enumeration."""
    if not isinstance(value, str):
        raise TypeError("Scope value must be provided as a string.")
    candidate = value.strip()
    if not candidate:
        raise ValueError("Scope value is required.")
    if candidate not in REVIEW_SCOPE_NAMES:
        raise ValueError(
            f"Scope '{candidate}' is not permitted. "
            f"Allowed values: {', '.join(REVIEW_SCOPE_NAMES)}"
        )
    return candidate


def normalise_method_names(values: str | Sequence[str]) -> Tuple[str, ...]:
    """Return a tuple of unique method names that match the TIDAS enumeration."""
    candidates = _extract_method_candidates(values)
    if not candidates:
        raise ValueError("At least one review method must be provided.")

    normalised: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        item = candidate.strip()
        if not item:
            continue
        if item not in REVIEW_METHOD_NAMES:
            raise ValueError(
                f"Method '{item}' is not permitted. "
                f"Allowed values: {', '.join(REVIEW_METHOD_NAMES)}"
            )
        if item not in seen:
            normalised.append(item)
            seen.add(item)
    if not normalised:
        raise ValueError("No valid review methods provided.")
    return tuple(normalised)


def _extract_method_candidates(values: str | Sequence[str]) -> list[str]:
    if isinstance(values, str):
        return [segment.strip() for segment in values.split(",")]
    candidates: list[str] = []
    for value in values or []:
        if isinstance(value, str):
            candidates.extend(segment.strip() for segment in value.split(","))
        else:
            raise TypeError("Review methods must be strings.")
    return candidates
