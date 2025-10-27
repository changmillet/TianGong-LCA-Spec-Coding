"""High-level workflow that enriches remote process datasets."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from tiangong_lca_spec.core.exceptions import SpecCodingError

from .reference_resolver import ReferenceMetadataResolver
from .repository import ProcessRepositoryClient
from .requirements import RequirementLoader
from .translation import PagesProcessTranslationLoader
from .updater import ProcessJsonUpdater


@dataclass(slots=True)
class WorkflowLogger:
    """Collects notes that need manual follow-up."""

    path: Path | None
    _messages: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._messages = []

    def log(self, message: str) -> None:
        self._messages.append(message)

    def flush(self) -> None:
        if self.path is None:
            return
        if not self._messages:
            if self.path.exists():
                self.path.unlink()
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as handle:
            for message in self._messages:
                handle.write(f"{message}\n")


class ProcessWriteWorkflow:
    """Coordinates requirement parsing, MCP retrieval, and dataset updates."""

    def __init__(
        self,
        repository: ProcessRepositoryClient,
        *,
        requirement_loader: RequirementLoader | None = None,
        translation_loader: PagesProcessTranslationLoader | None = None,
        resolver: ReferenceMetadataResolver | None = None,
    ) -> None:
        self._repository = repository
        self._requirements = requirement_loader or RequirementLoader()
        self._translations = translation_loader or PagesProcessTranslationLoader()
        self._resolver = resolver or ReferenceMetadataResolver(repository)

    def run(
        self,
        *,
        user_id: str,
        requirement_path: Path,
        translation_path: Path,
        output_dir: Path,
        log_path: Path | None = None,
        limit: int = 1,
    ) -> list[Path]:
        requirement_entries = self._requirements.load(requirement_path)
        translations = self._translations.load(translation_path)
        logger = WorkflowLogger(log_path)
        updater = ProcessJsonUpdater(translations, logger, resolver=self._resolver)

        json_ids = self._repository.list_json_ids(user_id)
        selected_ids = self._select_ids(json_ids, limit)
        if not selected_ids:
            raise SpecCodingError(f"No process JSON ids available for user '{user_id}'")

        output_dir.mkdir(parents=True, exist_ok=True)
        written_paths: list[Path] = []
        for json_id in selected_ids:
            document = self._repository.fetch_process_json(json_id)
            updated_document = updater.apply(document, requirement_entries)
            target_path = output_dir / f"{json_id}.json"
            target_path.write_text(
                json.dumps(updated_document, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            written_paths.append(target_path)

        logger.flush()
        return written_paths

    @staticmethod
    def _select_ids(ids: Iterable[str], limit: int) -> list[str]:
        if limit <= 0:
            return [item for item in ids]
        selected: list[str] = []
        for json_id in ids:
            selected.append(json_id)
            if len(selected) >= limit:
                break
        return selected


__all__ = ["ProcessWriteWorkflow", "WorkflowLogger"]
