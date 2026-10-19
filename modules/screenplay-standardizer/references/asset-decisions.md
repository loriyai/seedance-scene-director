# 资产审阅草稿、集中应用与变更影响

本接口只处理已明确选择的稳定ID和作者审阅决定。集合身份、道具分类及参考必要性须有原文证据和具体判断，不能由称谓或物件类别自动确定；接口不替代逐场原文审阅，也不执行画布操作。原有`asset-decisions-v1`决定结构和最终全量交付校验继续使用。下文的对象、段号及摘录仅为接口示例，实际决定须引用当前剧本与台账。

## 冻结候选前准备证据

跨段原文、段内换行或制表符可能不符合`visible_uses.evidence`等单行字段的要求。先确定要引用的真实连续范围，用内置适配器一次准备证据；不要等生成冻结台账和审阅决定后再逐文件替换换行。

```json
[
  {"evidence_id": "EV001", "source_paragraphs": [10, 11]},
  {"evidence_id": "EV002", "source_paragraphs": [18], "quote": "人物实际读到的逐字原文摘录"}
]
```

以上仅为选择结构，段号和摘录须改成当前原文。省略`quote`时取所引段落全文；提供时须逐字存在且唯一，跨段摘录用换行连接原段落。摘录须实际覆盖范围的首尾段，不能列出宽范围却只摘取其中一段。

```text
python scripts/evidence_draft.py prepare <source.json> <evidence-selections.json> --output <evidence-draft.json>
python scripts/evidence_draft.py check <source.json> <evidence-draft.json>
```

适配器只把证据中的空白连续段规范为单个空格，不改字词、标点或说话人。它提前拒绝不连续/重复/越界段号、改写摘录，以及移除空白后无法唯一定位的证据。输出保留`raw_evidence`、原始段落、`source_ref`、整份原文`source_digest`和绑定摘要；后续`check`重新对照实际原文，原文其他段落变化也会使旧草稿失效。

API `prepare_evidence_draft(paragraphs, selections, source_ref=...)`与CLI一致；`evidence_fields(paragraphs, draft, evidence_id)`先验证来源，再返回现有台账需要的`evidence/source_paragraphs`两个字段。作者据此填写初始候选的证据，并明确场次、事件与分类，完成现有场次关联和语义审阅后再冻结候选、准备审阅决定。适配器不推断场次或分类，不写台账/旧审阅记录，不填`reviewed_full_script`，不刷新冻结输入的摘要；输出固定`semantic_review: not_performed`和`formal_ready: false`。已有冻结输入确需改证据时，按既有变更影响及重新审阅流程处理。

证据草稿集中保存一次。辅助语义笔记和交接报告可引用其文件、绑定摘要与`evidence_id`，只写判断和异常，避免再次抄录整份原文或完整台账。正式事实/决定协议要求的实际`evidence`、场次及摘要仍须完整填写，不能用一句“见报告”代替；引用摘要只减少重复搬运，不表示已审阅或可自动复用。

## 固定输入后分批检查，最后集中应用

同一次归并与来源冻结后，各批可从同一完整候选台账生成`review-template`。每批有自己的精确scope、原记录摘要和决定值；批次之间不得重复处理同一资产或候选，人物修改和归档也不得跨批处理同一资产ID。所有批次的`input_ledger_digest`和`source_digest`必须与当前冻结输入完全一致。

```text
python scripts/asset_ledger.py review-check <standard.txt> <source.json> <frozen-ledger.json> <decisions-01.json> --batch <decisions-02.json>
```

`--batch`可重复。只检查一批时省略。`review-check`始终只读，既不写正式台账，也不删除不相关待核项来制造“全量通过”。它检查：

- 每批输入及逐项目标原值摘要、完整scope、重复/遗漏/错ID与允许的固定字段。
- 选定人物的真实关联场次与标准稿人物槽位；发声集合的原声标签、连续来源证据及共同发声审阅结构；背景群像的模式/政策组合、连续出场证据，且在完整标准稿中没有该称谓的独立台词。
- 选定物件候选的实际证据、连续且唯一定位的原文段落、场次覆盖、分类目标与场次引用；指向独立PROP时同时核验该目标的入选路径与可见使用证据。
- 选定归档的原记录、明确原因及活动引用；仅既有协议允许的同人物退场pending可按精确摘要解决。不能处理其他pending。

允许**不相关**的pending/inactive仍存在；如果本批目标或候选目标本身还有未解决待核，则拒绝草稿检查。未知全局问题仍由完整前检处置，不会因为本批检查通过自动消失。

报告固定为`status: draft_checked`、`formal_ready: false`、`full_ledger_validation: not_performed`、`semantic_review: author_judgment_required`，列出每批实际检查的ID、剩余待核数量及绑定来源/台账/标准稿/批次摘要的`binding_sha256`。它只证明选择的决定、证据和分类结构可计算地一致；原文是否真的构成共同发声、核心作用或足够参考，仍由作者实际审阅。

其他批次完成后，集中提交同一冻结输入：

```text
python scripts/asset_ledger.py review-apply <standard.txt> <source.json> <frozen-ledger.json> <decisions-01.json> --batch <decisions-02.json> --output <reviewed-ledger.json> --report <decision-match-report.json>
```

集中提交首先拒绝批次重叠、不同输入、旧摘要或原值冲突，再一次应用全部决定；随后仍执行完整生命周期、来源、身份、政策及台账校验。未解决的任何pending/inactive都会阻止正式输出，部分草稿通过不改变此门槛。输出及报告须为不同的新文件。

集中应用保留每批原始决定及摘要，最终台账可用原来的各批决定文件分别`review-verify`，也会在后续validate/design/render/bind中对全部有效决定自动对账。不得为了让旧批次继续使用，直接修改其`input_ledger_digest`或`expected_record_digest`；输入确实改变后，先查看影响，再对当前记录重新准备并实审相应决定。

如果选择逐批正式应用，下一批必须基于上次输出的新台账重新准备。多个基于同一旧输入的批次，应使用上述集中应用方式，不要先应用一批再给其他旧批次手改摘要。

## 归并与回执的前置保护

常规顺序是先确认实体与时间层、归并命名、建立SET，再最终提交相应审阅决定。已有审阅回执时，normalize写出前会先检查旧回执本身有效，再核对归并后决定字段及相关身份、事实、地点/布景和场次依赖。

如果会合并已审ID、改变分类字段或影响决定依据，立即拒绝写出并列出相关ID；不能先产出失效副本，再让后续设计才报错。只改制作显示名且审阅字段和相关依赖未变时可以继续，并报告`name_only_ids`；正式设计的摘要及编译检查仍需要执行，不把这一许可当成设计已经有效。

该保护不自动历史化、不重签回执、不刷新摘要。确需改变已审结构时，显式回到结构审阅或既有迁移流程，保留原回执和历史记录，核实变更后再建立当前决定。未关联已有回执的归并仍执行原有资产命名/引用规则，不能因此猜测身份。

## 变更影响清单

```text
python scripts/asset_ledger.py impact <before-ledger.json> <after-ledger.json>
python scripts/asset_ledger.py impact <before-ledger.json> <after-ledger.json> --before-source <before-source.json> --after-source <after-source.json> --before-design <before-design.json> --after-design <after-design.json>
```

命令只读并向stdout返回影响清单，不修改来源、设计、审阅或项目绑定。报告绑定变更前后台账及实际提供的来源/设计输入摘要，含`binding_sha256`。`direct`列直接变化，`dependent`列依赖影响及具体触发ID，`unknown`列需按全局处理的未知影响；分别给出`requires_semantic_review`和`requires_recompile`，并汇总待语义审阅/待编译ID。

- 只有制作`name`变化，来源身份及其他作者字段不变：标记`name_only`，建议确定性重编译，不据此要求重做全剧剧情理解。它仍不跳过现有完整检查或签发旧设计可复用。
- 局部事实、分类决定、场次关联或制作设计变化：列出直接ID，沿实际依赖传播；同一身体/身份锚点、基础服装到佩戴形象、PROP到关联佩戴人物、LOC到SET及SET父版本都保留依赖原因。
- 原文`source_digest`、实际来源内容、全局风格/政策或未识别字段变化：按全局/未知扩大，不从经验推断其他资产一定不受影响。只提供一侧来源或设计文件同样报告输入不完整带来的未知影响。
- 归档、待核和审阅历史等记录变化另列行政状态检查，不伪装成新的设计事实。

报告固定`formal_ready: false`、`automatic_review_reuse: false`。影响范围用于规划复核工作，不能据此自动复用审阅结论；当前全剧来源摘要保护与design/render/bind门槛保持不变。报告称某项需要重新编译，并不表示已经重编译、已经审阅或已经恢复绑定。

## 实际主顺序

先完成标准化与来源证据，使用`evidence_draft.py`准备跨行/跨段证据并实审其含义；确定基础人物、服装、地点和物件；预归并与SET建立；在同一冻结候选上保存、检查并集中应用分类决定，使基础候选可通过完整检查。涉及佩戴事件时，再登记完整variants/轨迹及关联PROP，核对受影响候选和引用后冻结最终设计清单。然后确定共享身份、基础服装与公共空间方案，进行代表性小批设计预览，合并完整方案，最终design、完整交付检查和绑定。

分类判断可以早于正式回执提交；ID归并和形象追加会改变输入，不能把初期暂定ID当作最终冻结清单。variants入口仍执行完整事实校验，不能机械地放到所有基础候选审阅之前。必要的来源变化与真实身份矛盾须重新审阅；本协议减少的是重复定位、模板拼接和发现错误过晚，不降低语义质量要求。
