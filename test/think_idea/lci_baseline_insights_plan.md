# LCI 方案 B：B3AE 基线洞察（Baseline Insights & Reporting）

## 背景与目标
- 基于全量 `processDataSet`（含 Elementary flow）沉淀热点、边界覆盖、图表素材，形成可复用的 **B3AE 基线报告**。  
- 聚焦排放、系统边界、地理/技术路径与跨项目可比性，为指标展示与策略制定提供支撑。

## 输入与依赖
- Stage 产物：`artifacts/<run_id>/cache/process_datasets.json`、`stage3_alignment.json`、`workflow_result.json`、`tidas_validation.json`。  
- Flow/Unit 详情：`artifacts/<run_id>/exports/flows/*.json`、`flowproperties/*.json`、`unitgroups/*.json`。  
- Dataset Review 输出：`review_findings.json`、`review/figures/*.png`（作为质量权重与证据）。  
- 可选外部库：LCIA 系数（GWP/ADP）、地理/行业分类表。  
- 底层模块：与方案 A 共享 `lci_analysis/common`（loaders、units、LLM 分类缓存）。

## 分析要点
1. **Elementary flow 热点**：解析 `flowType == "Elementary flow"`，按空气/水体/土壤子类统计排放贡献，并输出 Top-N 与累计曲线。  
2. **边界覆盖**：结合 state_code==20 数据集与 Stage 产物，对比 `common:generalComment`、`modellingAndValidation` 等描述，识别遗漏的系统单元/共产品。  
3. **影响指标**：若提供 LCIA 系数，计算 GHG/资源枯竭；否则输出“量值占比 + 数据质量”双轴评分。  
4. **地理/技术路径洞察**：解析 `processInformation` 的地点、年份、技术路线，评估代表性并生成可视化。

## 工作流程
1. **数据聚合**：复用共享 loader，加载 Dataset Review 结果作为质量权重。  
2. **排放热点分析**：统一 Elementary flow 的单位（kg/m³ 等），生成热点矩阵、Sankey 图。  
3. **边界/技术路径解析**：必要时调用 LLM 将文本描述结构化，标注缺失与冲突。  
4. **指标计算**：基于量值 + LCIA（可选）得出流程/流贡献及代表性。  
5. **报告生成**：输出  
   - `analysis/baseline_summary.json`（机器可读），  
   - `analysis/baseline_brief.md`（人工审核骨架），  
   - `analysis/figures/`（Sankey、热点矩阵、时间序列等），  
   - 可选 `analysis/baseline.xlsx`。  
6. **与 Review 联动**：将边界/热点缺口反馈给 Dataset Review 的 `boundary_coverage`、`provenance` 检查器。

## 模块与目录
```
src/tiangong_lca_spec/lci_analysis/baseline/
├─ cli.py                  # 命令入口：uv run tg lci baseline ...
├─ models.py               # BaselineIndicator, HotspotSlice 等
├─ aggregators/            # emissions.py, boundary.py, representativeness.py
├─ lcia/                   # coefficients.py（可选，管理影响因子）
├─ reporters/              # summary_json.py, md_report.py, html_report.py
└─ workflow.py             # 串联 loader → aggregator → reporters
```
- 公共模块位于 `src/tiangong_lca_spec/lci_analysis/common/`（datasets.py、flows.py、units.py、classifier_cache.py、rules/），供 upstream/baseline 共享。

## 结果与 QA
- 产物：`analysis/baseline_summary.json`, `analysis/baseline_brief.md`, `analysis/figures/*`, 可选 `analysis/baseline.xlsx`。  
- QA 指标：  
  - Elementary flow 覆盖率 ≥ 99%，缺失条目列入 `baseline_brief.md`。  
  - 边界/技术路径解析正确率（抽样 10%）≥ 95%。  
  - 若计算 LCIA 指标，与参考值（如 `test/process_data/f697...`）差异 ≤ 5%。  
  - 报告引用的图表与 `analysis/figures` 保持一致，可通过 snapshot 测试校验。
