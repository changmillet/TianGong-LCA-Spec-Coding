# LCI 方案 A：生命周期系统流优先级（Lifecycle System Flow Prioritization）

## 背景与目标
- 以 `test/process_data/f697c94d-80ff-4043-abf3-77156e5a4b8e.json` 等终止系统为样例，回答“下一步应追溯哪些产品/副产品的数据集，以及输出侧如何处置/跟踪”。  
- 聚焦 **Product/Waste flow**，在原材料/能源累计 90% 与辅料 Top2 的基础上，同时评估输出产品/废弃物流的下游处置优先级，并与 Dataset Review 行动项联动。

## 输入与依赖
- Stage 产物：`artifacts/<run_id>/cache/process_datasets.json`、`stage3_alignment.json`、`workflow_result.json`。  
- Flow/Unit 详情：`artifacts/<run_id>/exports/flows/*.json`、`flowproperties/*.json`、`unitgroups/*.json`（若缺失可通过 `ProcessRepositoryClient` 远程抓取）。  
- 模块依赖：`tiangong_lca_spec.core.units`、`tiangong_lca_spec.flow_alignment`、`src/tiangong_lca_spec/process_repository/repository.py`。  
- LLM Prompt：`.github/prompts/lci-flow-classification.prompt.md`（用于流类型标注）。

## 流分类策略
1. **Flow type 预筛**：从 flow JSON 读取 `flowType`，仅当值为 `Product flow` 或 `Waste flow` 时进入细分类与上游判断；`Elementary flow` 直接交给基线方案。  
2. **细分类别**：
   - **原材料**：输入方向，`usage_context` 含“原料/料液”等，或 flow 描述指向 feedstock。  
   - **能源**：名称/单位表明 electricity/steam/fuel（kWh/MJ 等）。  
   - **辅料/服务**：非原料/能源但用于催化、维护、包装等。  
   - **废弃物（非 Elementary）**：`flowType == "Waste flow"`。  
3. **LLM 加持**：对规则难以判定的流调用 prompt，输出 `{class_label, confidence, rationale}` 并缓存。

## 工作流程
1. **数据载入**：解析 `processDataSet.exchanges`；根据 `referenceToFlowDataSet` 追溯 flow → flow property → unit group。启动时一次性读取 `unitgroups/*.json`（共 13 条），构建 `{unit_group_uuid -> unit_family, reference_unit}` 映射；若未声明 reference unit，则取列表首项或 `@dataSetInternalID == "0"`。  
2. **单位换算**：按 `unit_family`（质量/能量/体积等）调用 `tiangong_lca_spec.core.units` 统一单位，记录换算路径，确保同量纲内累加。  
3. **分类与标签**：执行 Flow type 预筛 + LLM 分类，生成 `analysis/exchanges_enriched.parquet`。  
4. **占比计算与下游处置**：
   - 原材料：排序并累计至 90%，输出 `upstream_priority.raw_materials`。  
   - 能源：同理累计至 90%，输出 `upstream_priority.energy`。  
   - 辅料：按重量/数量排序取 Top2（单位不同需按照不同的单位排序），输出 `upstream_priority.auxiliaries`。  
   - 废弃物/产品输出：对 `exchangeDirection == "output"` 的 Product/Waste flow 统计占比，解析去向（landfill、recycling、onsite reuse 等）并形成 `downstream_priority.outputs`，供下游处置/追踪决策。  
5. **优先级评分**：结合占比、数据质量（来自 Review）、Stage3 对齐状态计算 `priority_score`，形成追溯清单与行动项。  
6. **报告与导出**：写入 `analysis/upstream_priority.json`、`analysis/action_items.md`，并在 `analysis/figures/` 输出堆叠/累计图。

## 模块与目录
```
src/tiangong_lca_spec/lci_analysis/upstream/
├─ cli.py                  # 命令入口（uv run tg lci upstream ...）
├─ models.py               # ExchangeRecord / UpstreamPriority 等
├─ loaders/                # datasets.py, flows.py, artifacts.py
├─ classifiers/            # rules.py + llm.py
├─ calculators/            # units.py, contributions.py, scoring.py
├─ reporters/              # summary_json.py, figures.py, action_items.py
└─ workflow.py             # 编排入口
```

## 结果与 QA
- 产物：`analysis/exchanges_enriched.parquet`, `analysis/upstream_priority.json`, `analysis/action_items.md`, `analysis/figures/*.png`。  
- QA 指标：  
  - 单位链路解析成功率 ≥ 98%，失败条目需在行动项中说明。  
  - 原材料/能源累计 90% 结果与 `test/process_data/f697c94d-80ff-4043-abf3-77156e5a4b8e.json` 基准一致。  
  - LLM 分类抽检一致率 ≥ 90%，记录 prompt 版本。  
  - 与 Dataset Review 的 `balances` 检查器交叉验证质量守恒。
