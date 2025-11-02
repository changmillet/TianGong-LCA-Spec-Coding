# 天工 LCA 数据集审核工作流指引（Review Process Workflow Prompt）

本说明聚焦“自动化流程数据集审核”的执行规范。请在动手前阅读仓库根目录的 `AGENTS.md` 与 `Tiangong LCA Spec Coding Development Guidelines`，确保协作方式与环境一致。

## 0. 执行约定
- **统一驱动脚本**：默认使用拟新增的 `scripts/review_process_workflow.py` 触发全流程；若需拆分阶段，可调用后续将实现的子命令（如 `--stage knowledge-base`、`--stage report`）。
- **知识库先行**：审核依赖的 PEF Method、LCD 等权威文档需先落库；首选 `scripts/build_review_knowledge_base.py` 生成结构化资料（JSONL/向量索引），置于 `artifacts/review_process/knowledge_base/`。
- **凭据配置**：除 OpenAI/MCP key 外，若后续需要访问文档仓库或内部数据湖，请在 `.secrets/secrets.toml` 增补 `[document_registry]`、`[review_storage]` 等段落。
- **日志必查**：`ReviewWorkflowLogger` 产出的 `artifacts/review_process/review_process_workflow.log` 记录缺口与异常，用于同事复核；日志必须清空或说明后才能视为完成。
- **数据来源权威**：任何字段的判断、修改或否定都需引用知识库原文；审查结论中须保留引用标识（如段落 ID、页码）。
- **最小写回**：脚本仅写入审核元数据、报告模版与 SARS 包；不得直接覆盖流程 JSON，避免破坏原始数据。

## 1. 前置检查
1. **依赖安装**：执行
   ```bash
   uv sync --group dev
   ```
   确保 `anyio`、`httpx`、`pydantic`、`structlog`、`python-docx`（报告生成）等可用。
2. **文档采集**：
   - PEF Method 正式文本（最新版本 PDF/HTML），建议使用 Defi/自定义抓取脚本转为分段 Markdown。
   - LCD 原始文件及补充说明，统一放置在 `data/source_documents/pef/` 与 `data/source_documents/lcd/`。
   - 可选：合规标准（ISO 14040/14044）、命名规则手册等辅助资料。
3. **AI 工具配置**：
   - `KnowledgeBaseBuilder`：解析 PDF/Markdown，输出段落索引与向量存储。
   - `ReviewTaskPlanner`：读取需求 YAML（待补 `test/requirement/review_data.yaml`），拆分审核任务。
   - `ReviewMcpClient`：后续联通 Codex/远端审查代理，确保在 `.secrets/secrets.toml` 中指明 `service_name`、`tool_name`。
4. **数据集入口**：
   - 默认从 `artifacts/extraction/` 读取待审流程 JSON；若尚无抽取结果，先运行过程提取工作流。
   - 记录数据源路径于 `ReviewRunConfig`，便于日志追踪。

## 2. 操作步骤

### Step 1：整理审阅知识库
1. 用 `scripts/build_review_knowledge_base.py` 将 PEF Method、LCD 等文档解析为段落对象，生成：
   - `knowledge_segments.jsonl`（含 `source`, `section`, `text`, `citation`）。
   - 可选向量索引（供语义检索）。
2. 成功后产物放在 `artifacts/review_process/knowledge_base/`；写日志记录文档版本号与采集时间。
3. 若有缺页/无法解析，日志中注明并联系资料维护者补齐。

### Step 2：载入候审数据集与上下文
1. `ReviewDataLoader` 读取目标流程 JSON、元数据（流程名称、功能单元、位置等）。
2. 将 `ReviewContext` 序列化至 `artifacts/review_process/run_<timestamp>/context.json`，供后续步骤引用。
3. 若缺少关键字段，立即终止并在日志记录缺失项。

### Step 3：语义一致性与逻辑自检
1. `ReviewConsistencyAnalyzer` 核对流程描述、命名与功能单元之间是否互相佐证：
   - 描述段落与名称的主语、过程范围是否一致。
   - 数据集引用的生命周期阶段是否与方法论匹配。
2. 利用知识库检索对应章节，输出 `consistency_findings.json`，包含疑似冲突、建议修改。

### Step 4：数据来源溯源比对
1. `SourceTraceValidator` 针对每条重要数据（定量值、引用表、模型假设），匹配知识库或外部原始文件中的对应章节。
2. 若找不到可信来源，标记为 `missing_source` 并将建议添加至审核意见。
3. 支持多源引用的字段需记录全部来源及优先级。

### Step 5：交换流与物质/能量守恒校验
1. `ExchangeMassBalanceChecker` 将输入输出分类为：原料、辅料、能源、产品、副产品、废弃物流。
2. 根据分类判断质量守恒、能量守恒是否成立；若存在差异，输出定量差值与可能原因。
3. 对含副产品的流程，触发 `AllocationAdvisor` 提示可选分配策略（质量、能量、经济等），并检验是否已在数据集中体现。

### Step 6：定义审查范围与方法
1. `ScopeMethodConfigurator` 读取 `review_data.yaml` 中的 `scope`、`method` 需求，并结合数据集信息自动拟定：
   - 审查角色（默认独立第三方 AI 审核者）。
   - 审查类型（合规、数据质量、模型一致性等）。
2. 将确认后的范围与方法写入 `artifacts/review_process/run_<timestamp>/scope_method.json`。
3. 若同一 Scope 下需多套方法，依次执行并记录分别的结果。

### Step 7：生成审查详情与结论
1. `ReviewDetailComposer` 汇总前述步骤的发现，生成结构化条目（`status`、`finding`、`evidence`, `recommendation`）。
2. `ReviewReportAssembler` 根据模版（待存放于 `templates/review_report.docx`）生成 Word/Markdown 报告：
   - 包含执行摘要、审查范围、方法说明、发现列表、结论（通过/有条件通过/不通过）、整改建议。
3. 将报告文件写入 `artifacts/review_process/run_<timestamp>/report/`，并输出 PDF/Word 两种格式（使用 `python-docx` 与 `pandoc`/`docx2pdf`）。

### Step 8：合规性校验与归档
1. `ComplianceChecklistGenerator` 对照 ISO 14040/14044、PEF 要求逐条核对，产出 `compliance_matrix.json`。
2. 若存在不符合项，附带引用证据与整改建议。
3. `SarsPackageWriter` 将报告、合规清单、审查数据打包成 SARS 文件，并写入 `artifacts/review_process/run_<timestamp>/package/`。
4. 更新 `review_process_workflow.log` 总结执行状态，准备同步给运营或提交 PR。

## 3. 常见问题与诊断
- **文档解析失败**：确认 Defi/解析脚本输出是否为 UTF-8，必要时先转 Markdown 再入库。
- **知识库检索不准**：检查向量模型、分段策略是否一致；可在构建时调整分段长度或添加关键词索引。
- **命名与描述冲突**：多数源于旧版命名规则；建议引用命名规范手册，并在审核建议中说明冲突原因。
- **物质守恒差异大**：可能漏录副产品或蒸发损耗；复查原文或请求数据提供者补充说明。
- **合规矩阵无匹配条目**：更新 `compliance_rules.yaml`（待新增）以覆盖最新法规或客户要求。
- **SARS 打包失败**：核实 docx → pdf 转换依赖，或确认输出目录是否存在旧锁文件。

## 4. 结束清单
1. `artifacts/review_process/run_<timestamp>/` 下存在完整的知识库快照、上下文、发现列表、合规矩阵与报告文件。
2. `review_process_workflow.log` 已记录执行摘要，并在必要时同步给审查负责人。
3. 所有审查结论均附带知识库引用或原文页码，可复现。
4. 若新增脚本/工具，已通过格式化、构建、测试（`uv run black .`、`uv run ruff check`、`uv run python -m compileall`、`uv run pytest`），并在 PR 中说明影响面。
5. 如需进一步自动化，评估是否将 `ReviewWorkflow` 纳入阶段性流水线（Stage 7 之后），并提前告知运维团队。
