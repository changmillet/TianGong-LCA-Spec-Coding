# 天工 LCA 数据集更新工作流指引（Write Process Workflow Prompt）

本说明聚焦“批量补齐与更新远端流程数据集”的执行规范。请在动手前阅读仓库根目录的 `AGENTS.md`，了解通用协作约定与环境说明。

## 0. 执行约定
- **遵循标准脚本**：首选 `scripts/write_process_workflow.py` 触发更新流程，不建议手写长 JSON 或跳过步骤。确需自定义参数时再查看 `--help`。
- **凭据先行确认**：默认 `.secrets/secrets.toml` 已配置 `TianGong_LCA_Remote.api_key` 等密钥。如 MCP 报 401/403，再检查密钥是否缺失或过期。
- **输出目录固定**：所有中间文件、最终 JSON 及日志统一放置在 `artifacts/write_process/`，避免与其他工作流产物混淆。
- **需求文件优先**：一切字段更新必须来自结构化需求（默认 `test/requirement/write_data.yaml`）。若要改动具体值，先更新需求文件，再重新运行工作流。
- **日志必查**：`WorkflowLogger` 会将缺失映射、占位引用、更新冲突写入日志。只有在日志为空时才能视为本次批量更新完成。
- **最小化写回**：只新增/覆盖明确配置的字段；不删除原有字段，也不在脚本外随意改动 JSON，确保远端流程可追溯。

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
1. 调用 MCP 服务 `TianGong_LCA_Remote` 的 `Database_CRUD_Tool`：
   ```json
   {
     "operation": "select",
     "table": "processes",
     "filters": {"user_id": "a8397fcd-6d7a-4779-a6db-5806541e6df3"},
     "limit": 5000
   }
   ```
2. 将返回数据中的 `id` 序列写入 `artifacts/write_process/user_a8397fcd-ids.json`（数组，每行独占一个 ID）。
3. 若响应为空或接口异常，立即写日志并停止后续步骤，等待人工确认。

### Step 2：获取原始流程 JSON
1. 取 Step 1 列表首个 ID，通过 `Database_CRUD_Tool` 查询：
   ```json
   {
     "operation": "select",
     "table": "processes",
     "filters": {"id": "<first_process_id>"},
     "limit": 1
   }
   ```
2. 将 `json` 字段中的文档保存到 `artifacts/write_process/<first_process_id>.json`，保持缩进与编码（UTF-8，无 BOM）。

### Step 3：解析需求配置
1. `RequirementLoader` 会将需求 YAML 解析成 `RequirementBundle`：
   - `global_updates`：对所有流程通用的字段。
   - `process_updates`：通过 UI 名称匹配特定流程，支持 `fields` 与 `exchange_updates`。
2. `PagesProcessTranslationLoader` 从 `pages_process.ts` 抽取中文标签 → UI key 映射，确保枚举、布尔转写符合实际字段。
3. 匹配流程名称时，组合以下字段生成候选：`processInformation.dataSetInformation.name` 下的 `baseName`、`treatmentStandardsRoutes`、`mixAndLocationTypes`、`functionalUnitFlowProperties`。无法匹配时只落 `global_updates` 并在日志记录。

### Step 4：应用字段映射并更新 JSON
1. `ProcessJsonUpdater` 基于 `FIELD_MAPPINGS` 将需求条目落到 Schema 指定路径。常见类型处理：
   - **多语言**：生成 `{ "@xml:lang": "...", "#text": "..." }` 单体或列表。
   - **引用**：优先调用 `ReferenceMetadataResolver` 补齐 `@type` / `@version` / `@uri`。若远端缺元数据，则写入占位描述并记录日志。
   - **枚举/布尔**：通过翻译映射定位具体枚举值；布尔值统一输出 `"true"` / `"false"`。
2. `exchange_updates` 支持对 `exchanges.exchange` 批量修正（目前 `match=all`）。若需求中出现未映射的标签或不支持的 match 规则，保持原值并写日志。
3. `_post_update_cleanup` 会自动处理时间戳、空引用、多余列表结构 —— 无需手动修改。

### Step 5：写回远端并同步日志
1. 更新后的 JSON 会覆盖 `artifacts/write_process/<json_id>.json`；完成后调用：
   ```json
   {
     "operation": "update",
     "table": "processes",
     "id": "<json_id>",
     "version": "<原版本号，缺省保留 01.01.000>",
     "jsonOrdered": <更新后的 JSON 文档>
   }
   ```
2. 远端仅做结构校验，如需严格 Schema 验证可在本地额外运行 `uv run python -m compileall` 或定制脚本。
3. 日志写入 `artifacts/write_process/write_process_workflow.log`。运行结束后检查该文件：
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
4. 若修改了 `process_update` 源码或脚本，完成代码审查前务必运行最小化测试（单元或集成）并给出结论。
