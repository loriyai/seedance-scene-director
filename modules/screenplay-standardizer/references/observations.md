# 逐场观察与证据交接（observations-v1）

逐场整理时同步记录人物、服装、地点、佩戴、声音和物件的原文证据，供后续全剧统筹和资产初始化直接使用。可以在全量 review 之前收集；收集、结构检查、样本审阅通过均不等于语义统筹完成。只修改身份、人物行、声源或场界决定实际影响的场稿，再更新相应 review 与观察。

观察不是第二份剧本。`source.json` 仍是原文正文来源；观察只保留段落编号、短的逐字证据片段及场次去向。场次清单保存源摘要、场稿摘要、标题和段落范围；导入审阅快照只存校验回执，不复制全文、paragraph_map 或对白正文。

## 生产顺序

1. 首次实读原文时随逐场草稿记录观察；明确主体的外貌、服装、物件结构按下节粒度记为 `visual_fact` 候选，保留 `pending` 状态和需要确认的具体依据。
2. 用 `prepare` 绑定当前源与场稿，再 `check`。样本/局部 review 可验证当前场次接口；此时不得宣称完成全剧身份判断。
3. 全剧统筹确认目标、归属和事实。保留匿名人物原编号；如 `记者1`、`记者2`、`保镖01`、`保镖02` 不自动等同。LOC 合并、佩戴身份、群像计数、主要道具筛选仍用各自审阅入口。
4. 对允许导入的事实明确填写 `confirmed`、`target`、`fact` 与 `basis`，并提供覆盖这些场次且摘要匹配的 review。
5. `asset_ledger init --observations ... --observations-review ...` 或独立 `import` 将确认事实写到对应稳定 ID；未确认观察进入可索引的 review queue。主全量 review、assemble、最终台账 validate 的放行要求不变。

## 首次实读的事实候选粒度

复合摘录先拆清“谁的什么属性”：记录`tentative_subject`、原段号、短的逐字`evidence`，用`fact.key`标明属性；能精确定位时填写`target`，尚未确定的目标不猜ID。明确眉骨、下颌、服装或物件部件等视觉信息，用`visual_fact`候选承接，不能全部只留成物件提及再让设计阶段重找；尚未审定主体、适用版本或归属时仍为pending，不自动导入。

稳定属性与瞬时状态分别记录：薄肌、眉骨属于对应人物身体，衣服剪裁属于适用服装；水位、湿润、动作和当场光照归当前场次或状态观察，不能混成永久外貌。晚霞不成为人物固定肤色。按现有类型和目标表达，不新造事实类别；同主体同属性可用一条必要证据，不要求每个名词都建记录。

## 记录格式

先写观察列表 `records.json`，由 prepare 计算摘要，避免手填或刷新旧摘要。

```json
[
  {
    "observation_id": "OBS0001",
    "scene_number": 1,
    "kind": "visual_fact",
    "tentative_subject": "陈默",
    "source_paragraphs": [1],
    "evidence": "穿灰色布衣",
    "status": "pending",
    "requires_decision": false,
    "basis": "原文写出服装；待全剧统筹确认主体及适用形象",
    "target": {"category": "characters", "source_entry": "陈默-基础形象"},
    "fact": {"field": "visual_facts", "text": "灰色布衣", "attribution": "叙述", "key": "服装"}
  }
]
```

`observation_id` 是稳定的 OBS 标识，重新导入时保留。`source_paragraphs` 必须连续且位于所指场次；`evidence` 必须是相应原文的逐字片段，跨连续段落时使用原段落间的换行。不得把改写句作为证据，也不得用别场证据补足本场事实。

可以导入的两类事实：

- `visual_fact` 对应 `fact.field=visual_facts`。
- `personality_fact` 对应人物的 `fact.field=personality_facts`。

事实必须有 `text` 和 `attribution`。可选 `fact.key` 用于显式标记同一属性；同场、同目标、同字段、同 key 的矛盾值会拒绝导入。工具不通过文本相似度推断矛盾，人工仍须统筹语义。

其余 `candidate_mention`、`identity`、`costume_state`、`location_identity`、`wear_event`、`sound_source`，以及新增的`scene_boundary`、`mobile_space`、`asset_dependency`仅作为观察/决定索引，不能夹带 `fact` 自动修改资产。`source_conflict` 必须保持 `pending` 且 `requires_decision=true`；处理矛盾后保留 observation_id，改为对应已解决的观察类型，并记录依据。

`source_conflict`只用于实际影响主体、声源、场界或依赖且无法判定的冲突。疑似笔误可原样保留、归属仍明确时，按对应的非冲突观察类型记下原文与`basis`，设`requires_decision=false`；例如“陈默顾立刻转头”能明确动作主体时，不擅删“顾”，也不因疑似多字阻塞交付。

`target` 支持：

- `category + asset_id`：已有稳定 ID 的明确目标，必须在本场关联。
- 人物初始化前：`category=characters + source_entry`，按本场人物原条目精确且唯一定位；可显式重复填写相同 `scene_number`，不可跨场猜身份。条目必须在当前逐场稿人物行中。
- `category=locations` 或 `category=scenes`：由观察的 scene_number 唯一定位本场已关联 LOC 或 SC。
- SET 和 PROP 必须明确 asset_id；本工具不会新增道具、合并地点或推断使用次数。

`status=pending` 永不写入事实，哪怕填写了 target 和 fact。普通候选默认 `requires_decision=false`，只进入索引，不阻塞交付。真实尚未解决的身份、矛盾等决定必须设为 true，生成 `pending_reviews.kind=逐场观察待核`。重新导入完整观察文件，保留同一 observation_id 更新状态/依据后，仅替换本模块原摘要对应的待核记录；其他 pending 不受影响。

下游约束索引沿用这些观察ID、原始目标到稳定资产ID的映射及来源摘要。字词相同但主体、场界或适用状态变化时，受影响判断仍须重审；不能为增加导入量批量把pending改confirmed，也不能刷新hash冒充重新核验。

## 命令与接口

起草时优先登记两类跨阶段风险：一是上车/下车、车内外切镜与真实道路转场，先判SC边界，再判载体PROP、实际LOC和是否确需内部SET参考；二是可摘戴遮挡物与同一人物形象依赖，记录物件、佩戴者、摘戴事件及原文范围，待ID稳定后关联PROP与佩戴CHAR。不能为了参考图方便先拆场或建立重复地点，也不能把道具单品图的无人/单件构图写进佩戴物的物理属性。

`check`返回`risk_diagnostics`，按当前所绑定场次扫描有限的车辆及遮挡物用词，提供原文段号、少量原句和已有相关观察ID。它是非阻塞的早期提示，`risk_scan=heuristic_not_complete`；不自动确认观察、不选择图片资产、不声称找齐风险。写者实读后补充上述观察；真正尚未解决且影响场界、身份或资产依赖的事项设`requires_decision=true`。仅有“出现面具/车辆”的候选不自动阻塞。

场界需要保留的决定由screenplay_review的`boundaries`入口承载，观察引用同一原文并登记处理依据；不能用`confirmed`观察替代场界复核。部分场次审阅涉及相邻场稿时，导入前还核对`boundary_drafts`，避免隔壁场稿已改变仍复用本场旧回执。

在模块 scripts 目录使用：

```text
python -B scene_observations.py prepare records.json --source work/source.json --workdir work --output observations.json
python -B scene_observations.py check observations.json --source work/source.json --workdir work --review work/review.json
python -B scene_observations.py import observations.json --source work/source.json --workdir work --review work/review.json --ledger assets.json --output assets-with-observations.json
```

prepare 不修改确认状态。check 不需要完整 review；返回 `ready_to_import_facts=false` 时，只能继续收集/检查，不能导入已确认事实。实际导入确认事实要求对应场次在当前版本的 passed、sample_passed 或 partial_passed 报告中，且源、段落范围和场稿哈希完全匹配。局部报告仅授权其覆盖场次的结构证据复用，不能替代正式全量审核。

可调用函数均不写文件：

```python
prepare_observations(source_path, workdir, observations, scene_numbers=None) -> bundle
validate_observations(payload, source_path, workdir, review=None, ledger=None) -> report
import_observations(data, payload, source_path, workdir, review=None) -> (new_data, report)
validate_imported_observations(data, paragraphs, script=None) -> report
remap_observation_targets(data, redirects) -> new_data
```

`review` 可为 JSON 对象或路径。import/remap 返回深拷贝；原台账不被修改。旧台账没有 `observations` 字段时验证返回 not_registered。常规 validate 必须提供 parse_script 的 script，才能同时验证正文/人物行；不传 script 的内部检查只返回 source_and_facts_checked，不宣称完整通过。

导入后，`observations.source_payload` 和 payload_digest 保持原交接内容；imports 记录 observation_id、初始/当前稳定目标、SC 归属和实际写入事实。常规 validate 逐项核对原证据、稿件摘要、目标及事实是否仍在台账中。摘要通过不证明语义归属正确，工具报告始终声明 `semantic_review=not_performed`。

重复导入不会复制事实。不能静默删除旧观察或更改已经导入的事实；必须先归档旧交接、人工处理旧事实，再按当前源重新审阅。对已确认的资产合并，normalize 在处理其 redirects 后调用 remap；该函数保留原 bundle 和摘要，记录 initial_target_id → target_id 的重定向链。验证要求旧 ID 已退役、不跨类别、无循环，且事实在当前目标上仍存在。

## 改稿迁移与历史重审

migrate 保留 delivery_contract、observations_history 和所有旧观察信息。源、场稿、场次去向及导入事实仍一致时，原交接可原样保留。任一绑定变化，旧 observations 整体归档至 observations_history；原摘要不修改，并生成 `逐场观察迁移待复核`。归档同时保存迁移后仍存在的旧导入事实，防止只关闭待核却留下错误事实。

失效后必须人工重新审阅，可用当前 source/scenes 重新 prepare。新 bundle 的 `reconciliations` 是只针对指定历史的消解入口：

```json
{
  "reconciliations": [
    {
      "history_digest": "填写迁移待核项的精确历史摘要",
      "basis": "已逐项核对当前原文、场稿与旧事实",
      "resolutions": [
        {"observation_id": "OBS0001", "outcome": "reconfirmed", "new_observation_id": "OBS0001", "basis": "人工重审后确认服装事实仍成立"}
      ]
    }
  ]
}
```

每个旧 observation_id 必须有一个处理结果：

- `reconfirmed`：对应当前已确认新观察，并提供该场当前 review；旧事实必须被本次实际导入的新事实确认，或已人工移除。
- `retained_pending`：对应当前 pending 新观察；旧事实必须先移除，真正未决事项继续由新 pending 阻塞。
- `withdrawn`：明确撤回理由，旧导入事实必须已经移除。

import 只消解匹配 history_digest 的这一项迁移待核，在历史中追加处理回执，旧 bundle/摘要完整保留。未知历史、漏项、未通过当前审阅、旧事实仍在但未被确认均拒绝。其他迁移待核仍需其原流程处理；最后再次运行完整 validate。没有自动语义重基线或自动恢复保证。
