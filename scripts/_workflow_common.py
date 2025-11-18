"""Shared utilities for staged Tiangong LCA workflow scripts."""

from __future__ import annotations

import json
import shutil
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tiangong_lca_spec.core.llm import OpenAIResponsesLLM, load_openai_credentials


def load_secrets(path: Path) -> tuple[str, str]:
    """Load OpenAI API credentials from the secrets file."""
    return load_openai_credentials(path)


def load_paper(path: Path) -> str:
    """Load the paper content, accepting raw markdown or JSON fragments."""
    raw = path.read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(parsed, dict) and "result" in parsed:
        fragments = [item.get("text", "") for item in parsed["result"] if isinstance(item, dict) and item.get("text")]
        return json.dumps(fragments, ensure_ascii=False)
    return raw


def dump_json(data: Any, path: Path) -> None:
    """Write JSON to disk with UTF-8 encoding, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


ARTIFACTS_ROOT = Path("artifacts")
LATEST_RUN_ID_PATH = ARTIFACTS_ROOT / ".latest_run_id"
RUN_CACHE_DIRNAME = "cache"
RUN_EXPORT_DIRNAME = "exports"


def generate_run_id() -> str:
    """Return a UTC timestamp-based identifier, e.g., 20251030T053000Z."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ensure_run_root(run_id: str) -> Path:
    """Create (if needed) and return the root directory for a run."""
    run_root = ARTIFACTS_ROOT / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    return run_root


def ensure_run_cache_dir(run_id: str) -> Path:
    """Create (if needed) and return the cache directory for a run."""
    run_root = ensure_run_root(run_id)
    cache_dir = run_root / RUN_CACHE_DIRNAME
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def ensure_run_exports_dir(run_id: str, *, clean: bool = False) -> Path:
    """Create (if needed) and return the exports directory for a run."""
    run_root = ensure_run_root(run_id)
    export_root = run_root / RUN_EXPORT_DIRNAME
    if clean and export_root.exists():
        shutil.rmtree(export_root)
    for name in ("processes", "flows", "sources"):
        (export_root / name).mkdir(parents=True, exist_ok=True)
    return export_root


def resolve_run_id(run_id: str | None) -> str:
    """Return the provided run ID or fall back to the most recent run."""
    if run_id:
        return run_id
    latest = load_latest_run_id()
    if latest:
        return latest
    raise SystemExit("Run ID not provided and no previous run metadata found. " "Run stage1_preprocess first or supply --run-id explicitly.")


def load_latest_run_id(path: Path = LATEST_RUN_ID_PATH) -> str | None:
    """Load the latest run identifier recorded on disk, if any."""
    if not path.exists():
        return None
    run_id = path.read_text(encoding="utf-8").strip()
    return run_id or None


def save_latest_run_id(run_id: str, path: Path = LATEST_RUN_ID_PATH) -> None:
    """Persist the most recent run identifier for subsequent stages."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(run_id, encoding="utf-8")


def run_cache_path(run_id: str, relative: str | Path) -> Path:
    """Return a path under the run-specific cache directory."""
    cache_dir = ensure_run_cache_dir(run_id)
    return cache_dir / Path(relative)
