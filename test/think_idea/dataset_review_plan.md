# Dataset Review 开发方案

## 背景
- 当前工作流通过 `scripts/stage1_preprocess.py` → `stage4_publish.py` 以及 `src/tiangong_lca_spec/orchestrator/workflow.py` 产出 `processDataSet`、ILCD artifacts 与本地 TIDAS 校验结果，但缺少独立的“数据体检”环节。
- Stage 3 在导出 artifacts 时会执行一次 `tidas-validate` 并生成 `artifacts/<run_id>/cache/tidas_validation.json`，但不会给出字段级证据、回归对比、质量/能量守恒等分析。
- 结合仓库现状（`artifacts/<run_id>/cache/*`、`tiangong_lca_spec/tidas*`、`.github/prompts`），需要设计一个 Dataset Review 能力，为 Stage 3→Stage 4、B3AE 报告和人工复核提供结构化输入。

## 目标
1. 紧贴项目现有目录与脚本，构建 Validation（Schema+TIDAS）与内容 Review（LLM+LCI）两条流水线，确保 `processDataSet` 在结构与证据层面可追溯。
2. 复用 `tiangong_lca_spec.tidas_validation.TidasValidationService`、`tiangong_lca_spec.tidas` schema 摘要、`test/think_idea/lci_analysis_capability.md` 中规划的分析模块，输出机器可读的 `review_findings.json`。
3. 在 `artifacts/<run_id>/review/` 下沉淀日志、差异分析、证据清单、可视化等工件，为 Stage 4 发布、Cut-off 策略与回归测试提供依据。

## 输入与依赖
- **数据来源（必须双通道覆盖）**：  
  1. **Stage 1–4 产物**：由 `scripts/stage1_preprocess.py`→`scripts/stage4_publish.py` 以及 `src/tiangong_lca_spec/orchestrator/workflow.py` 生成，核心文件位于 `artifacts/<run_id>/cache/` 与 `artifacts/<run_id>/exports/`。  
  2. **数据库 `state_code == 20` 记录**：通过 `ProcessRepositoryClient`（`src/tiangong_lca_spec/process_repository/repository.py`）调用 `Database_CRUD_Tool` 的 `select` 操作，`list_extra_filters={"state_code": 20}` 拉取待审核条目，并用 `fetch_record()` 取回 `json/json_ordered`。
- **Stage 产物明细**：  
  - `artifacts/<run_id>/cache/process_datasets.json`（Stage 2/3 merge 输出，来源 `scripts/stage3_align_flows.py`）  
  - `artifacts/<run_id>/cache/stage3_alignment.json`、`stage1_clean_text.md`（用于内容对照）  
  - `artifacts/<run_id>/exports/`（ILCD process/flow/source JSON，供 TIDAS CLI 使用）
- **核心模块**：  
  - `tiangong_lca_spec.tidas_validation.TidasValidationService`（`src/tiangong_lca_spec/tidas_validation/service.py`）封装 `uv run tidas-validate`  
  - `tiangong_lca_spec.tidas.TidasSchemaRepository`（`src/tiangong_lca_spec/tidas/schema_loader.py`）生成字段清单  
  - `tiangong_lca_spec.workflow.artifacts.generate_artifacts`（`src/tiangong_lca_spec/workflow/artifacts.py`）必要时补 artifacts  
  - `test/think_idea/lci_analysis_capability.md` 覆盖的 LCI 分析脚本/图表
- **外部依赖**：`.secrets/secrets.toml` 中配置的 `tiangong_lca_remote`（MCP 远端）、OpenAI/LLM、`tidas-tools` CLI。
- **协同资产**：在 `.github/prompts/` 新增 `review-process-workflow.(en|zh).prompt.md`，统一 LLM 指令与输出格式。

## 工作流程
Dataset Review 在 Stage 3 之后运行，流程拆成 Preparation、Validation 轨、内容 Review 轨，最终合并输出。

### 0. Preparation
0. **“重新起跑”固定流程**  
   - **确认 run_id**：明确本轮重算的 run（例如 `f697c94d`），并记录在 issue/commit 描述中，避免误删其它 run 的产物。  
   - **备份必要文件**：若 `artifacts/<run_id>/analysis`、`exports` 或 `review/` 中有需要保留的 JSON/Excel/日志，将内容复制到 `notes/` 或单独分支。  
   - **清理旧 artifacts**：仅删除目标 run 相关目录，如 `rm -rf artifacts/<run_id>/{analysis,cache,exports,review}`；全局缓存（`artifacts/cache`、`artifacts/lci_demo`）保留以便回归对比。  
   - **重新执行 Stage/CLI**：按 Stage1→Stage4 以及 `uv run python -m tiangong_lca_spec.lci_analysis.upstream.cli …` 顺序重算，新的产物会在空目录下生成。  
   - **写入记录**：在 `review_manifest.json` 或 run README 中注明“已执行 clean restart”，并标记删除/重算的范围，方便 QA 追溯。
1. **解析 run 上下文**  
   - 使用 `_workflow_common.resolve_run_id()` 或读取 `artifacts/latest_run_id` 确定当前 Stage 产物；若 Stage 3 尚未执行，则调用 `scripts/stage3_align_flows.py` 产生 `process_datasets.json` 与 exports。
2. **加载 Stage 产物**  
   - 读取 `process_datasets.json`、`stage3_alignment.json`、`tidas_validation.json`，并利用 `tiangong_lca_spec.core.json_utils` 展平 `processDataSet`（DataFrame/pydantic），供 Validation/内容 Review 共享。
3. **拉取数据库 state_code==20 数据集**  
   - 通过 `ProcessRepositoryClient`（`src/tiangong_lca_spec/process_repository/repository.py`）实例化 MCP 客户端，设置 `list_tool_name="Database_CRUD_Tool"`、`list_extra_filters={"state_code": 20}`。  
   - 调用 `list_json_ids(user_id)` 收集目标记录，再用 `fetch_record("processes", json_id)` 获取 `json_ordered`。若某记录不属于当前用户，可用 `preferred_user_id` 再筛一遍。  
   - 将数据库记录与 Stage 产物按 UUID 去重合并，标记来源（`stage_artifact` / `db_state20`）以便后续比对。
4. **复核工作区**  
   - 在 `artifacts/<run_id>/review/` 下初始化 `snapshots_index.json`（含 UUID、版本、来源、哈希）与 `review_manifest.json`（列出所有产物）。

### Validation 轨（Schema + CLI + 回归）
> 本轨道对“Stage 产物 + state_code==20 的数据库记录”合并后的全集执行校验。

1. **Schema/结构检查**  
   - 通过 `TidasSchemaRepository.summarize_properties("tidas_processes.json", pointer)` 构建 checklist，校验 `processInformation`、`modellingAndValidation`、`administrativeInformation`、`exchanges.exchange`。  
   - 检测字段缺失、非法单位、重复 `@dataSetInternalID`、无 `FlowSearch hints` 等，写入 `review_findings.json.validation.schema`.
2. **TIDAS CLI 复跑**  
   - 使用 `TidasValidationService(command=["uv","run","tidas-validate","-i", exports_dir])` 对 `artifacts/<run_id>/exports` 再次校验，捕获 `TidasValidationFinding`。  
   - 按 dataset UUID 聚合 error/warning/info，写入 `review_findings.json.validation.cli` 与 `validation_summary.log`。
3. **回归检测**  
   - 与 `review/snapshots_index.json`（上一 run）比对 `exchanges` 数量、`quantitativeReference`、`LCIMethod`、哈希；  
   - 如出现 `exchanges` 下降 >30%、版本未变但哈希变化等情况，记入 `review_findings.json.validation.regression`，并生成 `diff_summary.md`.

### 内容 Review 轨（LLM + 知识库 + LCI）
1. **文献定位/Chunk 抓取**  
   - 根据 `processInformation.common:generalComment`、`modellingAndValidation.dataSourcesTreatmentAndRepresentativeness.referenceToDataSource`、Stage 1 clean text中的引用，调用 MCP `tiangong_lca_remote` 获取文献段落。  
   - 将 chunk 存入 `review/evidence/<process_uuid>/chunk_<n>.md`，并在 `evidence_manifest.json` 中记录来源、页码、哈希。
2. **字段级 Prompt 验证**  
   - 以 `.github/prompts/review-process-workflow*.prompt.md` 为模板，为每个 dataset 生成结构化提示：包含字段值、对应 chunk、Stage 1 clean text片段、必要的单位/功能单位说明。  
   - LLM 输出 `{field: {match_status, evidence_chunk_id, correction, confidence}}`，写入 `review_findings.json.content.field_checks`。
3. **LCI Analysis 加持**  
   - 根据 `test/think_idea/lci_analysis_capability.md` 执行：  
     - 质量守恒：对比输入/输出/副产品总量；  
     - 能量合理性：结合 Stage 3 `matched_flows` 与技术路线判断能耗；  
     - 污染物完整度：利用 FlowSearch hints 和常见排放清单识别遗漏。  
   - 输出 `review_findings.json.content.lci_analysis`，并在 `review/figures/*.png` 绘制 Pareto、过程-流热力图。
4. **行动项整理**  
   - 统一 schema/TIDAS/LLM/LCI 的失败条目至 `action_items.md`，包含问题描述、证据链接、优先级、建议处理人，供 Stage 2/3 owner 跟进。

### 产物一览
- `validation_summary.log`、`review_findings.json`（含 `validation`、`content`、`regression`）  
- `snapshots_index.json`、`review_manifest.json`、`diff_summary.md`、`action_items.md`  
- `evidence_manifest.json`、`review/evidence/*`、`review/figures/*.png`

## 模块与目录设计
为便于工程化落地，Dataset Review 能力新增 `src/tiangong_lca_spec/review/` 模块，整体结构如下：

```text
src/tiangong_lca_spec/review/
├─ cli.py                    # 命令入口（例如 `uv run tg review ...`）
├─ models.py                 # ReviewJob / ReviewFinding / ReviewScore 等数据类
├─ rules/
│   ├─ common.yaml           # 通用规则阈值
│   └─ packaging.yaml        # 领域包复写（示例）
├─ checks/
│   ├─ schema_guard.py       # TIDAS 结构校验 + `tidas_validation` 结果解析
│   ├─ completeness.py       # 必填元数据、FlowSearch hints 完整性
│   ├─ balances.py           # 质量守恒/能量守恒检查（复用 LCI 分析指标）
│   ├─ units_consistency.py  # 单位链路、量纲一致性
│   ├─ boundary_coverage.py  # 系统边界覆盖度、关键单元遗漏
│   ├─ provenance.py         # 数据来源/时空/技术代表性与证据链
│   ├─ mapping_consistency.py# ILCD / EF 词汇一致性
│   └─ crossfile_diff.py     # 历史版本 diff、state_code==20 漂移检测
├─ scoring/
│   ├─ pedigree.py           # DQI/Pedigree 评分矩阵
│   └─ aggregator.py         # Finding 聚合、A/B/C 评级
├─ reporters/
│   ├─ md_report.py          # Markdown 报告
│   ├─ html_report.py        # 可嵌图 HTML
│   └─ xlsx_export.py        # 明细导出
└─ workflow.py               # 编排：加载数据 → 执行 checks → 触发 scoring/reporters
```

与现有代码的衔接：
- `cli.py` 调用 `workflow.run(review_job)`，并通过 `ProcessRepositoryClient`（`src/tiangong_lca_spec/process_repository/repository.py`）加载 Stage 产物 + state_code==20 数据。
- `checks/schema_guard.py` 复用 `tiangong_lca_spec.tidas_validation.TidasValidationService`，`checks/balances.py` 复用 `test/think_idea/lci_analysis_capability.md` 中的分析器。
- `reporters` 输出对应到文档所述的 `validation_summary.log`、`review_findings.json`、`action_items.md` 等文件。

## 审核内容或指标：
- **输入输出（exchange）完整性和一致性**：即包含原料（资源）、能源消耗、产品（副产品）、污染物、废弃物、单位/量纲一致性、系统边界覆盖度。
- **Schema 完整率**：`processDataSet` 关键模块非空率 ≥ 95%，单位/ID 校验通过率 ≥ 98%；低于阈值直接阻断 Stage 4。  
- **TIDAS 稳定度**：`tidas-validate` error 数量 / dataset ≤ 5%；CLI 版本必须与 `pyproject.toml` 中 `tidas-tools` 对齐，变更后 24h 内完成升级。  
- **证据覆盖率**：≥ 85% 的重点字段（功能单位、地理/时间、LCI amount）有 chunk 或 Stage 1 清洗文本作佐证，LLM 需输出匹配或修订建议。  
- **LCI 健康度**：质量/能量守恒通过率 ≥ 95%；污染物遗漏列表必须为空或附补数计划，否则列为高优先级行动项。  
- **回归守卫**：若 `exchanges` 数量跌幅 >30%、`quantitativeReference` 或 `LCIMethod` 改变未在文档说明、或 dataset 哈希变化但版本未更新，自动标为 blocking。  
- **测试钩子**：在 `test/test_orchestrator_tidas.py` 或新增测试中断言 `review_findings.json` 的统计值，防止回归；同时引入样例数据（如 `test/process_data/model_electricity.json`）用于 CI 端的最小化 review。
- **state_code=20 覆盖率**：通过 `ProcessRepositoryClient` 拉取的 `state_code == 20` 数据集必须 100% 进入 Review 管线，并在 `review_manifest.json` 中记录来源。

## 集成与后续
1. **CLI 入口**：新增 `scripts/review_dataset.py --run-id <id> [--skip-cli] [--prompt review-process-workflow.prompt.md]`，沿用 `_workflow_common` 的 run cache 逻辑。  
2. **Orchestrator Hook**：在 `WorkflowOrchestrator._validate` 之后增加可选的 `enable_review` 分支，便于 `scripts/run_test_workflow.py --review` 一站式运行。  
3. **Prompt 管理**：在 `.github/prompts/` 中维护 review prompt，与现有 `extract-process` prompt 一致地记录版本与示例。  
4. **文档/指南**：更新 `README.md`、`AGENTS.md` QA 章节，说明 review 命令、输入输出、常见问题。  
5. **自动化**：CI 可配置可选 job，在关键目录（`src/tiangong_lca_spec/tidas*`, `test/think_idea/*`, `.github/prompts/*`）变更时自动运行 dataset review 的最小子集，并与黄金 `review_findings.json` 比对，及时捕获回归。
