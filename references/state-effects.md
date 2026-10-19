> **历史结构化稿专用（legacy）**：本文件仅用于读取或维护已有 reference-v3 / strict-draft-v1 数据。v0.19 新起草及明确重做使用 [文字稿工作流](storyboard-workflow.md)；不得把这里的字段、放行或回执要求加到 scene-text-v1 起草与交付。旧脚本保留兼容，不代表新稿仍须执行。

# 关键动作与状态效果的唯一计划

新起草在同一份表演、声音计划里确定影响连续性的关键动作及后果，使用 `action_state_plan.policy="action-state-v1"`。已有 `action_plan` 仍是唯一动作清单，不另建重复动作表。只为需要跨镜跟踪、明确先后或改变状态的关键动作登记稳定 ID，不要求所有微动作、每镜或每句台词新增记录。

本协议负责把**作者已经明确的决定**落实到逐镜变化和承接摘要。它不会从“驶离”“抱住”“放下”等中文词语推断影响字段，不能保证发现作者完全未声明的语义遗漏。作者和独立主审仍须核对动作是否实际影响位置、持有、接触、形态、在场与相关参照；程序检查已声明的影响是否完整实现，不证明原文支持或表演成立。

没有 `action_state_plan` 的历史输入保持原有行为，不强制迁移。字段存在但 policy 未知、格式错误或编译缺失时直接报错，不能回退旧模式绕过校验。

## 动作身份、窗口和影响范围

新协议的 `action_plan` 每个关键动作包含原有 `record/quote/start/end`，增加 `action_id` 和 `state_targets`。`performance` 仍用于实际表演过程；原有 `before/after` 仍表示与整条声音事件的先后关系。

```json
{
  "action_id": "car-departs",
  "record": "L000018",
  "quote": "小车驶离酒吧",
  "start": 1,
  "end": 4,
  "milestones": {"clear-doorway": 2.5},
  "state_targets": [
    {"entity": "car", "fields": ["facts.位置", "facts.运动"]}
  ],
  "performance": "车辆起步经过门口边界，驶入道路后继续前行。"
}
```

`action_id` 是作者确定的非空单行稳定标识，不因镜头重排改名。`start/end` 是单元内明确动作窗口，`milestones` 可选，名称到时间的映射必须位于动作窗口内部；`start/end` 是保留锚点，不能再登记为里程点。

`state_targets` 只声明实际要改变的实体字段。字段为 `present`、`asset_id` 或 `facts.<事实键>`；事实键可以包含点，首个 `facts.` 后的完整字符串就是原事实键。没有状态效果但需要明确动作先后时填写 `[]`。例如车辆只是原地启动，作者可以只登记“运动”或其他实际变化，不被程序强迫改变位置。若明确登记了“位置”和“运动”，effects 却只落实运动，程序将拒绝；若两处都遗漏位置，仍需语义核对发现。

## 唯一效果源及派生项

效果只写在单元的 `action_state_plan.effects`，不再手动维护第二套可改的 `shots[].continuity.changes`：

```json
{
  "policy": "action-state-v1",
  "effects": [
    {
      "effect_id": "car-clears-doorway",
      "action_id": "car-departs",
      "at": "clear-doorway",
      "entity": "car",
      "set": {"facts": {"位置": "酒吧外道路，已离开门口停靠点", "运动": "继续前行"}}
    }
  ]
}
```

`effect_id` 稳定且在单元内唯一。`at` 引用动作的 `start`、`end` 或一个已明确的里程点。`entity/set` 沿用原有状态更新语义：只改变 `present/asset_id/facts`，facts 的 `null` 删除已存在事实。每个效果必须至少产生一次实际变化；每个声明的影响字段必须由对应动作的效果覆盖，效果不能偷偷添加未声明字段。同一时点不得有两条效果重复写同一实体的同一字段；需要多个结果时由作者确定唯一结果，不能靠列表顺序解决矛盾。

编译器从关联动作的 `record/quote` 生成原格式 evidence，不建立独立的自由证据副本。源证据仍由绑定状态核对范围及逐字引文，正确引用不证明效果解释正确。

effect中每个写入字段都必须产生实际变化，不能把未变“位置”和已变“运动”放在一起冒充两项均已落实；关系仍成立时走精确绑定的retain，不把旧值再写一遍作为更新。动作计划放行还绑定其实际读取的实体定义和进入态，修改进入条件后旧动作/声音依据失效，相机单改不影响该绑定。

程序按明确锚点只执行一次效果，再派生：

- 每镜 `continuity.changes`。
- 从 `continuity.initial` 重放得到的 `continuity.final`。
- 从进入态与末态生成的 `start_state/end_state` 后台摘要。

摘要稳定列出实体 ID、在场状态、当前形象与已登记事实，不自动写新的剧情动作。人物站位 `global`、正文、摄影、取景与可见性仍由作者判断，按既有作者关联前检核对；末态摘要的生成不替代可见表现。

普通装配时，已存在的派生项必须与权威计划一致，矛盾立即拒绝。精确修订工具核对稳定 ID、旧值与版本后，调用统一的 `clear_derived` 或 `recompile_unit`，不在各临时脚本中各自维护一份“应该清除哪些字段”的规则。

## 切点、持续状态与声音顺序

先按作者指定镜长建立明确时码，再编译效果。动作 `start` 正好位于切点时归入右侧新镜；动作 `end` 正好位于切点时归入左侧结束镜。内部里程点若恰好落在切点，必须在 effect 明确 `cut_side="before"` 或 `"after"`；不由程序猜测相位应属于哪镜。未落切点的内部锚点自然属于完整容纳该时间的镜窗。

动作窗口结束**不会自动清除持续状态**。拥抱、握持、支撑等关系可以在动作结束后保持，并经连续边界传递到下一单元。只有来源与呈现方案确实支持释放时，另一个明确效果才能删除或改变接触事实。不能为满足该协议补造松手、放下、离场动作。

`order` 可在摄影前核对动作与动作、动作与指定声音语义片段的端点关系：

```json
{
  "order": [
    {
      "before": {"action_id": "stand-up", "at": "end"},
      "after": {"action_id": "step-forward", "at": "start"}
    },
    {
      "before": {"action_id": "step-forward", "at": "end"},
      "after": {"record": "L000021", "part": 2, "edge": "start"}
    }
  ]
}
```

`before` 端点时间必须小于或等于 `after` 端点。声音 `part` 从 1 起，对应唯一 `audio_plan` 中该 record 的片段顺序，`edge` 为 start/end；必须指向实际存在的声音事件及片段。改分句或顺序时核对相应关系，不从旧序号猜测新的语义。同期动作不必强写先后；这里只保存实际需要的约束。原 action 的 `before/after` 与新 order 可以并存，但不得互相矛盾。

## 相对关系在参照变化时重新核对

需要持续追踪的相对关系明确登记依赖，而不是只留下“某人面前”“某车前方”一段文字：

```json
{
  "relations": [
    {
      "relation_id": "owner-relative-position",
      "entity": "owner",
      "fact": "位置",
      "depends_on": [
        {"entity": "car", "fields": ["facts.位置"]}
      ]
    }
  ]
}
```

这表示 owner 的“位置”事实依赖 car 的位置。一旦所依赖字段真正改变，相关事实必须在同一明确时点有更新或删除效果；例如车主实际留在原路面位置，应把参照明确改成该固定位置，不能让旧描述自动随车移动。关系事实已经结束后不会无限检查，也不会因相机移动而擅自改变真实位置。

若参照变化后关系确实仍成立，作者可以在现有前检中明确一次 `retain`。程序先返回这一次触发效果的 ID 和精确摘要，作者写入具体依据：

```json
{
  "relation_checks": [
    {
      "relation_id": "owner-relative-position",
      "trigger_effect_ids": ["car-clears-doorway"],
      "trigger_sha256": "从当前待核提示逐字取得的64位摘要",
      "decision": "retain",
      "basis": "作者说明此次确实仍成立的具体空间或运动依据。"
    }
  ]
}
```

这个摘要绑定关系、触发效果、时点及前后状态和原关系值；效果内容变化或再次触发后不能沿用旧判断。重复、过期或没有实际触发的 retain 会被拒绝。装配器只返回待核提示，不填写决定或依据；关系更新、删除直接写 effect，不再另写 retain。这些记录只用于实际需要的关系，不新增每镜或全员必填表。

## 长期接口与门槛

`scripts/state_effects.py` 由正常装配、精确修订和现有校验入口调用，不需再写临时脚本：

- `validate_plan(state, unit)`：摄影前即可检查关键动作证据、锚点、影响覆盖、先后约束及关系失效；不要求已存在 shots。state 为 `None` 只检查内部结构，实际制作传入绑定 state 核对原文。
- `compile_unit(unit, state=None)`：返回已编译副本；现存派生项冲突时拒绝，不改输入。
- `clear_derived(unit)`：只在新协议下删除副本的逐镜 changes、final、start_state/end_state，保留明确决定、取景及正文。
- `recompile_unit(unit, state=None)`：对已经由精确修订入口核实旧值/版本的输入，清除并重编译派生项。
- `validate_compiled(state, unit)`：要求派生项实际存在且与当前权威计划一致，不能用“末态和漏写 changes 彼此一致”绕过。

`storyboard_assembly.py` 在普通装配中调用编译器；`continuity.validate_unit` 在入口校验当前编译结果，因此 `director.validate_unit` 同样拦截矛盾或缺失。`audio_ready` 在摄影前核对计划并把新状态计划、对应规则摘要绑定到原有声音/动作核对中。计划改变后旧依据失效，不自动签发新依据。没有新协议的历史输入不改变原有 payload 形状和执行路径。
