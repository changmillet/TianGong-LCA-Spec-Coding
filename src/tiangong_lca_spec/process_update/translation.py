"""Utility helpers for working with `pages_process.ts` translations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from tiangong_lca_spec.core.exceptions import SpecCodingError


@dataclass(frozen=True, slots=True)
class PagesProcessTranslation:
    """Lookup table from Chinese labels to translation keys."""

    _value_to_key: Mapping[str, str]

    def key_for_value(self, value: str) -> str | None:
        return self._value_to_key.get(value)


class PagesProcessTranslationLoader:
    """Parse the TypeScript translation file into reusable lookups."""

    _ENTRY_PATTERN = re.compile(r"'(?P<key>[^']+?)'\s*:\s*'(?P<value>[^']*?)'")

    def load(self, path: Path) -> PagesProcessTranslation:
        if not path.exists():
            raise SpecCodingError(f"Translation file '{path}' does not exist")
        mapping: dict[str, str] = {}
        for match in self._ENTRY_PATTERN.finditer(path.read_text(encoding="utf-8")):
            key = match.group("key").strip()
            value = match.group("value").strip()
            # Preserve the first occurrence to avoid overriding more specific labels.
            mapping.setdefault(value, key)
        if not mapping:
            raise SpecCodingError(f"No translation entries parsed from '{path}'")
        return PagesProcessTranslation(mapping)


__all__ = ["PagesProcessTranslation", "PagesProcessTranslationLoader"]
