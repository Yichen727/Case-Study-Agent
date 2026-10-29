# Case Study Agent

从 PowerPoint 案例库中检索合适的案例幻灯片，自动补全元数据、按需纠错，选择并排序后导出可编辑的原始 PPTX。

产品基线：**Updated MVP Design Baseline v1.2**，以及用户于 2026-10-29 确认的后续规则：元数据自动补全、七字段齐全即 Confirmed、缺失即 Needs Review，取消逐字段审批；搜索只用一个自由文本输入框，由 AI 识别条件。后续规则优先于此前冲突的设计；实施进度见 [PLAN.md](PLAN.md)。

## 产品目标与工作流

采用 **Single Workflow Architecture**，遵循 **Deterministic First**：

```text
PPTX 导入 → 幻灯片识别与提取 → AI 元数据补充 → Slice Case 入库与审核
→ 混合检索 → 匹配分类与解释 → 用户选择与排序 → 原始 PPTX 导出
```

确定性规则优先完成文件验证、内容提取、证据检查和导出；AI 用于必要的元数据推断、搜索请求理解与语义检索。架构不引入 Multi-Agent。

## v1.2 需求

### 文件导入与来源

- 支持本地 `.pptx` 上传，以及 OneDrive / SharePoint Online 的 PowerPoint 链接，包括 Office 在线打开或编辑链接。
- 云端导入先验证地址、认证状态及当前用户读取权限，再通过只读 Microsoft Graph API 获取文件。阶段 8 支持公司账户的完整文件路径链接，真实租户授权仍待配置后验证，范围见下方 Microsoft setup。
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

- 用户只输入一段自由文本，AI 识别必须条件、偏好与排除项，不需要另填 Must-have 筛选。
- 组合结构化字段、关键词、明确条件及使用限制的 Hard Matching，与 Embeddings 语义检索。
- `NEEDS_REVIEW` 和 `CONFIRMED` 均可检索，每张 Slice Case 独立参与，不按名称去重。
- 匹配等级为 **High Match / Partial Match / Not a Match**。
- 搜索结果直接展示 High Match，Partial Match 默认折叠；Not a Match 仅用于内部分类，不在前端展示。
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

技术栈：Python、Streamlit、python-pptx、SQLite、requests、BeautifulSoup、MSAL。

| 模块 | 当前实现 | 尚待完成 |
| --- | --- | --- |
| 导入 | 本地多选上传；公司 OneDrive / SharePoint 完整路径链接、Microsoft 登录与后台只读下载；统一验证 / 快照 / 解析，有界队列与重试 | 配置 Entra 应用后验证真实公司登录、权限和下载 |
| 解析 | 提取标准章节、非标准标题、同段 BRIEF、组合形状、原始 Slide ID、页码及文本 / 形状链接类型 | 按后续真实样本完善必要结构，不自动重写正文 |
| Slice Case | `source_id:original_slide_id` 唯一身份；同名文件、相同页码及相同原始 Slide ID 跨来源隔离 | 后续选择、排序和导出继续沿用该身份 |
| 元数据 | 原文 / 网页优先，后台 OpenAI 补缺失 Client / Services，要求原文引用；推断缓存及完整来源 / 修改历史 | 公司样本的 AI 质量标注与校准 |
| 元数据纠错 | Needs Review 默认只列缺失记录、无分页；Client / Services 直接编辑，其他字段及原文来源折叠；一个 Save 自动重算状态 | 随真实样本继续验收 |
| 检索 | 单自由文本输入；AI 条件解析、FTS5＋缓存 Embeddings、证据判断、三档分类与解释；保留独立 Slice Case；内部 ID / 数值相似度不展示 | 用户手测及扩大独立标注集，继续校准解析与匹配质量 |
| 选择与排序 | 搜索卡片 Checkbox，Selected cases 拖拽 / 单条右上角 × 移除 / 顺序确认，跨搜索与结果分页保留选择；用户已确认正常 | 持续样本反馈 |
| 导出 | 确认顺序 → 后台生成原始可编辑 PPTX → 下载；快照校验、跨来源、独立重复页及 notes、结果复用和清理；阶段 7 已获用户验收 | 持续核对新增真实样本 |

本地上传按 PPTX 文件内容 SHA-256 识别重复：相同文件即使改名也复用已有来源和案例，不增加数量、不重跑解析及元数据补全，保留已有人工修改。内容不同的文件 / 新版本仍独立导入，同名案例和相同页码不合并。成功任务删除 `data/incoming/` 的待处理副本；失败任务保留副本供 Retry，Clear finished tasks 会清理终态任务及其副本。原件快照保留供后续导出。Local storage 提供闲置快照检查和显式删除，重新核对当前数据库、备份和活动导入引用后才清理；不会自动删除旧材料。

单个后台 worker，最多容纳 4 个执行中 / 排队任务；有活动任务时才每秒局部刷新进度，重启后的未完成任务需手动 Retry。网页成功缓存 24 小时、失败缓存 60 秒，可在 Needs Review 的 Retry automatic enrichment 中重试；人工编辑和显式清空不会被后台覆盖。单页预算 8 秒，网页补充总预算 20 秒，连接 / 读取超时最多 2 秒，响应正文上限 1 MiB；预算在请求和读取之间检查。不可访问的网页不阻止有效案例入库。

页面只读取当前标签页的数据，All cases 每页 25 条，元数据正文按当前案例加载；选择跨分页和标签页保留。搜索结果在数据版本变化时失效。每次页面执行做轻量 schema 版本检查，数据库缺失或为空时自动建表。

All cases 列顺序为 Title、Source deck、Slides、Pillar、Client、Services、Status。成功提示、搜索卡片和选择器隐藏内部 ID；完全相同的选择标签附加可读的 copy 序号，底层仍按独立 Case ID 操作。

Status 的 Confirmed 表示 Case Name、Pillar、Client、Services、Brief、Creative、Impact 已齐全；None、空白或 Unknown 仍视为缺失。AI 补全同样可直接产生 Confirmed；数据库、表格与搜索卡片使用同一规则。Confirmed 不将 AI 来源改为人工验证，检索硬条件仍须结合证据判断。搜索框支持多行输入，Enter 换行，点击 Search 提交；输入过程中不执行检索。

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

未配置 OpenAI Key 仍可导入、检索和人工审核；Microsoft 登录需要下方应用注册配置，本地上传不受影响。若系统的 `python` 指向不可用的 Windows 应用别名，请使用实际 Python 解释器执行上述命令。

### Microsoft setup

阶段 8 按本机 Windows、公司 Microsoft 365 账户实现。你给出的 `https://freud.sharepoint.com/:p:/r/sites/Atomized1/Shared%20Documents/.../presentation.pptx?...` 属于支持格式，粘贴完整浏览器地址即可，不需要下载地址。

首次使用需要公司 Microsoft Entra 中的一次应用注册；如果没有注册权限，请让公司 IT 完成以下设置：

1. 创建单租户应用，例如 `Case Study Agent Demo`，账户类型选择仅此组织目录。
2. Authentication 中添加 **Mobile and desktop applications** 平台，Redirect URI 设置为 `http://localhost`。本机浏览器登录使用 MSAL 交互式授权和 PKCE，见 [Microsoft 登录说明](https://learn.microsoft.com/en-us/entra/msal/python/getting-started/acquiring-tokens)。
3. API permissions 添加 Microsoft Graph → **Delegated permissions → Sites.Read.All**。这是本实现按站点路径定位文件所需的只读委托权限；应用仍受当前登录用户的读取权限限制。按公司策略完成用户或管理员同意，不添加 Application 权限或读写权限。见 [站点 API 权限](https://learn.microsoft.com/en-us/graph/api/site-get?view=graph-rest-1.0)。
4. 将 Overview 中的 **Directory (tenant) ID** 和 **Application (client) ID** 填入根目录 `.env`。两个 ID 都是应用标识，不是密码；不需要 Client Secret：

```dotenv
MICROSOFT_TENANT_ID=填写目录租户ID
MICROSOFT_CLIENT_ID=填写应用客户端ID
MICROSOFT_SHAREPOINT_HOSTS=freud.sharepoint.com,freud-my.sharepoint.com
```

重启 Demo，左侧 Presentation source 选择 OneDrive / SharePoint → Sign in with Microsoft → 在本机浏览器使用公司账户登录 → 粘贴链接 → Import presentation。下载、解析、补全与索引在后台执行，可继续搜索已有案例；失败后按提示 Reconnect / Retry，或切回 Local upload。

只支持允许租户域名下带 `.pptx` 文件路径的公司 SharePoint / OneDrive 链接，包括 `/:p:/r/` 在线打开 / 编辑地址、直接路径，以及包含文件路径 `id` 的 `onedrive.aspx` 地址。首版不解析 `1drv.ms` 短链接、`/:p:/s/` 不透明分享码、仅含文件 GUID 的 `Doc.aspx`，也不支持个人消费者 OneDrive；请从浏览器复制带完整文件路径的地址或本地上传。

身份凭据仅在当前服务端会话内存保存，Disconnect 清空凭据；关闭 / 重启 Demo 后需重新登录。普通页面刷新、选择和排序不检查云端版本、不重复下载。Graph 的认证只用于固定 API；下载跳转仅接受允许租户及受信 Microsoft 下载域名，不转发 Graph 令牌，见 [官方文件下载流程](https://learn.microsoft.com/en-us/graph/api/driveitem-get-content?view=graph-rest-1.0)。当前配置尚缺应用 ID，真实登录与公司权限验收待进行，详见 [阶段 8](evaluation/STAGE8.md)。

云端导入按文件 ID、版本和内容哈希复用：相同文件版本重复导入不增加案例，保留人工修改；不同文件或新版本独立保留。本地与云端来源独立，即使文件内容一致也不混淆身份；相同内容可以共用原件快照。导入后云端变化不会改变旧案例的导出内容。

### OpenAI 配置

在项目根目录新建 `.env`，参照 `.env.example` 填写自己的配置。Key 放在被 Git 忽略的 `.env`，示例文件保持空 Key：

```dotenv
OPENAI_API_KEY=your-local-key
OPENAI_METADATA_MODEL=gpt-5.4-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
```

配置后，新导入会先进行确定性原文 / 网页提取，再自动补充仍缺失的 Client / Services。已有来源可在 Needs Review → Retry automatic enrichment → Retry AI enrichment 重试，无需重导入。AI 补全后自动重算状态。元数据请求只发送需要补充的幻灯片文本；搜索索引请求发送案例文本，新查询发送用户的搜索文本，均不上传 PPTX 文件。Key 不进入任务、数据库、前端状态或日志。

实现使用 [OpenAI Responses 结构化输出](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)，默认模型 [gpt-5.4-mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini)。每批 6 个不同内容项、每任务最多 60 个未缓存内容项 / 10 次请求、每页最多 6000 字符、每次输出最多 3000 token；请求超时 20 秒，任务预算 90 秒，关闭自动重试。缓存按内容、待补字段、模型和提示版本复用，包括 Unknown。输入 / 输出 token 和请求数保存在 `ai_runs`，实际费用以账户计费为准。

推断需要可匹配的原文引用，引用不足时保留 Unknown；这项检查不能代替人工判断语义是否正确。模型需支持 Responses、结构化输出和 low reasoning；不自动切换其他模型或供应商。配额、权限或网络错误不影响已入库案例，可在配置恢复后重试。

### 使用搜索

在加高的 Search cases 多行框输入完整需求，点击 Search。可以直接写必须满足、偏好及排除条件；AI 在后台理解条件，前端不再展示 Understood requirements，Matching evidence 保留原文和未确认项。High Match 直接展示，Partial Match 默认折叠，Not a Match 不展示。没有 High Match 时会提示展开相关案例；元数据 Confirmed 表示字段齐全，不保证满足本次搜索条件。

案例向量在后台增量生成，默认使用 [text-embedding-3-small](https://developers.openai.com/api/docs/models/text-embedding-3-small) / 768 维；已有案例无需重导入。侧栏不再展示 Search readiness，准备失败时在对应任务中提示并提供 Retry search preparation。首次提交新请求最多进行一次条件解析和一次查询向量请求，两者并行；相同请求复用持久缓存。搜索不抓网页、不生成案例向量，切换标签和展开证据不会重复调用 AI；修改元数据后，仅变化的文档由后台更新索引。

当前 60 案例实测，新请求约 3.16–7.09 秒，缓存查询约 44–59 ms；首次请求仍受 OpenAI / 网络延迟影响。服务不可用时明确显示 Keyword-only，未确认的硬条件不能据此成为 High Match。可直接使用 [五个代表性长 Prompt](evaluation/STAGE5_PROMPTS.md) 手测；详细基线、解析差异与已知限制见 [阶段 5 验证](evaluation/STAGE5.md)。

### 选择案例与确认顺序

在 High Match 或展开后的 Partial Match 卡片上勾选 Select case。同一等级超过 10 条时可切换结果页，选择会跨页、跨搜索保留；同一页不重复加入，同名不同页和跨来源版本可以同时选择。

打开 Selected cases 标签页（或点击搜索结果上方的计数按钮），上下拖动列表设定顺序。每条显示案例名、文件名、页码及元数据状态，点击右上角 × 即可移除；不再单独提供 Remove cases 栏目。取消搜索卡片的勾选也能移除案例。

点击 Confirm order 确认最终顺序；之后增加、删除或重排都需要重新确认。选择保存在当前 Streamlit 会话，尚未加入跨会话持久化。勾选、拖拽、移除和确认只更新本地选择状态，不调用 AI 或重建索引。验证与手测步骤见 [阶段 6](evaluation/STAGE6.md)。

### 导出原始 PPTX

打开 Export，核对 Slide order，然后点击 Generate PPTX；后台完成后点击 Download PPTX。任务固定使用点击时已确认的顺序，之后继续搜索或修改选择不会改变正在生成的文件。相同选择、快照和引擎版本复用任务或已有文件，不重复生成。

导出直接复制原始快照中的幻灯片、布局、媒体和关系，不将索引元数据改写到原片。Needs Review 和未验证 AI 元数据仍显示提示，但不阻止导出。缺原件、快照变化或不支持的页面尺寸组合会给出明确提示。

生成文件保留在 `data/exports/`；Clear finished tasks 同时清理对应终态任务和生成文件，保留原件。下载内容仅在点击时读取，不在每次页面刷新时加载大 PPTX。当前库已备份升级到 schema 6，原有 60 条案例和 328 条修改历史不变。步骤、样本及限制见 [阶段 7](evaluation/STAGE7.md)。

当前按本地单用户运行，服务监听 `127.0.0.1`。本地任务属于本地操作人，云端任务绑定登录的 Microsoft 身份；凭据不存入任务或数据库。案例库仍为本地操作人的共享库，不应直接以此配置部署为多用户服务。

SQLite 默认位置为 `data/case_studies.sqlite3`。首次启动新版会先备份旧数据库到 `data/backups/`，再进行版本化事务迁移。旧记录、正文和修改历史保留；旧 `confirmed` 转为 `NEEDS_REVIEW`。旧记录缺原始快照和 Slide ID 时标记为需重导入，不猜测原件关联。请使用已授权的测试材料。

阶段 1–7 已交付且获用户确认；阶段 8 云端导入代码与离线验证完成，待配置应用并进行真实公司账户验收。历史记录见 [阶段 8](evaluation/STAGE8.md)、[阶段 4](evaluation/STAGE4.md)、[阶段 3](evaluation/STAGE3.md)、[阶段 1](evaluation/STAGE1.md)、[阶段 2](evaluation/STAGE2.md)。

## 验证与评估

```powershell
python -m pytest -q
```

阶段 8 完整离线回归 **161 项通过**，含 31 项新增 Microsoft 导入 / 认证检查及模拟登录的界面检查。真实 Edge 的选择、拖拽、×、确认、生成、实际下载、复用和清理回归另行记录。公司 8 页导出样本的 slide XML 逐字节一致，外链和 24 个媒体 / 图表 / 嵌入部件检查通过；阶段 7 已获用户确认。普通 pytest 屏蔽本地 OpenAI 配置，Microsoft 测试替换认证和 HTTP，不访问真实公司文件；离线通过不等同于真实租户验证。

本次已备份并将旧库中同一文件的 4 次重复导入从 240 条案例归并为 60 条；修改历史及 AI 处理记录保留。处理记录与去重边界见 [重复上传修复](evaluation/DUPLICATE_IMPORTS.md)。

```powershell
python -B -m evaluation.run_stage4          # 真实 SDK + 模拟 HTTP，不访问外网
python -B -m evaluation.run_stage4 --live   # 使用本地 Key，只发送 6 张合成案例
python -B -m evaluation.run_stage5          # 离线检索，允许复用已有缓存，不产生新 API 调用
python -B -m evaluation.run_stage5 --live --cold --output evaluation/stage5_retest.json
# 最后一条在临时数据库副本中清除查询缓存，真实调用五个 prompt 并测量界面重运行；原库不清缓存
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
  services/microsoft_auth.py 本机浏览器登录、内存凭据与断开连接
  services/microsoft_source.py 允许租户链接解析、只读 Graph 与受限下载
  services/enrichment_service.py 网页补充编排、去重与写入保护
  services/web_enrichment.py 允许域名抓取、预算及持久缓存
  services/ai_enrichment.py  OpenAI 结构化推断、证据校验、缓存与用量
  db/migrations.py          版本化 schema、迁移前备份和旧数据迁移
  db/repository.py          SQLite 读写、字段来源和修改历史
  search/keyword_search.py  兼容原有调用入口
  search/documents.py       搜索文档、内容哈希及本地 FTS 同步
  search/intent.py          一次 AI 请求理解、条件原句及持久缓存
  search/embedding_service.py 后台增量向量、查询缓存及本地矩阵
  search/hybrid_search.py   FTS5 / 语义候选合并和排序
  search/classification.py  条件证据、匹配分类及解释
  export/slide_export.py    有序案例定位、快照完整性与导出边界
  export/openxml_export.py  原始 OOXML 复制及独立重复页 / notes
  ui/streamlit_app.py       Streamlit 界面
  ui/review_panel.py        简洁纠错表单与折叠原文来源
  ui/selection_state.py     有序案例选择、确认与导出顺序快照
  ui/selection_panel.py     Selected cases 拖拽、移除及确认界面
  ui/selected_case_list.py  内置轻量拖拽列表及单条 × 移除组件
  ui/export_panel.py       后台生成、确认顺序预览及延迟下载
  ui/microsoft_panel.py    Microsoft 登录、链接输入及后台导入
tests/                      基础测试
evaluation/                 样本预期、测量脚本、初始基线和合成 PPT
run.py                      本地启动入口
PLAN.md                     v1.2 分阶段实施及验收计划
```
