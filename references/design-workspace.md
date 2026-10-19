# 共享制作基线与受控设计分组

本流程供已经完成全剧身份、适用造型、地点复用、群体和道具筛选的资产阶段使用。它限制各设计草稿的可写范围，并冻结共享设计；不替代全剧统筹，结构编译通过也不代表美术或原文语义审阅通过。下列人物服装及室内布置仅为字段示例，须按实际对象形态与已核实事实填写，不能作为新增剧情事实的依据。

正式台账仍由主代理单入口写入。后台设计者只交分组 JSON；不得修改台账、共享基线、其他分组、正式提示词或画布。旧项目可继续使用原来的完整 `design` 路径；本模块不更改原编译器或旧提示词。

## 顺序与并行边界

1. 先完成完整候选台账的原文证据、稳定 ID、身份/服装连续性、LOC/SET 关系、佩戴关系和所有审阅结论落盘；完整台账验收通过。
2. 按[初稿前的短约束表](asset-image-design.md#初稿前的短约束表)读取事实、吸收候选、可见范围及继承来源，再确定所有独立图片 CHAR 的 `identity_anchors` 和 `identity_assignments`。先设计下游要共享的基础 CHAR、佩戴 PROP、基础 SET；用当前完整台账和风格文件冻结共享制作基线。未设计资产仍留在完整台账，不制造删减版台账。
3. 草稿保存先做`design_scope.py`字段检查，再按实际字段类型选代表资产编译预览；有SET时包含一个代表SET，佩戴组同时实读PROP单品与CHAR佩戴板。作者按短约束表记录实际提示词如何落实约束，并检查美术效果，再扩写其余设计。`design-check`的同身份闭包要求仍有效；本模块将未冻结同身份形象放在同组，有效冻结的同身份设计作为只读依赖。
4. 计划精确覆盖全部尚未冻结图片资产。每个 ID 只有一个拥有者；有未冻结的跨组依赖时拒绝计划，改为同组，或先完成共享依赖并重新冻结、重建计划。
5. 在宿主并发预留允许的数量内，只运行无写入依赖冲突的组。基础 CHAR/佩戴 CHAR/关联 PROP 未冻结时放同组；同身份服装组不拆散。SET 版本必须先冻结基础/父空间，不与尚未冻结的父 SET 并行设计。
6. 主代理逐组验证真实交付文件、完成实际验收并关闭交接，收齐精确ID集合后合并，再走原全量`design → render → bind`。交接关闭后才改变其输入；不得通过刷新旧摘要或将已完成任务记为cancelled绕过关闭校验。只交资产的项目按交付范围结束。

全剧身份、地点同一性、造型变化、群体分类或道具取舍由主代理统一处理，不能由分组各自并行推断；分组也不能改变已冻结共享设计。共享决定需要修改时，修改其作者输入，建立新基线和新计划；旧基线下提交会因输入 hash 失效而被拒绝。已有作者设计携带过期来源摘要时须重新审阅当前依赖，不得只替换摘要继续复用。

## 文件与命令

脚本：`modules/screenplay-standardizer/scripts/design_workspace.py`。所有命令只向 stdout 输出 JSON，不改输入或正式台账。主代理将输出保存到独立草稿文件，切勿把 shell 重定向目标设为任一输入文件。

```powershell
python "$script" freeze script.txt source.json ledger.json shared-design.json video-style.txt
python "$script" plan baseline.json groups.json
python "$script" verify-group baseline.json plan.json group-result.json --group-id character-a
python "$script" merge baseline.json plan.json character-a.json sets.json props.json
```

`freeze` 固定五个原文件的绝对路径及原始字节 SHA256：剧本、source.json、完整台账、共享设计输入、风格。每次计划、验证和合并重新检查这些文件，并核对基线内嵌内容与原文件。结果的 `baseline_sha256` 和 `plan_sha256` 绑定完整内容，宿主交接另行绑定基线/计划文件的原始字节 hash。文件不存在、风格/来源变化、共享方案变动时拒绝沿用。

共享设计输入沿用原设计字段，增加完整的身份分配表；`designs` 只放本轮明确冻结的设计，可为空，不会自动借用台账中的旧设计。

```json
{
  "image_design_version": "gpt-image-2-v2",
  "identity_anchors": [
    {"id":"identity-a","age":"成年男性","body":"修长体型","face":"固定脸型与五官","hair":"黑发短发"}
  ],
  "identity_assignments": {"CHAR0001":"identity-a","CHAR0002":"identity-a"},
  "designs": [
    {"asset_id":"CHAR0001","attribution":"制作设计","basis":"当前已审来源与服装依据","reviewed_source_facts":true,"identity_anchor_id":"identity-a","styling":"具体服装设计"}
  ]
}
```

`identity_assignments` 精确覆盖全部独立图片 CHAR；v2 多人群像和 `prompt_only` 背景群体不填。相同身份/世次不能拆成不同锚点，不同已知身体不能共用锚点；佩戴版本继承对应基础人物。锚点不可闲置或引用未知 ID。

`groups.json` 是数组：

```json
[
  {"group_id":"character-a","owned_asset_ids":["CHAR0002"]},
  {"group_id":"sets","owned_asset_ids":["SET0001"]},
  {"group_id":"props","owned_asset_ids":["PROP0001"]}
]
```

计划增加 `read_only_asset_ids` 及 `read_only_dependency_sha256`。后者以 `asset:ID`、`anchor:ID`、`location:ID` 为键，绑定冻结设计、共享身份锚点和 LOC 事实。实际分组提交必须逐字复用计划的范围及依赖摘要，不能自行扩大范围。

设计交接中的约束索引沿用既有ID、证据和摘要，不成为另一份事实源。候选去向、事实主体、适用版本、基础服装、关联PROP或父SET变化时，重查受影响资产及依赖闭包；只改制作显示名按impact区分重编译与语义重审。pending不能借索引升级为confirmed，输入hash变化也不能通过替换旧摘要沿用审阅。

```json
{
  "baseline_sha256":"从基线复制",
  "plan_sha256":"从计划复制",
  "group_id":"character-a",
  "owned_asset_ids":["CHAR0002"],
  "read_only_dependency_sha256":{"asset:CHAR0001":"从计划复制","anchor:identity-a":"从计划复制"},
  "designs":[
    {"asset_id":"CHAR0002","attribution":"制作设计","basis":"当前已审来源和另一套服装依据","reviewed_source_facts":true,"identity_anchor_id":"identity-a","styling":"本版本具体服装设计"}
  ]
}
```

上例为同身份另一套服装；佩戴版本仍使用既有 `covering / visible_identity` 及基础继承规则。提交不能新增 `identity_anchors` 或其他共享字段。漏项、重复 ID、越界设计、依赖摘要不符、旧计划、缺组或重复组均拒绝。

## 空间版本必须从冻结空间派生

对于 `variant != base` 的 SET，分组设计不直接写四个完整空间字段，而是引用已冻结的直接父 SET；无父引用的兼容情况使用唯一基础 SET。编译前从父 SET 原样复制 `space / materials_palette / furnishings / lighting`，再应用明确差异，最后展平为原编译器接受的四字段。其他字段原样保留，正式台账不增加新空间协议字段。

```json
{
  "asset_id":"SET0002",
  "attribution":"制作设计",
  "basis":"同一房间，只增加本场原文明示的红绸",
  "reviewed_source_facts":true,
  "space_reference":{
    "asset_id":"SET0001",
    "design_sha256":"baseline.shared_designs.SET0001的规范JSON摘要",
    "differences":[
      {"field":"furnishings","operation":"append","new":"厅内挂有红绸","source_fact_text":"厅内挂上红绸","basis":"本场明确增加红绸，其余布置沿用"}
    ]
  }
}
```

每项差异只能选择四个空间字段之一，同一字段最多一项。`append` 用分号追加；`replace` 另含 `old`，必须在父字段中唯一精确匹配，且 `new` 实际不同。`source_fact_text` 必须精确引用当前 SET 自己已经验证的 `visual_facts[].text`；不能仅凭“多年后”擅自加破败、翻修或换家具。程序检验引用与变换，不判断文义充分性；作者仍须审阅差异是否由事实支持。

`differences: []` 合法，用于原文没有明确视觉变化的已审时间版本。差异为空不另造衰老/破损效果。不得同时提交 `space_reference` 和四个完整空间字段。父设计 hash 改变即失效。多级时间版本按父链串行冻结；冻结一个版本时同样使用以上引用协议，提供其已冻结祖先设计，不能把父空间自由改写成另一套。

## Python 接口与验收含义

- `freeze_from_files(script, source, ledger, shared_design, style) -> baseline`：生产入口，完整原文验收和五文件冻结。
- `validate_baseline(baseline, require_files=True)`：供交接准备要求真实输入文件，拒绝仅内存构造的生产基线。
- `plan(baseline, groups) -> plan`；`validate_plan(baseline, plan)`；`group_spec(baseline, plan, group_id) -> group`：计划及拥有范围/只读依赖查询。
- `verify_group(baseline, plan, group_id, submission) -> report`：检查真实提交并在完整台账上编译，报告只属于 `structurally_valid_group`，`semantic_review=not_performed`、`formal_ready=false`、`bindable=false`；返回该组的展平设计和提示词预览。
- `merge(baseline, plan, submissions) -> full_design_payload`：重新验每个实际组，收齐后运行原 `apply_design` 和完整 `require_designed` 验证，返回完整旧格式设计 JSON。主代理仍用正常命令落正式台账并导出/绑定。全资产已冻结时 `groups=[]`、`submissions=[]` 合法。

纯内存 `freeze(script, paragraphs, data, payload, style)` 供已验证解析结果的可信调用者和测试使用；生产交接必须要求真实文件基线。本模块不分配后台代理、不执行并发预留，也不生成语义审阅凭证。宿主负责独立草稿目录、并发上限、ownership reservation 和单入口正式写入；共享内容或来源变化后不会自动重用旧审阅。
