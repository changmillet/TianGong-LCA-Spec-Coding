# ecoinvent 非 elementary flow 对比工作流指引

本文档基于 `plan/ecoinvent-compare.md`，描述 Codex 在执行 ecoinvent Flow ↔ Tiangong Flow 对比时的完整流程、产物路径、断点续跑策略与自检要点。默认工作目录为仓库根目录，所有 Python 命令使用 `uv run`。

## 前置约定与角色
- 角色：Codex 负责串联 flow 解析、FlowSearch、process 引用与汇总导出；用户只需提供 ecoinvent 数据、MCP 凭据与运行需求。
- 优先走标准命令：使用下文 CLI 模板跑全流程；非必要不改路径/参数，避免反复 `--help` 或手写中间文件。
- 输入先校验：确认 `ecoinvent/flows/*.xml`、`ecoinvent/processes/*.xml` 存在且格式正确；缺文件或解析报错先修复再跑。
- MCP/CRUD 预检：若首次跑，先用 1 条样本调用 FlowSearch/CRUD 验证联通性和凭据，避免长时间超时后才发现配置问题。
- 重试边界：同一请求最多重试 2 次；持续失败时记录原因（超时/工具缺失/权限），不要无限迭代。

## 1. 目标与产物
- 输入：`ecoinvent/flows/*.xml` 与 `ecoinvent/processes/*.xml`（ILCD 格式）。
- MCP 服务：`tiangong_lca_remote`，需在 `.secrets/secrets.toml` 中配置 `url/service_name/tool_name/api_key/timeout`。常用工具：
  - `Search_Flows_Tool`：FlowSearch 主检索，返回 Tiangong flow 候选；
  - `Database_CRUD_Tool`：读取 Tiangong flow/process 数据集（用于 flow_type 填充与 process 引用校验）；
  - `Search_Processes_Tool`：按 flow_uuid/名称检索引用该 flow 的 Tiangong process。
- 输出目录：
  1. `artifacts/cache/flow_usage_details.full.jsonl`：记录每个 ecoinvent flow 的 process usage 详情；
  2. `artifacts/cache/flow_search_results.full.jsonl`：FlowSearch 结果（增量写入，可断点续跑）；
  3. `artifacts/cache/tiangong_flow_process.jsonl`：Tiangong flow → process 引用缓存；
  4. `artifacts/ecoinvent_flow_similarity.full.json`：最终 JSON（汇总 flow、FlowSearch、process 引用、错误信息等）；
  5. `artifacts/flow_search_overview.xlsx`：Excel 报表（统计面板 + FlowSearch + Process 引用 + 最终 JSON）。

## 2. 工作流分段
1. **Flow 解析与索引**：流式解析 `flows/*.xml`，保留 `UUID/baseName/classification/flowProperties/typeOfDataSet` 等信息，写入 `FlowRecord` 与 `flow_usage_details.full.jsonl`。
2. **Process usage 统计**：解析 `processes/*.xml`，按 `referenceToFlowDataSet/@refObjectId` 关联 flow，统计 `usage_count/exchange_occurrences`，并保留全部 `process_usages` 详情。
3. **候选列表生成**：默认全量保留（可通过 `--min-usage` 调整），输出 `top_flows:list[FlowUsageSummary]`。
4. **FlowSearch（增量 + 断点续跑）**：
   - 针对每个 flow 构造 `FlowQuery(exchange_name=name, description=分类末级/同义词)`；
   - 使用 `FlowSearchService` 调用 `Search_Flows_Tool`，结果写入 `artifacts/cache/flow_search_results.full.jsonl`；
   - 再跑同一命令：已有非空 `matches` 的 flow 会被跳过；`matches` 为空的 flow 仅在 `--retry-empty-matches` 开启时才会重搜；
   - FlowSearch 仅使用返回的首条候选作为匹配，不启用 LLM 辅助选择；
   - 若配置了 `--flow-dataset-cache`，会调用 CRUD 拉取 Tiangong flow 数据集，缓存到 `flow_datasets/` 并回填 `flow_type_tiangong`；失败原因记录到 `notes`（例如 `flow_dataset:flow_dataset_fetch_failed`）。
5. **Tiangong flow 占位同步**：基于最新 FlowSearch 结果，同步 `tiangong_flow_process.jsonl`，为每个匹配到的 Tiangong flow 写入空引用占位，供后续 process fetch 填充。
6. **Process 引用补齐**：
   - 读取 Tiangong flow UUID，依次调用 `Search_Processes_Tool` 和 `Database_CRUD_Tool`；
   - 检查获取到的 process ILCD JSON 是否引用该 flow，引用结果写入 `tiangong_flow_process.jsonl`；若未命中，则记录 `process_reference_note`（如 `process_search_failed`、`process_reference_missing` 等），最终 JSON 的 `notes` 会包含这些标签；
   - 支持断点续跑：只对 `tiangong_flow_process.jsonl` 中 `references` 为空的 flow 进行搜索/CRUD；如配置 `--process-dataset-cache` 会缓存原始 process 数据集。
7. **汇总与导出**：
   - `artifacts/ecoinvent_flow_similarity.full.json`：包含 flow 元信息、FlowSearch 匹配、process 引用、`notes`（`low_similarity`、`process_search_failed` 等）；
   - `artifacts/flow_search_overview.xlsx`：包含统计（Sheet1）、FlowSearch 明细（Sheet2）、Tiangong process 引用（Sheet3）、最终 JSON（Sheet4）。

## 3. CLI 指南
推荐一次性执行以下命令完成所有步骤：

```bash
uv run python scripts/ecoinvent_compare_flows.py \
  --enable-search \
  --enable-process-fetch \
  --retry-empty-matches \
  --flow-usage-details artifacts/cache/flow_usage_details.full.jsonl \
  --search-output artifacts/cache/flow_search_results.full.jsonl \
  --process-cache artifacts/cache/tiangong_flow_process.jsonl \
  --process-dataset-cache artifacts/cache/process_datasets \
  --flow-dataset-cache artifacts/cache/flow_datasets \
  --output artifacts/ecoinvent_flow_similarity.full.json \
  --excel-output artifacts/flow_search_overview.xlsx
```

后台运行示例（追加日志文件、进度显示，避免终端阻塞）：
```bash
nohup uv run python scripts/ecoinvent_compare_flows.py \
  --enable-search \
  --enable-process-fetch \
  --retry-empty-matches \
  --flow-usage-details artifacts/cache/flow_usage_details.full.jsonl \
  --search-output artifacts/cache/flow_search_results.full.jsonl \
  --process-cache artifacts/cache/tiangong_flow_process.jsonl \
  --process-dataset-cache artifacts/cache/process_datasets \
  --flow-dataset-cache artifacts/cache/flow_datasets \
  --output artifacts/ecoinvent_flow_similarity.full.json \
  --excel-output artifacts/flow_search_overview.xlsx \
  --log-file artifacts/logs/ecoinvent_compare.nohup.log \
  --show-progress \
  > artifacts/logs/ecoinvent_compare.stdout.log 2>&1 &
```

说明：
- FlowSearch / Process 引用均具备断点续跑能力：若日志出现 `flow_search.interrupted` 或 process fetch 报错，重复执行同一命令即可继续；
- 需要局部调试时，可在命令中加入 `--min-usage 4000` 等参数，限制参与流程的 flow 数量；
- 命令执行完成后，`artifacts/cache/flow_search_results.full.jsonl` 和 `artifacts/cache/tiangong_flow_process.jsonl` 会记录全部中间结果，方便后续分析或导出。

## 4. 自检与日志
1. **Flow usage**：关注 `flow_usage.completed` 日志，确认 `flows_with_usage` 与输入规模一致；如 XML 解析失败，会打印 `flow_usage.parse_failed`。
2. **FlowSearch**：
   - `flow_search.lookup/request/response`：确认请求参数与候选数量合理；
   - `flow_search.filtered_out`：查看被过滤候选是否需要加同义词；
   - `flow_search.interrupted`：表示搜索未完成，需重跑；`flow_search.completed` 表示成功写回；
   - `notes` 中的 `flow_dataset:*` 表示 CRUD 拉取 flow 数据集失败或被禁用，需检查服务/凭证。
3. **Process 引用**：`process_fetcher.*` 日志将指明 search/CRUD 是否失败、是否找到引用；`process_reference_note` 会写入最终 JSON。
4. **产物自检**：
   - `flow_usage_details.full.jsonl`：随机检查某个 `flow_uuid`，确认 `process_usages` 数量与 `usage_count` 一致；
   - `flow_search_results.full.jsonl`：确认 `matches` 与 `notes` 符合预期（如 `low_similarity`、`Flow search returned no candidates`）；
   - `tiangong_flow_process.jsonl`：确认 Tiangong flow 的 `references` 是否包含 process UUID；
   - `flow_search_overview.xlsx`：打开 Excel 验证统计/明细是否齐全。

## 5. 常见风险
1. **MCP 超时**：`flow_search.timeout` 或 `process_search_failed` 可能由网络/服务端波动导致；可在 `.secrets` 中调大 `timeout` 或分批执行。
2. **Process 引用缺失**：若 Tiangong flow 未引用任何 process，会在 `notes` 中记录 `process_reference_missing`；需后续人工确认是否需创建新 flow/过程。
3. **Flow/Process CRUD 配置缺失**：`flow_dataset_fetch_failed` 或 `process_crud_failed` 多为工具未配置或凭证问题，需检查 MCP 服务端和 `.secrets`。
4. **输出体积**：全量 JSON / Excel 可达数十 MB，确保 `artifacts/` 未被纳入 git。
5. **版本更新**：当 `ecoinvent/` 或 Tiangong reference 数据更新时，务必重新执行全流程并检查 `summary.git_revision`。
