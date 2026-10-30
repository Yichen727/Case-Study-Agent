# 阶段 0：样本与测量基线

记录日期：2026-10-28。阶段 0 的本地样本与测量已完成；当前范围仅保留本地上传。未修改生产解析、数据库、UI 或导出逻辑，未把公司 PPT 复制进仓库。

## 环境与复现

- 用户确认：继续在本地 Windows 运行，使用公司 Microsoft 账户。是否可注册应用及批准读取权限仍待核实，不影响阶段 1。
- 实测环境：Windows 10、Python 3.12.4（Anaconda）、SQLite 3.45.3。
- 依赖：Streamlit 1.59.2、python-pptx 1.0.2、pytest 9.1.1、requests 2.34.2、BeautifulSoup 4.12.3。
- 本机检测到 Microsoft PowerPoint / Office `16.0.20326.20232`，作为后续保真人工验收环境；本阶段未启动 PowerPoint，不代表已经验证输出保真。
- 当前 Streamlit 配置允许上传最多 500 MB，样本约 321.1 MiB；浏览器上传耗时尚未测量。

在项目根目录运行，`python` 应指向包含项目依赖的实际解释器。本次使用 `C:\Users\86153\anaconda3\python.exe`：

```powershell
python -B -m pytest -q -p no:cacheprovider
python -B -m evaluation.make_fixture_decks
python -B -m evaluation.run_baseline '..\Atomized Studios Creds Deck 2025.pptx'
```

原始阶段 0 测量值见 [baseline_current.json](baseline_current.json)，人工预期见 [sample_manifest.json](sample_manifest.json)。基线脚本用临时 SQLite 数据库调用现有 `ingest_deck`，将网页补充替换为“不可用”结果；不写入当前案例库，不发送网络或 AI 请求。记录的失败是现有实现与目标的差距，不通过修改预期使其变为通过。后续重新运行默认写入 `baseline_latest.json`，保留历史报告；阶段 1 结果见 [STAGE1.md](STAGE1.md)。

## 样本清单

公司原件：`Atomized Studios Creds Deck 2025.pptx`，共 83 页、336,673,828 字节。SHA-256：

```text
82eb81f91313aedd7062b104a3dd46731e5643a082b970e13dfb1c591b098043
```

人工预期为 60 张独立案例页：19–21、23–73、77–82。第 22 页及 74–76 页是分隔 / 介绍页，不能因为位于案例范围内就建立案例。

| 样本 | 覆盖内容 | 预期 |
| --- | --- | --- |
| 19 / 23、20 / 24、21 / 25 | 非标准案例、同名多页 | 提取第二行标题，三段字段为 None，不合并 |
| 22 | CASE STUDIES 分隔页 | 不建立案例 |
| 26 | 标准三段、图片、READ MORE | THE BOTTOM LINE；原文与链接可追溯 |
| 29 | 标准案例无链接 | 三段提取，链接为 NONE |
| 31 / 32、33 / 34 | 同名 READ MORE / WATCH VIDEO | 每页独立，两个版本均可召回和选择 |
| 64 / 65、81 / 82 | 同名、有链接 / 无链接 | 每页独立，无链接版本不可丢失 |
| 79 | 无三段的 AI AVATARS | 标题保留，三段字段为 None |
| 80 | BRIEF 与正文同段、原始 Impact 为 TBC | 后续修复三段提取，保留原文 TBC，不生成成果 |
| `fixtures/source_a.pptx` | 2 页同名，第二页延续；原生可编辑图表、图片、超链接 | 两张独立 Slice Case；后续导出保留这些对象 |
| `fixtures/source_b.pptx` | 另一来源，第 1 页同名；母版背景不同 | 不覆盖 source_a 的第 1 页；跨来源导出保留母版差异 |

两份补充 PPT 只含合成测试内容，由 [make_fixture_decks.py](make_fixture_decks.py) 生成，不冒充公司案例。公司原件没有原生 chart 部件且只有一个 slide master，故增加上述最小补充样本。没有把多来源保真问题误标为已通过。

已检查补充文件的页数、章节、图片、原生图表、超链接和不同母版 XML，并在临时数据库实测跨来源导入：预期 3 张独立案例，当前仅保存 2 张，source_b 的 `slide-1` 覆盖了 source_a 的 `slide-1`。结果见 [fixture_checks.json](fixture_checks.json)，这是阶段 1 的关键修复验收项。

`sample_manifest.json` 包含 18 张案例页的标题、章节与链接预期、5 个典型查询，以及结构化约束的正向、冲突、未知示例。原始公司文件继续保留在项目上一级，作为人工对照原页；没有重新生成或更改它。

## 本次测量

现有测试：**5 passed in 0.32s**。

| 项目 | 实测结果 | 解释 |
| --- | --- | --- |
| 幻灯片 / 案例页 | 83 / 60 | 案例页集合与人工清单一致 |
| 三段均提取成功 | 52 页 | 第 80 页存在三段内容但当前未提取；其余无三段页按约定保留 None |
| 缺少案例标题 | 6 页 | 19–21、23–25，当前标题筛选误排除 CASE STUDY: 标题 |
| 本地解析 | 469.404 ms | 单次测量，未宣称冷磁盘性能或统计分位数 |
| 案例保存 | 8.508 ms | 临时 SQLite，单次测量 |
| 离线导入总计 | 499.409 ms | 包括解析、构建、模拟网页不可用和入库；不含实际下载、网页补充或浏览器上传 |
| LIKE 查询 | p50 约 1.0–1.34 ms，p95 约 1.13–1.70 ms | 每个查询 20 次，60 条案例；没有语义检索 |
| 完整案例列表读取 | p50 1.462 ms / p95 1.728 ms | 20 次，60 条案例 |
| AppTest 首次执行 | 3,174.932 ms | 含框架初次执行开销；不含浏览器绘制 |
| AppTest 重运行 | p50 26.185 ms / p95 26.762 ms | 5 次，无远端任务；样本量小，仅作初始观察 |
| 每次页面重运行 | 3 次 list_cases、1 次 init_db | 实际探针计数；需要在 UI / 任务阶段减少重复工作 |
| AppTest 异常 | 0 | 普通已入库页面可执行，未验证全部交互流程 |
| 网页补充入口 | 51 次，41 个不同 URL | 本次全部模拟不可用；允许域名下有 17 次 HTTP 访问机会，均为不同 URL |
| 实际 HTTP / AI 请求 | 0 / 0 | 未测量真实网页延迟，不能据此计算在线导入成功率或完整 API 成本 |

所有 60 条记录在此离线测量中均缺 Client / Services，这是现有原文提取不足及关闭网页补充的结果，不能推广为真实在线补充后的缺失率。现有 Streamlit 调用还出现 `use_container_width` 弃用提示，记入 UI 阶段集中处理。

## 查询与证据基线

| 查询 | 必须包含的样本页 | 当前结果 |
| --- | --- | --- |
| THE BOTTOM LINE | 26 | 通过 |
| EY: THE IMPACT SERIES | 31、32 | 通过，两个版本均保留 |
| EY: AI MULTI-LANGUAGE FILMS | 64、65 | 通过，有链接 / 无链接版本均保留 |
| STREET OF DREAMS | 19、23 | 失败：标题未提取，原始全文也未进入当前 LIKE 检索字段 |
| ocean conservation campaign | 26 | 失败：整句 LIKE 无法完成此语义匹配 |

这些是“必须召回指定页”的初始样例，不是穷尽相关性标注，不用它们计算全库 precision / recall 或宣布最终检索准确率。

约束证据样例：第 31 页原始分类为 `CASE STUDY SUSTAINABILITY`，可支持 Pillar 包含 Sustainability；若条件要求 Pillar 包含 Podcast，则与该结构化分类不符。第 80 页 Impact 为 TBC，不能证明已获得确定的数值成果。后续还须测试：仅凭未验证的 `AI_INFERRED` Client，Mandatory Client 条件只能为未确认。

这些约束判断是验收预期；当前 Demo 尚无分类器，不把预期当作实测通过。

## 阶段结论与后续

- 本地基线与可复现入口已建立，不设置未经验证的成功率、延迟或成本阈值。
- 下一阶段按 PLAN 的阶段 1 实施：来源模型、全局身份、字段来源和可回退的数据库迁移；先修复数据基础，不顺带改写检索或导出。
- 标题提取和第 80 页章节提取留给阶段 2；重复读取、远端串行等待及 UI 弃用参数留给阶段 3。
- 本地上传成功率、在线网页补充、云端认证 / 权限、AI 推断质量、完整相关性评估、排序和原片导出保真尚待对应阶段实测。
- 本机 PowerPoint 已检测到，但自动化能力和导出打开检查仍待阶段 2 验证。
