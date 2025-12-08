# ecoinvent 非 elementary flow 对比方案

## 1. 背景与目标
- 目标：基于 `ecoinvent/` 目录下的 ILCD 数据，梳理所有 `Product flow` / `Waste flow` 的使用情况，调用 Tiangong MCP 搜索相似流并补齐 process 引用，最终输出结构化 JSON + Excel 报表。
- 数据规模：`ecoinvent/flows` 14,051 条（Product 3,265，Waste 1,002），`ecoinvent/processes` 25,419 个。必须使用流式解析与增量缓存以避免内存爆炸。
- 凭证：根据 `AGENTS.md` 配置 `.secrets/secrets.toml`（`tiangong_lca_remote` 默认提供 `url/service_name/tool_name/api_key/timeout`），首次集成前建议先调用 `FlowSearchService` 做连通性自检。

## 2. 输入数据
1. **Flow 元数据**：解析 `ecoinvent/flows/*.xml`，保留 `UUID`、`baseName`、`synonyms`、`classification`、`flowProperties`、`version`、`typeOfDataSet` 等信息，写入 `FlowRecord`。
2. **Process 引用**：解析 `ecoinvent/processes/*.xml`，读取 `processInformation`、`geography`、`exchanges.exchange.referenceToFlowDataSet` 等字段；若 `refObjectId` 命中 `FlowRecord`，记录 `ProcessUsage`（方向、数量、defaultProvider、comment）。
3. **Tiangong MCP**：
   - FlowSearch：`Search_Flows_Tool`（REST/SSE），由 `FlowSearchService` 管理；
   - Process 引用：`Search_Processes_Tool`（按 Tiangong flow UUID + 名称关键字检索候选 process IDs）+ `Database_CRUD_Tool`（读取 `processes` 表 json_ordered，检查 `referenceToFlowDataSet`/`quantitativeReference` 是否引用该 flow）。
4. **Excel 报表**：使用 `openpyxl` 生成 `artifacts/flow_search_overview.xlsx`，包含统计面板和 3 份明细表。

## 3. 分析流程
1. **Flow 过滤与索引**
   - `xml.etree.ElementTree.iterparse` 流式遍历 `flows/*.xml`；
   - 仅保留 `Product flow` / `Waste flow`，构建 `flow_index = {uuid: FlowRecord}`；
   - 落盘 `artifacts/cache/flow_usage_details.full.jsonl`，记录 `FlowUsageSummary` 及完整 `process_usages`。

2. **Process usage 统计**
   - 遍历 `processes/*.xml`：
     - 解析 `process_uuid/process_name/geography`；
     - 遍历 `exchanges.exchange`，若 `referenceToFlowDataSet/@refObjectId` 在 `flow_index` 中，则记录 `ProcessExchangeUsage`；
   - 对每个 flow 统计 `usage_count`（引用独立 process 数）、`exchange_occurrences`（总 exchange 行数）。

3. **排序与过滤**
   - 默认保留所有 flow，可通过 `--min-usage`、`--flow-type` 等参数做局部调试；
   - 输出 `top_flows = list[FlowUsageSummary]`，用于 FlowSearch 输入与最终 JSON。

4. **FlowSearch（增量 + 断点续跑）**
   - 遍历 `top_flows`，构造 `FlowQuery(exchange_name=name, description=classification末级或同义词)`；
   - 核心逻辑：
     - 结果写入 `artifacts/cache/flow_search_results.full.jsonl`，文件中 1 行=1 flow，若已有 `matches` 则跳过；
     - 每处理 50 条就 flush 到磁盘，异常或 `Ctrl+C` 时会输出 `flow_search.interrupted`（“中断，请继续执行”），重跑同一命令即可继续；
     - `FlowCandidate.flow_name` 统一格式为 `base_name; treatment; location; flow_property`；
     - `errors` 字段记录超时/无候选等原因。

5. **Process 引用补齐**
   - 基于 Tiangong flow UUID，依次调用：
     1. `Search_Processes_Tool`：`query = "flow_uuid:<uuid> <combined_name>"`，获取候选 process IDs；
     2. `Database_CRUD_Tool`：`operation=select table=processes id=<uuid>`，读取 ILCD JSON；
     3. 检查 `quantitativeReference.referenceToReferenceFlow` / `exchanges.exchange.referenceToFlowDataSet` 是否引用该 flow；
   - 结果写入 `artifacts/cache/tiangong_flow_process.jsonl`；若未找到引用，记录 `process_reference_note`（`process_search_failed` / `process_dataset_missing` / `process_reference_missing` 等）；
   - 与 FlowSearch 一样支持断点续跑：每次只处理 `references` 为空的 Tiangong flow。

6. **最终 JSON + Excel 汇总**
   - `artifacts/ecoinvent_flow_similarity.full.json`：
     - `source`：flow/process 目录、版本、git revision；
     - `summary`：flow/process 总数、匹配数量、是否启用 search/process-fetch；
     - `results[]`：包含 ecoinvent flow 元信息、`tiangong_matches`（含 `process_reference_count/note`）、`notes`（`low_similarity`、`process_search_failed` 等）；
   - `artifacts/flow_search_overview.xlsx`：
     1. `stats`：高层统计；
     2. `flow_search_matches`：来自 `flow_search_results.full.jsonl` 的所有匹配；
     3. `tiangong_process_refs`：`tiangong_flow_process.jsonl` 的引用详情；
     4. `final_similarity`：最终 JSON 的表格化视图。

## 4. CLI 执行指引
标准命令（一次性完成所有步骤）：

```bash
uv run python scripts/ecoinvent_compare_flows.py \
  --enable-search \
  --enable-process-fetch \
  --flow-usage-details artifacts/cache/flow_usage_details.full.jsonl \
  --search-output artifacts/cache/flow_search_results.full.jsonl \
  --output artifacts/ecoinvent_flow_similarity.full.json \
  --excel-output artifacts/flow_search_overview.xlsx
```

说明：
- FlowSearch/Process 引用均具备断点续跑机制：如日志出现 `flow_search.interrupted` 或 process fetch 错误，重跑同一命令即可继续完成；
- 可通过 `--min-usage`、`--process-search-limit` 等参数限制执行范围；
- 所有中间产物存储于 `artifacts/cache/`，便于复盘或手动编辑。

## 5. 注意事项与风险
1. **MCP 超时**：若 FlowSearch 或 Process 工具偶发超时，日志会写入 `Flow search timeout` / `process_search_failed`，重跑即可继续；可视情况在 `.secrets` 调高 `timeout`。
2. **process 引用缺失**：若某 Tiangong flow 仅匹配到流程但未引用该 flow，会在 `notes` 中记录 `process_reference_missing`；需与业务团队确认是否创建新 flow/过程。
3. **输出体积**：全量 JSON 和 Excel 可能较大（几十 MB）。确保 `artifacts/` 未被纳入 git。
4. **版本管理**：如 `ecoinvent/` 或 Tiangong reference 数据更新，务必重新执行全流程并检查 `summary.git_revision`。
