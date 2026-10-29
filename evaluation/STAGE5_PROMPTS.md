# 阶段 5：五个代表性搜索 Prompt

这些请求依据公司 PPTX 的 60 张独立案例（48 个不同标题）编写，覆盖主题、交付方式、成效证据、多个版本和证据不足的情况。它们是第一轮校准与回归样本，不是独立测试集。

使用方法：刷新 Demo，将每段英文原样粘贴到加高的 Search cases 多行输入框，点击 Search；Enter 用于换行。Matching evidence 展示原文依据和未确认项，条件解析在后台完成。界面不显示数值相似度。

Needs Review / Confirmed 表示元数据完整性，与 High / Partial / Not a Match 分开。案例元数据缺失，但原文能支持请求的条件时，仍可参与检索。

前端直接展示 High Match，Partial Match 默认收起，需要时展开；Not a Match 仅保留内部分类，不展示。

下列“预期”是验收目标，并非每次全新 AI 解析都保证同样分类。两组真实解析的目标案例均进入前四位，但第 1 / 5 组在更严格的条件划分下为 Partial Match；完整结果与限制见 [STAGE5.md](STAGE5.md)。遇到差异时先查看匹配理由和原文证据。

## 1. AI-led hospitality storytelling

Find AI-led content for a global hospitality brand. I am looking for a cinematic brand film that brings hotel environments, fictional characters and membership benefits to life within a constrained event budget. The work must use generative AI in the actual production, rather than simply advertise a product with AI features, and must be relevant to hotels or hospitality. A humorous narrative and a luxury travel setting would be useful. Keep distinct slide versions of the same project as separate results, and explain which parts of the original slide support the match.

- 优先核对页码：81、82。
- 预期 High Match：81、82。
- 已观测差异：若 AI 将“brand film”单列为硬条件，原文支持酒店题材和 AI 制作，但不直接使用该表述，当前证据检查会保留 Partial Match。
- 检查重点：Distinguish AI used to make a film from AI being the product being advertised; retain both hotel-film versions.

## 2. Interactive experiences with measured outcomes

Find interactive digital or immersive experiences with measurable outcomes. The audience must actively participate, rather than only watch a conventional film, and the original material must report a quantified audience or campaign result such as views, reach, engagement rate or conversion. I am interested in responsive installations, interactive storytelling or digital experiences that make a complex subject tangible. Sustainability would be a useful theme. Do not treat the number of projectors, screens, soundscapes, planned activities or people affected by a social problem as proof of campaign effectiveness. If an experience is relevant but no measured result is supplied, explain that gap instead of inventing an outcome.

- 优先核对页码：71、72。
- 预期 High Match：无；不能把证据缺口当成已满足。
- 预期 Partial Match：71、72。
- 检查重点：A deliberate evidence-gap query: interactive installation is documented, quantified campaign effectiveness is not.

## 3. Conservation storytelling with proven reach

I need a purpose-led environmental campaign that turns a complex conservation issue into a bold, accessible film for public audiences and decision-makers. The case must contain a quantified campaign outcome, rather than only describe an ambition or say engagement was strong. I would prefer a cinematic concept, recognisable talent, social distribution and evidence that the campaign contributed to wider conversation or policy action. Please prioritise the original evidence for reach or views and distinguish real results from background statistics about the environmental problem. General sustainability films can be related examples, but should not be called a high match when measured outcomes are missing.

- 优先核对页码：26。
- 预期 High Match：26。
- 检查重点：Outcome evidence comes from the original Impact section; general sustainability relevance is insufficient.

## 4. Global internal strategy communications

Find a case showing how a large organisation turned a complex internal strategy launch into an ongoing content ecosystem for employees and senior partners across countries. It must include both a leadership webcast and a podcast series, with a consistent creative or audio identity across the formats. I would prefer an example at the scale of hundreds of thousands of employees or thousands of partners, supported by leadership participation and promotional assets. A standalone podcast is a useful partial match but does not satisfy the full multi-format requirement. Include separate versions and continuation slides where their evidence is relevant, even when a record still has missing structured metadata.

- 优先核对页码：51、53。
- 预期 High Match：21、25、51、53。
- 预期 Partial Match：52。
- 检查重点：Both formats are mandatory; Needs Review remains searchable; duplicate titles retain independent slide identity.

## 5. Reusable multilingual leadership content

Show me scalable multilingual leadership content for international corporate events. The work must use AI voice dubbing or lip-sync to let a senior leader address audiences in their own languages, and must allow reuse without additional filming for each language or event. Human editing or oversight is a preference because brand trust matters. Generic AI interviews, avatars with no multilingual evidence, or a conventional film about AI are only related examples. I want the original evidence for the multilingual production method and the reduction in repeated shoots, not an invented estimate of cost savings or audience performance.

- 优先核对页码：64、65。
- 预期 High Match：64、65。
- 已观测差异：若 AI 将领导者身份单列为硬条件，原文写了人名而未明确职位，当前证据检查会保留 Partial Match。
- 检查重点：Require the actual multilingual production method and reusable filming evidence; preserve both link variants.

