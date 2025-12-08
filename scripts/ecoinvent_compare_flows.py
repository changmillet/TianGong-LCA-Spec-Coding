#!/usr/bin/env python3
"""
CLI orchestrating the ecoinvent non-elementary flow comparison workflow.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from tiangong_lca_spec.core.config import get_settings
from tiangong_lca_spec.core.logging import configure_logging
from tiangong_lca_spec.ecoinvent_compare import ProcessFetchConfig, RunConfig, run_workflow


def _build_parser() -> argparse.ArgumentParser:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Compare ecoinvent non-elementary flows with Tiangong search results.",
    )
    parser.add_argument(
        "--flow-dir",
        type=Path,
        default=Path("ecoinvent/flows"),
        help="Directory containing ecoinvent flow XML files (default: %(default)s).",
    )
    parser.add_argument(
        "--process-dir",
        type=Path,
        default=Path("ecoinvent/processes"),
        help="Directory containing ecoinvent process XML files (default: %(default)s).",
    )
    parser.add_argument(
        "--flow-usage-details",
        type=Path,
        default=settings.cache_dir / "flow_usage_details.jsonl",
        help="Path to write the flow usage JSONL file (default: %(default)s).",
    )
    parser.add_argument(
        "--search-output",
        type=Path,
        default=settings.cache_dir / "flow_search_results.jsonl",
        help="Path to write the FlowSearch JSONL output (default: %(default)s).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=settings.artifacts_dir / "ecoinvent_flow_similarity.json",
        help="Final aggregated JSON path (default: %(default)s).",
    )
    parser.add_argument(
        "--detail-reference-path",
        type=Path,
        help="Override the path recorded inside the final JSON detail_reference (defaults to --flow-usage-details).",
    )
    parser.add_argument(
        "--min-usage",
        type=int,
        default=1,
        help="Minimum number of referencing processes required to keep a flow (default: %(default)s).",
    )
    parser.add_argument(
        "--enable-search",
        action="store_true",
        help="Enable Tiangong FlowSearch stage (disabled by default).",
    )
    parser.add_argument(
        "--flow-search-server",
        help="Optional override for the MCP FlowSearch server name (defaults to settings.flow_search_service_name).",
    )
    parser.add_argument(
        "--flow-search-tool",
        help="Optional override for the MCP FlowSearch tool name.",
    )
    parser.add_argument(
        "--enable-process-fetch",
        action="store_true",
        help="Enable Tiangong process reference lookups (requires search + CRUD tools).",
    )
    parser.add_argument(
        "--process-server",
        help="MCP server name for the process search/CRUD tools (defaults to FlowSearch server).",
    )
    parser.add_argument(
        "--process-search-tool",
        default="Search_Processes_Tool",
        help="Tool name for Tiangong process search (default: %(default)s).",
    )
    parser.add_argument(
        "--process-crud-tool",
        default="Database_CRUD_Tool",
        help="Tool name for retrieving Tiangong process JSON data (default: %(default)s).",
    )
    parser.add_argument(
        "--process-cache",
        type=Path,
        default=settings.cache_dir / "tiangong_flow_process.jsonl",
        help="Cache file for Tiangong process references (default: %(default)s).",
    )
    parser.add_argument(
        "--process-dataset-cache",
        type=Path,
        default=settings.cache_dir / "process_datasets",
        help="Directory to cache raw Tiangong process datasets (default: %(default)s).",
    )
    parser.add_argument(
        "--flow-dataset-cache",
        type=Path,
        default=settings.cache_dir / "flow_datasets",
        help="Directory to cache raw Tiangong flow datasets (default: %(default)s).",
    )
    parser.add_argument(
        "--process-search-limit",
        type=int,
        default=10,
        help="Maximum number of process search candidates to inspect (default: %(default)s).",
    )
    parser.add_argument(
        "--excel-output",
        type=Path,
        default=settings.artifacts_dir / "flow_search_overview.xlsx",
        help="Path to write the Excel overview workbook (default: %(default)s).",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=settings.artifacts_dir / "logs" / "ecoinvent_compare.log",
        help="Path to write workflow logs (use '-' to log to stdout).",
    )
    parser.add_argument(
        "--show-progress",
        action="store_true",
        help="Print a simple progress indicator to stdout (enabled automatically when logging to a file).",
    )
    parser.add_argument(
        "--retry-empty-matches",
        action="store_true",
        help="Re-run FlowSearch for flows whose cached matches are empty.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    settings = get_settings()
    log_path = args.log_file
    if log_path and str(log_path) == "-":
        log_path = None
    show_progress = args.show_progress or (log_path is not None)
    configure_logging(settings=settings, log_path=log_path)
    process_server = args.process_server or args.flow_search_server or settings.flow_search_service_name
    process_fetch_cfg = ProcessFetchConfig(
        enabled=args.enable_process_fetch,
        server_name=process_server,
        search_tool_name=args.process_search_tool,
        crud_tool_name=args.process_crud_tool,
        cache_path=args.process_cache,
        dataset_cache_dir=args.process_dataset_cache,
        max_search_results=max(1, args.process_search_limit),
    )
    run_config = RunConfig(
        flow_dir=args.flow_dir,
        process_dir=args.process_dir,
        detail_path=args.flow_usage_details,
        search_output_path=args.search_output,
        final_output_path=args.output,
        detail_reference_path=args.detail_reference_path,
        min_usage=max(1, args.min_usage),
        enable_search=args.enable_search,
        flow_search_server=args.flow_search_server,
        flow_search_tool=args.flow_search_tool,
        process_fetch=process_fetch_cfg,
        excel_output_path=args.excel_output,
        show_progress=show_progress,
        retry_empty_matches=args.retry_empty_matches,
        flow_dataset_cache_dir=args.flow_dataset_cache,
    )
    run_workflow(run_config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
