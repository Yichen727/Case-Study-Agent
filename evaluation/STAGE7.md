# 阶段 7 — 原始 PPTX 导出集成

日期：2026-10-29。状态：实现与自动验证完成，用户已明确反馈“完美！没有问题！”并要求进入阶段 8。以下保留样本、验证范围及可复现手测步骤。

## 使用方式

1. 在搜索结果选择案例，在 Selected cases 拖拽排序，然后点击 Confirm order。
2. 打开 Export，核对 Slide order，点击 Generate PPTX。
3. 等待后台任务完成，点击 Download PPTX 下载。生成时仍可搜索、查看或修改元数据。
4. 添加、删除或重排后先重新确认，再生成新文件。旧下载固定使用原生成顺序；如果当前选择不同，界面会明确提示，并可展开 Slides in this file 核对旧文件内容。

Needs Review 和含未验证 AI 元数据的案例仍可导出，界面保留提示，不增加人工审批步骤，不修改原片或添加水印。索引中的人工元数据修改不会写入原始幻灯片。

## 导出与缓存

- 使用唯一的 OOXML 导出引擎，按 Case ID 定位 source snapshot 和 original Slide ID；不通过重新绘制形状或图片生成近似 PPTX。
- 点击 Generate 时捕获已确认的不可变顺序，保存完整任务输入。后台不再读取之后变化的 UI 选择。
- 前端只检查轻量元数据和快照是否存在；后台为每份不同快照校验 SHA-256，再复制原始幻灯片及关系。
- 同源、跨来源及已经导入的云端快照走同一引擎。实际 Microsoft 认证与链接下载仍属于阶段 8；此处没有宣称完成真实云端导入。
- 不同案例引用同一原始页时，每个输出页及其 notes 拥有独立 part，媒体、布局及主题仍共享。输出页数、顺序与独立选择一致。
- 复用键包含有序 Case ID、原始 Slide ID、快照 SHA-256 及引擎版本 `openxml-v2`。相同请求复用正在排队 / 执行的任务，或仍存在的成功文件；元数据文字变化不会重新生成原片。
- 下载使用 Streamlit 的[延迟数据读取](https://docs.streamlit.io/1.53.0/develop/api-reference/widgets/st.download_button)，只在点击时读取 PPTX；刷新、切换页面不会反复加载大文件或重建导出。
- 导出文件位于 `data/exports/`。Clear finished tasks 同时清理终态任务及不再被其他任务引用的生成文件；活动任务受保护，原始 `data/uploads/` 快照不删除。
- 引擎先写临时文件，完整成功后原子替换结果；失败移除临时文件。重启后的未完成任务标记 Interrupted，可 Retry；已成功文件可再次复用。

缺快照、快照变化、原始页不存在、不同页面尺寸或内部链接目的页未选中时显示安全的可恢复提示；不输出空白页，不自动缩放或重建案例。前端不显示原始内部异常、路径或令牌。

## 数据库

schema 5 → 6 增加 `tasks.cache_key` 及复用查询索引；没有增加审批或修改案例结构。

当前库已由新版初始化流程自动备份、升级。备份为 `data/backups/case_studies.before-v6-2be5f8bc.sqlite3`。逐表核对升级前备份：sources、cases、slides、字段来源、修改历史、审批历史及 AI runs 保持不变；现有 **60 条案例、328 条修改历史**，完整性检查 ok、外键错误 0。详细记录见 [stage7_migration.json](stage7_migration.json)。

## 验证结果

- **130 项完整离线测试通过，41.59 秒**；最终任务清理锁调整后另行通过 9 项导出 / 后台任务检查。两条既有 protobuf 弃用警告，无测试失败。
- 覆盖任务输入冻结、后台运行时人工修改不影响原片、重复请求 / 文件复用、缺失及变化的快照、引擎版本失效、跨来源、共享快照重复页、独立 notes 关系、不同尺寸、原始 XML / 媒体 / 图表 / 母版与包关系。
- 界面回归验证未确认时不能生成、已完成任务复用、选择变化后仍明确展示旧下载，以及渲染时不读取 PPTX 数据。
- **真实本机 Edge 无界面浏览器 17 项检查通过**：选择、拖拽、确认、生成、真正下载文件、原文与顺序核对、重复生成只保留一个任务、× 删除同步、清空选择和完成任务文件清理。全程只有 3 次主动 Search 执行检索，没有额外搜索或真实 OpenAI 调用。

浏览器记录见 [stage7_browser.json](stage7_browser.json)，使用隔离库和合成案例。公司样本在实际数据库及快照的隔离副本中处理，生产案例不改动，未启动 PowerPoint COM。

### 可直接检查的导出样本

| 样本 | 顺序 / 内容 | 大小与生成观测 |
| --- | --- | --- |
| [company_selected.pptx](artifacts/stage7/company_selected.pptx) | 公司原页 82、81、65、64、26、19、79、80，共 8 页 | 58,643,200 bytes；约 2.18 秒生成，相同请求约 4.1 ms 命中缓存 |
| [mixed_sources.pptx](artifacts/stage7/mixed_sources.pptx) | B 第 1 页、A 第 2 页、导入云端快照 A 第 1 页、本地 A 第 1 页，共 4 页 | 56,546 bytes；约 71 ms 生成，包含独立重复原始页 |
| [browser-selected.pptx](artifacts/stage7/browser-selected.pptx) | 浏览器实际下载的两页合成案例，按拖拽确认顺序 | 47,389 bytes |

公司样本每张 slide XML 与原件逐字节一致，外部超链接不变，全部内部关系可解析，24 个媒体 / 图表 / 嵌入部件匹配原件；混合样本同样通过，含 3 个部件。notes 独立复制由专门的合成测试覆盖。原始检查记录见 [stage7_exports.json](stage7_exports.json)。

这些时间是当前样本的一次观测，不是 p95 或大型库性能承诺；不含本地文件上传、浏览器下载时间或 PowerPoint 绘制。结构与字节检查不能代替目标 PowerPoint 的打开、编辑和视觉验收，不宣称所有 OOXML 特性完全无损。

## 手动验收

1. 重启 Demo，搜索并选择不同案例版本，调整顺序并确认。
2. Generate PPTX → Download PPTX，核对输出页数和顺序；可先打开上面的两份样本。
3. 在本机 PowerPoint 打开，检查无修复提示；逐项核对文字、布局、字体、图片、图表、超链接及可编辑性。
4. 连续生成相同选择，确认复用成功结果；增删 / 重排后重新确认，验证新文件对应新顺序。
5. Clear finished tasks 后生成文件应被清理；原始快照及案例仍保留，之后可以重新生成。

已知限制沿用选定引擎：不混合不同幻灯片尺寸；多个来源含嵌入字体的组合尚未验证；内部跳转目的页必须一起选入。实际机器缺字体时的显示差异需在 PowerPoint 中核对。

复现命令（全部可离线运行，不发送公司内容给 AI）：

```powershell
python -B -m pytest -q
python -B -m evaluation.run_stage6 --export --output evaluation/stage7_browser.json
python -B -m evaluation.run_stage7
```
