# 阶段 6 — 选择、拖拽排序与确认

日期：2026-10-29。状态：实现及自动验证完成，待用户手动验收。用户已确认阶段 5 和重复上传修复正常。

## 使用方式

1. 正常搜索，在 High Match 或展开的 Partial Match 卡片上勾选 Select case。超过 10 条的结果支持分页；换页、换搜索、换标签页不清空选择。
2. 点击 Selected cases 标签页，或搜索结果上方的 Selected cases 计数按钮。列表显示案例名称、来源文件、页码及 Confirmed / Needs Review，内部 ID 不展示。
3. 上下拖动列表设置顺序。需要移除时，点击该案例右上角 ×；不再单独选择 Remove cases。回到搜索卡片取消勾选也能移除。
4. 列表顶部的 Generate PPTX 点击时直接确认当前顺序并后台生成，取消 Confirm order；增删或重排后再次 Generate 即采用新顺序。
5. Download PPTX 与生成按钮并排，文件准备好后可下载；不再另设 Export 区域或标签页。

2026-10-30 快捷操作更新：High Match 折叠栏默认展开；Select all High Match 覆盖本次搜索所有结果页，保留手选 Partial Match 和其他搜索选择；Clear all selections 清空全部选择并同步可见 / 离页勾选。批量操作不重新执行检索或 AI。

当前验证：139 项完整离线回归通过（34.60 秒），其中 19 项选择 / 界面 / 导出定向检查亦单独通过。AppTest 覆盖 11 个 High Match 跨两页全选，并保留 Partial Match 与之前搜索的选择；清空后两个结果页勾选均取消，搜索函数未额外执行。真实 Edge 的 22 项检查通过，见 [selection_shortcuts_browser.json](selection_shortcuts_browser.json) 与[当前界面](selection_shortcuts_browser.png)。真实浏览器采用合成 PPTX 及固定 High / Partial 等级测试界面，不用于评估检索质量，也没有调用 AI。

同一 Slice Case 不会重复加入；相同 Case Name 的不同页、相同页码的不同来源可以同时加入。完全相同的显示标签用 copy 序号区分，排序不依赖显示名称。

## 实现

- `app/ui/selection_state.py` 保存有序 Case ID 与确认快照，独立于 Streamlit 会清理的离页 Checkbox 状态。
- 新增 / 移除保留其余案例顺序；拖拽返回值必须包含完整且唯一的已选 ID，不接受丢失、重复或替换案例的顺序。
- `app/ui/selected_case_list.py` 使用 [Streamlit 内置 v2 组件](https://docs.streamlit.io/develop/concepts/custom-components/components-v2/register)，在同一列表实现拖拽与右上角 ×；不再依赖 streamlit-sortables，也不保留备用排序引擎。
- 搜索卡片选择和 Selected cases 面板使用局部 fragment。勾选、拖拽、移除和确认不触发 PPTX 解析、网页抓取、AI 或索引重建。
- 选中列表仅查询轻量字段；不读取每条案例正文，也不扫描来源 PPTX。没有更改数据库 schema 或写入生产元数据。
- `export_order_snapshot()` 仅在当前顺序已确认时返回不可变 tuple；之后 UI 发生变化，不会改变已捕获的快照。实际导出按钮及后台任务绑定在阶段 7 实现。
- 选择保存于当前 Streamlit 会话；跨会话持久化未纳入本阶段。

## 自动验证

右上角 × 的点击无效问题已在真实浏览器中复现并修复：`action` 是一次性 trigger，原先误设了持久状态的 `default`，导致事件无法正确传至后端。现已移除此默认值，仅保留事件回调。默认值只适用于状态值，参见 [Streamlit 组件挂载说明](https://docs.streamlit.io/develop/concepts/custom-components/components-v2/mount)。

修复后 **12 项选择状态 / 界面回归通过，23.07 秒**，并通过真实本机 Edge 的 14 项检查：× 移除指定案例、连续删除至空列表、回搜索页勾选同步、保留剩余顺序、拖拽及删除使确认失效、跨搜索保留、导出占位预览。整个浏览器验证只有 3 次主动 Search 执行检索，选择操作未追加搜索；无浏览器或 Streamlit 异常，未调用真实 OpenAI、未修改生产案例库。

最新原始记录：[stage6_inline_remove.json](stage6_inline_remove.json)。[最新截图](stage6_inline_remove.png) 显示右上角 ×。以下 122 项完整测试、旧截图和性能数据保留为原始阶段 6 的历史基线。

完整离线测试：**122 passed，31.95 秒**。包含已有解析、来源隔离、元数据、导出、搜索和去重回归；另有两条现有 protobuf 弃用警告，无失败。

新增 / 更新验证覆盖：

- 同一案例不重复，跨来源相同 Slide ID 和同名不同页保持独立。
- 原生 Checkbox 跨结果分页和标签页保留，移除后回到卡片取消勾选。
- 增加、删除、重排使确认失效；无实际变化的操作不误清确认。
- 非法拖拽顺序不能丢失或重复案例；确认快照不会随之后的选择改变。
- Selected cases 移除与确认控件，以及 Export 占位页的确认顺序预览。

AppTest 不操作第三方 iframe 内部拖拽，因此另用 **真实本机 Edge、无界面模式**检查生产 UI 和实际组件。测试使用隔离数据库与合成 PPTX，禁用 OpenAI，生产数据库保持不变。

浏览器验证全部通过：真实鼠标拖拽 3 条同名 / 同页码的独立记录，确认失效、重新确认、新搜索后保留顺序、单条移除、移除后重新确认，以及导出占位页预览。仅两次主动 Search 执行检索，选择动作没有追加检索；无浏览器错误或 Streamlit 异常。

浏览器原始记录：[stage6_browser.json](stage6_browser.json)。[界面截图](stage6_browser.png) 为合成案例，不含公司案例内容。

三条合成选择的一次观测：勾选至计数更新约 **185 ms**；实际鼠标拖动至界面反映新顺序约 **464 ms**，包含模拟鼠标移动、服务器往返及浏览器刷新。这不是多次测量的 p95，也不是大型列表的延迟承诺。

可复现命令：

```powershell
python -B -m pytest -q
# 以下仅供浏览器验证；Playwright 不是应用运行依赖，需要本机已安装 Edge。
python -m pip install playwright
python -B -m evaluation.run_stage6
```

浏览器脚本只启动自己管理的临时服务和无界面浏览器，完成后关闭，不访问真实 OpenAI。

## 建议手动验收

1. 重启 Demo，使用已有五个 prompt 中任意一个，勾选几个案例，包括同名的不同页版本。
2. 换一个 prompt 再选案例，确认之前的选择仍在；结果超过一页时，换页再回来查看勾选状态。
3. 打开 Selected cases，将末项拖到首项，点击顶部 Generate PPTX 直接确认并生成。
4. 点击某条案例右上角 ×，确认旧确认消失、其余顺序保留；回搜索结果确认对应 Checkbox 未勾选。
5. 再次 Generate 并下载，核对文件顺序与点击生成时的顺序一致。
