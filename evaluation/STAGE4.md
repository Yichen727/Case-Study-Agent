# 阶段 4：自动元数据补全与简洁纠错

更新：2026-10-29。实现与自动验证完成，待用户按下面步骤手测。

本次用户确认的规则取代此前逐字段验证、Approve / Reopen 的设计。系统重心是搜索匹配，正常导入不需要逐条人工确认。

## 当前规则

- 按“原文提取 → 网页补充 → AI 补全缺失 Client / Services”在后台自动处理。
- Case Name、Pillar、Client、Services、Brief、Creative、Impact 七个字段全部有值，自动保存并显示 **Confirmed**。
- 任意一项是 None、空白或 Unknown，保存并显示 **Needs Review**。Brief / Creative / Impact 缺失同样需要 Needs Review，包括公司第 19–25 页中的非标准案例和第 79 页。
- AI 补全后可以直接 Confirmed，不要求逐字段确认。AI 不补写三段正文或 Campaign Outcome；非标准正文继续保留 None。
- Confirmed 表示元数据完整，不把 AI 字段的来源自动改成人工验证。字段来源、引用、模型及历史仍在数据库保留，供检索证据判断使用。
- 两类状态均可检索。保存修改后自动重算状态；修改后仍齐全则保持 Confirmed，清空任何核心字段则回到 Needs Review。

## 精简界面

原 Complete metadata 改为 **Needs Review**：

- 默认只列缺失记录，使用可输入搜索的案例选择框，不分页。只查询轻量选项，全文仅加载当前案例。
- Show all cases 可以查看和修改已 Confirmed 的记录。
- 直接显示 Client、Services；Case Name、Pillar 和三段正文收在 Other metadata。
- 只有一个 Save metadata 按钮，整张表单原子保存并重算状态。去掉字段选择、来源 / 验证下拉框、Editor / Reviewer ID、Verify、Approve、Reopen。
- 原文、链接与字段来源收在 Original text and sources。搜索卡片与列表不再反复提示逐条验证 AI。
- 已有来源的补充重试收在 Retry automatic enrichment；新导入仍自动补充，不需要点击该区。
- 保存后保留当前案例，即使刚变为 Confirmed；后台补充同步未编辑的值，保留正在输入的草稿。

人工改动及显式清空不会被后台覆盖。保存只修改索引元数据，原始 PPTX、幻灯片正文和快照保持原样。旧审批服务已移除，数据库中的历史审批记录保留。

## AI 实现边界

Responses API + Pydantic 结构化输出，默认 `gpt-5.4-mini`。输入为必要的幻灯片文本，不上传 PPTX；没有网页搜索、外部工具或多供应商回退。

非空建议必须带原文引用，规范化空白和外层双引号后仍须在原文中匹配，否则保留 Unknown。引用匹配不能代替语义正确性判断。AI 来源记录为 `AI_INFERRED`，自动 Confirmed 不将其改为 `VERIFIED`。

每批最多 6 个不同内容项，每任务最多 60 个未缓存内容项 / 10 次请求；每页最多 6000 字符，输出最多 3000 token。请求超时 20 秒、任务预算 90 秒，在批次间检查；关闭自动重试。按内容、待补字段、模型与提示版本缓存，包括 Unknown；缓存不占新请求名额。

Key 保存在被 Git 忽略的本地 `.env`，不进入前端、任务或数据库。`ai_runs` 保存请求数、用量及安全错误码。AI 未配置或失败时保留有效案例与缺失信息。

## 验证结果

```powershell
& 'C:\Users\86153\anaconda3\python.exe' -B -m pytest -q
& 'C:\Users\86153\anaconda3\python.exe' -B -m evaluation.run_stage4
```

**91 项测试通过**，覆盖七字段逐项缺失、AI 补齐自动 Confirmed、三段分别缺失仍 Needs Review、完整案例检索、原子保存、人工改动保护、无分页选择、状态变化后保留当前案例、后台草稿保留及旧流程的解析 / 导出 / 来源隔离回归。普通 pytest 和默认评估均离线，不使用本地真实 Key 调用 OpenAI。

最新 [离线记录](stage4_offline.json) 使用真实 SDK + 模拟响应：6 张合成案例、7 个 AI 值、5 个 Unknown；元数据完整的案例自动 Confirmed，AI 来源仍保留，重复导入全部命中缓存。

此前已完成的 [真实 API 基线](stage4_live.json) 保留：6 张合成案例的 Client / Services 各 6 项检查符合预期；一次请求约 4.31 秒，981 input / 519 output tokens，重复导入 0 次新请求。这是小样本基线，不代表公司案例准确率。本次 UI / 状态修订没有重复真实调用或产生新的 API 费用。引用引号修复前的 [对照记录](stage4_live_before_quote_fix.json) 继续保留。

[官方结构化输出说明](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)、[默认模型说明](https://developers.openai.com/api/docs/models/gpt-5.4-mini)。

## 实际数据库升级

schema 3 → 4 自动备份到 `data/backups/case_studies.before-v4-434a4093.sqlite3`，仅将案例工作状态按七字段完整性重算。现有 **60 条案例：52 Confirmed、8 Needs Review**。

除案例状态和数据版本外，案例内容、来源、正文、字段来源、修改历史、旧审批历史、AI 缓存和任务数据均与备份一致，完整性 / 外键检查通过。详见 [升级核查](stage4_simplification_migration.json)。旧阶段 4 的 [schema 3 核查](stage4_migration.json) 作为历史保留。

## 用户手工测试

1. 重启 Demo，进入 Needs Review。默认只看到缺失记录，没有 Page 控件，也没有确认 / 批准按钮。
2. 检查第 19–21、23–25 页等非标准案例：即使 Client / Services 已补齐，缺失三段时仍是 Needs Review。无需为了消除该状态而编造正文。
3. 正常导入带缺失 Client / Services 的 PPTX，观察后台自动补全；其他五字段已有值时应自动 Confirmed，无需人工确认。
4. 修改 Client / Services 并保存，确认仍停留在当前案例；需要纠正三段时展开 Other metadata，按真实材料修改后保存。
5. 勾选 Show all cases，选择完整案例。清空任一核心字段并保存，应变为 Needs Review；补齐后自动恢复 Confirmed。列表与搜索卡片状态应一致。
6. Original text and sources 中仍能查看原文、链接与 AI 来源；原片内容保持不变。

公司材料的推断准确率继续通过标注评估；Mandatory 约束和混合匹配在阶段 5 实现，自动 Confirmed 本身不替代硬条件证据。
