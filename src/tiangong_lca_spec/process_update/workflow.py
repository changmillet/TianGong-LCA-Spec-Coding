"""High-level workflow that enriches remote process datasets."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

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

        account_user_id = self._repository.detect_current_user_id()
        if not account_user_id:
            account_user_id = user_id.strip() if user_id else None
        if not account_user_id:
            raise SpecCodingError(
                "Unable to determine authenticated user id for write-process workflow"
            )

        output_dir.mkdir(parents=True, exist_ok=True)
        written_paths: list[Path] = []
        for json_id in selected_ids:
            record = self._repository.fetch_record("processes", json_id)
            if not record:
                logger.log(f"[{json_id}] record not found; skipping update.")
                continue

            state_code = record.get("state_code") if isinstance(record, Mapping) else None
            record_user_id = (
                record.get("user_id").strip()
                if isinstance(record, Mapping) and isinstance(record.get("user_id"), str)
                else None
            )
            if state_code != 0:
                logger.log(
                    f"[{json_id}] skipped: state_code={state_code!r} (read-only); no update applied."
                )
                continue
            if record_user_id and record_user_id != account_user_id:
                logger.log(
                    f"[{json_id}] skipped: record owned by user {record_user_id}, current api user "
                    f"is {account_user_id}; no update applied."
                )
                continue

            document_payload = record.get("json") or record.get("json_ordered")
            if isinstance(document_payload, str):
                try:
                    document = json.loads(document_payload)
                except json.JSONDecodeError as exc:
                    logger.log(
                        f"[{json_id}] failed to parse JSON payload ({exc}); skipping update."
                    )
                    continue
            elif isinstance(document_payload, Mapping):
                document = json.loads(json.dumps(document_payload))
            else:
                logger.log(
                    f"[{json_id}] unsupported record payload type "
                    f"{type(document_payload)!r}; skipping update."
                )
                continue

            document = self._repository.fetch_process_json(json_id)
            analysis = updater.analyse(document, requirement_entries)
            scope_summary = analysis.describe_scope()

            if not analysis.needs_update():
                logger.log(
                    f"[{json_id}] requirements satisfied ({scope_summary}); no update applied."
                )
                if analysis.available_process_names and analysis.matched_process_name is None:
                    logger.log(
                        f"[{json_id}] available process requirements: "
                        + ", ".join(analysis.available_process_names)
                    )
                if analysis.unsupported_labels:
                    logger.log(
                        f"[{json_id}] unsupported requirements: "
                        + ", ".join(sorted(set(analysis.unsupported_labels)))
                    )
                continue

            logger.log(
                f"[{json_id}] update plan ({scope_summary}): {analysis.describe_missing()}"
            )
            if analysis.available_process_names and analysis.matched_process_name is None:
                logger.log(
                    f"[{json_id}] no matching process requirement found in: "
                    + ", ".join(analysis.available_process_names)
                )
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
