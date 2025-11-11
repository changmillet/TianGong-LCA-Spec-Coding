"""Fetch all process datasets for a user, run offline validation, and emit a summary log."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from tiangong_lca_spec.core.config import get_settings
from tiangong_lca_spec.core.exceptions import SpecCodingError
from tiangong_lca_spec.core.mcp_client import MCPToolClient
from tiangong_lca_spec.core.models import TidasValidationFinding
from tiangong_lca_spec.process_update import ProcessRepositoryClient
from tiangong_lca_spec.tidas_validation import TidasValidationService


@dataclass(slots=True)
class DatasetSnapshot:
    """Lightweight representation of a remote process record."""

    uuid: str
    version: str | None
    path: Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download process datasets for a user and run the local TIDAS validation."
    )
    parser.add_argument(
        "--user-id",
        default=None,
        help="Target user id; defaults to the configured platform user.",
    )
    parser.add_argument(
        "--service-name",
        default=None,
        help="Optional override for the MCP service name.",
    )
    parser.add_argument(
        "--list-limit",
        type=int,
        default=5000,
        help="Maximum number of process records to request from MCP.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory where downloaded datasets and logs will be written. "
        "Defaults to artifacts/user_validation/<user_id>/<timestamp>/",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Optional explicit path for the summary log file.",
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Download datasets and build the summary without invoking TIDAS validation.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = get_settings()
    service_name = args.service_name or settings.flow_search_service_name

    with MCPToolClient(settings) as client:
        repository = ProcessRepositoryClient(
            client,
            service_name,
            list_tool_name="Database_CRUD_Tool",
            list_table="processes",
            list_limit=args.list_limit,
        )
        user_id = _determine_user_id(args.user_id, repository)
        if not user_id:
            raise SystemExit("Unable to determine user id; supply --user-id or configure the platform user.")

        run_root, processes_dir, log_path = _prepare_output_locations(user_id, args.output_dir, args.log_file)
        snapshots = _download_process_datasets(repository, user_id, processes_dir)
        if not snapshots:
            _write_summary_log(
                log_path,
                user_id,
                snapshots,
                [],
                {},
                [],
            )
            print(f"No datasets fetched for user {user_id}; summary written to {log_path}")
            return

        findings: list[TidasValidationFinding] = []
        if not args.skip_validation:
            validator = TidasValidationService(
                settings,
                command=["uv", "run", "python", "-m", "tidas_tools.validate"],
            )
            findings = validator.validate_directory(run_root)

        errors_by_uuid, general_errors = _partition_validation_errors(findings)
        successes = [
            snapshot
            for snapshot in snapshots
            if not errors_by_uuid.get(snapshot.uuid)
        ]
        _write_summary_log(
            log_path,
            user_id,
            snapshots,
            successes,
            errors_by_uuid,
            general_errors,
        )

        print(f"Fetched {len(snapshots)} datasets for user {user_id} -> {processes_dir}")
        if args.skip_validation:
            print(f"Validation skipped; summary written to {log_path}")
        else:
            print(f"Validation complete; summary written to {log_path}")


def _determine_user_id(
    requested_user_id: str | None,
    repository: ProcessRepositoryClient,
) -> str | None:
    if isinstance(requested_user_id, str) and requested_user_id.strip():
        return requested_user_id.strip()
    detected = repository.detect_current_user_id()
    if isinstance(detected, str) and detected.strip():
        return detected.strip()
    return None


def _prepare_output_locations(
    user_id: str,
    output_dir: Path | None,
    log_file: Path | None,
) -> tuple[Path, Path, Path]:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = (output_dir or Path("artifacts") / "user_validation") / user_id / timestamp
    processes_dir = root / "processes"
    processes_dir.mkdir(parents=True, exist_ok=True)
    if log_file:
        final_log_path = log_file
    else:
        final_log_path = root / "validation_summary.log"
    final_log_path.parent.mkdir(parents=True, exist_ok=True)
    return root, processes_dir, final_log_path


def _download_process_datasets(
    repository: ProcessRepositoryClient,
    user_id: str,
    processes_dir: Path,
) -> list[DatasetSnapshot]:
    try:
        json_ids = repository.list_json_ids(user_id)
    except SpecCodingError as exc:
        print(f"Listing JSON ids failed: {exc}")
        return []

    snapshots: list[DatasetSnapshot] = []
    for json_id in json_ids:
        record = repository.fetch_record(
            "processes",
            json_id,
            preferred_user_id=user_id,
        )
        if not record:
            print(f"[warn] Record {json_id} missing; skipping.")
            continue
        record_user_id = record.get("user_id")
        if isinstance(record_user_id, str) and record_user_id.strip() and record_user_id.strip() != user_id:
            print(f"[warn] Record {json_id} belongs to user {record_user_id}; skipping.")
            continue
        document = _extract_document(record, json_id)
        if not document:
            print(f"[warn] Record {json_id} missing JSON payload; skipping.")
            continue
        version = _extract_version(record, document)
        target_path = processes_dir / f"{json_id}.json"
        target_path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        snapshots.append(
            DatasetSnapshot(
                uuid=json_id,
                version=version,
                path=target_path,
            )
        )
    return snapshots


def _extract_document(record: dict[str, Any], json_id: str) -> dict[str, Any] | None:
    payload = record.get("json_ordered") or record.get("json")
    if payload is None:
        return None
    if isinstance(payload, str):
        payload = payload.strip()
        if not payload:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            print(f"[warn] Record {json_id} contains invalid JSON: {exc}")
            return None
    if isinstance(payload, dict):
        return json.loads(json.dumps(payload))
    print(f"[warn] Record {json_id} has unsupported payload type {type(payload)!r}")
    return None


def _extract_version(record: dict[str, Any], document: dict[str, Any]) -> str | None:
    version = record.get("version")
    if isinstance(version, str) and version.strip():
        return version.strip()
    dataset = document.get("processDataSet")
    if isinstance(dataset, dict):
        version_override = dataset.get("@version")
        if isinstance(version_override, str) and version_override.strip():
            return version_override.strip()
    return None


def _partition_validation_errors(
    findings: Iterable[TidasValidationFinding],
) -> tuple[dict[str, list[str]], list[str]]:
    errors: dict[str, list[str]] = defaultdict(list)
    global_errors: list[str] = []
    for finding in findings:
        if finding.severity != "error":
            continue
        uuid = _uuid_from_path(finding.path)
        message = finding.message.strip()
        if uuid:
            errors[uuid].append(message)
        else:
            global_errors.append(message)
    return errors, global_errors


def _uuid_from_path(path: str | None) -> str | None:
    if not path:
        return None
    candidate = Path(path).stem
    text = candidate.strip()
    if len(text) == 36 and text.count("-") == 4:
        return text
    return None


def _write_summary_log(
    log_path: Path,
    user_id: str,
    snapshots: list[DatasetSnapshot],
    successes: list[DatasetSnapshot],
    errors_by_uuid: dict[str, list[str]],
    general_errors: list[str],
) -> None:
    lines: list[str] = []
    lines.append(f"user_id: {user_id}")
    lines.append(f"total_datasets: {len(snapshots)}")
    lines.append("datasets:")
    for snapshot in snapshots:
        version_text = snapshot.version or "unknown"
        lines.append(f"- {snapshot.uuid} (version: {version_text})")

    lines.append("")
    lines.append("validation_succeeded:")
    if successes:
        for snapshot in successes:
            version_text = snapshot.version or "unknown"
            lines.append(f"- {snapshot.uuid} (version: {version_text})")
    else:
        lines.append("- none")

    lines.append("")
    lines.append("validation_failed:")
    failures = [snapshot for snapshot in snapshots if errors_by_uuid.get(snapshot.uuid)]
    if failures or general_errors:
        for snapshot in failures:
            version_text = snapshot.version or "unknown"
            lines.append(f"- {snapshot.uuid} (version: {version_text})")
            for message in errors_by_uuid.get(snapshot.uuid, []):
                lines.append(f"  * {message}")
        if general_errors:
            lines.append("- general_errors:")
            for message in general_errors:
                lines.append(f"  * {message}")
    else:
        lines.append("- none")

    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    main()
