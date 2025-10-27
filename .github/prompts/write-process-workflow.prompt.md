# 天工 LCA 数据集更新流程工作流指引（写 Process Workflow Prompt）

## 目标概述
- 面向 `userid = a8397fcd-6d7a-4779-a6db-5806541e6df3` 的远程流程数据，完成字段补齐与写入。
- 依赖 MCP 服务和本地需求文档，生成符合 `tidas_processes.json` Schema 的 `processDataSet`。
- 不确定的数据点统一记录到工作日志文件，由人工后续审核。

## 前置检查
1. **密钥**：`.secrets/secrets.toml` 必须存在 `TianGong_LCA_Remote.api_key`。仓库已配置，可直接使用。
2. **依赖**：确保已执行 `uv sync --group dev`，可在任务结束前运行四步校验命令：
   ```bash
   uv run black .
   uv run ruff check
   uv run python -m compileall src scripts
   uv run pytest
   ```
3. **目录约定**：本工作流使用 `artifacts/write_process/` 输出中间文件与日志。

## 使用工具
- **MCP**：服务名 `TianGong_LCA_Remote`，主要工具：
  - `Database_CRUD_Tool`：对 Supabase 表执行 select/insert/update/delete。
  - 如需流程详情，可扩展使用 `Search_processes_Tool`（选做）。
- **本地资料**：
  - `test/requirement/write_data.md`：字段内容需求。
  - `test/requirement/pages_process.ts`：中文标签对应英文 key。
  - `src/tidas/schemas/tidas_processes.json`：Schema 约束。

## 操作步骤
### Step 1：列出目标用户的流程 JSON ID
1. 通过 `Database_CRUD_Tool` 执行 `select`：
   ```json
   {
     "operation": "select",
     "table": "processes",
     "filters": {"user_id": "a8397fcd-6d7a-4779-a6db-5806541e6df3"},
     "limit": 5000
   }
   ```
2. 收集返回数据中的 `id` 字段，写入 `artifacts/write_process/user_a8397fcd-ids.json`（JSON 数组）。
3. 若 MCP 报错或返回为空，将报错信息写入日志文件 `artifacts/write_process/write_process_workflow.log`，并告知人工确认。

### Step 2：提取首个流程 JSON
1. 使用 Step 1 列表中的第一条 ID，调用 `Database_CRUD_Tool`：
   ```json
   {
     "operation": "select",
     "table": "processes",
     "filters": {"id": "<first_process_id>"},
     "limit": 1
   }
   ```
2. 将 `json` 字段（`processDataSet`）保存到 `artifacts/write_process/<first_process_id>.json`，保留原有结构。

### Step 3：解析字段需求
1. 读取 `test/requirement/write_data.yaml`：
   - `global_updates` 对应全局字段；`process_updates` 按流程名称（UI 显示）匹配专属字段；如存在 `exchange_updates`，按 `match` 规则更新 exchange（目前支持 `all`）。
   - 多语言内容（示例：“建模信息——数据切断和完整性原则”）按 `zh`、`en` 提供；单值字段（如 UUID、枚举）直接使用。
2. 结合 `test/requirement/pages_process.ts`：
   - 建立中文标签 → 英文 key 的映射，例如 `数据切断和完整性原则` → `pages.process.view.modellingAndValidation.dataCutOffAndCompletenessPrinciples`。
   - 该映射用于定位 Schema 中的实际字段。

### 应用顺序（推荐）
1. 先从 `processInformation.dataSetInformation.name` 及其补充字段（`treatmentStandardsRoutes`、`mixAndLocationTypes`、`functionalUnitFlowProperties`）拼装出界面名称，匹配 `process_updates.process_name`。
2. 执行顺序：先应用 `global_updates`，再匹配流程名称并执行对应 `fields`，最后处理命中的 `exchange_updates`。
3. 未匹配到流程名称时，仅保留全局字段；日志记录跳过项以便人工确认。

### Step 4：映射到 `tidas_processes.json`
1. 依据 Step 3 解析出的需求条目，结合固定的“需求标签 → Schema 路径 → 值类型”映射表（例如 `FIELD_MAPPINGS`），逐条更新 `processDataSet`。该映射表可随业务扩展，不应硬编码特定文档。
2. 写入规则示例：
   - **多语言文本**：构造 `{ "@xml:lang": "...", "#text": "..." }` 列表，保持语言代码与值一一对应。
   - **引用字段**：统一生成 `GlobalReferenceType` 结构（`@type`、`@refObjectId`、`@version`、`@uri`、`common:shortDescription`），不足信息以占位符提示需要人工复核。
   - **枚举/布尔值**：确保落地值与 Schema 声明一致（布尔值用 `"true"`/`"false"` 字符串，枚举值使用英文枚举项）。
3. 若需求条目与映射表不匹配，或缺乏足够信息，则跳过该字段并在日志中标记，待人工补充。

### Step 5：写回数据库（最小校验）
1. 将更新后的 `processDataSet` 写回 `artifacts/write_process/<first_process_id>.json`（UTF-8，无 BOM，保留缩进）。
2. 使用 `Database_CRUD_Tool` 触发 `update` 操作即可满足远端的基础校验需求，示例：
   ```json
   {
     "operation": "update",
     "table": "processes",
     "id": "<first_process_id>",
     "version": "<原版本号，缺省可保留 01.01.000>",
     "jsonOrdered": <本地写入的 JSON 文档>
   }
   ```
   远端目前只做结构层面的检查（必填字段、字段类型），不强制完整的 TIDAS Schema。若后续需要通过严格验证，再补充缺失字段并跑本地 `tidas_processes.json` 校验。
3. 未覆盖的字段保持原样，不要删除原数据。

## 日志规范
- 默认日志：`artifacts/write_process/write_process_workflow.log`。
- 记录内容包括：MCP 错误、字段缺失、人工确认项。
- 每次运行如无异常，可清空日志文件。

## 总结
遵循上述步骤，可获得目标用户的流程 ID 列表、首个流程的 JSON 文件，以及基于需求文档和 Schema 的字段补齐版本。遇到不可判断事项，请写入日志并提示用户。***
