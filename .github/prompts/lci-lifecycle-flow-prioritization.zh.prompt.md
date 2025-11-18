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
- 数据类型与引用链：项目内所有 ILCD 数据都以独立 JSON 保存。`process` 文件中的 `exchanges.exchange.referenceToFlowDataSet` 指向某个 `flow` JSON；该 `flow` JSON 的 `referenceToFlowPropertyDataSet` 再指向 `flowproperty` JSON；`flowproperty` JSON 的 `referenceToUnitGroup` 则引用 `unitgroup` JSON。`exchange.amount/meanAmount` 的单位取自 `unitgroup.referenceUnit`，因此只有沿着这条链加载后才能正确完成单位规范化。
- Flow/Unit 详情：
  - Stage3 场景：复制该 run 的 `artifacts/<run_id>/exports` 到新的 `exports_refresh/` 并传给 CLI；
  - `tiangong_reference_package` 仅提供常用的 flowProperty/unitGroup 基线，需与本次运行的 `exports/flows/*.json` 搭配使用；
  - 若仅有单个 `processDataSet` JSON（如 `test/process_data/*.json`）且缺少对应 flow 文件，需运行 `--fetch-mcp-flows` 或 Stage3 对齐脚本，将所需 flow/flowProperty/unitGroup 写入本地目录后再执行分析。
- 关键模块：
  - `tiangong_lca_spec.lci_analysis.upstream.workflow`（核心编排）。
  - `tiangong_lca_spec.lci_analysis.common.FlowRegistry`、`units.normalise_amount`。
  - `tiangong_reference_package`（离线 flow/flowProperty/unitGroup 基线数据）。
- 运行目录：默认写入 `artifacts/<run_id>/analysis/` 或自定义的 `--output-dir`。

## 2. 可用工具与命令
- **Stage 脚本**：`scripts/stage1_preprocess.py` → `stage2_extract_processes.py` → `stage3_align_flows.py`（必要时 `stage4_publish.py`）。全部通过 `uv run python scripts/<stage>.py …` 调用。
- **生命周期优先级 CLI**：
  ```bash
  uv run python -m tiangong_lca_spec.lci_analysis.upstream.cli \
    --process-datasets <datasets.json 或 test/process_data/*.json> \
    --flows-dir <latest_exports>/flows \
    --flow-properties-dir <latest_exports>/flowproperties \
    --unit-groups-dir <latest_exports>/unitgroups \
    --output-dir artifacts/<run_id>/analysis \
    --run-id <run_id> [--fetch-mcp-flows] \
    [--secrets .secrets/secrets.toml] \
    [--classification-prompt .github/prompts/lci_flow_classification.prompt.md] \
    [--classifier-cache artifacts/<run_id>/analysis/cache/flow_classifier_cache.json] \
    [--llm-cache-dir artifacts/<run_id>/analysis/cache/openai/upstream]
  ```
  - `<latest_exports>`：Stage3 场景指向复制后的 `artifacts/<run_id>/exports_refresh`；单 process JSON 场景若缺失 flow，则先执行 `--fetch-mcp-flows` 写入该目录，再传给 CLI。
- **辅助 CLI**：`scripts/list_*_children.py` 系列用于查询 ILCD 分类层级；`scripts/run_test_workflow.py` 可在 CI 下跑回归。
- **MCP 自检**：随手写 5 行脚本调用 `FlowSearchService`，确认网络/凭据后再跑 Stage 3+优先级分析。

## 3. 角色与任务
- **Codex**：解析 process dataset、关联标准 flow、统一单位，并完成上游/下游优先级分析。
- **LLM**：根据 prompt 输出结构化 JSON（分类+占比+行动项），调用前须先整理完输入数据，避免把原始 XML 直接丢给模型。
- **工具脚本**：提供批处理、MCP 抓取与单元换算，支撑 LLM 生成可交付的 JSON。

## 4. 数据准备流程
1. **载入数据集**：`load_process_datasets` 自动兼容多种 schema；CLI 未传入 `datasets` 时默认从 `--process-datasets` 读取。
2. **构建 FlowRegistry**：
   - Stage3 场景：复制当次 run 的 `artifacts/<run_id>/exports/`（或 `tiangong_reference_package` 覆盖件）到新的临时目录，确保引用的是最新导出；
   - 单 process JSON 场景：运行 `--fetch-mcp-flows` 重新拉取相关 flow/flowProperty/unitGroup，并写入指定目录；完成后再执行 CLI。严禁复用旧 run 遗留的 `exports/`，以免基线错配。
3. **单位规范化**：`normalise_amount` 会基于 unitGroup 参考单位与 flow property 提示推断 `unit_family`；若仍缺失，在结果 `notes` 中补充 `unit_family_missing`。
4. **分类**：Rule-first（`classifiers/rules.py`）先利用 flow 类型、单位族、ILCD 分类定位 `raw_material/energy/auxiliary/product_output/by_product/waste/emission/resource`。输出流若 Flow 分类 Level 1 标记为 emission，则直接归入 `emission`；Process reference flow 直接标记为 `product_output`；若输出 Product flow 的 allocation 系数/产量显著小于主产品，则标记为 `by_product`。仍为 `unknown` 时，再由 LLM fallback 读取完整 flow JSON、`processInformation`、`modellingAndValidation`、`common:intendedApplications` 等 DatasetContext 信息判断，并把结果写入 `classifier_cache`。
5. **累计排序**：`calculators.build_priority_slices` 先按 `unit_family` 切分，再在同一量纲内排序（默认不再截断，`share/cumulative_share` 仅作为展示）；`build_downstream_slices` 聚合全部产品/废弃物流；`build_default_actions` 自动草拟高/中优先行动项。
6. **写入结果**：`write_summary_json` 生成 `output_dir/upstream_priority.json`，schema_version=2；所有对用户的反馈都应引用该文件。

## 5. 输出要求
- **文件位置**：`<output_dir>/upstream_priority.json`、`<output_dir>/upstream_priority.xlsx`。启用 `--reference-flow-stats` 时，还会生成 `<output_dir>/reference_flow_stats.json` 以及 `reference_processes/<flow_uuid>/*.json`（保存命中的 processDataSet）。
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
          "flow_role": "raw_materials | energy | auxiliaries",
          "unit_family": "mass | energy | ...",
          "reference_unit": "kg | MJ | ...",
          "total_amount": 123.45,
          "share": "12.34%",
          "cumulative_share": "45.67%",
          "reference_process_count": 5,
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
- **Excel 结构**：`upstream_priority.xlsx` 含 6 个工作表——`Upstream`（列顺序固定为：`dataset_uuid, dataset_name, unit_family, flow_role, flow_type, exchange_name_zh, exchange_name_en, flow_name_zh, flow_name_en, flow_uuid, reference_unit, total_amount, share_percent, cumulative_percent, reference_process_count`）、`Downstream`（同样的列顺序并追加 `downstream_path, downstream_action`）、`Unknown`、`Actions`、`Notes`、`Metadata`。表头与 JSON 字段保持一致，`share`/`cumulative_share` 同时提供原始数值与百分比字符串，便于业务复核与透视分析。
- **下游层**：`downstream_priority` 采用与 `upstream_priority` 相同的 unit_family → entries 结构，条目额外包含 `downstream_path` 与 `downstream_action` 字段，用于追踪处置路径和建议动作。
- **Reference flow 统计（可选）**：`reference_flow_stats.json` 以 flow UUID 为键，记录 `process_count`、`process_ids`；`upstream_priority.(json|xlsx)` 中的 `reference_process_count` 字段来源于此。
- **数值规范**：`share`/`cumulative_share` 使用百分号字符串，保留四位有效数字；无法计算时填 `null`。
- **行动项**：优先列 `high/medium`，覆盖上游追溯与下游处置，证据引用具体流名/占比/日志位置。

## 6. LLM 提示策略
- 在调用 LLM 之前，先整理好输入 JSON（含 `run_context`、`dataset`、`flow_details`、`unit_groups`、`review_flags` 等字段）。
- LLM 需：
  1. 判定每条 exchange 的 `class_label`、信心与证据；
  2. 计算各分类贡献并保留全量条目（辅料依旧聚焦 Top2 行动项），`share`/`cumulative_share` 仅用于排名展示，不再据此截断；
  3. 为输出流推断 `downstream_path`（landfill/recycle/reuse/unknown）；
  4. 记录 `unknown_classification` 的原因；
  5. 汇总行动项/备注，确保与 CLI schema 一致。
- 产出的 JSON 必须与上节格式完全一致，不要额外添加解释文本；如某字段缺失，返回空数组。

## 7. QA 与排障
- **缺少 unit_family**：确认 `tiangong_reference_package/unitgroups` 是否包含所需条目；若仍缺失，向数据基线维护者申请更新。
- **Flow 匹配困难**：优先检查 `tiangong_reference_package/flows` 与 Stage 2 导出；确需远程检索再通过流程 owner 申请 MCP 读权限。
- **输出为空**：检查 `process_datasets` 是否真含 `exchanges.exchange`；必要时用 `uv run python - <<'PY'` 简单遍历确认。
- **重新生成结果**：删除 `output_dir/upstream_priority.json` 后重跑 CLI，确保日志中出现 `lci.upstream.workflow.complete`。

按照以上指引，Codex 能在生命周期优先级分析中与 Stage 1~3 产物、MCP 工具保持一致，快速输出可追溯、可入库的 JSON 结果。
