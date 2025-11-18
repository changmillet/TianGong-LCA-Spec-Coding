"""Lightweight persistent cache for flow classification results."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tiangong_lca_spec.core.logging import get_logger

LOGGER = get_logger(__name__)


@dataclass
class CacheEntry:
    flow_uuid: str
    label: str
    confidence: float | None
    rationale: str | None


class ClassifierCache:
    """Stores flow classification results on disk to avoid repeated LLM calls."""

    def __init__(self, cache_path: Path | str | None = None) -> None:
        self._path = Path(cache_path) if cache_path else None
        self._entries: dict[str, CacheEntry] = {}
        if self._path and self._path.exists():
            self._load()

    def get(self, flow_uuid: str) -> CacheEntry | None:
        return self._entries.get(flow_uuid)

    def set(self, entry: CacheEntry) -> None:
        self._entries[entry.flow_uuid] = entry

    def flush(self) -> None:
        if not self._path:
            return
        payload = {
            uuid: {
                "label": entry.label,
                "confidence": entry.confidence,
                "rationale": entry.rationale,
            }
            for uuid, entry in self._entries.items()
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        LOGGER.info("lci.classifier_cache.write", path=str(self._path), count=len(payload))

    def _load(self) -> None:
        try:
            payload: dict[str, Any] = json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            LOGGER.warning("lci.classifier_cache.decode_failed", path=str(self._path))
            return
        for uuid, info in payload.items():
            if not isinstance(info, dict):
                continue
            entry = CacheEntry(
                flow_uuid=uuid,
                label=str(info.get("label", "")),
                confidence=info.get("confidence"),
                rationale=info.get("rationale"),
            )
            self._entries[uuid] = entry
        LOGGER.info("lci.classifier_cache.loaded", count=len(self._entries), path=str(self._path))
