# 阶段 2：统一导入、模板修复与原片导出验证

日期：2026-10-29。**阶段 2 已完成。** 自动结构检查通过，用户确认两份导出样本手工测试全部通过；选定原始 OOXML 部件与关系复制路线。验收范围为下述样本及已记录边界，阶段 3 尚未开始。

## 已完成

| 模块 | 结果 |
| --- | --- |
| 统一导入 | 已获取的文件进入同一验证、快照、解析和事务入库流程；支持携带 LOCAL / ONEDRIVE / SHAREPOINT 来源信息，不含云端认证 / 下载实现 |
| 文件验证 | 检查真实 ZIP / PPTX 结构、主文档类型及必要关系；不依靠扩展名判断有效性 |
| 资源边界 | 压缩文件最多 500 MiB、解包总量最多 1 GiB、最多 10,000 个 ZIP 条目、单个 XML 最多 8 MiB；拒绝危险路径、加密条目、DTD / 实体声明 |
| 导入错误 | 提供统一错误类型和 v1.2 八类标准提示；当前本地入口区分 INVALID_FILE、FETCH_FAILED、PARSE_FAILED，非法文件不创建来源 / 案例 |
| 安全展示 | 前端显示安全提示和重新上传 / 重试操作说明；日志只记录错误代码或异常类型，原始异常、认证信息不进入前端 |
| 标题提取 | CASE STUDY 分类标题与 CASE STUDY: 案例标题分开识别，去除标题固定前缀；第 22 页分隔页排除 |
| 正文提取 | 按文本 run 识别同段的 BRIEF 与正文；保留原始 TBC 等结果，不生成成果内容 |
| 原文元数据 | 提取显式 Client / Services 标签，优先于网页值；不猜测标题冒号前的词一定是 Client |
| 链接 | 提取文本 run、整形状点击链接及组合形状内链接；记录 READ_MORE / WATCH_VIDEO / OTHER，无链接显示 NONE |
| 网页地址 | 跳转每一步均限制为允许的 HTTPS 域名，拒绝携带用户密码及非标准端口的地址；同域规范化跳转仍可用 |
| 入库与清理 | 来源、案例、正文及字段来源事务提交；失败回滚并清理本次快照，批量中的其他有效文件可继续导入 |

来源成功快照在解析前处于 UPLOADED，解析并准备入库后为 PARSED。网页登录、AI 推断、缓存和后台任务仍由后续阶段处理。

## 公司样本检查

已用原始 83 页 PPTX 检查，结果见 [baseline_stage2.json](baseline_stage2.json)：

- 仍识别 60 张独立案例，与样本清单完全一致。
- 18 张标注页的标题、章节及链接检查全部通过，已无标题为空的案例。
- 19 / 23：STREET OF DREAMS；20 / 24：WEOY；21 / 25：ALL IN。每页独立，三段字段仍为 None。
- 第 26 页保留 THE BOTTOM LINE 及标准三段；第 80 页提取三个章节，Impact 保留原文 TBC。
- 完整三段从阶段 1 的 52 页增加到 53 页；无三段的其他案例不强行拆分。
- 本轮离线测量解析约 629 ms，导入总计约 1.25 s。包含验证、快照和入库；没有实际网页 / AI 请求，不代表在线完整处理时间或浏览器上传耗时。

现有案例库也已更新，结果见 [stage2_refresh.json](stage2_refresh.json)：

- 执行前备份到 `data/backups/case_studies.before-stage2-5bb805c5.sqlite3`。
- 补齐 6 个漏提取标题、第 80 页的 3 个章节字段，并更新 34 个案例的链接标签 / 类型。
- 共 43 个字段变化，案例仍为 60 条，Source ID / Case ID 不变；不会新增一组重复案例。
- Client、Services、Pillar、原始正文和来源记录均保持原值。原有 4 条人工修改历史逐条保留，自动修正另外追加为 parser_stage2 历史。
- 原始公司 PPTX 的 SHA-256 与阶段 0 一致，数据库完整性与外键检查通过。

[repair_stage2_fields.py](repair_stage2_fields.py) 是一次性维护工具，不在页面启动时执行。默认只预览，`--apply` 才备份并提交；人工编辑过的字段跳过，写入时再次核对当前值。没有原件快照的来源跳过，快照哈希不符则停止。

## 导出 PoC

选定实现为 [openxml_export.py](../app/export/openxml_export.py)：复制原始 OOXML 幻灯片和依赖，保留图片、图表工作簿、母版、主题和超链接关系；大二进制部件流式复制。跨源部件重命名并重写关系，母版 / 版式内部标识重新分配以避免冲突。未重新绘制 shape 或改写案例文字。后续生产导出沿用此路线，不并存多套导出引擎。

生成脚本：[export_poc.py](export_poc.py)。结构结果：[stage2_export.json](stage2_export.json)。已准备两份可打开检查的文件：

| 文件 | 页序与检查目的 |
| --- | --- |
| [company_subset.pptx](artifacts/stage2/company_subset.pptx) | 原件 80、19、26、31、32、64、65、79 页，验证自定义顺序、非标准案例、同名版本和原始内容 |
| [mixed_sources.pptx](artifacts/stage2/mixed_sources.pptx) | source_b 第 1 页 → source_a 第 2 页 → source_a 第 1 页，验证不同母版背景、延续页、图片、原生图表和超链接 |

自动检查确认：11 张输出幻灯片 XML 与对应原始部件字节一致，所有内部关系目标存在；合成样本的图片、图表和工作簿内容保留，跨来源两个母版 / 版式标识无冲突。公司子集约 39.4 MB，生成约 1.22 s；混合样本约 55 KB，生成约 0.02 s。这些是结构与内容结果，不能代替 PowerPoint 显示验收。

当前边界：

- 输入文件的页面尺寸必须一致；不同尺寸明确拒绝，不自动缩放或裁切。
- 尚未验证多个带嵌入字体文件混合，第二个及后续来源含嵌入字体时拒绝，避免默默改变字体。
- 内部跳转指向未选中幻灯片时拒绝，不偷偷把未选案例一起带入输出。
- Presentation 级自定义放映 / 扩展列表不沿用，避免引用已排除的页；案例页的正文和 shape 内容保留。
- 生产的选择、排序、下载与数据库导出接口仍属阶段 6 / 7；当前 Streamlit Export 入口仍为待集成占位。

## PowerPoint 用户手工验收通过

2026-10-29，用户对前述两份样本的测试反馈为：**“全都没有问题！”** 确认项目对应此前给出的清单：

- 文件打开无损坏或修复提示。
- 公司子集及跨来源样本页序正确。
- 文字、字体、图片和版式与原件一致。
- 超链接有效，文字与图表可编辑。
- 跨来源组合的不同背景保留。

据此完成阶段 2 的样本保真验收，并定稿 OOXML 复制路线。上述结论来自用户手工测试，不是自动像素比较，也不扩展为所有 PPTX 特性的无损保证。

此前的自动化尝试保留为历史记录：

首次后台验证中，PowerPoint 返回演示文稿对象，但读取 Slides 时出现 COM E_FAIL。尚未完成原始对照文件验证，无法判断原因是自动化环境还是导出兼容性。

后续后台复验未执行，用户选择自行完成测试。记录见 [stage2_powerpoint.json](stage2_powerpoint.json)；自动渲染对比未完成，不能宣称自动“像素一致”。这不影响后来用户报告的手工验收结果。

可选的 [validate_powerpoint.py](validate_powerpoint.py) 已准备私有只读文件副本、隐藏窗口及关闭自动修复的验证流程，但修订后的流程尚未获准运行，不能视作已通过。参数依据 [Microsoft Open2007 文档](https://learn.microsoft.com/en-us/office/vba/api/powerpoint.presentations.open2007)。

无需为本次阶段验收重新运行 PowerPoint 自动化。后续接入新的版式或资产类型时，按相同清单补充样本验证。

## 自动测试与复测

最终结果：**43 passed in 14.92s**。新增覆盖有效 / 非法包、资源上限、结构有效但解析失败、敏感错误脱敏、同域 / 越界跳转、原文优先、组合形状、同段标题、链接类型、原片 XML / 图表 / 依赖、尺寸拒绝和保留人工修改的旧记录刷新；原有 UI 与隔离测试仍通过。

```powershell
python -B -m pytest -q -p no:cacheprovider --tb=short
python -B -m evaluation.run_baseline '..\Atomized Studios Creds Deck 2025.pptx' --output evaluation/baseline_latest.json
python -B -m evaluation.export_poc
python -B -m evaluation.repair_stage2_fields
```

本地界面复测：刷新后重新搜索 STREET OF DREAMS / WEOY / ALL IN，核对同名不同页分别出现；查看第 80 页的三段正文；上传伪装成 .pptx 的普通文本，确认只显示安全提示且案例数量不增加。原有记录已修正，无需为这些修复重新上传。
