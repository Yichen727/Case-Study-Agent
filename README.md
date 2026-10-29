# Case Study Agent

从 PowerPoint 案例库中检索合适的案例幻灯片，自动补全元数据、按需纠错，选择并排序后导出可编辑的原始 PPTX。

产品基线：**Updated MVP Design Baseline v1.2**，以及用户于 2026-10-29 确认的元数据简化规则：自动补全、七字段齐全即 Confirmed、缺失即 Needs Review，取消逐字段审批。该后续规则优先于此前冲突的审批设计；实施进度见 [PLAN.md](PLAN.md)。

## 产品目标与工作流

采用 **Single Workflow Architecture**，遵循 **Deterministic First**：

```text
PPTX 导入 → 幻灯片识别与提取 → AI 元数据补充 → Slice Case 入库与审核
→ 混合检索 → 匹配分类与解释 → 用户选择与排序 → 原始 PPTX 导出
```

确定性规则优先完成文件验证、内容提取、约束判断和导出；AI 用于必要的元数据推断与语义检索。架构不引入 Multi-Agent。

## v1.2 需求

### 文件导入与来源

- 支持本地 `.pptx` 上传，以及 OneDrive / SharePoint Online 的 PowerPoint 链接，包括 Office 在线打开或编辑链接。
- 云端导入先验证地址、认证状态及当前用户读取权限，再通过授权的 Microsoft Graph / SharePoint API 获取文件。具体认证及链接解析方式需结合实际账户验证。
- 本地与云端文件通过同一套 PPTX 验证、解析和导出流程；读取不修改云端原件。
- 成功导入保留原始文件快照，避免云端更新导致检索内容与导出内容不一致。
- 每个来源记录 `source_id`、`source_type`（`LOCAL` / `ONEDRIVE` / `SHAREPOINT`）、适用的 `original_source_url`、可获取的 `source_file_id` / `source_version`、`imported_at`、`ingestion_status`、`source_snapshot_reference`。
- 使用最小权限，安全保存访问令牌，限制外部访问地址；读取权限不等于任意重新分发许可，仅处理已授权且符合内部使用要求的材料。

### 独立 Slice Case

- 每张符合条件的案例幻灯片都是独立 Slice Case，通过 `source_id` 关联原文件。
- 保留独立 Case ID、原始 PPTX Slide ID、页码、文本和超链接。
- 同名案例、READ MORE / WATCH VIDEO 版本、有链接 / 无链接版本，以及跨页延续页均不合并。
- 相同 Case Name 可以对应多条记录，也可以同时被选入导出。

### 公司样本的模板约定

已核对公司提供的 `Atomized Studios Creds Deck 2025.pptx`（83 页）：

- 第 26 页起的标准 CASE STUDY 是主要处理模板，优先提取 BRIEF / CREATIVE / IMPACT。
- 第 19–25 页中的非标准案例仍保留独立记录，并提取分类标题下方第二行的案例标题；去除固定 `CASE STUDY:` 前缀即可得到 Case Name。
- 这些非标准页缺少三段格式，`brief` / `creative` / `impact` 暂保留 `None`（数据库 `NULL`），不强行拆分或用 AI 补写；保留原文与链接，按 Needs Review 处理。
- 第 22 页实际为 `CASE STUDIES` 分隔页，应排除；其余同名案例页不合并。

页码仅用于该样本验收，解析器按内容结构识别。阶段 2 已实现上述规则，并修复第 80 页同段 BRIEF 的提取；第 79 页无三段格式，继续保留 None。样本记录见 [PLAN.md](PLAN.md)。

### 元数据补全与纠错

- 优先确定性提取 Case Name、Pillar、Brief、Creative、Impact、Slide ID 和 Hyperlink。
- Client / Services 优先来自原文或可访问的 READ MORE 页面；缺失时才根据幻灯片内容进行 AI 推断。
- 推断结果标记 `AI_INFERRED`；无法可靠判断时显示 `Unknown`。每个字段保存来源与验证信息。
- 不编造原始材料未支持的 Campaign Outcome，不用 AI 改写原始案例内容。
- 来源处理状态为 `UPLOADED` / `PARSED`；案例状态自动计算并保存为 `CONFIRMED` / `NEEDS_REVIEW`。
- Case Name、Pillar、Client、Services、Brief、Creative、Impact 全部有值即 Confirmed；任何一项为 None、空白或 Unknown 都为 Needs Review，包括非标准案例缺失三段正文的情况。
- AI 补全不要求逐条确认；需要时人工纠错，保存后自动重算状态。来源与依据在详情中保留，历史修改记录继续保存。

### 检索、选择与导出

- 组合结构化字段、关键词、明确条件及使用限制的 Hard Matching，与 Embeddings 语义检索。
- `NEEDS_REVIEW` 和 `CONFIRMED` 均可检索，每张 Slice Case 独立参与，不按名称去重。
- 匹配等级为 **High Match / Partial Match / Not a Match**。
- 展示案例名、匹配理由、原文证据、Slide Number、Link Type、Review Status 和不确定信息；不展示数值相似度。
- 未验证的 AI 推断不能单独证明 Mandatory Hard Constraints 已满足。
- 用 Checkbox 选择，在 Selected Cases 中删除或 Drag & Drop 排序；最终确认顺序决定导出顺序。
- 从已导入快照导出可编辑 PPTX，尽可能保留原文、版式、字体、图片、图表和超链接，不重新生成案例内容。

### 导入错误

验证失败即停止导入，不创建无效 Case Records。前端只显示安全提示，并按错误提供 Retry、Reconnect 或 Local Upload 操作。

| Error Code | UI 提示 |
| --- | --- |
| `INVALID_URL` | Please enter a valid OneDrive or SharePoint link. |
| `UNSUPPORTED_SOURCE` | This source is not supported. Please use OneDrive, SharePoint or local upload. |
| `AUTH_REQUIRED` | Sign in with Microsoft to access this presentation. |
| `ACCESS_DENIED` | You don't have permission to access this file. |
| `FILE_NOT_FOUND` | The presentation could not be found. Please check the link. |
| `INVALID_FILE` | The linked file is not a valid PowerPoint presentation. |
| `FETCH_FAILED` | Unable to retrieve the file. Please try again. |
| `PARSE_FAILED` | The presentation was retrieved but could not be processed. |

以上为链接导入的标准提示；本地文件验证复用错误分类，提示文案按上传场景调整。令牌、原始认证信息和敏感内部错误不传到前端。

## 当前 Demo 实际能力

技术栈：Python、Streamlit、python-pptx、SQLite、requests、BeautifulSoup。

| 模块 | 当前实现 | 尚待完成 |
| --- | --- | --- |
| 导入 | 本地多选上传，后台验证 / 解析 / 入库；有界队列、进度、失败重试；独立来源与共享内容快照 | 云端认证 / 下载 |
| 解析 | 提取标准章节、非标准标题、同段 BRIEF、组合形状、原始 Slide ID、页码及文本 / 形状链接类型 | 按后续真实样本完善必要结构，不自动重写正文 |
| Slice Case | `source_id:original_slide_id` 唯一身份；同名文件、相同页码及相同原始 Slide ID 跨来源隔离 | 后续选择、排序和导出继续沿用该身份 |
| 元数据 | 原文 / 网页优先，后台 OpenAI 补缺失 Client / Services，要求原文引用；推断缓存及完整来源 / 修改历史 | 公司样本的 AI 质量标注与校准 |
| 元数据纠错 | Needs Review 默认只列缺失记录、无分页；Client / Services 直接编辑，其他字段及原文来源折叠；一个 Save 自动重算状态 | 随真实样本继续验收 |
| 检索 | SQLite `LIKE`；检索 `CONFIRMED` / `NEEDS_REVIEW`，展示来源文件、页码及内容；内部 ID 不展示 | FTS5、结构化约束、语义检索、匹配分类及解释 |
| 选择与排序 | 导出页 multiselect | Checkbox、Selected Cases、拖拽及顺序确认 |
| 导出 | OOXML 原片复制、同源 / 跨来源及自定义顺序结构检查通过；两份样本经用户 PowerPoint 手工验收，路线已确定 | 生产选择 / 排序 / 下载集成（阶段 6 / 7）；UI 仍为占位入口 |

每次显式导入创建新的来源和独立案例；相同字节的文件复用 `data/uploads/` 中的快照，不合并记录。成功任务删除 `data/incoming/` 的待处理副本；失败任务保留副本供 Retry，Clear finished tasks 会清理终态任务及其副本。原件快照保留供后续导出。Local storage 提供闲置快照检查和显式删除，重新核对当前数据库、备份和活动导入引用后才清理；不会自动删除旧材料。

单个后台 worker，最多容纳 4 个执行中 / 排队任务；有活动任务时才每秒局部刷新进度，重启后的未完成任务需手动 Retry。网页成功缓存 24 小时、失败缓存 60 秒，可在 Needs Review 的 Retry automatic enrichment 中重试；人工编辑和显式清空不会被后台覆盖。单页预算 8 秒，网页补充总预算 20 秒，连接 / 读取超时最多 2 秒，响应正文上限 1 MiB；预算在请求和读取之间检查。不可访问的网页不阻止有效案例入库。

页面只读取当前标签页的数据，All cases 每页 25 条，元数据正文按当前案例加载；选择跨分页和标签页保留。搜索结果在数据版本变化时失效。每次页面执行做轻量 schema 版本检查，数据库缺失或为空时自动建表。

All cases 列顺序为 Title、Source deck、Slides、Pillar、Client、Services、Status。成功提示、搜索卡片和选择器隐藏内部 ID；完全相同的选择标签附加可读的 copy 序号，底层仍按独立 Case ID 操作。

Status 的 Confirmed 表示 Case Name、Pillar、Client、Services、Brief、Creative、Impact 已齐全；None、空白或 Unknown 仍视为缺失。AI 补全同样可直接产生 Confirmed；数据库、表格与搜索卡片使用同一规则。Confirmed 不将 AI 来源改为人工验证，检索硬条件仍须结合证据判断。搜索框支持按 Enter 或点击 Search 提交，输入过程中不执行检索。

Needs Review 使用可输入搜索的案例选择框，不分页；默认只列缺失记录，Show all cases 可修改完整案例。保存后保留当前案例，即使它刚变为 Confirmed；后台刷新未改动的值，并保留正在输入的草稿。主动提交搜索时切换到 Search results。Streamlit 最低版本为 1.59.2。

Client / Services 直接展示在表单中；Case Name、Pillar 及三段正文收在 Other metadata。移除 Verify / Approve / Reopen 及来源 / 验证下拉框，搜索卡片不再重复要求确认 AI。Original text and sources 保留原文与来源；历史在数据库保留。编辑仅修改索引元数据，不改写原始幻灯片。

## 本地运行

建议 Python 3.10+。在项目根目录使用 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python run.py
```

未配置 OpenAI Key 仍可导入、检索和人工审核；Microsoft 集成尚未实现。若系统的 `python` 指向不可用的 Windows 应用别名，请使用实际 Python 解释器执行上述命令。

### OpenAI 配置

在项目根目录新建 `.env`，参照 `.env.example` 填写自己的配置。Key 放在被 Git 忽略的 `.env`，示例文件保持空 Key：

```dotenv
OPENAI_API_KEY=your-local-key
OPENAI_METADATA_MODEL=gpt-5.4-mini
```

配置后，新导入会先进行确定性原文 / 网页提取，再自动补充仍缺失的 Client / Services。已有来源可在 Needs Review → Retry automatic enrichment → Retry AI enrichment 重试，无需重导入。AI 补全后自动重算状态。请求只发送该来源中需要补充的幻灯片文本，不上传 PPTX 文件；搜索、切换标签及保存审核不触发 AI。Key 不进入任务、数据库、前端状态或日志。

实现使用 [OpenAI Responses 结构化输出](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)，默认模型 [gpt-5.4-mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini)。每批 6 个不同内容项、每任务最多 60 个未缓存内容项 / 10 次请求、每页最多 6000 字符、每次输出最多 3000 token；请求超时 20 秒，任务预算 90 秒，关闭自动重试。缓存按内容、待补字段、模型和提示版本复用，包括 Unknown。输入 / 输出 token 和请求数保存在 `ai_runs`，实际费用以账户计费为准。

推断需要可匹配的原文引用，引用不足时保留 Unknown；这项检查不能代替人工判断语义是否正确。模型需支持 Responses、结构化输出和 low reasoning；不自动切换其他模型或供应商。配额、权限或网络错误不影响已入库案例，可在配置恢复后重试。

当前按本地单用户运行，服务监听 `127.0.0.1`。任务统一属于本地操作人；Microsoft 身份与任务隔离在阶段 8 接入，不应直接以此配置部署为多用户服务。

SQLite 默认位置为 `data/case_studies.sqlite3`。首次启动新版会先备份旧数据库到 `data/backups/`，再进行版本化事务迁移。旧记录、正文和修改历史保留；旧 `confirmed` 转为 `NEEDS_REVIEW`。旧记录缺原始快照和 Slide ID 时标记为需重导入，不猜测原件关联。请使用已授权的测试材料。

阶段 1–3 已交付，阶段 4 已完成实现及合成样本的真实 API 验证，待用户公司样本手测。推断与审核步骤见 [阶段 4 记录](evaluation/STAGE4.md)；后台任务、多文件身份和导出样本分别见 [阶段 3](evaluation/STAGE3.md)、[阶段 1](evaluation/STAGE1.md)、[阶段 2](evaluation/STAGE2.md)。

## 验证与评估

```powershell
python -m pytest -q
```

当前 91 项测试通过，覆盖七字段自动状态、AI 补全后 Confirmed、三段缺失仍 Needs Review、原子保存、历史保留、无分页纠错，以及原有推断、解析、导出、多来源隔离和后台交互。普通 pytest 会屏蔽本地 OpenAI 配置，全部离线执行。公司 18 张标注页全部通过自动检查；两份导出样本于 2026-10-29 通过用户 PowerPoint 手工验收。

```powershell
python -B -m evaluation.run_stage4          # 真实 SDK + 模拟 HTTP，不访问外网
python -B -m evaluation.run_stage4 --live   # 使用本地 Key，只发送 6 张合成案例
```

真实调用本次六张合成案例的 Client / Services 各 6 项检查通过，包含应保留的 Unknown；这不是公司案例准确率或完整质量保证。详见 [阶段 4 验证](evaluation/STAGE4.md)。自动像素对比未完成，其余 MVP 功能仍按 PLAN 推进。

阶段 0 已完成本地样本与离线测量，见 [基线报告](evaluation/BASELINE.md)。`evaluation/` 提供公司样本的人工预期、可复现测量脚本，以及用于跨来源身份和导出检查的合成 PPT；公司原件未复制到仓库。该基线不含真实云端 / AI 调用或浏览器绘制耗时。

最终评估包括：本地上传成功率、OneDrive / SharePoint 导入成功率、认证与权限错误处理、源文件完整性、AI 元数据推断准确率、检索与分类质量、人工审核正确性、自定义排序正确性、原始 PPTX 导出保真度，以及处理耗时与 API 成本。**先用实际样本建立基线，再确定目标阈值。**

## 实现约束

- 保持单工作流、清晰函数和现有轻量技术栈，围绕真实样本实现常见路径。
- 不为极少数情况堆叠大量分支，不同时维护多套认证、检索或导出后端。
- 文件读取边界、权限、记录身份、约束证据和原件完整性属于必要检查。
- 网页补充、AI、建索引和导出按需执行；耗时任务不占用前端交互路径。
- 缓存可复用计算，限制任务并发，分页展示；勾选和拖拽不触发网络请求或重新解析。

## 项目结构

```text
app/
  config.py                 路径配置
  models.py                 来源、字段来源与解析数据模型
  ingestion/pptx_parser.py  幻灯片解析与 Slice Case 构建
  ingestion/validation.py   PPTX 包、资源上限及危险 XML 验证
  ingestion/errors.py       统一安全导入错误
  services/ingest_service.py 验证、快照、解析、先入库后补充
  services/task_service.py   有界后台执行、任务状态、重试
  services/source_service.py 共享快照、引用保护及显式清理
  services/enrichment_service.py 网页补充编排、去重与写入保护
  services/web_enrichment.py 允许域名抓取、预算及持久缓存
  services/ai_enrichment.py  OpenAI 结构化推断、证据校验、缓存与用量
  db/migrations.py          版本化 schema、迁移前备份和旧数据迁移
  db/repository.py          SQLite 读写、字段来源和修改历史
  search/keyword_search.py  当前 LIKE 检索
  export/slide_export.py    原始导出占位接口
  export/openxml_export.py  已通过样本验收的原始 OOXML 导出实现
  ui/streamlit_app.py       Streamlit 界面
  ui/review_panel.py        简洁纠错表单与折叠原文来源
tests/                      基础测试
evaluation/                 样本预期、测量脚本、初始基线和合成 PPT
run.py                      本地启动入口
PLAN.md                     v1.2 分阶段实施及验收计划
```
