# 天工 LCA 数据集更新工作流指引（Write Process Workflow Prompt）

本说明聚焦“批量补齐与更新远端流程数据集”的执行规范。请在动手前阅读仓库根目录的 `AGENTS.md`，了解通用协作约定与环境说明。

## 核心代码复用
- **脚本入口**：`scripts/write_process_workflow.py` 直接复用 `.github/prompts/extract-process-workflow.prompt.md` 中同系列脚本的设计模式（Stage 结构、run-id 缓存、OpenAI/MCP 客户端等）。
- **主要服务**：`src/tiangong_lca_spec/process_update/workflow.py` 中的 `ProcessWriteWorkflow` 负责按步骤组织 `ProcessRepositoryClient` → `RequirementLoader` → `ProcessJsonUpdater`，与提取流程工作流的 orchestrator 保持一致。
- **远端访问**：`ProcessRepositoryClient` 与 `ReferenceMetadataResolver` 共用提取工作流描述的 MCP 封装；无需另写 CRUD 逻辑。
- **需求解析与字段映射**：`RequirementLoader`、`PagesProcessTranslationLoader`、`ProcessJsonUpdater` 提供完整能力，外部只需准备 YAML/TS 配置；布尔、枚举、多语言等类型处理逻辑与提取工作流使用的 schema 工具保持一致。
- **日志&清理**：`WorkflowLogger`、`ProcessJsonUpdater._post_update_cleanup()` 延续提取工作流中“日志记录+自动修正”的惯例，不必自行写清理脚本。
- **合规条目**：`ProcessJsonUpdater` 默认填充 EF 3.1（UUID `c84c4185-d1b0-44fc-823e-d2ec630c7906`）合规声明，与 `test/requirement/compliance_declarations.md` 对应，省去重复维护。

## 0. 执行约定
- **遵循标准脚本**：首选 `scripts/write_process_workflow.py` 触发更新流程（与提取工作流的 Stage 脚本共享 `_workflow_common` 工具）。确需自定义参数时再查看 `--help`。
- **凭据先行确认**：默认 `.secrets/secrets.toml` 已配置 `TianGong_LCA_Remote.api_key` 等密钥。如 MCP 报 401/403，再检查密钥是否缺失或过期。
- **输出目录固定**：所有中间文件、最终 JSON 及日志统一放置在 `artifacts/write_process/`，避免与其他工作流产物混淆。
- **需求文件优先**：一切字段更新必须来自结构化需求（默认 `test/requirement/write_data.yaml`）。若要改动具体值，先更新需求文件，再重新运行工作流。
- **日志必查**：`WorkflowLogger` 会将缺失映射、占位引用、更新冲突写入日志。只有在日志为空时才能视为本次批量更新完成。
- **最小化写回**：只新增/覆盖明确配置的字段；不删除原有字段，也不在脚本外随意改动 JSON，确保远端流程可追溯。
- **禁止删除远端记录**：执行写回时只能通过 `insert` 写入新版本或在本地比对差异；任何 `delete`/`drop` 操作一律禁止，以便保留旧版本供对比和审计。

## 1. 前置检查
1. **依赖安装**：执行 `uv sync --group dev`，确保 PyYAML、anyio 等依赖齐备。涉及 Python 代码改动后，可使用惯例四步校验：
   ```bash
   uv run black .
   uv run ruff check
   uv run python -m compileall src scripts
   uv run pytest
   ```
2. **需求/翻译文件**：
   - `test/requirement/write_data.yaml`：声明全局字段与按流程名称匹配的定制字段，支持 `exchange_updates`。
   - `test/requirement/pages_process.ts`：中文标签 → 英文 key 映射，供枚举解析与字段定位使用。
3. **Schema 参考**：`src/tidas/schemas/tidas_processes.json` 可用于了解字段约束，必要时结合 `FIELD_MAPPINGS` 扩展支持的 UI 标签。

## 2. 操作步骤

### Step 1：列出目标用户的流程 JSON ID
- `ProcessWriteWorkflow` 调用 `ProcessRepositoryClient.list_json_ids()` 自动完成该步骤，输出保存至 `artifacts/write_process/user_<user_id>-ids.json`。脚本参数 `--user-id` 对应 `filters.user_id`。
- 若需手工验证，可复用提取工作流中记录的 `Database_CRUD_Tool` 调用方式；出现空列表或接口异常立即写日志并中止。

### Step 2：获取原始流程 JSON
- `ProcessRepositoryClient.fetch_process_json()` 会把远端 `json` 字段标准化为 Python 字典并写入 `artifacts/write_process/<process_id>.json`。脚本参数 `--limit` 控制抓取数量（≤0 处理全部）。
- 如需排查异常，可参考提取工作流的 MCP 样板请求，但默认无需重复实现。

### Step 3：解析需求配置
- `RequirementLoader` 将 `write_data.yaml` 解析为 `RequirementBundle`，覆盖 `global_updates` 与 `process_updates` 两种粒度；逻辑与提取工作流对 schema 的解析保持一致。
- `PagesProcessTranslationLoader` 复用 `pages_process.ts` 的翻译条目，为 `ProcessJsonUpdater` 提供枚举、布尔映射。
- `ProcessWriteWorkflow._select_ids()` 和 `ProcessJsonUpdater._locate_process_requirement()` 会组合 `baseName`/`treatmentStandardsRoutes`/`mixAndLocationTypes`/`functionalUnitFlowProperties` 进行匹配；无法匹配时仅应用 `global_updates` 并写日志。

### Step 4：应用字段映射并更新 JSON
1. `ProcessJsonUpdater` 基于 `FIELD_MAPPINGS` 将需求条目落到 Schema 指定路径，相关行为与提取工作流中的 `build_tidas_process_dataset()` 一致：
   - **多语言**：生成 `{ "@xml:lang": "...", "#text": "..." }` 单体或列表。
   - **引用**：优先调用 `ReferenceMetadataResolver` 补齐 `@type` / `@version` / `@uri`。若远端缺元数据，则写入占位描述并记录日志。
   - **枚举/布尔**：通过翻译映射定位具体枚举值；布尔值统一输出 `"true"` / `"false"`。
2. `exchange_updates` 支持对 `exchanges.exchange` 批量修正（目前 `match=all`）。若需求中出现未映射的标签或不支持的 match 规则，保持原值并写日志。
3. `_post_update_cleanup()` 会对照 TIDAS schema 自动处理时间戳、空引用、多余列表结构，并在以下场景复用提取工作流的默认行为：
   - `validation.review.@type` 缺失时自动设为 `"Not reviewed"`，并在该类型下移除 scope、reviewDetails、reviewReference。
   - `validation.review.common:scope`/`common:method` 空缺时补齐 `"Documentation"`。
   - `modellingAndValidation.complianceDeclarations.compliance` 缺失或不完整时，默认注入 EF 3.1 合规声明（短描述、URI、状态值均来源于 `test/requirement/compliance_declarations.md`）。

### Step 5：写回远端并同步日志
- `ProcessRepositoryClient` 在 `ProcessWriteWorkflow.run()` 末尾会将更新后的 JSON 写回 MCP；若需要手动重放，可参考脚本中 `payload = {"operation": "update", ...}` 的构造方式，重点保持 `version` 与远端一致。
- 远端仅做结构校验，如需严格 Schema 验证可在本地额外运行 `uv run python -m compileall` 或 `uv run tidas-validate -i artifacts/write_process`。
- 日志由 `WorkflowLogger` 写入 `artifacts/write_process/write_process_workflow.log`。运行结束后检查该文件：
   - **空文件** → 本轮更新无需要人工处理的异常。
   - **非空** → 将日志内容同步给人工同事或在 PR 描述中说明。

## 3. 常见问题与诊断
- **未解析到流程名称**：确认需求 YAML 中 `process_name` 是否与 UI 名字一致，必要时在 YAML 中添加更多组合名称或别名。
- **引用字段缺元数据**：`ReferenceMetadataResolver` 依赖远端表（contacts/sources/flows/processes）。若 ID 仍落空，检查 Supabase 中是否存在对应记录，或请运营补充。
- **枚举映射失败**：通常是翻译文件无对应项。可在 `pages_process.ts` 搜索中文标签，若缺失则需先补全翻译再运行。
- **更新覆盖旧值**：如日志提示“replaced existing value”，说明脚本覆盖了原字段。若这是预期行为，可忽略；否则回溯需求配置是否填写正确。
- **多流程批量处理**：可设置 `--limit` 为更大值或 ≤0 处理全部 ID，并留意日志文件逐轮清理。

## 4. 结束清单
1. `artifacts/write_process/` 下存在最新的流程 JSON 与 ID 列表。
2. `write_process_workflow.log` 已检查，必要时同步人工处理项。
3. 需求 YAML 中新增/修改的字段已提交版本控制，确保后续运行保持一致。
4. 若修改了 `process_update` 源码或脚本，完成代码审查前务必运行最小化测试（单元或集成）并给出结论；测试策略可复用提取工作流文档中针对 Stage 脚本的建议。
