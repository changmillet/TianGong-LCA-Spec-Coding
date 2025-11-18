"""Helpers for loading process dataset bundles."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tiangong_lca_spec.core.logging import get_logger

LOGGER = get_logger(__name__)


def load_process_datasets(path: Path | str) -> list[dict[str, Any]]:
    """Load the merged process dataset JSON written by Stage 3.

    The file typically contains either:
    - {"process_datasets": [...]}  (preferred structure), or
    - {"processDataSets": [...]}   (legacy naming), or
    - a bare list of processDataSet blocks.
    """

    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"process_datasets file not found: {file_path}")

    payload = json.loads(file_path.read_text(encoding="utf-8"))
    datasets: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        for key in ("process_datasets", "processDataSets"):
            value = payload.get(key)
            if isinstance(value, list):
                datasets = [item for item in value if isinstance(item, dict)]
                break
        else:
            # Handle single dataset serialized under "processDataSet"
            single = payload.get("processDataSet")
            if isinstance(single, dict):
                datasets = [payload]
            else:
                LOGGER.warning(
                    "lci.load_process_datasets.unknown_format",
                    path=str(file_path),
                    keys=list(payload.keys()),
                )
    elif isinstance(payload, list):
        datasets = [item for item in payload if isinstance(item, dict)]

    if not datasets:
        raise ValueError(f"process_datasets file has no datasets: {file_path}")

    LOGGER.info("lci.load_process_datasets.loaded", count=len(datasets), path=str(file_path))
    return datasets
