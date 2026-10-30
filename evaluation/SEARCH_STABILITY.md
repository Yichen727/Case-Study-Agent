# 短搜索请求稳定性修复

日期：2026-10-30。状态：实现、138 项完整离线回归及真实查询 API 验证通过。

## 实际原因

读取现有查询缓存后确认存在西班牙语 summary / description；旧解析策略并未统一要求英语，错误输出又被缓存复用。

另一条真实缓存包含 `ai_production` 的同义词 `[generative AI, AI-generated, AI-made]`。原证据判断未将 made 视为通用制作词，于是误把 AI-made 当作具体制作方式，只查这一种措辞，覆盖了有效的 generative AI 证据，导致两个 GEN AI BRAND FILM 版本变为 Partial Match。不是案例合并或原文缺失。

即使条件有直接证据，旧分类仍可能因语义分数或关键词覆盖率不足而降级，放大了不同表达和关键词语言漂移带来的差异。

## 修复

- 解析策略升级为 `search-intent-v5-en`。摘要、条件描述、检索词和未确认项明确要求英语；request_quote 保留输入原文。字段 schema 同样说明语言要求。旧版本解析缓存不再命中，界面中的旧结果也按策略版本失效；不删除案例或历史缓存。
- 仅规范常见无关搜索包装：例如 Show me the case/cases about、Find case studies about，以及大小写、末尾标点、AI-generated 连字符；等价请求给解析与查询向量相同的输入和缓存键。
- 不对整个文本做单复数词干化，不去除否定、具体方式、数字或限制。`one case` 等数量表达不会被当作普通 case/cases 包装剥离。
- AI-made / AI-created / AI-generated 等通用制作表达使用相同证据路径。明确请求 dubbing、lip sync、Adobe Firefly 时仍需对应原文证据，不能用普通 AI 品牌片替代。
- 所有正向必须条件均被原文或可信字段直接证明时，不再被低语义分数降级；未知条件、未确认解析项及排除条件仍阻止 High Match。没有正向证据时继续使用原来的相关性判断。
- 保持每个新请求最多一次解析和一次查询向量，并行执行；不增加逐案例 LLM、自动翻译、额外语言模型或案例向量重建。

## 验证

搜索定向检查 **33 项通过**；完整离线回归 **138 passed，37.52 秒**，含原有两条 protobuf 弃用提示。

真实调用在当前公司案例库的临时副本中执行，只发送搜索文本，不上传 PPTX，不生成案例向量，不写回生产数据库。原始记录见 [search_stability_live.json](search_stability_live.json)。

以下五个输入得到相同的结果顺序和分类，两个 GEN AI BRAND FILM 原始页面均为 High Match，摘要均为 `Cases featuring AI-generated content.`：

1. `Show me the cases about AI generated content!`
2. `Show me the case about AI generated content!`
3. `Show me cases about AI-Generated content`
4. `Find case studies about AI generated content.`
5. `AI generated content`

第一条约 3.07 秒；后四条解析和查询向量缓存均命中，约 23.5–25.3 ms。这是当前 60 案例的一次观测，不是 p95 或所有表达形式的承诺。

另行验证：

- `AI-made content`：两个品牌片仍为 High Match，摘要为英语。
- 增加 `Must use AI dubbing`：品牌片因缺配音方式证据保持 Partial Match。
- 增加 `Must have at least 10 million views`：品牌片因缺观看量证据保持 Partial Match。

合计 8 个查询变体，仅 4 次 INTENT、4 次 QUERY 请求。INTENT 使用 6,191 input / 522 output tokens；QUERY 使用 27 input tokens。摘要均已人工核对为英语，具体用量不等于费用，费用以账户计费为准。

复现命令：

```powershell
python -B -m pytest -q
python -B -m evaluation.run_search_stability --live
```

长请求仍依赖模型正确拆分条件；本次不承诺任意改写都具有完全相同的解析或排名。严格条件不因表达近似而放宽，后续质量评估继续沿用已标注案例与真实查询。
