# 天工 LCA 数据集审核工作流指引（新版）

本说明定位于“过程数据集自动化审核”。请先阅读仓库根目录的 `AGENTS.md` 与《Tiangong LCA Spec Coding Development Guidelines》，再按本文的阶段划分执行校验。

## 0. 执行约定
- **先校验再审核**：所有流程数据集必须先通过结构化 validation（必做）与 TIDAS 校验（推荐）后，再进入审核步骤。
- **首选程序化判断**：除非出现模糊语义、缺乏结构化数据，否则不要调用 LLM。仓库已内置 `ProcessReviewService` 的规则化检查。
- **数据源可追溯**：每条审查结论需指向论文原文（Stage 1 输出的去结构化 JSON）或其它权威来源；审查报告应包含引用信息。
- **最小写回原则**：审核阶段仅写入审查元数据及报告；不要直接覆盖流程 JSON 或 Stage 3 产物。
- **日志留痕**：审查脚本需输出结构化日志（建议使用 `structlog`），保存于 `artifacts/review_process/<run_id>/review.log`。

## 1. 核心组件一览
- `src/tiangong_lca_spec/process_review/service.py`
  - `ProcessReviewService.review(...)`：统一入口，自动串联 validation → numeric checks → source 对比 → 字段校验 → 报告生成。
  - 内建常量 `INDEPENDENT_REVIEW_TYPE`，用于固化审查类型为 *Independent external review*。
- `src/tiangong_lca_spec/process_review/checks.py`
  - `structural_validation`：检查 `processInformation`、`modellingAndValidation`、`administrativeInformation`、`exchanges` 是否齐备。
  - `check_exchange_balance`：按单位（或单位缺失时的占位符）校验输入/输出是否守恒，默认绝对差容忍度 `1e-3`。
  - `cross_check_sources`：将流程 exchange 与来源数据（`SourceRecord`）比对，差异超过相对阈值 `1e-2` 时报错。
  - `check_field_content`：基于 `FieldDefinition` 对文本/多语字段做类型、枚举、正则校验。
- `src/tiangong_lca_spec/process_review/models.py`
  - `SourceRecord`：承载拆解后的论文段落或表格。核心字段：`identifier`、`text`、`quantity`、`unit`、`context`。
  - `FieldDefinition`：描述 schema 期望（`path`、`description`、`expected_type`、`allowed_values`、`pattern`等）。
  - `ProcessReviewResult`：聚合 validation、review findings、元数据以及生成的 `ReviewReport`。

## 2. 输入准备
1. **流程数据集**：优先使用 Stage 3 产出的 ILCD JSON，例如 `artifacts/write_process/012c079b-e14f-421b-94af-7b2d962721a4.json`。脚本需兼容：
   - 顶层 `processDataSet` 包含完整信息；
   - `exchanges.exchange` 为数组。
2. **数据来源 JSON**：依照 Stage 1 输出或运营提供的去结构化 JSON，抽取表格行/段落生成 `SourceRecord` 列表。例如：
   ```python
   import json
   from tiangong_lca_spec.process_review import SourceRecord

   with open("artifacts/write_process/unstructured/clean_text.json", "r", encoding="utf-8") as fh:
       payload = json.load(fh)

   source_records = [
       SourceRecord(
           identifier=item["id"],
           text=item["text"],
           quantity=float(item["value"]) if item.get("value") else None,
           unit=item.get("unit"),
           context={"page": item.get("page_number")},
       )
       for item in payload.get("tables", [])
   ]
   ```
3. **字段解释**：schema 描述（计划放入 `description-tidas` 字段）尚未落地前，可临时维护在 YAML/JSON，并在审核时转换为 `FieldDefinition`：
   ```python
   from tiangong_lca_spec.process_review import FieldDefinition

   field_definitions = [
       FieldDefinition(
           path=("processInformation", "dataSetInformation", "name", "baseName"),
           description="流程的中英文名称；多语字段需包含 @xml:lang 与 #text。",
           required=True,
           expected_type="multilang",
       ),
       FieldDefinition(
           path=("modellingAndValidation", "LCIMethodAndAllocation", "LCIMethodPrinciple"),
           description="建模方法原则，需要匹配 Tiangong TIDAS 枚举。",
           expected_type="string",
           allowed_values=[
               "Attributional",
               "Consequential",
               "Mixed",
           ],
       ),
   ]
   ```

## 3. 校验阶段（Validation）
1. 调用 `structural_validation(dataset)`，若存在 `error` 级别结果，即刻终止审核并反馈缺失项。
2. 若环境允许，运行 `uv run tidas-validate -i artifacts/<run_id>/exports` 再启动审核；失败时记录日志并在结论中加注。
3. 校验结果会写入 `ProcessReviewResult.validation_findings`，后续报告会一并输出。

## 4. 审核阶段（Review Checks）
### 4.1 数值平衡
- 默认绝对容忍度 `1e-3`；可在 `ProcessReviewService(balance_tolerance=...)` 自定义。
- 逻辑：将每条 exchange 规范化（方向、数量、推断单位），对输入设负号、输出设正号，同单位求和。
- 结果：`abs(净值) > tolerance` → `numeric_balance` 分类；若单位缺失，仅发出 `warning`，否则 `error`。

### 4.2 数据对比（Source Consistency）
- 通过 `_match_source_record` 计算 exchange 名称与 `SourceRecord`（标识、正文、上下文）之间的相似度，阈值默认 0.5。
- 若未匹配或来源缺数值 → 发出 `warning`/`info`；数值差异同时超过绝对差 `1e-3` 与相对差 `1e-2` → `error`。
- 建议在 `context` 中保留 `page`、`table_id` 等线索，便于报告引用。

### 4.3 字段内容
- 基于 `FieldDefinition` 校验文本结构、可选值、正则模式。
- 缺失必填字段 → `error`；不在允许枚举或未满足正则 → `warning`。
- 随着 `description-tidas` 上线，可自动从 schema 生成 `FieldDefinition`，减少手工维护。

## 5. 审核输出
- `ProcessReviewResult.metadata` 固定 `review_type="Independent external review"`，`scope`、`method` 由调用方传入，必须与需求文档（如 `test/requirement/write_data.yaml`）保持一致。
- `ReviewReport`（Markdown 字符串）包括：
  - 标题、UUID、参考年份；
  - 校验/审核发现（按严重级别列出，附证据、建议）；
  - 结束语提醒补齐问题后再发布。
- 使用方可将 `report.summary` 写入概览字段，再将 `report.details` 渲染为 Markdown/PDF。若后续提供 Word 模板，可在 CLI 中追加模板渲染逻辑。

## 6. 最小示例
```bash
uv run python - <<'PY'
from pathlib import Path
import json

from tiangong_lca_spec.process_review import (
    FieldDefinition,
    ProcessReviewService,
    SourceRecord,
)

dataset = json.loads(Path("artifacts/write_process/012c079b-e14f-421b-94af-7b2d962721a4.json").read_text())

sources = [
    SourceRecord(
        identifier="table-3-row-2",
        text="Hydropower electricity input 286.236 kWh",
        quantity=286.236,
        unit="kWh",
        context={"page": 12},
    ),
    # 根据实际去结构化 JSON 继续补充
]

definitions = [
    FieldDefinition(
        path=("processInformation", "dataSetInformation", "name", "baseName"),
        description="流程名称（含中英文 @xml:lang）。",
        required=True,
        expected_type="multilang",
    ),
]

service = ProcessReviewService()
result = service.review(
    dataset,
    scope="Tiangong LCA electricity dataset QA",
    method="Cross-check against original paper tables and Stage 3 exports",
    sources=sources,
    field_definitions=definitions,
)

print(result.report.summary)
Path("artifacts/review_process/report.md").write_text(result.report.details, encoding="utf-8")
PY
```

## 7. QA 与诊断
- **流程无法进入审核**：检查 `validation_findings` 中的 `error`；通常是缺失 `processInformation` 段或 `exchange` 为空。
- **数值差异偏大**：确认 Stage 2/3 是否按原表逐行建模；必要时允许在报告中说明衔接差异。
- **来源匹配不上**：调整 `SourceRecord` 的 `identifier` 与 `text`，确保包含原表格关键字；如需更复杂比对，可扩展 `_match_source_record`。
- **字段校验频繁提醒**：补齐 schema 描述，或在 `FieldDefinition.allowed_values` / `pattern` 中完善规则。
- **报告缺少引用**：请在 `SourceRecord.context` 中加入分页/段落信息，或者在 `ReviewReport` 末尾追加引用列表。

## 8. 结束清单
1. `ProcessReviewResult.validation_findings` 中无 `error`，或已有复核记录说明。
2. 已写入审查元数据：`review_type="Independent external review"`, `scope`、`method` 对应需求。
3. 审查报告（Markdown/其它格式）存放于 `artifacts/review_process/<run_id>/`，并注明使用的数据来源。
4. 代码改动通过 `uv run black .`、`uv run ruff check`、`uv run python -m compileall src scripts`、`uv run pytest`。
5. 如遇外部服务不可用（TIDAS/知识库），在日志和报告中记录复现步骤，必要时通知运维或数据负责人。
