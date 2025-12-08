# Process Update 工具与使用指南

面向“根据用户需求批量更新和修改数据库已有流程数据”的场景，`tiangong_lca_spec.process_update` 提供了一套可组合的工具链。该工具链同样适用于将来自文献或其他外部来源整理出的结构化字段，写回远端 MCP 数据库。

## 模块概览

- **`src/tiangong_lca_spec/process_update/workflow.py` — `ProcessWriteWorkflow`**  
  读取需求文件与翻译键映射，调用仓库客户端获取流程 JSON，交给更新器落库，并可写出本地副本与日志，适合批量自动化执行。
- **`src/tiangong_lca_spec/process_update/repository.py` — `ProcessRepositoryClient`**  
  基于 MCP 工具封装了流程 JSON 的列举与获取逻辑，并支持对任意表执行单条记录查询，用于补齐引用类字段的元数据。
- **`src/tiangong_lca_spec/process_update/requirements.py` — `RequirementLoader`**  
  将 YAML 需求（如 `test/requirement/write_data.yaml`）解析成统一数据结构，现已支持 `global_updates`、`process_updates`、`templates` 以及 `process_bindings`。其中模板允许复用一组字段，再通过绑定按 JSON UUID 精确落库。
- **`src/tiangong_lca_spec/process_update/translation.py` — `PagesProcessTranslationLoader`**  
  从 `pages_process.ts` 提取“中文标签 → i18n key”的映射，用于把需求里的界面文案定位到 JSON Schema 目标字段。
- **`src/tiangong_lca_spec/process_update/updater.py` — `ProcessJsonUpdater`**  
  按内置 `FIELD_MAPPINGS` 将需求条目写入 `processDataSet`，自动处理多语言结构、引用元数据、枚举/布尔转换、交换项批量更新，并在需要人工确认时写日志。
- **`src/tiangong_lca_spec/process_update/reference_resolver.py` — `ReferenceMetadataResolver`**  
  结合仓库客户端查询引用对象（联系人、来源、流程、流），生成带 shortDescription/URI/version 的全局引用结构，方便文献来源等信息自动补齐。
- **`WorkflowLogger`（同 workflow.py）**  
  收集所有“需要人工核查”的记录，落地到日志文件，便于后续 QA。

## 关键输入

1. **需求 YAML**（`test/requirement/write_data.yaml`）：可同时声明三类配置——
   - `global_updates`：适用于账号内所有流程；
   - `process_updates`：仍可按流程名称匹配；  
   - `templates` + `process_bindings`：定义可复用字段模板，并通过绑定把模板应用到指定 JSON UUID。  
   仍支持 `exchange_updates` 批量修正输入/输出段落，适合将文献提取的描述、引用、定量信息结构化填入。
2. **翻译映射**（`test/requirement/pages_process.ts`）：提供界面中文标签到后台 key 的映射，保证需求配置与字段定位解耦。
3. **工作流提示**（`.github/prompts/write-process-workflow.prompt.md`）：给出操作步骤、日志规范与远端工具约定，可作为运行 SOP。

## 运行流程

1. 调用 `RequirementLoader.load()` 解析 YAML，得到 `RequirementBundle`（包含全局更新、名称匹配配置以及按 UUID 克隆的模板绑定）。
2. 调用 `PagesProcessTranslationLoader.load()`，建立中文标签到 UI key 的映射，供枚举解析等逻辑使用。
3. 通过 `ProcessRepositoryClient.list_json_ids(user_id)` 获取目标用户的流程 JSON ID；若需求文件声明了 `process_bindings`，则只会处理绑定出现的 UUID，并在日志里标注缺失或跳过原因。
4. 使用 `ProcessRepositoryClient.fetch_record()` 读取流程记录，并优先取 `json_ordered`（缺失时回退 `json`）作为原始 JSON 数据。
5. 用 `ProcessJsonUpdater.apply(document, requirement_bundle.for_json_id(json_id))` 写入需求字段：绑定的 UUID 会直接套用模板字段；未绑定的流程仍依赖名称匹配。遇到引用会调用 `ReferenceMetadataResolver` 补元数据；无法解析的项会记录日志。
6. 将更新后的 JSON 写到 `artifacts/write_process/<json_id>.json` 等输出目录，可选记入日志。
7. 按需通过 MCP 的 `Database_CRUD_Tool` 执行 `update` 将结果写回数据库（参见 prompt 指南）。

## 文献数据提取的衔接方式

- **结构化承载**：多语言字段、引用字段、枚举值、交换信息等都能通过 YAML 配置显式声明，将文献整理出的摘要、来源 UUID 等一次性写入。
- **引用元数据拉通**：`ReferenceMetadataResolver` 可把文献对应的来源/联系人数据集 UUID 转换成完整引用结构，无需手动填写 `shortDescription` 与版本号。
- **日志驱动复核**：当文献中存在缺失字段或需要人工判断的内容时，`ProcessJsonUpdater` 会把跳过／占位的字段写入日志，支撑资料回溯。
- **交换项批量处理**：对于文献中给出的输入输出状态、数据推导方法等统一描述，可通过 `exchange_updates` 全量应用。

## 使用示例

### CLI 方式

```bash
uv run python scripts/write_process_workflow.py \
  --user-id a8397fcd-6d7a-4779-a6db-5806541e6df3 \
  --requirement test/requirement/write_data.yaml \
  --translation test/requirement/pages_process.ts \
  --output-dir artifacts/write_process \
  --limit 10
```

运行后会在指定目录生成更新后的流程 JSON，同时在 `write_process_workflow.log` 中记录需要人工确认的事项。

### 程序化调用

```python
from pathlib import Path
from tiangong_lca_spec.core.config import get_settings
from tiangong_lca_spec.core.mcp_client import MCPToolClient
from tiangong_lca_spec.process_update import ProcessRepositoryClient, ProcessWriteWorkflow

settings = get_settings()
with MCPToolClient(settings) as client:
    repository = ProcessRepositoryClient(
        client,
        settings.flow_search_service_name,
        list_tool_name="Database_CRUD_Tool",
    )
    workflow = ProcessWriteWorkflow(repository)
    workflow.run(
        user_id="target-user-id",
        requirement_path=Path("test/requirement/write_data.yaml"),
        translation_path=Path("test/requirement/pages_process.ts"),
        output_dir=Path("artifacts/write_process"),
        log_path=Path("artifacts/write_process/workflow.log"),
        limit=0,  # 0 或负数表示处理全部
    )
```

## 扩展建议

1. 在 `FIELD_MAPPINGS` 中新增 UI 标签与 Schema 路径映射，可覆盖更多从文献提取的字段类型。
2. 若需要额外的数据清洗或校验（例如数值单位转换），可在 `ProcessJsonUpdater` 中扩展专用转换函数。
3. 结合 `scripts/` 下的阶段化 CLI，可在写入后运行端到端验证，确保文献数据与现有数据库模型一致。
