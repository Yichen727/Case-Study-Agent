# 阶段 5 — 单输入框混合检索、分类与解释

日期：2026-10-29。状态：实现与自动验证完成，用户反馈搜索测试效果非常好；已按反馈简化前端展示。

用户已确认阶段 4 测试正常，并将搜索交互调整为：**只输入一段自由文本，AI 判断必须条件、偏好和排除项**。此前可选 Must-have 筛选方案已被取代。选择、拖拽排序和导出 UI 集成仍属于阶段 6 / 7。

## 已实现

1. 加高的多行输入框接受自由文本，Enter 换行、点击 Search 提交。一次 Responses 结构化解析提取条件及对应用户原句，不能表达的硬条件保持未确认；按用户反馈不再展示 Understood requirements。
2. FTS5 提供关键词候选，缓存的本地向量矩阵提供语义候选，按融合名次排序。每路最多 60 个候选，合并最多 100 个；按独立 Case ID 合并重复召回，不按案例名称合并记录。
3. 原文与可信字段检查硬条件；明确冲突为 Not a Match，相关但缺证据为 Partial Match。High Match 必须主题相关、已识别硬条件均有证据。可选偏好不充当硬条件。
4. 区分 AI 实际用于制作与案例仅介绍 AI 产品；互动体验中的设备数量不证明 campaign outcome。量化成效优先检查原始 / 已验证 Impact，排除目标、计划数字和普通年份。
5. Needs Review 和 Confirmed 都可检索。Confirmed 不会把 AI 来源改为已验证；AI Client / Services 不能单独证明硬条件已满足。
6. 卡片展示等级、理由、原文摘录、来源、页码、Link Type、Review Status 和不确定项。High Match 直接展示，Partial Match 默认折叠，Not a Match 保留内部分类但不在前端展示。内部 ID 和数值相似度不展示。
7. 语义服务或请求解析不可用时明确提示 Keyword-only / 条件未确认，不静默声称完成混合检索。没有案例时不调用 AI。

## 计算与缓存

| 环节 | 实现与边界 |
| --- | --- |
| 本地搜索文档 | 七个结构化字段；非标准页额外保留原始正文。文档最多 6000 UTF-8 bytes，原文证据单独保存，不随文档截断 |
| 案例向量 | 默认 `text-embedding-3-small`、768 维，每批最多 32 个不同文档；按模型、维度、文档版本及内容哈希复用 |
| 索引更新 | 导入 / 自动补全 / 人工修改后增量更新；同内容可共享向量，案例身份仍独立；在原有单 worker 中运行 |
| 新请求 | 一次条件解析与一次查询向量并行；不会逐案例请求 LLM，不抓网页、不解析 PPTX、不构建案例向量 |
| 查询缓存 | 解析按规范化请求、模型及提示版本缓存；查询向量按规范化文本、模型及维度缓存 |
| 界面重运行 | 复用会话搜索结果；数据版本变化后重新执行本地检索，仍可复用解析与查询向量 |
| 超时 / 预算 | 查询向量请求 6 秒，条件解析 8 秒，索引请求 20 秒、索引任务预算 60 秒，连接超时 3 秒；SDK 自动重试关闭。预算并非操作系统硬中断 |
| 索引失败 | 保留已完成向量及 FTS；相同数据版本不在每次界面重运行时自动重试。对应失败任务提供 Retry search preparation；移除常驻 Search readiness 侧栏 |

文本进入 OpenAI 的范围为案例索引文本和用户搜索文本；不上传 PPTX、原片媒体或来源文件。API Key 留在本地配置，任务与记录不保存 Key。维度参数采用 OpenAI [Embeddings API](https://developers.openai.com/api/reference/python/resources/embeddings/methods/create) 支持的方式，模型说明见 [text-embedding-3-small](https://developers.openai.com/api/docs/models/text-embedding-3-small)。

## 数据与测试

完整离线测试：**118 passed，26.51 秒**；另有两条现有 protobuf 依赖弃用警告，无测试失败。pytest 屏蔽本地 Key，不发真实请求。

用户反馈后的前端简化已另行通过 **9 项界面回归测试**，覆盖默认收起 Partial Match、隐藏 Not a Match、移除 Search readiness 及交互时复用搜索结果；检索与分类逻辑保持不变。

之后新增的多行搜索框与移除 Understood requirements，按用户要求未运行测试，由用户刷新界面确认；上述测试结果为本轮调整之前的记录。

覆盖增量索引、同内容向量复用而记录独立、查询缓存、模型切换、写入中旧向量隔离、失败索引不反复排队、条件证据、排除条件、未验证 AI、成效数字、可选偏好、空库，以及单输入框和界面结果复用。原有解析、元数据、跨来源隔离及导出测试继续通过。

实际数据库已从 schema 4 备份并升级到 5：60 个案例 / 60 份搜索文档 / 60 个可用案例向量，52 Confirmed / 8 Needs Review。旧来源、案例、幻灯片、字段来源、修改与审批历史、AI 缓存及任务逐表保持不变；integrity_check 为 ok，无外键错误。记录见 [stage5_migration.json](stage5_migration.json)，备份位于 `data/backups/case_studies.before-v5-6a6c26b8.sqlite3`。

## 五个公司案例 Prompt

完整可复制请求见 [STAGE5_PROMPTS.md](STAGE5_PROMPTS.md)，机器可读预期见 [stage5_prompts.json](stage5_prompts.json)。以公司 60 张案例编写，属于开发校准 / 回归样本，不能当作独立质量评估集。

| 请求 | 主要目标页 | 实测结论 |
| --- | --- | --- |
| AI 酒店品牌内容 | 81、82 | 两种解析均进入前两位；一组 High，另一组因额外“brand film”条件无明确对应原文而 Partial |
| 有量化成效的互动体验 | 71、72 | 互动证据成立，量化成效缺失，保持 Partial；投影机 / 屏幕数量不能证明效果 |
| 有传播成效的环保内容 | 26 | 原始 Impact 的 665 million reach 支持 High |
| 全球内部战略传播生态 | 51、53；另有 21、25 | Webcast、Podcast 与统一创意身份支持 High；非标准 Needs Review 页独立参与 |
| 可复用的多语言内容 | 64、65 | 两种解析均进入前两位；一组 High，另一组将领导者身份列为硬条件，原文仅写人名而未明确职位，因此 Partial |

最终代码对两组保存的真实 AI 解析做了离线复验：[stage5_final.json](stage5_final.json)。两组均满足全部目标页前十召回（实际都在前四位）与预设错误 High Match 检查；第一组全部检查通过。第二组严格 High Match 预期只有 3/5 组完全通过，另外两组如上所述。**没有修改测试预期来掩盖这项解析差异，也没有将缺少证据的条件直接判为满足。**

## 实际延迟与用量

真正冷查询测试在临时数据库副本中进行，仅副本清除查询缓存，保留案例向量；原数据库缓存与材料不清理。原始记录见 [stage5_cold.json](stage5_cold.json)。

| 测量 | 本次观测 |
| --- | --- |
| 5 个全新请求，包含真实解析与查询向量 | 3.16–7.09 秒 / 请求 |
| 立即重复相同请求，两种查询缓存均命中 | 43.8–58.6 ms / 请求 |
| AppTest 缓存搜索提交 | 104 ms |
| AppTest 随后 5 次界面重运行 | 67.5–75.0 ms，无异常 |

AppTest 测量的是服务器执行，不含浏览器绘制；测量时阻断 API 构造器，确认缓存搜索及随后的界面重运行未额外调用 AI。结果只代表当前 60 案例规模，不是服务延迟承诺。全新长 prompt 仍需要等待网络与模型响应。

上述界面延迟记录来自前端简化之前的阶段 5 基线；后续仅调整结果呈现与异常重试入口，查询与索引流程未变。

| 范围 | 请求数与 tokens |
| --- | --- |
| 当前 60 案例索引 | 2 次 INDEX，15,576 input tokens |
| 临时副本的五个新请求 | 5 次 INTENT：7,155 input / 3,557 output tokens；5 次 QUERY：577 input tokens |
| 主库开发累计记录 | 20 次 INTENT：22,100 input / 12,887 output tokens，包含多轮提示修订；并非五次正常搜索的成本 |

调用用量保存在 `search_usage`，实际费用以 OpenAI 账户计费为准；不根据本次开发累计值估算单次搜索费用。主库此前的真实调用记录见 [stage5_live.json](stage5_live.json)。

## 手动验收

1. 重启 `python run.py`，若有 Processing 任务，等待其完成；索引自动在后台准备，无需操作 Search readiness。准备失败时可在对应任务中重试。
2. 逐一粘贴五个完整 prompt，点击 Search 提交，检查主要目标页、独立链接版本及匹配证据；Enter 用于多行换行。
3. 展开 Matching evidence，检查原文证据是否满足请求；其中应能看到缺少量化成效、未确认条件或 AI 来源提示。
4. 重复相同搜索、切换标签、展开卡片，确认已有结果正常复用；Needs Review 修改并保存后状态及检索内容应更新。
5. 确认 High Match 直接显示，Partial Match 默认收起，没有 Not a Match 标签页或卡片；只有相关案例时会提示展开 Partial Match。

可复测命令：

```powershell
python -B -m evaluation.run_stage5
python -B -m evaluation.run_stage5 --live --cold --output evaluation/stage5_retest.json
```

第一条不发新请求，可以使用已有缓存；第二条明确启用真实 API，并在临时数据库副本中测量。未建索引的副本仍需发送案例文本建立索引。手工复测请使用不同输出文件保留本次基线。

## 已知边界

- AI 对同一句话的条件划分仍可能变化；相同请求缓存后保持稳定，新的措辞仍需结合 Matching evidence 判断结果。尚未证明任意长 prompt 的全部隐含条件都能正确提取。
- 证据检查是有限的确定性规则与短语 / 同句词项检查，不等于完整的语义证明。模糊职位、客户别名、复杂数值条件或使用许可缺少明确依据时应保持未确认。当前量化规则以常见传播指标和下限为主，不覆盖通用数字推理。
- Link Type 证明原片包含该类链接，不证明当前网页可访问或文件具有再分发权限。
- 语义相关性阈值是当前开发基线；这五个 prompt 与开发样本重合，召回与分类质量还需独立样本和用户反馈。阶段 5 交付实现，不宣称搜索准确率已经达标。
