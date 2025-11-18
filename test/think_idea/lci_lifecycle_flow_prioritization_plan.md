# LCI 方案 A：生命周期系统流优先级（Lifecycle System Flow Prioritization）

## 背景与目标
- 以 `test/process_data/f697c94d-80ff-4043-abf3-77156e5a4b8e.json` 等终止系统为样例，回答“下一步应追溯哪些产品/副产品的数据集，以及输出侧如何处置/跟踪”。  
- 聚焦 **Product/Waste flow**，在原材料/能源全量排序（仍输出 share/cumulative_share 方便聚焦 Top2 行动项）的基础上，同时评估输出产品/废弃物流的下游处置优先级，并与 Dataset Review 行动项联动。

## 输入与依赖
- Stage 产物：`artifacts/<run_id>/cache/process_datasets.json`、`stage3_alignment.json`、`workflow_result.json`。  
- 数据类型与引用链：所有 ILCD 实体均为独立 JSON。`process` → `flow` → `flowproperty` → `unitgroup` 形成单向引用；`exchange.amount/meanAmount` 的单位即 `unitgroup.referenceUnit`，因此需要沿链完整加载才能进行规范化。  
- Flow/Unit 详情：每次运行前从最新的 Stage3 `exports/`（或经 MCP 更新的临时目录）复制 `flows/`，并配合 `tiangong_reference_package/flowproperties|unitgroups` 使用，避免复用旧文件；单独 process JSON 场景需先执行 `--fetch-mcp-flows` 拉取缺失 flow。  
- 模块依赖：`tiangong_lca_spec.core.units`、`tiangong_lca_spec.flow_alignment`、`tiangong_reference_package/*`（flowProperty/unitGroup 基线）、`tiangong_lca_spec.core.llm`、`tiangong_lca_spec.lci_analysis.common.classifier_cache`、`openpyxl`（Excel 导出）以及 `lci_analysis.upstream.reference_usage`（Reference flow 统计）。  
- LLM Prompt：`.github/prompts/lci_flow_classification.prompt.md`；生命周期说明 `.github/prompts/lci-lifecycle-flow-prioritization.zh.prompt.md`。

## 流分类策略
1. **Rule-first**：运行 `classifiers/rules.py` 的启发式（方向、flowType、关键词），快速归类 `raw_material | energy | auxiliary | product_output | waste`，无缝覆盖绝大多数常规流。  
2. **LLM fallback**：若规则返回 `unknown`，读取完整 flow JSON（`FlowRegistry.get_flow_document`），携带 `exchange` 与流程级 context 调用 `.github/prompts/lci_flow_classification.prompt.md`；输出 `{class_label, confidence, rationale}` 并写入 `ClassifierCache`（如 `output_dir/cache/flow_classifier_cache.json`），避免重复耗费 token。  
3. **分类范围**：  
   - **原材料**：输入方向且描述为 feedstock/结构件/化学品；  
   - **能源**：名称/单位指向电力、燃料、蒸汽、气体等；  
   - **辅料/服务**：冷却介质、润滑/维护、公用工程、催化剂、包装、运输等；  
   - **主产品**：process 的 reference flow，直接判定为 `product_output`；  
   - **副产品 (`by_product`)**：`exchangeDirection == output & flowType == Product flow` 且 allocation 系数/产量明显小于主产品（例如 allocation fraction < 0.5 或 amount << reference flow）时，不经 LLM 直接标记为 `by_product`；  
   - **废弃物**：`flowType == "Waste flow"` 或名称含排放/尾矿/废水/废气；  
   - **Emission/resource**：输出流若 Flow 分类 Level 1 包含 emission，则强制标记为 `emission`；若分类为 natural resource，则标记为 `resource`；  
   - **Unknown**：LLM 仍无把握时返回并在结果 JSON 的 `unknown_classification` 中注明原因。

## 工作流程
1. **数据载入**：解析 `processDataSet.exchanges`；根据 `referenceToFlowDataSet` 追溯 flow → flow property → unit group。Stage3 场景直接引用该 run 的最新 `exports_refresh/`，单 process JSON 场景先执行 `--fetch-mcp-flows` 到本地 `exports_from_mcp/` 再加载。启动时一次性读取 `unitgroups/*.json`（共 13 条），构建 `{unit_group_uuid -> unit_family, reference_unit}` 映射；若未声明 reference unit，则取列表首项或 `@dataSetInternalID == "0"`。  
2. **单位换算**：按 `unit_family`（质量/能量/体积等）调用 `tiangong_lca_spec.core.units` 统一单位，记录换算路径，确保同量纲内累加。  
3. **分类与标签**：执行规则分类 → LLM fallback，生成 `analysis/exchanges_enriched.parquet` 并缓存 LLM 结果。  
4. **占比计算与下游处置**：
   - 原材料/能源/辅料：先按照单位族拆分（mass/energy/volume…），同一单位内按贡献排序并全部输出，字段包括 `flow_role`、`flow_type`、`exchange_name_zh`、`flow_name_zh`、`exchange_name_en`、`flow_name_en`、`flow_uuid`、`unit_family`、`reference_unit`、`total_amount`、`share`（百分比字符串）、`cumulative_share`、`dataset_uuid/name`、`classification_confidence`、`rationale`。  
   - 废弃物/产品输出：对 `exchangeDirection == "output"` 的 Product/Waste/Emission flow 统计占比，解析去向（landfill、recycling、onsite reuse 等）并形成 `downstream_priority.outputs`；结果保留全量条目。
5. **优先级评分**：结合占比、数据质量（来自 Review）、Stage3 对齐状态计算 `priority_score`，形成追溯清单与行动项。  
6. **报告与导出**：`analysis/upstream_priority.json` 中的 `upstream_priority` 顶层以 `unit_family` 为键，每个列表元素含 `reference_process_count` 等字段；`analysis/upstream_priority.xlsx` 生成 `Upstream/Downstream/Unknown/Actions/Notes/Metadata` 工作表，列顺序固定为 `dataset_uuid` → `reference_process_count`；开启 `--reference-flow-stats` 时额外写入 `reference_flow_stats.json` 与 `reference_processes/<flow_uuid>/*.json`。

## 模块与目录
```
src/tiangong_lca_spec/lci_analysis/upstream/
├─ cli.py                  # 命令入口，支持 --fetch-mcp-flows、--reference-flow-stats、--repository-state-code、--secrets、--classification-prompt、--classifier-cache
├─ models.py               # ExchangeRecord / PrioritySlice 等
├─ loaders/                # datasets.py, flows.py, artifacts.py
├─ classifiers/            # rules.py（启发式）, llm.py（LLM fallback）, service.py（组合调度）
├─ calculators/            # units.py, contributions.py, scoring.py
├─ reporters/              # summary_json.py, summary_excel.py, figures.py, action_items.py
├─ reference_usage.py      # Reference flow 使用统计与下载
└─ workflow.py             # 编排入口，加载 FlowClassifier，写入 schema_version=2
```

## 结果与 QA
- 产物：`analysis/exchanges_enriched.parquet`, `analysis/upstream_priority.json`, `analysis/upstream_priority.xlsx`, `analysis/action_items.md`, `analysis/figures/*.png`，可选 `reference_flow_stats.json` 与 `reference_processes/`。其中 `upstream_priority` 与 `downstream_priority` 均按 unit_family 聚合，后者额外包含 `downstream_path/downstream_action` 字段。
- QA 指标：  
  - 单位链路解析成功率 ≥ 98%，失败条目需在行动项中说明。  
  - 原材料/能源累计占比曲线与 `test/process_data/f697c94d-80ff-4043-abf3-77156e5a4b8e.json` 基准一致。  
  - LLM 分类抽检一致率 ≥ 90%，记录 prompt 版本。  
  - 与 Dataset Review 的 `balances` 检查器交叉验证质量守恒。

## 实践注意事项
- **Flow exports**：优先使用 Stage3 `exports/` 与 `tiangong_reference_package` 中的离线基线，必要时再协调运维补齐增量导出，默认不再在线拉取。  
- **OpenAI 凭据**：在 `.secrets/secrets.toml` 中配置 `[openai] api_key/model`，CLI 会根据 `--secrets` 参数加载（默认 `artifacts/<run_id>/cache/openai/upstream/` 缓存）。  
- **Prompt/缓存**：可通过 `--classification-prompt` 指定自定义 prompt；`--classifier-cache` 控制缓存文件位置，`--llm-cache-dir` 复用 Responses 缓存，便于人工抽查 LLM 输出。  
- **流程上下文**：Workflow 会将 `processInformation`、`modellingAndValidation`、`common:intendedApplications` 等摘要打包为 DatasetContext 传给 LLM；若用途信息缺失，请在 Stage 2/3 的 `generalComment` 或 intendedApplications 中补全。  
- **单数据集调试**：`test/process_data/*.json` 可直接作为 `--process-datasets` 输入，便捷验证规则+LLM 组合效果。  
- **输出结果**：`upstream_priority.json` 与 `.xlsx` 中包含 `share/cumulative_share`、`reference_process_count`、`downstream_priority.outputs`（含 `downstream_path/action`）、`unknown_classification` 与 `actions`；如启用 Reference flow 统计，则在 `metadata.reference_flow_stats` 中同步记录。  
- **后续扩展**：若引入更多量纲或流程分类，可在 `classifiers/llm.py` prompt 和 `rules.py` 中同步更新；必要时拓展 `ClassifierCache` 存储更多元数据。
