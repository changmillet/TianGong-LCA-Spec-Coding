"""Command-line entry point for lifecycle flow prioritisation."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from tiangong_lca_spec.core.config import get_settings
from tiangong_lca_spec.core.llm import OpenAIResponsesLLM, load_openai_credentials
from tiangong_lca_spec.core.logging import get_logger
from tiangong_lca_spec.core.mcp_client import MCPToolClient
from tiangong_lca_spec.lci_analysis.common import load_process_datasets
from tiangong_lca_spec.lci_analysis.common.classifier_cache import ClassifierCache
from tiangong_lca_spec.lci_analysis.upstream.classifiers import FlowClassifier, LLMFlowClassifier
from tiangong_lca_spec.lci_analysis.upstream.workflow import (
    LifecycleFlowPrioritizationWorkflow,
    WorkflowInputs,
)
from tiangong_lca_spec.process_repository import FlowBundleFetcher, ProcessRepositoryClient

LOGGER = get_logger(__name__)
DEFAULT_PROMPT_PATH = Path(".github/prompts/lci_flow_classification.prompt.md")
DEFAULT_PROMPT_TEXT = """\
# LCI Flow 分类
- 根据 exchange direction、单位、flowType、flow 文本判断类别；
- 可选标签：raw_material, energy, auxiliary, product_output, waste, unknown；
- 输出 JSON: {"class_label": "...", "confidence": 0-1, "rationale": "说明"}；
- 若无法判定，返回 unknown 并说明缺失信息。\
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--process-datasets", type=Path, required=True, help="Path to process_datasets.json.")
    parser.add_argument("--flows-dir", type=Path, help="Directory containing flow JSON exports.")
    parser.add_argument("--flow-properties-dir", type=Path, help="Directory containing flowproperty JSON exports.")
    parser.add_argument("--unit-groups-dir", type=Path, help="Directory containing unitgroup JSON exports.")
    parser.add_argument("--output-dir", type=Path, required=True, help="Directory for analysis outputs.")
    parser.add_argument("--run-id", help="Optional run identifier for metadata.")
    parser.add_argument(
        "--fetch-mcp-flows",
        action="store_true",
        help="Fetch missing flow/flowProperty/unitGroup records via MCP and persist to --mcp-export-root.",
    )
    parser.add_argument(
        "--mcp-service-name",
        help="Override MCP service name (defaults to flow_search_service_name from settings).",
    )
    parser.add_argument(
        "--mcp-export-root",
        type=Path,
        help="Directory where fetched flow bundles will be written (defaults to --output-dir/exports).",
    )
    parser.add_argument(
        "--secrets",
        type=Path,
        default=Path(".secrets/secrets.toml"),
        help="Secrets file containing OpenAI credentials for LLM-based flow classification.",
    )
    parser.add_argument(
        "--classification-prompt",
        type=Path,
        help="Override path for the LLM flow-classification prompt template.",
    )
    parser.add_argument(
        "--classifier-cache",
        type=Path,
        help="Path to persist LLM classification cache JSON (defaults to <output_dir>/cache/flow_classifier_cache.json).",
    )
    parser.add_argument(
        "--llm-cache-dir",
        type=Path,
        help="Directory for raw LLM response cache (defaults to <output_dir>/cache/openai/upstream).",
    )
    parser.add_argument(
        "--reference-flow-stats",
        action="store_true",
        help="Fetch repository processes that use each flow as the reference flow and count them.",
    )
    parser.add_argument(
        "--repository-user-id",
        help="Explicit repository user id to use when listing processes (auto-detected when omitted).",
    )
    parser.add_argument(
        "--repository-state-code",
        type=int,
        help="Optional state_code filter applied when listing repository processes.",
    )
    parser.add_argument(
        "--reference-process-export-dir",
        type=Path,
        help="Directory where reference processes will be downloaded (defaults to <output_dir>/reference_processes).",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    datasets = load_process_datasets(args.process_datasets)
    flows_dir = args.flows_dir
    flow_properties_dir = args.flow_properties_dir
    unit_groups_dir = args.unit_groups_dir

    repository: ProcessRepositoryClient | None = None
    mcp_client: MCPToolClient | None = None
    try:
        need_repository = args.fetch_mcp_flows or args.reference_flow_stats
        if need_repository:
            settings = get_settings()
            service_name = args.mcp_service_name or settings.flow_search_service_name
            extra_filters = {}
            if args.repository_state_code is not None:
                extra_filters["state_code"] = args.repository_state_code
            mcp_client = MCPToolClient(settings)
            repository = ProcessRepositoryClient(
                mcp_client,
                service_name,
                list_tool_name="Database_CRUD_Tool",
                list_extra_filters=extra_filters or None,
            )

        if args.fetch_mcp_flows:
            if repository is None:
                raise SystemExit("--fetch-mcp-flows requires MCP repository access")
            export_root = args.mcp_export_root or args.output_dir / "exports"
            fetcher = FlowBundleFetcher(repository)
            _prefetch_flow_bundles(datasets, fetcher, export_root)
            flows_dir = flows_dir or export_root / "flows"
            flow_properties_dir = flow_properties_dir or export_root / "flowproperties"
            unit_groups_dir = unit_groups_dir or export_root / "unitgroups"

        classifier_cache: ClassifierCache | None = None
        llm_classifier: LLMFlowClassifier | None = None
        llm_client = _maybe_create_llm(
            args.secrets,
            args.llm_cache_dir or args.output_dir / "cache" / "openai" / "upstream",
        )
        if llm_client:
            cache_path = args.classifier_cache or args.output_dir / "cache" / "flow_classifier_cache.json"
            classifier_cache = ClassifierCache(cache_path)
            prompt_text = _load_prompt(args.classification_prompt)
            llm_classifier = LLMFlowClassifier(llm_client, prompt_text, cache=classifier_cache)

        flow_classifier = FlowClassifier(llm_classifier=llm_classifier)
        workflow = LifecycleFlowPrioritizationWorkflow(classifier=flow_classifier)
        reference_export_dir = args.reference_process_export_dir
        inputs = WorkflowInputs(
            process_datasets=args.process_datasets,
            flows_dir=flows_dir,
            flow_properties_dir=flow_properties_dir,
            unit_groups_dir=unit_groups_dir,
            output_dir=args.output_dir,
            run_id=args.run_id,
            repository=repository if args.reference_flow_stats else None,
            repository_user_id=args.repository_user_id,
            reference_flow_stats=args.reference_flow_stats,
            reference_process_export_dir=reference_export_dir,
        )
        result_path = workflow.run(inputs, datasets=datasets)
        if classifier_cache:
            classifier_cache.flush()
        LOGGER.info("lci.upstream.cli.done", result=str(result_path))
    finally:
        if mcp_client:
            mcp_client.close()


def _prefetch_flow_bundles(
    datasets: list[dict[str, Any]],
    fetcher: FlowBundleFetcher,
    export_root: Path,
) -> None:
    for dataset in datasets:
        pd = dataset.get("processDataSet") or dataset
        exchanges = _extract_exchanges(pd)
        for exchange in exchanges:
            bundle = fetcher.fetch_bundle(exchange.get("referenceToFlowDataSet"))
            fetcher.persist_bundle(bundle, export_root)


def _extract_exchanges(process_dataset: dict[str, Any]) -> list[dict[str, Any]]:
    exchanges = process_dataset.get("exchanges", {}).get("exchange")
    if isinstance(exchanges, list):
        return [item for item in exchanges if isinstance(item, dict)]
    return []


def _maybe_create_llm(secrets_path: Path | None, cache_dir: Path) -> OpenAIResponsesLLM | None:
    if not secrets_path or not secrets_path.exists():
        return None
    try:
        api_key, model = load_openai_credentials(secrets_path)
    except SystemExit:
        LOGGER.warning("lci.upstream.llm_secrets_missing", path=str(secrets_path))
        return None
    cache_dir.parent.mkdir(parents=True, exist_ok=True)
    return OpenAIResponsesLLM(api_key=api_key, model=model, cache_dir=cache_dir)


def _load_prompt(path: Path | None) -> str:
    candidate_paths = [path, DEFAULT_PROMPT_PATH]
    for candidate in candidate_paths:
        if candidate and candidate.exists():
            return candidate.read_text(encoding="utf-8").strip()
    return DEFAULT_PROMPT_TEXT.strip()


if __name__ == "__main__":  # pragma: no cover
    main()
