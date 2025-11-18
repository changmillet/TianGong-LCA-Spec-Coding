"""Utility helpers for unit-family detection and lightweight conversions."""

from __future__ import annotations

from typing import Any

from tiangong_lca_spec.core.logging import get_logger

LOGGER = get_logger(__name__)


def normalise_amount(
    amount: float | int | str | None,
    unit: str | None,
    unit_family: str | None,
) -> tuple[float | None, str | None]:
    """Return the numeric amount normalised within its unit family.

    At this stage we do not perform real conversions; we merely ensure the
    numeric type is consistent and the unit family label is propagated.
    This function is intentionally lightweight and can be extended in the
    future when full conversion tables are available.
    """

    numeric = _coerce_float(amount)
    if numeric is None:
        return None, unit_family

    if not unit_family:
        inferred_family = infer_unit_family_from_unit(unit)
        unit_family = inferred_family or unit_family

    return numeric, unit_family


def infer_unit_family_from_unit(unit: str | None) -> str | None:
    if not unit:
        return None
    text = unit.strip().lower()
    if text in {"kg", "g", "ton", "t"} or "gram" in text:
        return "mass"
    if text in {"kwh", "mj", "gj"} or "joule" in text:
        return "energy"
    if text in {"m3", "nm3", "l", "litre", "liter"}:
        return "volume"
    if text in {"m2"}:
        return "area"
    return None


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
            return float(text)
        except ValueError:  # pragma: no cover - defensive
            LOGGER.debug("lci.units.parse_failed", value=value)
            return None
    return None
