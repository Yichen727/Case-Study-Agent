# 阶段 8 — 公司 OneDrive / SharePoint 导入

日期：2026-10-29。状态：代码与离线验证完成；真实公司账户登录、权限和下载待配置应用注册后验收。阶段 7 已获用户确认。

## 本次范围

用户确认只需连接导入公司 OneDrive / SharePoint 的 PPTX，并提供 freud 租户的完整 Office 编辑地址。本实现能解析这种 `https://freud.sharepoint.com/:p:/r/sites/Atomized1/Shared%20Documents/.../file.pptx?...` 格式，不需要直接下载 URL。

支持允许租户内的完整 `.pptx` 路径、`/:p:/r/` 地址，以及 `onedrive.aspx?id=完整文件路径`。公司 OneDrive 使用 `freud-my.sharepoint.com/personal/.../Documents/...pptx`。首版不处理 `1drv.ms`、`/:p:/s/` 不透明分享码、只有 GUID 的 `Doc.aspx` 或个人消费者 OneDrive；使用完整路径或本地上传即可，不增加备用下载引擎。

## 实现

- `microsoft_auth.py`：MSAL 公共桌面应用，单租户浏览器交互式登录与 PKCE。登录在后台，普通页面重运行不访问 Microsoft；点击 Sign in / Reconnect 才打开本机浏览器。
- `microsoft_source.py`：先校验链接，再用 Graph GET 定位站点、文档库和 driveItem；使用当前账户授权。只申请委托 `Sites.Read.All`，没有 Application 权限、读写权限或 `/shares` 调用。
- Graph `/content` 返回下载跳转时，只访问配置租户和 `.files.1drv.com` 受信下载地址；不向跳转地址转发 Bearer。不会把用户 URL 当作任意 HTTP 抓取入口。
- 凭据与刷新缓存仅在当前服务端会话内存保存，Disconnect 清空；任务中的 owner 为账户身份哈希。数据库、任务 JSON、界面错误与日志不写令牌、认证响应或临时签名下载 URL。
- 下载使用 1 MiB 分块，最多 4 次跳转，连接 / 读取超时 5 / 10 秒，获取阶段预算 180 秒，文件上限 500 MiB。预算在请求和分块间检查；已经开始的读取受单次超时约束。认证页面另有 180 秒完成窗口。
- 下载前后比较 eTag，期间版本变化时拒绝导入；实际字节仍走现有 PPTX 校验、快照、解析、事务入库、补充和建索引。成功后清理下载副本，原快照继续用于导出。
- 云端同文件 ID、同版本和同内容复用记录，保护人工修改；新版本与不同文件独立保留。相同内容可共享快照，本地与云端来源身份独立。
- 共用现有单 worker / 容量 4 的队列。后台任务与登录分开；浏览、选择和排序不重新下载。Retry 要求相同 Microsoft 身份重新授权，不能借其他账户或匿名方式绕过权限。
- 若解析入库后补充失败，Retry 继续处理该次导入的快照，不替换成已经变化的云端内容。切换导入方式仍可使用本地上传。

API 与权限依据：[MSAL 浏览器登录 / PKCE / localhost](https://learn.microsoft.com/en-us/entra/msal/python/getting-started/acquiring-tokens)、[按站点路径读取与权限](https://learn.microsoft.com/en-us/graph/api/site-get?view=graph-rest-1.0)、[Graph 文件下载与预授权跳转](https://learn.microsoft.com/en-us/graph/api/driveitem-get-content?view=graph-rest-1.0)。

## 已执行验证

完整离线回归：**161 passed，35.02 秒**；含 **31 项新增 Microsoft 检查**。两条现有 protobuf 弃用提示不影响结果。

| 检查 | 结果 |
| --- | --- |
| 用户示例格式、公司 OneDrive 路径 | 可解析；解析本身不联网 |
| URL、租户、端口、用户信息、路径穿越、令牌参数及外部 / 内部跳转 | 验证阶段拒绝，不创建无效案例 |
| 未登录、401、403、404 / 410、429 / 500 | 分别得到 AUTH_REQUIRED、ACCESS_DENIED、FILE_NOT_FOUND 或 FETCH_FAILED |
| HTML 假文件、损坏的实际 PPTX、下载期间文件更新 | INVALID_FILE、PARSE_FAILED、FETCH_FAILED；无无效来源 / 案例 |
| 授权下载 → 解析 → 快照 → 案例 | 合成 PPTX 成功导入两条案例；SHA-256 内容及来源字段一致 |
| 同文件重复导入、元数据人工修正、新 eTag | 不增加重复案例、保留修正；新版本独立来源和案例 |
| OneDrive 与 SharePoint 统一流程 | 两者均通过模拟授权下载与实际 PPTX 解析 |
| 云端更新后的旧案例导出 | 反向顺序导出，slide XML 与导入原件一致；导出零 Graph 请求 |
| 重试发生在入库后的补充失败 | 重新检查授权，不重复下载或替换原快照 |
| 凭据清理、任务身份、重定向认证头 | 内存缓存断开时清空，其他身份不可查看任务，不向下载地址发送 Bearer，任务中无测试令牌 |
| AppTest 界面 | 未登录提示、模拟登录、保留已粘贴链接、后台导入、重复刷新无请求、Disconnect 和切回本地上传通过 |

Microsoft 测试替换 MSAL 登录和 HTTP 响应，文件校验、PPTX 解析、快照、SQLite、后台任务与导出使用实际代码。没有访问公司示例文件，没有发起真实登录，也没有使用本地 OpenAI Key。不能把这些结果当作真实云端导入成功率或网络耗时基线。

复现：

```powershell
python -B -m pytest -q
python -B -m pytest -q tests/test_microsoft_import.py tests/test_microsoft_auth.py
python -B -m evaluation.run_stage6 --export --output evaluation/stage8_regression.json
```

最后一条使用本机无界面 Edge，复查既有选择、拖拽、移除、确认、原片生成和实际下载；它不验证真实 Microsoft 登录。

本次 Edge **17 / 17 检查通过**，无浏览器或 Streamlit 异常。首次勾选约 169 ms，拖拽约 279 ms，生成等待约 1.44 秒；实际下载 47,389 bytes 的两页原片，内容和确认顺序均通过比对。原始记录：[stage8_regression.json](stage8_regression.json)。这些是当前合成样本的一次观测，不代表云端下载速度或 p95。

## 配置与真实验收

当前环境尚未配置 Tenant ID 和 Client ID，未触发公司认证。需要在 Entra 注册单租户公共桌面应用，Redirect URI 为 `http://localhost`，委托权限 `Sites.Read.All`；不需要 Client Secret。公司是否允许注册应用、用户同意或要求管理员批准仍需核实。具体步骤见 [README Microsoft setup](../README.md#microsoft-setup)。

配置并重启后手动验证：

1. OneDrive / SharePoint → Sign in with Microsoft，使用公司账户完成登录和必要同意；不能登录时核对 IT 的应用注册 / 同意策略。
2. 粘贴用户提供的完整编辑链接并导入，检查后台进度、案例数量、标题和页码；非 CASE STUDY 模板可能成功解析但得到零案例，这是识别结果，不代表下载失败。
3. 再次导入同一文件版本，案例数量应不变，人工修改保留。
4. 使用已知无权读取、已失效的文件链接核对错误；Disconnect 后不能提交云端导入，重新登录可以继续。不要通过撤销本人唯一权限来制造测试条件。
5. 分别验证公司 OneDrive 和 SharePoint 真实文件；网络故障应为 FETCH_FAILED，本地上传仍可用。
6. 从导入案例中选页、排序、生成并下载，在 PowerPoint 检查原片；仅用专门测试副本验证云端后续更新，旧记录的导出仍应对应旧快照，新导入应形成新版本。

实际验收记录暂为空：Microsoft 登录、有效 SharePoint / OneDrive 下载、真实 403 / 404、企业同意策略、网络时长均待测试。不启动阶段 9，不宣称阶段 8 已完成真实环境验收。

运行边界：本机单用户工具，案例库由本地 Windows 操作人使用；云端任务的身份过滤不是多用户数据库访问控制。仅全球商业 Microsoft 365，下载使用直接 HTTPS，不读取系统代理 / netrc；如公司网络强制代理或返回不同下载域名，应针对实际环境验证后再调整，不自动扩大地址范围。数据库继续使用 schema 6，本阶段测试只写临时数据库，现有公司案例未改动。
