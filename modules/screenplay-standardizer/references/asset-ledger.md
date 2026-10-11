# 资产台账结构

后台主文件使用 UTF-8 JSON；分类视觉描述 TXT 由它生成。标准剧本的场景序号用于显示，`SC`（场次）、`LOC`（物理地点）、`SET`（布景）、`CHAR`、`PROP` ID 用于长期关联。首次建立台账可用 `asset_ledger.py init` 生成无事实的索引骨架；若已有台账，先用 `asset_migrate.py` 迁移并审核，不重新初始化或重编号。`init` 生成的 `time_basis: 待核` 是草稿，须逐场确认后才能渲染。

```text
python scripts/asset_ledger.py init <standard-script.txt> <task-work-dir> --output <asset-ledger.json>
python scripts/asset_ledger.py build-scenes <identity-reviewed-ledger.json> --output <sets-draft.json>
python scripts/asset_ledger.py normalize <new-unbound-ledger.json> --output <normalized-ledger.json> --report <naming-audit.json>
python scripts/asset_ledger.py render <standard-script.txt> <task-work-dir>/source.json <asset-ledger.json> --output <asset-catalog.txt>
```

## JSON 字段

```json
{
  "schema_version": 3,
  "source_digest": "由 init 计算的原文段落摘要",
  "scenes": [
    {
      "id": "SC0001",
      "number": 1,
      "heading": "场景1-日-内-昏暗房间",
      "source_range": [1, 19],
      "location_id": "LOC0001",
      "scene_asset_id": "SET0001",
      "character_ids": ["CHAR0001"],
      "prop_ids": [],
      "visual_facts": [],
      "time_basis": {"kind": "原文明示", "source_paragraphs": [1]}
    }
  ],
  "characters": [
    {
      "id": "CHAR0001",
      "name": "卢筱筱-基础形象",
      "aliases": [],
      "visual_facts": [],
      "personality_facts": []
    }
  ],
  "locations": [
    {"id": "LOC0001", "name": "昏暗房间", "aliases": [], "visual_facts": [], "identity_status": "confirmed", "identity_basis": "核实原文所属主体及空间，关联场次为同一物理房间"}
  ],
  "scene_assets": [
    {"id": "SET0001", "name": "昏暗房间", "location_id": "LOC0001", "variant": "base", "parent_id": null, "visual_facts": []}
  ],
  "props": [
    {"id": "PROP0001", "name": "枪", "aliases": [], "visual_facts": []}
  ],
  "retired_ids": [],
  "inactive_assets": {"characters": []},
  "pending_reviews": []
}
```

`init` 只建立场次、人物和待核地点索引，scene_assets为空且scene_asset_id为null，不自动提取资产事实或主要道具。地点按场景先分配独立 ID，核实是同一地点后再合并；明确姓名的角色只按完整人物条目初步归并。`卢筱筱-基础形象` 与 `卢筱筱-黑色裙子` 分别建立稳定 ID；同一完整条目在多场出现且身份明确时复用 ID。泛称有台词角色先按身份核实；群像草稿直接加地点后缀，在地点核实后normalize按同地点同原条目/形象复用，不保留逐SC重复资产。群像只用于无姓名、无台词且原文明示阵营、身份、人设或职业的背景人物。若相同条目指向不同实体，拆成不同 ID，并用逐场关联指向各自实体。新 ID 只追加，废弃的 ID 放入 `retired_ids`，不回收。场景的 `number`、`heading` 可随剧本修改；`id` 保持稳定。

原文用词与制作名称分开：人物source_entry对应人物行完整条目，未填则兼容以name匹配；人物name是唯一制作名称，可加入世次、当前身份、形象及地点限定；场景 `heading` 对应完整标题；地点主名称对应至少一个关联场景标题中的地点字样，其他已确认同一地点的标题字样可放入 `aliases`；道具source_name须原样出现在至少一个关联场景正文，每场出现source_name或aliases；未填兼容以name匹配。name用于唯一图片引用，可使用有依据的版本限定，例如“惊堂木-京城茶馆”；不把制作名写进原台词，不把剧情说明当外形事实。

新项目heading统一为 `场景X-日/夜-内/外-地点`，例如 `场景1-日-内-昏暗房间`，实际名称不含选项斜杠，并通过文件名字符检查。读取兼容旧项目的冒号与间隔点标题，保持绑定剧本与台账场次标题一致；图片文件名使用scene_assets的name，不自动迁移旧项目。

`time_basis.kind` 只能是 `原文明示` 或 `格式默认`。前者的 `source_paragraphs` 必须列出该场原文明示时间的段落；后者须为空数组。标题中默认写 `日` 不代表原文明确是白天。脚本生成的待核值不能直接通过 `render`。

每条 `visual_facts` 或 `personality_facts` 采用以下结构。`text` 是简短事实，`evidence` 是原文中的连续原句或词组；证据跨段时，可引用各段共有的短词组，或拆成多条事实。`source_paragraphs` 使用 `prepare` 生成的原文段落编号。`scene_ids` 只列证据所在场景，不表示此特征在未明示的后续场景仍持续。

```json
{
  "text": "穿黑色裙子",
  "evidence": "穿着黑色裙子",
  "source_paragraphs": [27],
  "scene_ids": ["SC0002"],
  "attribution": "叙述"
}
```

人物性格只填原文直接命名的稳定特点。出自人物自述或他人评价时，`attribution` 填实际说话人并在 `text` 中写成“某人自述／某人认为……”，不把评价升级成客观结论。仅有一次温柔语气、一次发怒或某个行动时，`personality_facts` 保持空数组。性格也不写入剧本人物行的“形象”。

可记录的明示视觉信息包括：人物外貌、明显年龄、发型、衣着及配饰；地点布局、固定陈设、可见材质；道具形状、颜色、材质、相对大小、标记与状态。逐场临时的天气、光线放在 `scenes[].visual_facts`；固定布局与陈设放在locations，真实布景变化以有证据的decor版本记录。未知信息留空。`基础形象`、默认的 `日`、自动生成的 ID 和未出现的生图风格都不是原文事实。

道具只收录对剧情、人物动作或视觉识别有作用的主要物件。相同source_name且无明确视觉变化默认复用一个PROP图片资产，不因地点/持有人变化另建；不同原词仅在确认别名后归并，不用近义词盲合。物理上是否同一件由prop_occurrences独立保存，未知不冒认同一实体；服装通常作为人物视觉事实，不自动建为道具。

## 核对

`render` 检查：原文摘要、场景与段落范围、ID 引用、每场人物source_entry（缺省name）与人物行完整条目逐项、逐字、按顺序匹配、场景标题及地点名称与剧本一致、道具source_name和别名在关联正文中出现、全目录制作名称唯一及安全、形象启用区间、资产事实的场景出现关系、证据在原文段落中出现。`pending_reviews` 或 `inactive_assets.characters` 未处理完时不能渲染。它无法判断摘要是否正确表达证据，也无法证明“性格”是否真的被原文明确写出；这些仍需人工逐条核对。

生成的 TXT 只保留三个标题及其列表：`角色列表`、`场景列表`、`主要道具列表`。角色以完整人物资产 `name` 为标签，列核心可见特征，其下另起一行 `出现的场景：场景1，场景3。`。该行由scenes[].character_ids反查角色ID，场景号取number，排序去重，不同形象分别统计；仅发声的OS/画外音角色也计入。场景按scene_assets的name逐项列固定元素与真实布景版本，其下另起“使用的场次：场景1，场景3。”，按scene_asset_id反查并排序；不逐场重复列布景，不混入临时日夜、天气及光照；主要道具列核心可见特征。没有明示信息时写“未明示”，不从 `基础形象` 或格式默认时间补造。合理服装制作推定另起“制作设计：”行，不与原文特征混写。TXT不展示后台ID、出处段落、证据原句、别名表、其他逐场索引或性格栏；完整信息仍由 JSON 保存。

## 改稿迁移

先对新稿运行 `prepare`、逐场复核和 `assemble`，再迁移旧台账：

```text
python scripts/asset_migrate.py <old-ledger.json> <old-source.json> <new-script.txt> <new-workdir> --output <migrated-draft.json> --report <migration-report.json> [--mapping <mapping.json>]
```

脚本验证旧台账的原文摘要。原文内容完全相同且唯一对应的场景自动继承旧 `SC` ID；其他场景需在映射文件中给出旧 ID 对应的新场景号数组，删除则填 `null`。拆场或并场时用 `primary_ids` 明确哪个新场继续使用哪个旧 SC ID；拆场的其余新场填 `null`，由脚本分配新 ID。例如：

```json
{
  "scenes": {"SC0001": [1, 2], "SC0002": [3], "SC0003": [3], "SC0004": null},
  "primary_ids": {"1": "SC0001", "2": null, "3": "SC0002"},
  "characters": {"CHAR0007": "新人物行完整条目-形象描述"}
}
```

一对一场景不必填写 `primary_ids`。旧 SC ID 只能由一个新场继承；合并时其余旧 SC ID 进入 `retired_ids`，但其可定位的事实证据改关联至新 SC ID。新场景、新人物、新地点使用比所有旧 ID、暂存 ID 与废弃 ID 更大的编号。

旧证据只在对应新场的原文段落中唯一匹配且证据仍逐字出现时自动迁移。资产事实还必须关联到该资产实际出现的新场景；否则保留在迁移报告的待核项中。拆场的道具出现关系和拆并场的时间依据需要复核。场景标题的地点名发生变化时，先建立新 LOC ID 并报告身份待核；不能自动把新名称当作旧地点别名。

未改场景的原文段落序列完全一致时，证据编号按场景起点偏移直接迁移；只对改动场景重新搜索证据。迁移报告列出 `unchanged_source_scenes` 数量，方便判断复用范围。

旧人物source_entry（缺省name）没有在新剧本人物行出现时，完整记录先放入 `inactive_assets.characters`，同时加入 `pending_reviews`；这表示“可能改名、换形象或退场”，并不立即废弃人物 ID。若实际改名或原有形象条目更名，在映射中用 `characters` 指定旧 ID 对应的新人物行完整条目后重新迁移；若出现新形象，则分配新的 `CHAR` ID，不将旧形象 ID 改名挪用。若确认退场，人工把 ID 放入 `retired_ids`，移除暂存记录，并核对其旧事实是否需要保留为历史记录。处理所有待核项后才清空 `pending_reviews` 并渲染。旧版 `schema_version: 1` 可迁移，但每场 `time_basis` 都须复核。

## 物理地点与布景版本

scenes保留全部剧情场次及heading，不删场、不合并叙事；scene_assets存储真正要创建节点的布景资产。先结合原文的所属主体、建筑、楼层、具体空间核实LOC身份。别名和同名不等于同一物理空间；确认同一实体才合并LOC并重指场次，废弃LOC ID写入retired_ids。填写identity_status="confirmed"和非空identity_basis说明核实依据；无法确认保持pending，最终render及bind阻止通过。不同地点的泛称相同时，用有原文依据的所属主体或空间限定区分布景name，禁止编造地点。

build-scenes是确定性索引脚本：只按已确认LOC ID建立每个地点唯一的base SET，填写各场scene_asset_id；不按名称字符串自动合并、不提取或杜撰事实。已有SET ID与decor关联保持，新增ID高于现用及废弃SET；输入错误不静默覆盖。输出为新文件，再补充原文事实并完整render校验。身份待核、重复基础布景、无效关联均阻止后续阶段。

日夜、天气、光照、取景方向、场次号与排序仅属于SC或分镜，不能作为增加SET的理由。真实布景变化才允许少量版本：variant="decor"，parent_id指向同LOC基础SET，name为“地点-有依据的布景状态”，visual_facts须有原文证据并关联确实使用该版本的SC。同一视觉状态复用SET，不逐场建立新版本；基础布景只承载共用固定元素。程序验证结构和证据出处，语义上是否真实改变布景仍须人工核实。

旧schema 2可读取或按旧版改稿迁移，不能直接传输画布。升级须先离线给出旧SC→LOC/SET及节点的保留/修改/合并/删除方案，保留旧文件和映射快照，由用户抉择后再建立schema 3新绑定。不得自动把所有旧SC改为SET、静默合并图片或删除节点。schema 3改稿迁移保持SET ID和可定位证据；原文变动、版本关联不唯一或版本未使用进入pending_reviews，处理后才能生成最终台账。

## 命名预检与形象启用

资产处理阶段一次完成以下表格和预检，分镜阶段只消费已确认结果：

- name是全局唯一、安全的制作/图片名称，不使用SC编号消除所有重名；source_entry（人物）与source_name（道具）保留准确原词，不为制作改名重写剧本或台词。
- 群像名为“原完整人物条目-已核实地点名”，例如“村民-群像-基础形象-林家村祠堂门口”。同LOC、同原条目、同形象默认复用；明确不同队伍需不同形象时填写group_qualifier事件限定或visual_version。地点身份未核实不能合并；跨地点的旧群像ID先拆分。制作名初次由init加地点后缀，normalize在已确认LOC后归并。
- 同名道具默认共享视觉资产，不要求先证明物理上是同一件。明确升级、变形或不同外观才设置visual_version（缺省base），以“source_name-版本”命名；逐场prop_occurrences保留实体id、asset_id、count、持有人/状态等原有字段。count可为null表示未知，不编数量。例如两个“黄绫”可同图但仍是两个实例，不能推成同一件穿越场景。
- 角色身份、世次/当前身份、视觉版本三层分开，参考“陈默-第一世-基础形象”“陈默-第二世-林默-基础形象”“陈默-第二世-林默-富家日常形象”。同世次换装保持面部身份；不同世次身体不能只因旁白仍称陈默而复用旧身体。
- “第二世即将重开”、结算、加点仍用上一世形象；实际重生进入新家庭才启用新世次。发财、获知富家身份或刚到富家认亲不自动换衣；实际换装/洗漱、合理时间跳跃或稳定的新生活阶段才启用新造型。可提前登记候选，但不提前挂到场景character_ids。
- 涉及重生/换装的CHAR登记appearance对象：identity、incarnation、variant、active_from、可选active_until（均为SC）、trigger、basis。trigger只能为source_appearance/rebirth/change_clothes/time_jump；basis须为启用场原文连续短句。active_until为包含该场的末场。生成者核实“即将”与“已经”的语义，程序只检查时段、枚举和出处，不能理解剧情。
- 合理服装推定放production_design数组，沿用事实记录的text/evidence/source_paragraphs/scene_ids结构，但attribution固定“制作设计”。evidence说明合理场景依据，不声称原文写了推定服装。不随意补固定面容、颜色、首饰、能力。原文明示服装仍进visual_facts。
- normalize只用于新未绑定台账，写不同的新文件及报告，不改输入；合并保留首个稳定ID，将旧ID记retired_ids，报告保存redirects及完整被合并记录，重指SC和实体记录。不迁移旧session、图片或节点。明确视觉版本须在运行前登记，不能依靠程序判断两条事实是否冲突。
- render及bind全量检查名称唯一、ID、原词映射、启用区间、证据及关联，资产传输前仍检查全部目标。分镜只检查本范围；旧项目无关范围问题不阻止本地有效输出，全量云端操作不能绕过重名。
- 显示名独立调整不改source/逐场稿，不重跑剧情复核；只重跑台账/映射预检。事实、形象启用或正文改变时按影响范围复核，正文变化可用--previous-report复用未变场。旧交付与画布保留，不静默迁移。

示例形象元数据：

```json
{
  "id": "CHAR0002",
  "name": "陈默-第二世-林默-基础形象",
  "source_entry": "林默-基础形象",
  "source_speaker": "林默",
  "appearance": {
    "identity": "陈默",
    "incarnation": "第二世",
    "variant": "基础形象",
    "active_from": "SC0005",
    "trigger": "rebirth",
    "basis": "他重生成为林默"
  }
}
```

示例仅展示新增字段，aliases/visual_facts/personality_facts等既有必填字段仍需保留。若叙述仍称陈默，以该场真实人物行/声源名填写source_entry/source_speaker，不改变原台词归属。
