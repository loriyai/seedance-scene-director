> **历史结构化稿专用（legacy）**：本文件仅用于读取或维护已有 reference-v3 / strict-draft-v1 数据。v0.19 新起草及明确重做使用 [文字稿工作流](storyboard-workflow.md)；不得把这里的字段、放行或回执要求加到 scene-text-v1 起草与交付。旧脚本保留兼容，不代表新稿仍须执行。

# 严格草稿装配

新起草默认使用 `scripts/storyboard_assembly.py assemble`，承接同一份表演、声音、摄影和状态判断，替代为某一场临时编写的组稿 helper。先在计划阶段取得声音/动作 `readiness=ready`，再确定详细摄影和可见性并装配；装配后核对未变声音依据仍有效，完成作者关联前检、冻结和独立场次审阅。它只做结构装配和确定性派生，不创作动作、摄影、可见性、情绪、反应、声音切分或依赖理由，不填写语义 `passed`，不新增审批或审阅轮次。只有实际改变声音或动作计划才返回相应计划前检。

旧输入、旧 receipt 和旧项目不要求迁移。只有明确交给本接口的新草稿采用 `draft_policy="strict-draft-v1"`；其他现有命令保持各自兼容边界。程序不读取或写入 project，不改历史冻结稿。

## 命令和纯 API

在项目作者工作目录保存正常的分镜请求 JSON，顶层仍为 `mode/request_id/span/units` 等现有字段。按当前任务取得的 Python 路径运行：

```text
<python> -B <Skill根>/scripts/storyboard_assembly.py assemble --input <作者判断-v1.json> --output <装配草稿-v1.json>
```

输出目录须已经存在。输入与输出须为不同路径，输出必须不存在；原文件和已有结果均不覆盖。失败返回具体单元、镜头、字段路径，不保存缺判断的半成品。成功状态 `assembled` 只表示本接口已装配，不能当作原文、表演或摄影已通过。之后的 source、asset、声音和审阅检查仍使用现有 director 流程。

长期脚本可导入以下纯函数；函数返回副本或派生结果，不修改调用者传入的对象：

- `assemble_batch(document)`：装配含 `units` 的正常请求，保留请求元数据。
- `assemble_unit(unit, path="unit")`：装配单个单元。
- `derive_dialogue_parts(unit, path="unit")`：从唯一声音计划及已经明确的镜窗，返回与 `shots` 同顺序的片段数组。供精确修订工具在核对旧值和版本后重编译使用，不能据此批准旧声音审阅。

## 输入与派生边界

单元仍明确提供 reference-v3 的原文范围、关联场景、资产、人物站位、承接状态和声音事件等原有判断。每镜明确提供 `scene_id/framing/angle/label/purpose/camera/composition/depth_of_field/lighting/transition/text/focus`。原有必要的引用、近景理由、环境依据、镜数依据、声音方向及工作流字段继续按原规则填写；本接口不会因它们未被本地装配检查拦截而宣称最终有效。

可以省去以下重复计算：

| 字段 | 装配行为 | 已有值如何处理 |
| --- | --- | --- |
| `unit.draft_unit_id` | 首次缺失时分配 `draft-UUID` | 保留作者明确的非空单行 ID；请求内不得重复 |
| `shot.review_id` | 首次缺失时分配 `shot-UUID` | 保留原值；本单元不得重复 |
| `shot.start/end` | 作者给整数 `duration` 时累计时码 | 与 duration 矛盾即拒绝，不挪动镜窗 |
| `unit.duration` | 从镜头末时码派生 | 不一致即拒绝，不补动作或缩时 |
| `unit.continuity.final` | 从 initial 按镜头 changes 重放 | 不一致即拒绝，不覆盖作者矛盾判断 |
| `shot.subjects` | 从显式 visible 和本镜已到达的唯一资产形象派生 | 明确给出的名单须符合实际可见性，不补画外解释 |
| `shot.dialogue_parts` | 从唯一 audio_plan 的完整片段时窗分配 | 与计划矛盾即拒绝，不静默覆盖旧文字 |

首次保存装配结果后，后续修订以该结果或保留其中稳定 ID 的版本为输入。已经有 `draft_policy` 的草稿若丢失单元或镜头 ID 会被拒绝，新增镜头须由精确修订工具或作者明确另建 ID。不能反复从没有 ID 的最初文件重新装配，再把新分配 ID 当作旧镜。草稿 ID 不占正式编号，不写入 `id`，也不根据镜头顺序猜测旧身份。复制单元作为另一新单元时须明确另建身份，不能复制旧 ID。

镜头 `duration` 是本接口的输入便利字段，展开后只保留原有 `start/end`。若两种时码写法并存，必须完全一致。单镜仍为 1 至 6 秒整数，单元累计 4 至 30 秒；入口合法范围由现有校验核对；镜数无额外数量门槛，单元组织与摄影选择统一见[创作规则总集](storyboard-rules.md)。

## 可见性必须先明确

每镜 `continuity.changes` 必须明确填写数组，没有变化则填写 `[]`。每个已登记实体均须有 `mode` 与具体 `basis`。下面两种写法均可使用；分组只代表作者确认这些已列实体共享本镜依据，不代表程序判断它们同框：

```json
{
  "changes": [],
  "visibility": {
    "driver": {"mode": "visible", "basis": "驾驶员肩部和侧脸在驾驶侧车窗取景中可见"},
    "passenger": {"mode": "occluded", "basis": "副驾完全被驾驶员和近侧座椅挡住"}
  }
}
```

```json
{
  "changes": [],
  "visibility_groups": [
    {"entities": ["observer_a", "observer_b"], "mode": "offscreen", "basis": "两人仍在门外台阶，取景边界只覆盖室内门框以内"}
  ],
  "visibility": {
    "speaker": {"mode": "visible", "basis": "发言者上身位于门内中景，门框未挡住其脸和手"}
  }
}
```

装配后只保留规范 `visibility`。实体遗漏、重复归组、单项和共享组重复判断、未知实体或空依据均报错。没有列入 `subjects` 的实体不能自动成为 `offscreen`；没有列入可见组的实体也不能自动成为画外、遮挡或缺席。空实体集合仅用于确实无需追踪实体的原文环境镜头，不能用于绕过资产与原文检查。

`focus` 始终由作者明确。已登记且可见的资产才可派生为 `subjects`；临时非资产实体不变成图片引用。同一实体镜内进入、离开或切换形象时，使用完整变化重放得到的在场状态集合核对。如果可见实体在本镜到达两个以上资产形象，作者须明确填写实际入画的 `subjects`，不能把所有版本自动同时加入。该规则保留现行逐镜摘戴检查及“本镜至少部分时段可见”的边界。

## 声音只有一份片段计划

有声单元须明确提供原有 `audio` 事件和唯一 `audio_plan=[{"record":"L...","parts":[...]}]`。每个 part 保留作者确定的原文、起止时码、停顿、读法及语气覆盖等字段，装配器只补所属 `record` 并放入完整容纳它的镜窗：

```json
{
  "record": "L000004",
  "parts": [
    {"text": "先停下来。", "start": 0.2, "end": 1.8, "pause": 0.2},
    {"text": "听我说完。", "start": 2.1, "end": 3.7, "pause": 0.2}
  ]
}
```

片段必须有序、位于声音事件内、完整落在一个镜窗，拼接逐字还原事件原台词；两个片段之间是否应停顿和切分是否自然仍由现有声音检查及作者判断。没有完整容纳片段的镜窗时，返回该片段路径，由作者调整计划或切点；不切词、不按时长比例分字、不重新估计语速，也不默认整句落镜。无声明确 `audio=[]`，可提供 `audio_plan=[]`，程序由此派生空 `dialogue_parts`。

修订须先改权威计划，再通过精确修订工具核对稳定 ID、旧值和版本后重编译派生项。普通 assemble 遇到现存 `dialogue_parts` 或末态与新计划冲突时会拒绝，这个拒绝用于防止把矛盾旧稿静默带入冻结。声音计划变化后的原有放行及相关审阅摘要仍须按实际影响失效；复制片段不代表复制批准。
