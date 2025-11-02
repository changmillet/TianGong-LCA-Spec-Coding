"""CLI entry point for the write-process workflow."""

from __future__ import annotations

import argparse
from pathlib import Path

from tiangong_lca_spec.core.config import get_settings
from tiangong_lca_spec.core.mcp_client import MCPToolClient
from tiangong_lca_spec.process_update import ProcessRepositoryClient, ProcessWriteWorkflow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Populate remote process JSON fields.")
    parser.add_argument(
        "--user-id",
        required=False,
        default=None,
        help="User identifier to query MCP for (defaults to secrets configuration when omitted).",
    )
    parser.add_argument(
        "--requirement",
        default="test/requirement/write_data.yaml",
        type=Path,
        help="Path to the requirement markdown file.",
    )
    parser.add_argument(
        "--translation",
        default="test/requirement/pages_process.ts",
        type=Path,
        help="Path to the pages_process.ts translation file.",
    )
    parser.add_argument(
        "--output-dir",
        default=Path("artifacts") / "write_process",
        type=Path,
        help="Directory where enriched JSON files will be written.",
    )
    parser.add_argument(
        "--log-file",
        default=str(Path("artifacts") / "write_process" / "write_process_workflow.log"),
        help="Path to the workflow log file (set empty to disable).",
    )
    parser.add_argument(
        "--service-name",
        default=None,
        help="Override MCP service name (defaults to flow search service).",
    )
    parser.add_argument(
        "--list-tool",
        default="Database_CRUD_Tool",
        help="MCP tool name used to list JSON identifiers.",
    )
    parser.add_argument(
        "--fetch-tool",
        default="Fetch_Process_JSON",
        help="MCP tool name used to retrieve a process JSON document.",
    )
    parser.add_argument(
        "--list-table",
        default="processes",
        help="Database table used when listing JSON identifiers via Database_CRUD_Tool.",
    )
    parser.add_argument(
        "--list-limit",
        type=int,
        default=5000,
        help="Maximum number of rows to request when listing JSON identifiers.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1,
        help="Number of JSON identifiers to process (<=0 means all).",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    settings = get_settings()
    service_name = args.service_name or settings.flow_search_service_name
    log_path = Path(args.log_file) if args.log_file else None

    configured_user_id = settings.platform_user_id
    effective_user_id = configured_user_id or (args.user_id.strip() if args.user_id else None)
    with MCPToolClient(settings) as client:
        repository = ProcessRepositoryClient(
            client,
            service_name,
            list_tool_name=args.list_tool,
            fetch_tool_name=args.fetch_tool,
            list_table=args.list_table,
            list_limit=args.list_limit,
        )
        workflow = ProcessWriteWorkflow(repository)
        workflow.run(
            user_id=effective_user_id,
            requirement_path=args.requirement,
            translation_path=args.translation,
            output_dir=args.output_dir,
            log_path=log_path,
            limit=args.limit,
        )


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    main()
