# 天工 LCI 生命周期流优先级分析指引

本说明面向 Codex，在 Tiangong LCA Spec Coding 项目中开展生命周期系统流追踪、上游/下游优先级分析时保持统一写法。关于环境准备与协同规范，请参考 `AGENTS.md`/`AGENTS_ZH.md`。**注意：工作站无裸 `python`，所有脚本必须以 `uv run python …` 或 `uv run python -m module` 方式执行。**

## 0. 执行约定
- **尊重阶段产物**：生命周期优先级分析建立在 Stage 1~3 产物之上（尤其是 `process_datasets.json` 与 `exports/flows|flowproperties|unitgroups`），如缺失原始产物先补齐，不要手写替代文件。
- **覆盖上游+下游**：结果需同时回答“上游追溯优先级”“下游处置优先级”，禁止只输出原材料列表或只看废弃物。
- **统一命令模板**：复用下述 CLI（`uv run python -m tiangong_lca_spec.lci_analysis.upstream.cli …`），不要反复跑 `--help`。若需额外参数，再单独说明。
- **日志即记录**：所有关键输出（路径、统计）都要通过 `structlog` 或 CLI 日志打印，便于 QA 查询；手动推理的 JSON 仅用于临时讨论，交付必须来源于 CLI 写入的文件。
- **凭据假设已配置**：`.secrets/secrets.toml` 默认可用；遇到缺失/超时再回报用户并附带日志片段。
- **LLM 调用遵循提示**：本文件定义的输出 JSON 是 prompt 的唯一基准，任何新增字段需先更新此文档。

## 1. 输入与依赖
- `artifacts/<run_id>/cache/process_datasets.json` 或单一 `processDataSet` 样例（如 `test/process_data/f697c94d-80ff-4043-abf3-77156e5a4b8e.json`）。
- Flow/Unit 详情：`artifacts/<run_id>/exports/flows/*.json`、`flowproperties/*.json`、`unitgroups/*.json`；缺失时可用 MCP 自动补齐。
- 关键模块：
  - `tiangong_lca_spec.lci_analysis.upstream.workflow`（核心编排）。
  - `tiangong_lca_spec.lci_analysis.common.FlowRegistry`、`units.normalise_amount`。
  - `ProcessRepositoryClient` + `FlowBundleFetcher`（MCP 拉取 flow 数据）。
- 运行目录：默认写入 `artifacts/<run_id>/analysis/` 或自定义的 `--output-dir`。

## 2. 可用工具与命令
- **Stage 脚本**：`scripts/stage1_preprocess.py` → `stage2_extract_processes.py` → `stage3_align_flows.py`（必要时 `stage4_publish.py`）。全部通过 `uv run python scripts/<stage>.py …` 调用。
- **生命周期优先级 CLI**：
  ```bash
  uv run python -m tiangong_lca_spec.lci_analysis.upstream.cli \
    --process-datasets artifacts/<run_id>/cache/process_datasets.json \
    --flows-dir artifacts/<run_id>/exports/flows \
    --flow-properties-dir artifacts/<run_id>/exports/flowproperties \
    --unit-groups-dir artifacts/<run_id>/exports/unitgroups \
    --output-dir artifacts/<run_id>/analysis \
    --run-id <run_id> [--fetch-mcp-flows] \
    [--secrets .secrets/secrets.toml] \
    [--classification-prompt .github/prompts/lci_flow_classification.prompt.md] \
    [--classifier-cache artifacts/<run_id>/analysis/cache/flow_classifier_cache.json] \
    [--llm-cache-dir artifacts/<run_id>/analysis/cache/openai/upstream] \
    [--flows-dir tiangong_reference_package/flows] \
    [--flow-properties-dir tiangong_reference_package/flowproperties] \
    [--unit-groups-dir tiangong_reference_package/unitgroups]
  ```
  - `--fetch-mcp-flows`：使用 `.secrets` 中的 MCP 凭据批量抓取缺失的 flow/flowProperty/unitGroup；可配合 `--mcp-service-name`、`--mcp-export-root`。
- **辅助 CLI**：`scripts/list_*_children.py` 系列用于查询 ILCD 分类层级；`scripts/run_test_workflow.py` 可在 CI 下跑回归。
- **MCP 自检**：随手写 5 行脚本调用 `FlowSearchService`，确认网络/凭据后再跑 Stage 3+优先级分析。

## 3. 角色与任务
- **Codex**：解析 process dataset、关联标准 flow、统一单位，并完成上游/下游优先级分析。
- **LLM**：根据 prompt 输出结构化 JSON（分类+占比+行动项），调用前须先整理完输入数据，避免把原始 XML 直接丢给模型。
- **工具脚本**：提供批处理、MCP 抓取与单元换算，支撑 LLM 生成可交付的 JSON。

## 4. 数据准备流程
1. **载入数据集**：`load_process_datasets` 自动兼容多种 schema；CLI 未传入 `datasets` 时默认从 `--process-datasets` 读取。
2. **构建 FlowRegistry**：优先读取本地 `exports/`；若为空，启用 `--fetch-mcp-flows` 写入 `output_dir/exports/`，再二次运行。
3. **单位规范化**：`normalise_amount` 尝试识别 unit_family；若缺失，在结果 `notes` 中补充 `unit_family_missing`。
4. **分类**：Rule-first（`classifiers/rules.py`）产出 `label/confidence/rationale`；若仍为 `unknown`，LLM fallback 会读取完整 flow JSON 以及 `processInformation`/`modellingAndValidation`/`common:intendedApplications` 等 DatasetContext 信息再做判断，并把结果写入 `classifier_cache`。
5. **累计排序**：`calculators.build_priority_slices` 负责原材料/能源累计到 ≥90%；`build_downstream_slices` 聚合产品/废弃物；`build_default_actions` 自动草拟高/中优先行动项。
6. **写入结果**：`write_summary_json` 生成 `output_dir/upstream_priority.json`，schema_version=2；所有对用户的反馈都应引用该文件。

## 5. 输出要求
- **文件位置**：`<output_dir>/upstream_priority.json`。
- **JSON 结构**：
  ```jsonc
  {
    "upstream_priority": {
      "<unit_family>": [
        {
          "exchange_name_zh": "string",
          "flow_name_zh": "string",
          "exchange_name_en": "string",
          "flow_name_en": "string",
          "flow_uuid": "string",
          "category": "raw_materials | energy | auxiliaries",
          "unit_family": "mass | energy | ...",
          "reference_unit": "kg | MJ | ...",
          "total_amount": 123.45,
          "share": "12.34%",
          "cumulative_share": "45.67%",
          "classification_confidence": 0.9,
          "rationale": "依据",
          "dataset_uuid": "string",
          "dataset_name": "中文; English"
        }
      ]
    },
    "downstream_priority": { ... },
    "unknown_classification": [ ... ],
    "actions": [ ... ],
    "notes": [ ... ],
    "metadata": { ... }
  }
  ```
- **数值规范**：`share`/`cumulative_share` 使用百分号字符串，保留四位有效数字；无法计算时填 `null`。
- **行动项**：优先列 `high/medium`，覆盖上游追溯与下游处置，证据引用具体流名/占比/日志位置。

## 6. LLM 提示策略
- 在调用 LLM 之前，先整理好输入 JSON（含 `run_context`、`dataset`、`flow_details`、`unit_groups`、`review_flags` 等字段）。
- LLM 需：
  1. 判定每条 exchange 的 `class_label`、信心与证据；
  2. 计算各分类贡献并截断到 90%（辅料取 Top2）；
  3. 为输出流推断 `downstream_path`（landfill/recycle/reuse/unknown）；
  4. 记录 `unknown_classification` 的原因；
  5. 汇总行动项/备注，确保与 CLI schema 一致。
- 产出的 JSON 必须与上节格式完全一致，不要额外添加解释文本；如某字段缺失，返回空数组。

## 7. QA 与排障
- **缺少 unit_family**：确认 `exports/unitgroups` 是否存在；无则运行 CLI 时加 `--fetch-mcp-flows`。
- **Flow 匹配困难**：先在 `scripts/list_*_children.py` 或 MCP 中查询分类，再更新 Stage 2 的 `FlowSearch hints`。
- **输出为空**：检查 `process_datasets` 是否真含 `exchanges.exchange`；必要时用 `uv run python - <<'PY'` 简单遍历确认。
- **重新生成结果**：删除 `output_dir/upstream_priority.json` 后重跑 CLI，确保日志中出现 `lci.upstream.workflow.complete`。

按照以上指引，Codex 能在生命周期优先级分析中与 Stage 1~3 产物、MCP 工具保持一致，快速输出可追溯、可入库的 JSON 结果。
