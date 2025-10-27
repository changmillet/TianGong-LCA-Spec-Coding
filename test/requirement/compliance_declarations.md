# Compliance Declarations Reference（8bbf2314-41e6-4525-9bc4-aa0c9f8e5941）

该文件记录了远端流程数据集中 `modellingAndValidation.complianceDeclarations.compliance` 区块的示例条目，可用于后续补写 `compliance` 信息时对照字段含义与常见取值。

| @refObjectId | shortDescription (en) | approvalOfOverallCompliance | methodologicalCompliance | documentationCompliance | reviewCompliance | nomenclatureCompliance | qualityCompliance |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1ea48531-e397-4ca7-ac08-056e4fa11826 | ISO 14040 Environmental Management – Life Cycle Assessment – Principles and Framework, 2006 | Fully compliant | Fully compliant | Not defined | Fully compliant | Not defined | Not defined |
| 1adb438d-4a8b-4919-885e-0a66da3c0f2a | ISO 14044:2006. Environmental Management – Life Cycle Assessment – Requirements and guidelines. | Fully compliant | Fully compliant | Fully compliant | Not defined | Not defined | Not defined |
| d92a1a12-2545-49e2-a585-55c259997756 | ILCD Data Network - Entry-level | Fully compliant | Fully compliant | Fully compliant | Fully compliant | Fully compliant | Not defined |
| c84c4185-d1b0-44fc-823e-d2ec630c7906 | Environmental Footprint (EF) 3.1 | Fully compliant | Not defined | Not defined | Not defined | Fully compliant | Not defined |
| 779fb9ea-de54-4707-b7fc-6154661552b5 | Commission Recommendation (EU) 2021/2279. (Annex I. Product Environmental Footprint Method) | Fully compliant | Fully compliant | Fully compliant | Fully compliant | Fully compliant | Fully compliant |

补写规则提示：

- `common:referenceToComplianceSystem` 是 `GlobalReferenceType`，需同时提供 `@type`, `@refObjectId`, `@version`, `@uri`, `common:shortDescription` 等字段。
- 六项合规性字段（overall / methodological / documentation / review / nomenclature / quality）通常取 `Fully compliant`、`Not defined` 等枚举值。
- 若尚无权威引用，可保持 `Not defined`，并在日志中注明来源缺失。
