# 资产台账结构

主顺序、项目契约与新命令组合以[资产生产流程](../../../references/asset-workflow.md)为准。本页保留字段语义；下方旧命令仅作单独接口示例，新任务不以第二次normalize代替review-check/集中review-apply。

## 发声群体资产

仅在原文明示集合共同发声、且不能唯一展开为全部已知成员时，按独立集合人物保留CHAR与原集合称谓。新增`character_kind: speaking_ensemble`、`visual_mode: image_asset`及逐字对应标准台词标签的`source_speaker`；source_entry保留完整集合条目，不加背景群像的`-群像-`标记。该类型不参加背景群像筛选，不允许prompt_only，也不创建个人佩戴变体。原文能唯一确定全部成员时可沿用多声源标签，不为了工具格式强拆不确定集合或新增成员。

`ensemble_review`含`kind: speaking_ensemble`、`reviewed_full_script: true`、当前`source_digest`及具体`identity_basis`、`speech_basis`、`consistency_reason`。`visible_uses`至少一项，使用现有scene_id/event_id/evidence/source_paragraphs结构，给出集合实际出场或共同发声的连续来源证据；图片参考需要实际可见的集合，纯音频且无视觉身份的声效另按声音信息处理，不虚构人脸。脚本核对证据、关联、当前摘要及对应场次确有集合台词标签；原文是否明确集合、成员边界及视觉需求由作者审阅。

新制作采用gpt-image-2-v2的group_members与group_wardrobe多人参考板，同背景群像板保持16:9白底、每个样本只画一次正面全身、外貌可辨；不使用单人身份锚点及四视图。样本数不确定剧情人数或说话分工。原集合对白始终按原字词完整保存。更改集合身份审阅会使设计摘要过期，须重新核实并编译。此类型仅显式登记时启用，不改变旧台账、普通人物或原背景群像规则。

后台主文件使用 UTF-8 JSON；分类视觉描述 TXT 由它生成。标准剧本的场景序号用于显示，`SC`（场次）、`LOC`（物理地点）、`SET`（布景）、`CHAR`、`PROP` ID 用于长期关联。首次建立台账可用 `asset_ledger.py init` 生成无事实的索引骨架；若已有台账，先用 `asset_migrate.py` 迁移并审核，不重新初始化或重编号。`init` 生成的 `time_basis: 待核` 是草稿，须逐场确认后才能渲染。

```text
python scripts/asset_ledger.py init <standard-script.txt> <task-work-dir> --output <asset-ledger.json>
python scripts/asset_ledger.py normalize <identity-and-time-reviewed-ledger.json> --output <normalized-ledger.json> --report <naming-audit.json>
python scripts/asset_ledger.py build-scenes <normalized-ledger.json> --source <task-work-dir>/source.json --output <sets-draft.json>
python scripts/asset_ledger.py review-template <sets-draft.json> <review-selections.json> --output <review-decisions.json>
python scripts/asset_ledger.py review-apply <standard-script.txt> <task-work-dir>/source.json <sets-draft.json> <review-decisions.json> --output <reviewed-ledger.json> --report <decision-match-report.json>
python scripts/asset_ledger.py validate <standard-script.txt> <task-work-dir>/source.json <reviewed-ledger.json> --candidate
python scripts/asset_ledger.py design <standard-script.txt> <task-work-dir>/source.json <reviewed-ledger.json> <asset-design.json> --style-file <project>/video-style.txt --output <designed-ledger.json> --catalog-output <asset-catalog.txt>
python scripts/asset_ledger.py validate <standard-script.txt> <task-work-dir>/source.json <designed-ledger.json> --require-designed
python scripts/asset_ledger.py render <standard-script.txt> <task-work-dir>/source.json <designed-ledger.json> --output <asset-catalog.txt> --require-designed
```

先冻结全局身份、地点及归并后的稳定ID，再保存审阅决定。`review-template`生成的是待填模板，须实际审阅并填写后才运行`review-apply`；模板不自动宣称审阅通过。旧的`normalize --group-review`仍兼容背景群像流程，不处理发声集合；新项目已确认的集合、群像或候选分类名单应经下面的决定入口落盘并对账。

## 审阅决定应用与落盘对账

审阅名单与最终台账使用同一组稳定ID，不能仅比较“都是7项”。先由作者核实集合/背景群像边界、每项实际证据和候选归类；`review-selections.json`只列本次已经选定的类型及ID，例如：

```json
[
  {"kind": "speaking_ensemble", "asset_id": "CHAR0007"},
  {"kind": "background_group", "asset_id": "CHAR0012"},
  {"kind": "candidate_disposition", "candidate_id": "CAND0004"}
]
```

`review-template`从当前实际台账生成`input_ledger_digest`、`source_digest`、每条`expected_record_digest`和精确`scope`；前者与记录摘要均为排序键、UTF-8、不转义中文的紧凑JSON的SHA256。不要手填假摘要或用旧台账的对象索引修改新副本。模板保留现有证据供复核，但已有审阅声明会重置为未完成；缺少字段保留null，必须经审阅补齐，不能把模板直接视为通过报告。

决定文件顶层固定为`review_policy: asset-decisions-v1`、上述两个摘要、`scope`和`decisions`。`scope`必须分别列`character_ids`、`candidate_ids`、`archive_ids`，无项用空数组。决定ID与相应范围必须集合相等且无重复；原台账和逐项原记录均未变才允许应用。每项只接受以下固定操作，拒绝任意JSON patch或清空待核字段：

- `speaking_ensemble`：`asset_id`、`expected_record_digest`、`values`；values完整包含`character_kind: speaking_ensemble`、`visual_mode: image_asset`、`source_speaker`、`ensemble_review`、`visible_uses`。源条目必须已保留真实集合称谓，不能把背景群像名称直接换成发声集合。
- `background_group`：同上，但values完整包含`visual_mode`、`extraction_review`、`visible_uses`；目标须为已经核实的背景群像条目。不能通过此操作替代人物身份判断。
- `candidate_disposition`：`candidate_id`、`expected_record_digest`、`values`；values只含`disposition`、`target_id`、`requires_reference`、`reason`。候选证据和场次仍取现有记录，完整台账校验继续核对分类目标、出处和图片资格。
- `archive_inactive`：`asset_id`、`category`、`expected_record_digest`、具体`reason`。只能处理已处于inactive的记录，不能借此删除活动资产；明确排除后保留原记录到独立`asset_archive`并登记退役ID。已有退役ID但误留inactive的记录也可在无活动引用时显式归档。迁移产生的同ID、`kind: 人物可能改名或退场`待核记录，可显式提供`resolved_pending_digests`，逐条精确匹配摘要后存入归档的`resolved_pending_reviews`；其他待核不会自动清除。

`review-apply`先深拷贝、从新副本重建索引，应用固定操作后执行完整原文、台账、身份、生命周期、资产政策及设计检查；全部通过才写两个新文件。输出与报告都不能覆盖输入或旧产物。已设计台账若类型改变导致旧设计失效，会拒绝成为完整交付，不能通过改提示词或审阅摘要掩盖不一致。

`review-apply`是完成审阅后的候选提交入口，不是任意未完成台账的草稿保存器。存在无关pending或其他inactive时，同样不会写结果；可先在独立决定草稿中保存已确认的小批内容，完成其余待核后集中应用，或在一次决定中明确处理全部可归档项。不得为了让小批通过而删除未解决待核、遗漏资产或临时改变政策。

台账保存`asset_decision_receipts`，后续分批决定追加记录；同一稳定ID以最新显式决定为准，未选ID仍保留此前有效决定。`validate`、`render`、`design`和`bind`都会自动核对有效决定的真实字段，而不是相信独立报告。指定原决定文件可再次只读对账：

```text
python scripts/asset_ledger.py review-verify <standard-script.txt> <source.json> <reviewed-ledger.json> <review-decisions.json>
```

这个检查能发现已确认名单未写入、同数量错ID、漏一条字段、输入过期或原值冲突；不能从语言自动找齐尚未登记的集合或候选，也不能证明作者的语义判断正确。

## 待核、退役与新项目交付门槛

`pending_reviews`是尚未解决的审阅问题，`inactive_assets`是待决定去向的资产，二者任一非空都统一阻止validate、design、render、bind。inactive可按characters、props、locations、scene_assets、scenes存放记录；不能把已排除道具塞进inactive当作历史归档。`asset_archive`是已经明确退役的历史记录，必须含稳定ID、类别、具体原因及原记录，ID必须在`retired_ids`中且不在活动资产或活动引用中。历史已存在的retired_ids允许继续读取，不为旧合并ID编造原因；新增显式排除通过归档入口保留原因和记录。

新项目正式资产交付使用`validate --require-designed`和`render --require-designed`，并由导演bind的同名标志执行相同门槛：全部应生图资产有有效设计、prompt_only无图片派生字段、来源及设计摘要有效，才是完整图片提示词交付。design自己的最终验证始终使用该门槛。未带标志的validate/render保留旧事实台账读取兼容，不能据此宣称新项目完整设计已交付。

## JSON 字段

```json
{
  "schema_version": 3,
  "asset_policy": "visual-continuity-v2",
  "group_policy": "visual-continuity-v2",
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
      "name": "角色甲-基础形象",
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
  "props": [],
  "prop_coverage_review": {
    "source_digest": "由 init 计算的原文段落摘要",
    "reviewed_full_script": false,
    "reviewed_scene_ids": [],
    "candidates": []
  },
  "retired_ids": [],
  "inactive_assets": {"characters": []},
  "pending_reviews": []
}
```

`init` 只建立场次、人物和待核地点索引，scene_assets为空且scene_asset_id为null，不自动提取资产事实或主要道具。地点按场景先分配独立 ID，核实是同一地点后再合并；明确姓名的角色只按完整人物条目初步归并。`角色甲-基础形象` 与 `角色甲-黑色裙子` 分别建立稳定 ID；同一完整条目在多场出现且身份明确时复用 ID。泛称有台词角色先按身份核实；群像草稿直接加地点后缀，在地点核实后normalize按同地点同原条目/形象归并候选，再审阅图片筛选，不逐SC建图。群像只用于无姓名、无台词且原文明示阵营、身份、人设或职业的背景人物。新init的群像暂记visual_mode="prompt_only"但未完成审阅，不能直接交付；最终模式须依据全剧证据确认。若相同条目指向不同实体，拆成不同 ID，并用逐场关联指向各自实体。新 ID 只追加，废弃的 ID 放入 `retired_ids`，不回收。场景的 `number`、`heading` 可随剧本修改；`id` 保持稳定。

原文用词与制作名称分开：人物source_entry对应人物行完整条目，未填则兼容以name匹配；人物name是唯一制作名称，今生省略“今生”段，如“角色甲-基础形象”；前世、明确第几世、不同身份、服装/年龄版本及必要地点限定仍保留。appearance.incarnation、identity、source_entry和来源证据不删；不能按戏份多少猜同一人物或合并不同世次。normalize仅在新未绑定台账中移除姓名后独立“今生”段，保留ID，重名时报错并要求按真实身份消歧，不自动合并。已有绑定台账不迁移；场景 `heading` 对应完整标题；地点主名称对应至少一个关联场景标题中的地点字样，其他已确认同一地点的标题字样可放入 `aliases`；道具source_name须原样出现在至少一个关联场景正文，每场出现source_name或aliases；未填兼容以name匹配。name用于唯一图片引用，可使用有依据的版本限定，例如“惊堂木-京城茶馆”；不把制作名写进原台词，不把剧情说明当外形事实。

新项目heading统一为 `场景X-日/夜-内/外-地点`，例如 `场景1-日-内-昏暗房间`，实际名称不含选项斜杠，并通过文件名字符检查。读取兼容旧项目的冒号与间隔点标题，保持绑定剧本与台账场次标题一致；图片文件名使用scene_assets的name，不自动迁移旧项目。

`time_basis.kind` 只能是 `原文明示` 或 `格式默认`。前者的 `source_paragraphs` 必须列出该场原文明示时间的段落；后者须为空数组。标题中默认写 `日` 不代表原文明确是白天。脚本生成的待核值不能直接通过 `render`。

每条 `visual_facts` 或 `personality_facts` 采用以下结构。`text` 是针对当前资产本体的简短事实，`evidence` 是原文中的连续原句或词组；证据跨段时，可引用各段共有的短词组，或拆成多条事实。`source_paragraphs` 使用 `prepare` 生成的原文段落编号，不能重复或引用关联场景之外的段落。`scene_ids` 只列证据所在场景，程序分别验证每场所引连续段落包含该证据，不跨场景或跨段落缺口拼接证据；此字段不表示该特征在未明示的后续场景仍持续。

```json
{
  "text": "穿黑色裙子",
  "evidence": "穿着黑色裙子",
  "source_paragraphs": [27],
  "scene_ids": ["SC0002"],
  "attribution": "叙述"
}
```

个体CHAR和单件PROP的`visual_facts.text`不写同场人数、其他人物、共同动作、站位或剧情过程；多人共用原句时，只提取对当前人物或物件成立的外观/佩戴状态，原句仍逐字保留在`evidence`，完整共同事件放在正文、`visible_uses`或形象切换轨迹。个体佩戴形象仅描述该人物的佩戴状态及实际可见范围；关联PROP仅描述物件本体的形制和遮挡特征。关联PROP或基础CHAR将被继承的事实摘要也遵守此边界，不能让多人叙述经继承进入单角色图片提示词。群像、发声集合和显式多实体组合仍按各自规则处理。

人物性格只填原文直接命名的稳定特点。出自人物自述或他人评价时，`attribution` 填实际说话人并在 `text` 中写成“某人自述／某人认为……”，不把评价升级成客观结论。仅有一次温柔语气、一次发怒或某个行动时，`personality_facts` 保持空数组。性格也不写入剧本人物行的“形象”。

可记录的明示视觉信息包括：人物外貌、明显年龄、发型、衣着及配饰；地点布局、固定陈设、可见材质；道具形状、颜色、材质、相对大小、标记与状态。逐场临时的天气、光线放在 `scenes[].visual_facts`；固定布局与陈设放在locations，真实布景变化以有证据的decor版本记录。未知信息留空。`基础形象`、默认的 `日`、自动生成的 ID 和未出现的生图风格都不是原文事实。

## 主要道具筛选

新init默认asset_policy="visual-continuity-v2"，两条入选路径均须原文可见证据与具体外观连续性需求：

- `special_reusable`：至少2次独立可见使用，`consistency_reason`说明需固定的轮廓、色材、细节或结构。
- `core_continuity`：至少1次原文明示的可见使用；`story_role`具体说明物件承担的关键动作、视觉反差或结构交互，`consistency_reason`说明跨镜头或生成单元需要固定参考的部位和原因。单次连续剧情也可有充分的制作连续性需求，不能因只有“一次出行/争执/碰撞”就直接排除；也不能只写“很重要”“保持一致”或以车辆等类别自动入选。

词频、对白提及和同一事件拆镜不能冒充独立使用。审阅`special_reusable`时按实际动作与因果关系划分独立事件，不能把所有经过粗并为“一次出行”，也不能为凑两次把一次开门拆成多条；`core_continuity`单独判断参考需求，不靠扩大事件计数取得资格。普通固定家具通常归SET，普通日用品和临时小物件由分镜动作表达；有具体核心功能时再按上述路径审阅。服装通常归CHAR，可拆卸、交换或操作且承担核心功能的穿戴物件不能仅因可穿戴而一律排除。日用品经`special_reusable`入选时另填`special_features`说明原文依据与可辨特征，普通家具仍不走这条路径；`core_continuity`统一在`story_role`和`consistency_reason`中说明具体依据，不按家具、日用品或穿戴名称直接拒绝。

旧asset_policy="visible-reuse-v1"继续保持至少2次独立可见使用的原规则；旧台账缺少该字段仍按原规则读取，不自动升级、删PROP、改绑定或动下游节点。显式升级先按新规则复核并补全候选覆盖审阅，再重新校验和建立明确绑定。props可为空，但新策略仍须完成全剧候选覆盖审阅。相同source_name且无明确视觉变化默认复用一个PROP图片资产，不因地点/持有人变化另建。物理实体由prop_occurrences独立保存，不因共用图片冒认同一件。

多次复用路径示例：

```json
{
  "id": "PROP0001",
  "name": "青铜令牌",
  "source_name": "青铜令牌",
  "aliases": [],
  "visual_facts": [],
  "extraction_review": {"kind": "special_reusable", "consistency_reason": "先后取出和再次收起时轮廓与细节需匹配"},
  "visible_uses": [
    {"scene_id": "SC0001", "event_id": "取出并放桌", "evidence": "取出一枚青铜令牌，放到桌上", "source_paragraphs": [1]},
    {"scene_id": "SC0002", "event_id": "再次拿起收怀", "evidence": "再次拿起青铜令牌，收入怀中", "source_paragraphs": [2]}
  ]
}
```

核心连续性路径示例（该虚构剧情只有一次操作事件）：

```json
{
  "id": "PROP0002",
  "name": "机械匣",
  "source_name": "机械匣",
  "aliases": [],
  "visual_facts": [],
  "extraction_review": {
    "kind": "core_continuity",
    "story_role": "人物依次操作卡扣和齿轮才打开匣子，结构交互推动本段关键动作",
    "consistency_reason": "总览、手部操作和开匣结果需保持卡扣、齿轮与铰链的相对位置，依靠同一参考避免接触位置变化"
  },
  "visible_uses": [
    {"scene_id": "SC0001", "event_id": "操作并开匣", "evidence": "按下机械匣的卡扣，转动齿轮，匣盖弹开", "source_paragraphs": [3]}
  ]
}
```

event_id是同场实际可见事件标识，不是镜头号。每条evidence须唯一定位关联场内连续原文段落；同(scene_id,event_id)不重复，证据范围不重叠。source_paragraphs连续、按顺序且无重复；special_reusable至少两条，core_continuity至少一条。程序不做词频计数、不自动发现独立事件，也不能理解证据是否实际可见、story_role是否成立或参考是否必要；作者必须逐条核实，不能通过改kind、改名或泛泛理由绕过语义审阅。

## 候选反向覆盖审阅

新策略要求顶层`prop_coverage_review`；init只建立`reviewed_full_script:false`的空草稿，未审阅不得design/render或绑定。先逐场阅读原文中的可见动作、关键物件交互和视觉反差，收集需要作出分类决定的候选，再与最终台账反向核对。包括被排除候选，尤其是核心事件主体、明确外观/结构、可拆卸穿戴和跨镜头持续使用的物件；不要求把环境中的每个普通名词都变成独立资产。不能仅从最终props倒推一份全部“已覆盖”的表。

```json
{
  "prop_coverage_review": {
    "source_digest": "当前台账source_digest",
    "reviewed_full_script": true,
    "reviewed_scene_ids": ["SC0001", "SC0002"],
    "candidates": [
      {
        "candidate_id": "PC0001",
        "name": "青铜令牌",
        "scene_ids": ["SC0001", "SC0002"],
        "source_evidence": [
          {"scene_id": "SC0001", "evidence": "取出一枚青铜令牌，放到桌上", "source_paragraphs": [1]},
          {"scene_id": "SC0002", "evidence": "再次拿起青铜令牌，收入怀中", "source_paragraphs": [2]}
        ],
        "disposition": "asset",
        "target_id": "PROP0001",
        "reason": "已按多次复用路径入选，形制在两次使用中保持一致",
        "requires_reference": true
      },
      {
        "candidate_id": "PC0002",
        "name": "木桌",
        "scene_ids": ["SC0001"],
        "source_evidence": [
          {"scene_id": "SC0001", "evidence": "木桌靠着窗边", "source_paragraphs": [4]}
        ],
        "disposition": "set",
        "target_id": "SET0001",
        "reason": "普通固定陈设，位置与外观随房间布景统一，不需要单独道具图",
        "requires_reference": false
      },
      {
        "candidate_id": "PC0003",
        "name": "纸巾",
        "scene_ids": ["SC0001"],
        "source_evidence": [
          {"scene_id": "SC0001", "evidence": "抽出纸巾擦去水迹", "source_paragraphs": [5]}
        ],
        "disposition": "prompt_only",
        "target_id": null,
        "reason": "普通临时清洁用品，无需固定可辨形制，保留原动作即可",
        "requires_reference": false
      }
    ]
  }
}
```

示例字段需按实际剧本填写，不能复制示例证据。`source_digest`与当前原文摘要一致，`reviewed_scene_ids`须覆盖全部现用SC；`reviewed_full_script:true`表示生成者已完成逐场阅读与判断。每个候选ID唯一，可选`aliases`文字数组记录原文明示别称，`scene_ids`列出实际可见关联，`source_evidence`覆盖其中每场并能在该场原文段落定位。需要补齐候选时追加ID，不把候选展示序号当资产ID。

`disposition`只取`asset`、`character`、`set`、`prompt_only`，依次指向PROP、CHAR、SET或普通分镜描述。前三者的`target_id`必须存在且确实关联候选列出的每场；最后一种为null。`reason`说明实际分类依据，`requires_reference`记录是否需要图片参考；true不能归入prompt_only。穿戴件可用CHAR形象满足参考需求，固定陈设可由SET统一，但需核实对应设计确实包含该物件，不只挂一个目标ID。每个现用PROP都必须有asset候选覆盖，指向该PROP的候选场次合计与该PROP实际关联场次一致。

程序检查审阅声明、摘要、ID、场次覆盖、证据定位和目标关联，不能从空候选列表推知全剧没有物件，也不能证明所有核心物件都被发现。候选是否找齐、分类是否合理、证据是否证明可见和参考必要性，都由生成者通读后审阅。即使props为空，也不得跳过这项工作。全剧来源改变、资产归并或场次关联变化后重新核对覆盖审阅，不能只改摘要让旧声明通过。

分镜规划时仅复核当前范围：从原文与计划镜头的关键交互反查候选及目标资产。多镜头持续依赖某物件的轮廓、色材、结构或交互位置，而台账将其遗漏或仅写普通描述时，回到两条路径审阅；确认需要独立资产后，补齐其证据、设计、逐场关联及覆盖记录，再按明确重绑定流程提交。保留旧ID和交付，不重盘无关范围或静默更改画布；不能用“台账没有”作为无需参考的理由。 对已登记为需要图片参考的PROP候选，单元覆盖完整可见使用证据却未引用时输出复核提醒；若名称、制作名或显式别名能确定是该物件，却将其登记为空asset_ids的临时实体，则阻止提交。共用一句动作证据不等于同一物件，不能据此强行绑定。上述检查不能发现未登记候选，也不替代逐场语义复核。

## 群像图片筛选

人物行与characters/CHAR是完整剧情索引；是否制作图片由群像visual_mode决定。此筛选仅适用于无姓名、无独立台词且原文明示群体称谓的背景群像，具名角色、有独立台词的匿名角色以及SET/PROP不能用prompt_only规避图片规则。新台账group_policy="visual-continuity-v2"要求每个群像完成全剧审阅，图片入选有复用与核心连续性两条路径；文字足够时仍可prompt_only。不要因群像只参与一次剧情事件，就认定不需要跨镜头固定外观。

先normalize同LOC、同称谓、同形象的候选，再按完整剧本登记visible_uses。一次事件切成多镜、不同文句重复提名或对白提及不累计为独立出场；跨场次的同一连续事件仍只算一次，同场真实独立的再次出场可以累计。不要把所有行为笼统合并为“一次活动”后否决核心连续性需求，也不为凑复用次数拆分同一事件。群像图片只固定服饰、轮廓、色材等制作外观，不据此认定是同一批实体人物；剧情人数、队形、站位、动作进度和成员位置仍由原文及分镜状态管理。需要特定成员辨识时，先核实独立人物身份和原称谓，不用一张群像图替代个体参考，也不补造姓名或关系。

每个群像必填extraction_review：kind仍为"background_group"、reviewed_full_script=true、source_digest与台账当前摘要一致、identity_basis说明原文明示群体称谓且无姓名/独立台词。v2另在extraction_review内填写selection_basis和requires_reference，其组合为：

| visual_mode | selection_basis | requires_reference | 原文可见出场与具体理由 |
| --- | --- | --- | --- |
| image_asset | reusable | true | 至少2次独立可见出场；consistency_reason说明跨镜头或单元需保持的外观及参考必要性。 |
| image_asset | core_continuity | true | 至少1次可见出场；story_role说明具体关键行动、互动或视觉反差，consistency_reason说明为什么需要固定群体外观参考。 |
| prompt_only | prompt_only | false | 至少1次可见出场；exclusion_reason说明排除图片的依据，text_sufficiency_reason说明文字为何足以表达实际画面用途。 |

不得只用“同一次事件”作为prompt_only的全部依据，也不能只填“重要”“保持一致”就认定核心图片需求。多次出场但无需固定外观仍可prompt_only；一次事件承担核心作用也须证明实际参考需求，不能自动提取全部一次性群像。prompt_only保留source_entry、事实、CHAR和逐场character_ids，不含visual_design/image_prompt等生图派生字段；分镜保留人物与动作，渲染普通名称，不输出@引用，不生成图片、资产条目、画布节点或缺图报告项。

旧group_policy="visible-reuse-v1"保留图片至少2次独立可见出场的原门槛和原字段；旧无group_policy且无visual_mode时沿用原图片资产行为，已有visual_mode按旧规则校验。未明确选定的项目、绑定、图片和节点不迁移、不自动升降模式。原文可见性、独立事件、背景身份、剧情作用和文字是否足够仍由生成者审阅；程序只检查字段组合、审阅声明与摘要、连续原文证据、唯一定位及关联，不能自动找全遗漏或判断重要性。

可直接在normalize后的候选JSON填写上述字段，或用独立审阅JSON。以下为虚构示例，证据与画面用途须按实际原文和规划填写：

```json
{
  "group_policy": "visual-continuity-v2",
  "groups": [
    {
      "character_id": "CHAR0002",
      "visual_mode": "prompt_only",
      "extraction_review": {
        "kind": "background_group",
        "selection_basis": "prompt_only",
        "requires_reference": false,
        "reviewed_full_script": true,
        "source_digest": "当前台账source_digest",
        "identity_basis": "原文明示围观村民，无姓名和独立台词",
        "exclusion_reason": "普通围观背景，无特定成员辨识或固定外观要求",
        "text_sufficiency_reason": "该群体只作为远景环境人群，按原文交代围观与站位即可，无需重复辨认服饰细节"
      },
      "visible_uses": [
        {"scene_id": "SC0001", "event_id": "门前围观", "evidence": "村民站在门前围观", "source_paragraphs": [3]}
      ]
    }
  ]
}
```

核心连续性审阅项示例（独立JSON中的groups仍须覆盖全部已登记群像）：

```json
{
  "character_id": "CHAR0003",
  "visual_mode": "image_asset",
  "extraction_review": {
    "kind": "background_group",
    "selection_basis": "core_continuity",
    "requires_reference": true,
    "reviewed_full_script": true,
    "source_digest": "当前台账source_digest",
    "identity_basis": "原文明示围堵的守卫，无姓名和独立台词",
    "story_role": "守卫包围主人公并封住通道，群体行动形成当前段落的具体阻力",
    "consistency_reason": "全景与近距离互动中需保持守卫服饰轮廓和色材一致，群体外观参考不用于规定人数或队形"
  },
  "visible_uses": [
    {"scene_id": "SC0002", "event_id": "守卫围堵", "evidence": "守卫围住他，封住通道", "source_paragraphs": [8]}
  ]
}
```

```text
python scripts/asset_ledger.py normalize <sets-draft.json> --group-review <group-review.json> --source <source.json> --output <group-reviewed-ledger.json> --report <group-review-report.json>
```

groups须覆盖归并后全部群像CHAR，不填非群像；--source必需且须为同一原文摘要。独立审阅JSON顶层group_policy可选：省略时保留台账现有策略，旧台账无策略时采用legacy visible-reuse-v1；不会因为使用新版脚本自动升级。明确选择升级时才传group_policy="visual-continuity-v2"并完整填写v2审阅，null及未知策略拒绝。上面的显式v2示例不能不经审阅直接用于旧项目。

命令先执行命名归并，再应用审阅，保留原输入；若尚不知最终ID，先不带审阅参数normalize并读取redirects。合并前的群像筛选决定不能自动代表合并后的全剧群像，合并后须重审；可见证据会保留供审阅。design/render及bind再次校验，发现群像称谓的独立台词时阻止按背景群像处理。改稿迁移保留模式与旧审阅，只迁移可唯一定位的出场；v2来源、身份、关联场次或证据定位改变时，把审阅标为未完成并加入待核项，不自动刷新旧摘要或升降模式；最低出场次数按对应路径核查，不把单次core_continuity误判为未达两次。旧未选项目不改绑定、删图或删除节点。

进入分镜后只复核当前范围的群像画面作用。v2纯文字群像在至少2镜作为subjects，或任一镜作为focus时，程序提示“群像图片需求需复核”；对应场次的完整已审visible_uses证据落入本单元动作记录但未登记该群像为本单元角色时，提示复核覆盖与画内/画外参与。以上均为人工判断入口，不机械拒绝、升级图片或把两镜计成两次独立事件。image_asset群像被精确制作名、source_entry或显式别名确定，却登记为空asset_ids的临时实体时，程序阻止提交；共用一句动作证据不能证明同一实体。生成者仍须发现未登记群像和审阅真实参考需求，程序不能自动完成语义查漏。

分类TXT按实际图片资产顺序输出角色01、场景01、道具01，三类分别计数，超过99自然为100；prompt_only不占号。编号不进入name、ID、图片文件名、节点名或引用。

## 同一人物的佩戴形象与同场切换

遮面显著改变头部轮廓、身份辨识或跨镜外观，或者承担戴上/摘下情节时，审阅后可建立独立佩戴图片CHAR。不会因出现“口罩”一词就自动建图，不设至少两次出场门槛；始终属于固定制服且无需区分佩戴前后的形象，可直接纳入已有造型设计。命名保留原身份、世次及服装限定，例如`阿明-白衬衫-戴面具`；分类TXT显示为`角色02 - 阿明-白衬衫-戴面具`，展示序号不进入name。

仅显式执行以下命令启用`character_variant_policy: source-masked-v1`，旧台账及已交付回执不自动迁移。输入是作者审阅的完整JSON快照，输出新的待设计台账；保留已有设计供复核，须重新design及明确重绑定后用于新分镜，不改标准剧本、原人物行或既有画布。

```text
python scripts/asset_ledger.py variants <standard-script.txt> <source.json> <ledger.json> <appearance-review.json> --output <appearance-draft.json>
```

```json
{
  "policy": "source-masked-v1",
  "source_digest": "当前原文摘要",
  "reviewed": true,
  "variants": [
    {"id": "CHAR0002", "base_asset_id": "CHAR0001", "name": "阿明-白衬衫-戴面具", "label": "戴面具", "kind": "headgear", "design_basis": "完整遮头轮廓跨镜使用，需固定佩戴后的整体外观"}
  ],
  "scene_appearances": [
    {"scene_id": "SC0001", "base_asset_id": "CHAR0001", "initial_asset_id": "CHAR0001",
     "initial_evidence": {"source_paragraphs": [1], "evidence": "阿明未戴头套站在门口。", "basis": "原文明示未戴状态"},
     "transitions": [
       {"event_id": "wear", "from_asset_id": "CHAR0001", "to_asset_id": "CHAR0002", "source_paragraphs": [2], "evidence": "阿明戴上面具。", "basis": "本次戴上"},
       {"event_id": "remove", "from_asset_id": "CHAR0002", "to_asset_id": "CHAR0001", "source_paragraphs": [3], "evidence": "阿明摘下面具。", "basis": "本次摘下"}
     ]}
  ]
}
```

`variants`完整列出已有和新增佩戴资产。已有ID不能改绑另一个人物；重复应用同一base/label可复用原ID，显式新ID不能占用现用或废弃ID。佩戴类型为`headgear`或`face_covering`。程序生成的`appearance_variant`保留base_asset_id、label、kind、design_basis和当前source_digest，source_entry及source_speaker逐字继承基础人物。该基础资产确定原身份、体型与服装，不能借佩戴换脸、换世次或换衣；确有服装变化时先关联正确的服装基础资产。可以用`prop_asset_id`关联已存在的同款头套/面罩道具，供佩戴、拿取和摘戴共用设计；道具实体数量与持有人仍由prop_occurrences及连续性分别保存，三个同款头套不能因此变成同一个实物。

`scenes.character_ids`始终逐项对应原人物行，不能把基础和佩戴版本同时追加为两个人。独立的`character_appearances`数组登记本场合法形象及其初始状态、切换链；切换引用本场连续原文段落，event_id不重复，from/to依次衔接。同场无摘戴动作但已佩戴出场时，initial_asset_id直接用佩戴版本，initial_evidence说明本场状态，transitions可为空；不能用后文戴上事件证明前文已佩戴。程序检查引文、链条与摘要，原文是否真的证明该人物佩戴、最初状态以及参考必要性仍由作者核实。

分镜continuity中基础与佩戴版本属于同一个entity，其asset_ids列出本单元会使用的合法版本，以asset_id状态切换，不单独登记为两个人。初始形象按本单元之前已发生的事件继承，不能提前引用后文版本；changes的evidence使用对应事件完整动作引文，使其能在本场标准正文唯一定位。来源边界不能截断该切换动作证据；换镜不是重复佩戴的理由。未选范围之外的事件不需要在本单元重演。形象动作发生镜中可同时引用切换前后两图，后续镜头只使用当前形象。

同一entity还可以包含明确登记了相同appearance.identity与incarnation的其他服装基础形象及其佩戴版本；只有名称或source_speaker相同不证明同一实体。各服装基础的本场佩戴轨迹仍分别验证，不合并from/to链。回忆、想象或有来源的切场进入另一服装/既定佩戴状态时，按普通continuity登记进入场次证据及形象变化，不虚构一次戴上/摘下事件；同场实际摘戴仍须匹配本场事件。单位开始时的形象必须是本范围已经成立的状态，不能把后续造型当作初始状态。

现场说话的逐镜人物引用应与本镜当前或实际达到的佩戴造型一致，佩戴版本仍继承同一原说话人。检查摄影、动作和声音标签时，不让基础形象的图像引用隐式摘掉头套。一句话跨越摘戴时仍只登记一个完整audio事件，逐镜引用按实际状态复核；内心声、画外音与画内造型分别判断，不因声音来自同一人物就强迫画内嘴部动作或改变造型。

迁移保留佩戴ID与一对一可定位的本场轨迹，不让新形象参与原人物槽位匹配。原文版本、原人物映射或事件证据变化时保留旧审阅并登记待核；拆并场轨迹不得猜分配，待作者重审后重新运行variants。不能直接刷新摘要或删除待核项假装审核通过。分类TXT的出现场次按基础关联及本场形象轨迹共同反查。

## 移动载体与地点映射

现有schema 3每个SC仍绑定一个主要叙事LOC及其SET，不新增一SC多地点或第二张车内图协议。同一主要叙事地点的连续事件沿用所属SC，其heading、location_id和scene_asset_id保持经审阅的地点与布景关联，prop_ids按实际使用引用已审载体PROP；载体内部结构关系写入该PROP的structure/scale。取景位置、动作及人物出场完整保留。

连续行驶的路径与当前位置变化写入动作或连续性说明；场景标题优先保留原文明示的主要地点，遵循[地点称谓与移动过程](../INSTRUCTIONS.md#地点称谓与移动过程)，不为覆盖行驶范围自行拼接地名。LOC及SET的名称只沿用已审阅的地点称谓或有依据的必要消歧限定，不将移动过程登记为新地点或别名。若真正到达另一独立地点、发生实质转场则在那里拆分；不能为了少建场而把未证同一的路口、道路、金荣街写成同一LOC别名。持续在列车包厢等独立叙事空间发生的新段落可按原文建车厢SC/LOC/SET，车辆是否另需PROP按外观参考用途审阅，不是车辆一律只能当PROP。

单独内景参考需求先放`asset_dependency`观察：ID已稳定时，`target`沿用`{"category":"props", "asset_id":"PROP0001"}`；ID未确定时不填target，用`tentative_subject`保留原文车辆称谓。`basis`写车辆及需要稳定的座椅/出入口关系，附真实段落引用。该记录只保留制作需求；当前单品导出不会自动生成附加内景图，也不能借此伪造新SC。道具内饰本体设计仍写物理属性，图片展示选择遵循下节。

## 制作设计与图片提示词

原文visual_facts和personality_facts保持事实及出处，未知事实仍留空；完整美术方案另存visual_design，不写回事实字段。生成者结合剧本约束和[资产图片设计规范](../../../references/asset-image-design.md)写设计JSON，脚本只校验并编译，不设计外观、不调用生图模型、不改变ID/名称/形象启用。design覆盖全部图片CHAR/SET/PROP，排除prompt_only群像；其旧生图派生字段仅在显式design时清除。design只接受已核实schema 3台账，旧schema 2须按原显式迁移流程处理；旧schema 3可在用户授权后显式执行design，新政策字段不自动添加。normalize与群像筛选先于design执行。

输入design JSON必须覆盖全部现用CHAR、SET、PROP；即使某基础SET只供版本继承也须设计。设计数组不增加或删除资产。每项attribution固定“制作设计”，basis记简短设计依据，reviewed_source_facts=true表示作者已审阅所有原文明示约束，不代表程序能理解或自动修正语义冲突。

初稿先选择有代表性的小批资产，使用正式的只读`design-check`核对结构及依赖，避免完成全部设计才发现模板不兼容：

```text
python scripts/asset_ledger.py design-check <standard-script.txt> <source.json> <reviewed-ledger.json> <sample-designs.json> --style-file <video-style.txt> --asset-id CHAR0001 --asset-id SET0001
```

`--asset-id`可重复，ID不得重复；命令先对完整事实台账执行同一validate，不裁剪台账来绕过缺项，然后检查所选资产及依赖闭包。基础人物/服装、关联佩戴PROP、同LOC布景版本及共享身份依赖必须有本次设计或严格有效的既有设计，不能为了凑样本凭空填默认设计。报告含`input_sha256`及绑定本次ID/依赖的`binding_sha256`；复查可用`--expected-hashes <hashes.json>`传入上次的`input_sha256`，该对象必须含script/source/ledger/design/style五个文件SHA256，变化则重新准备样本。

该命令只向stdout返回partial预览及问题，不写正式台账、不建立绑定；样本通过也不代表全剧语义审阅通过。全部草稿合并后仍运行全覆盖design、render和最终绑定，不放宽完整交付门槛。

```json
{
  "identity_anchors": [
    {"id": "陈默-第一世", "age": "二十余岁成年男性", "body": "修长结实，比例自然", "face": "眉眼舒展，五官清晰，保留自然皮肤纹理", "hair": "乌黑长发束起"}
  ],
  "designs": [
    {"asset_id": "CHAR0001", "attribution": "制作设计", "basis": "沿用明示灰色布衣，补足适合古装短剧的外貌", "reviewed_source_facts": true, "identity_anchor_id": "陈默-第一世", "styling": "灰色粗布交领衣，深灰布腰带和布鞋，无额外首饰"},
    {"asset_id": "SET0001", "attribution": "制作设计", "basis": "陈府室内及放桌动作需要稳定空间", "reviewed_source_facts": true, "space": "入口正对后墙，侧窗在右，长桌与入口间留出通道", "materials_palette": "木构、米色墙面、暗棕家具", "furnishings": "侧窗下长桌，后墙旁两把椅子", "lighting": "窗侧柔和日光，暗部保留木纹"},
    {"asset_id": "PROP0001", "attribution": "制作设计", "basis": "两次使用的令牌需稳定外形，不新增未知铭文", "reviewed_source_facts": true, "structure": "圆角长方形，顶端小孔，简洁凸边", "materials_palette": "青铜主体，轻微自然氧化", "scale": "可单手握持，孔与主体比例合理", "state": "完整未损，正常使用磨痕"}
  ]
}
```

所有视觉文字字段为非空单行。identity_anchors记录共享的年龄与性别感、体型、五官、发型，不填无关背景故事。已登记appearance.identity与incarnation相同的CHAR共享identity_anchor_id，不同世次或身份的身体不得共享；没有appearance的简单或旧人物由作者审阅，不从名字拆分猜身份。已有换装版本只改styling，面部和体型继承同一锚点。已有真实发型等变化可选appearance_overrides对象（age/body/face/hair）及variant_differences文字数组，逐项写明确差异与保持项；这不会创建新CHAR，也不能代替原形象启用规则。覆盖face/body还必须在override_source_facts对象中以相应字段映射到该资产已有、经验证的visual_facts.text，例如face对应明确疤痕事实；程序只检查引用存在，是否证明该变化仍须审阅。跨世次不以覆盖方式借用旧身体锚点。

SET的space必须写主要活动区、出入口和固定相对关系；materials_palette/furnishings/lighting提供可拍材质、陈设与基础受光。普通家具进入SET，单幅无人物主视图；父SET共用事实继承规则保持原规定，不把temporal时间跨度推成自动破败。PROP的structure/materials_palette/scale/state只描写物件本体，不编造未知密码、铭文或剧情线索。单件完整、白底、不夹手部等要求属于编译器的展示层，不能填入这四个物理属性；人物佩戴形象只继承物理属性。

需整组参考的多实体PROP，在v2设计中显式填`product_layout: multiple_objects`与非空`layout_basis`（原组合证据、已知数量与参考展示范围），使用整组完整且各实体独立分开的组合版式。只需同款单个样本时沿用默认，在`basis`说明剧情实物数量不等于图片样本数。未知总数不凭样本推定；不从名称或数量词自动改版。此显式例外取代该资产的“单件”尾句，未选PROP、人物和旧v1保持原编译字节。

v2 PROP可选`image_presentation: {"view": "front_left_three_quarter"}`，view只接受front/front_left_three_quarter/front_right_three_quarter/top，省略保持原默认。它及product_layout/layout_basis只作用于PROP图片，不复制到CHAR。草稿阶段用`scripts/design_scope.py <设计草稿.json>`提前检查；字段范围、具体例子和历史兼容见[物体属性与图片展示分离](../../../references/asset-image-design.md#物体属性与图片展示分离)。源事实、ID关系和旧回执不由lint修改，检查通过仍不是语义批准。

v2图片群像不使用单个人物identity_anchor_id，改填group_members与group_wardrobe。group_members每人包含sample_id、age、body、face、hair，须有可辨外貌差异；varied/mixed模式另填个人styling。group_wardrobe包含mode（varied/uniform/mixed）、basis、era_region，uniform/mixed还须shared_style；统一服制下成员不另填服装或与shared_style完全一致，mixed保留共有服制及原文明示的等级/职责差异。不要通过样本数新增实际人物或擅改原文年龄、性别构成。完整示例和样本数边界以资产图片设计规范为准。

v2佩戴图的visual_design除covering外还须visible_identity，具体说明实际可见的身份部位及锚点；如口罩露出眼眉和头发，则保留这些可见特征，全头套可以写“脸与头发均遮挡，仅保留体型轮廓”。未关联独立道具时covering为形制、色材及覆盖范围的单行文字；appearance_variant.prop_asset_id已关联PROP时，covering必须为{fit,coverage}对象，只补充穿戴适配及覆盖位置，道具形制、色材、尺寸和状态从该PROP的已审设计统一继承，不能给每个人另写互相矛盾的头套。编译器不把被遮挡的完整五官重新要求画出。佩戴版本自动继承基础identity_anchor_id、styling和已核实appearance_overrides；不能声明与基础人物不同的值，原有覆盖事实由基础记录提供。

style-file使用项目原样保存的单行video-style.txt；只去除文件首尾空白，不折叠内部空格、不重新翻译或缩写。新未设计台账使用image_design_version="gpt-image-2-v2"；既有v1按原编译行为读取，只有设计JSON显式选择v2才升级，不能安装新版后使旧摘要失效。每个现用资产保存visual_design、image_prompt、image_format、image_design_digest，visual_design自动记录source_fact_digest及source_context_digest。新版按[资产图片设计规范](../../../references/asset-image-design.md)区分个体、图片群像和佩戴造型：普通个体保持16:9白底面部特写加三视图；群像用16:9白底多名外貌可辨人物、每人一次完整正面全身，衣着遵守时代地区与身份/服制；佩戴造型所有视图保持真实覆盖，不为展示五官摘除面罩。visual_design.covering具体说明头套/面罩形制、色材及覆盖范围；appearance_variant自动继承基础身份锚点及服装，拒绝借此更换人物外观或服装。SET和PROP仍为单幅主视图/完整单品版式，不伪造模型API参数或已生成图片。群像样本人数只服务外观参考，不确定剧情人数、队形或场面动作。

编译将原文明示可见特征作为优先约束，结合既定风格、已写设计和分类构图，不把审计出处或“无外观事实”等说明塞入生图提示词。render再次确定性编译并核对保存的提示词、版式、设计摘要、事实完整证据及场次映射摘要。未声明局部依赖的设计继续把台账顶层source_digest纳入source_context_digest，保留既有全剧保守策略。明确声明的局部依赖按下节逐场绑定，不能自动推定或迁移旧设计。变化事实、设计或依赖须审阅并显式design，不能只手改image_prompt；迁移保留旧设计，design_status逐项列出可复用与待审ID。旧无设计台账不受此检查影响。设计命令输出不同的新文件，不覆盖旧台账或旧TXT。

### 设计依赖与局部更新

在作者已核实设计上下文边界时，每条作者设计可选填：

```json
"source_scope": {
  "kind": "scenes",
  "scene_ids": ["SC0001", "SC0004"],
  "basis": "这两个出现场次包含该形象全部时代、身份、服装与动作适配依据；其他场次没有额外设计约束"
}
```

这是一条需要真实审阅的作者声明，不能根据资产出场列表自动填套话。scene_ids必须包含该资产全部出现场次，并加入未出场但提供时代、地区、身份或关系设计依据的场次；无法封闭依赖时不填，或用`{"kind":"global","basis":"具体全剧依赖说明"}`。旧设计不因安装新版而取得局部声明。程序能检查场次存在、无重复和出场覆盖，不能证明作者未漏掉跨场语义依赖。

design命令从当前source.json计算`design_source_scene_digests`；只读验证核对它与实际原文，不允许手改摘要。局部设计绑定所列场次的原文内容、事实、关联及共享设计依赖，无关场次的来源摘要变化不再使它过期；仅段落偏移不改变证据原意，仍须通过当前出处验证。资产显示名、实际场次映射、图像模式、筛选理由、身份锚点、基础形象或共享遮挡PROP变化仍会触发对应验证，不能用局部声明逃避。共享身份或道具的编译结果改变时，其依赖者也必须加入本次审阅。

完整初次设计保持原命令。已有设计的局部修订：

```text
python scripts/asset_ledger.py design <script.txt> <source.json> <current-ledger.json> <patch.json> --style-file <video-style.txt> --patch --output <new-ledger.json> --catalog-output <new-catalog.txt>
```

patch.json的`designs`只列变化资产；`identity_anchors`可省略，或只列变化锚点。未列设计必须仍通过其原始设计摘要、事实、上下文、身份与共享资产依赖、实际编译结果检查，原记录及摘要保留，不自动“刷新通过”。缺少新资产设计、过期未选项或遗漏共享依赖时报告具体ID，先补足审阅再重试；不重交无关项。换风格或设计版本仍用显式完整design，patch拒绝隐式全量切换。prop/group候选覆盖等前置审阅仍须通过；全剧可能出现新候选时复核候选范围，不等同于重新设计全部外观。

## 核对

`render` 检查：原文摘要、场景与段落范围、ID 引用、每场人物source_entry（缺省name）与人物行完整条目逐项、逐字、按顺序匹配、场景标题及地点名称与剧本一致、道具source_name和别名在关联正文中出现、全目录制作名称唯一及安全、形象启用区间、资产事实的场景出现关系、证据在原文段落中出现。新策略还检查道具入选路径、使用证据及prop_coverage_review的声明、全部SC覆盖、候选证据与目标关联、全部PROP覆盖。群像按group_policy检查模式、入选路径、参考需求、对应最低可见出场次数及审阅理由字段，v2纯文字模式还须说明文字足够的理由。`pending_reviews` 或 `inactive_assets` 任一分类未处理完时不能渲染。它无法判断摘要是否正确表达证据、候选是否语义完整或“性格”是否真的被原文明确写出；这些仍需人工逐条核对。

生成TXT只保留角色列表、场景列表、主要道具列表，各分类从01开始按台账数组顺序编号，超过99自然显示100，不截断。序号只用于查看统计，不写入稳定ID、name、图片名或节点名。完成design时主行为 `01 - 完整资产名：完整image_prompt`，下一行角色写出现的场景、SET/PROP写使用的场次，关联ID反查number后排序去重。旧未design台账仍显示原文核心可见特征，缺信息时保留“未明示”和旧独立production_design行，不静默补设计；新制作须先design，完整图片描述直接可用。TXT不显示后台ID、证据或性格栏；原文事实、出处、完整制作设计与格式元数据同存JSON，不能手工维护第二份提示词。

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

旧人物source_entry（缺省name）没有在新剧本人物行出现时，完整记录先放入 `inactive_assets.characters`，同时加入 `pending_reviews`；这表示“可能改名、换形象或退场”，并不立即废弃人物 ID。若实际改名或原有形象条目更名，在映射中用 `characters` 指定旧 ID 对应的新人物行完整条目后重新迁移；迁移更新source_entry及已登记的source_speaker，保留独立制作name与原ID。若出现新形象，则分配新的 `CHAR` ID，不将旧形象 ID 改名挪用。若确认退场，使用带稳定ID及证据的archive审阅决定，将完整旧记录移入archived_assets并登记retired_ids；核对无现用引用后逐项消解相应pending，不清空其他待核或直接删除旧记录。详见[审阅决定工作区](asset-decisions.md)。旧版 `schema_version: 1` 可迁移，但每场 `time_basis` 都须复核。

## 物理地点与布景版本

scenes保留按标准化规则确定的全部剧情场次及heading，不删减原文叙事；scene_assets存储真正要创建节点的布景资产。先按标准化规则确定主场归属，再结合原文的所属主体、建筑、楼层、具体空间核实LOC身份。同一主场内的车内外取景共用主场LOC/SET，不因车厢、车窗边或上下车另建LOC/SET。车辆内部不单独生成场景图片资产或场景节点；座椅、方向盘、车门窗及前后排关系归车辆道具的结构设计，当前取景、人物座次与动作归分镜。其他附属局部取景按同一原则核实，不能把每个拍摄位置都当成独立布景。别名和同名不等于同一物理空间；确认同一实体才合并LOC并重指场次，废弃LOC ID写入retired_ids。填写identity_status="confirmed"和非空identity_basis说明核实依据；无法确认保持pending，最终render及bind阻止通过。不同地点的泛称相同时，用有原文依据的所属主体或空间限定区分布景name，禁止编造地点。

build-scenes是确定性索引脚本：先确认并合并LOC、登记时间层及原样基准，对新台账normalize消除逐场init产生的同名群像/道具候选，再调用build-scenes；该命令执行完整制作名称预检，不能带着未归并的重复CHAR名称进入。它按已确认LOC ID建立每个地点唯一的base SET，再依据人工审阅登记的时间层及原样基准建立或复用SET，填写各场scene_asset_id；不按名称字符串自动合并、不扫描全文猜测跳时、不提取或杜撰视觉事实。无时间层元数据的schema 3仍按既有base/decor规则处理；已有SET ID及有效关联保留，新增ID高于现用及废弃SET。旧SET关联与新时间层不一致时报告映射/绑定迁移需求，不静默覆盖。输出为新文件，再补充原文事实、完成群像筛选与美术设计并完整render校验。身份待核、重复基础布景、无效关联均阻止后续阶段。

日夜、天气、光照、取景方向、场次号与排序仅属于SC或分镜，不能作为增加SET的理由。明示真实布景变化可建立少量版本：variant="decor"，parent_id指向同LOC基础SET，name为“地点-有依据的布景状态”，visual_facts须有原文证据并关联确实使用该版本的SC。明示大跨度跳时另用temporal版本，规则见下节。同一视觉状态及同一时间层复用对应SET，不逐场建立新版本；基础布景与LOC事实只承载所有版本共用的固定元素，decor/temporal分类描述继承这些共性，再追加本版本事实。temporal的parent_id可指向同LOC的base、decor或temporal；这是版本承接关系，不表示前一版婚堂、废墟或其他非共性状态延续。已移除物件或已改变状态必须从共用字段移出，分别登记于实际适用的版本，避免被继承至无此元素的版本。现有schema不含结构化删除/覆盖字段，程序不猜测自由中文描述中的删除语义；共用性及移除关系须人工核对。程序验证结构、同LOC及无循环parent链和证据出处，语义上是否真实改变布景仍须人工核实。

旧schema 2可读取或按旧版改稿迁移，不能直接传输画布。升级须先离线给出旧SC→LOC/SET及节点的保留/修改/合并/删除方案，保留旧文件和映射快照，由用户抉择后再建立schema 3新绑定。不得自动把所有旧SC改为SET、静默合并图片或删除节点。schema 3改稿迁移保持SET ID、时间层、原样基准及可定位证据；原文变动、时间层起始场/基准场对应不明确、版本关联不唯一或版本未使用进入pending_reviews，保存旧记录以供审核，不静默丢弃temporal_layers。处理后才能生成最终台账。

## 时间层与原样复用

“较大的时间跨度”按剧本叙述、前后场与实际时间变化审阅，不设置3年等统一硬门槛。叙述明确“3年后”“十年后”可据上下文登记；角色说“十年前我来过这里”而镜头仍在当前时间，不因此登记新层。`time_basis`仍只负责标题日夜，不能替代这里的时间层。

在顶层可选`temporal_layers`登记确认的跳时：

```json
{
  "temporal_layers": [
    {
      "id": "TL0001",
      "label": "十年后",
      "start_scene_id": "SC0002",
      "evidence": "十年后，他回到陈家老宅",
      "source_paragraphs": [12],
      "review_basis": "叙述在此切入十年后的当前剧情，前后场确认是大跨度跳时"
    }
  ]
}
```

`id`是台账内唯一时间层标记，不能只按相同label盲合；`label`取连续证据中的时间跨度原词，并通过图片文件名检查；`start_scene_id`为实际启用该层的首场，该场须登记相同`temporal_layer_id`。evidence/source_paragraphs必须在该起始场原文范围中连续且逐字出现，review_basis记录语义审阅依据。起始场可属于另一地点，后续场通过明确的层ID承接这一来源，不能假装后续每场再次明示跳时。

各SC登记`temporal_layer_id`，例如`"temporal_layer_id": "TL0001"`。首次跳时前省略表示原始层；首个登记跳时及之后的每场须显式选择审阅确认的实际层。回忆/闪回原始层写`null`，回忆某个已登记层写其ID，不按全局最新跳时强制套层。同LOC在同层的正常连续出场复用SET；明确decor变化仍保留相应视觉版本。

同LOC在较早时间层已有出场、进入新的确认时间层且没有明示原样时，build-scenes生成：

```json
{
  "id": "SET0002",
  "name": "陈家老宅-十年后",
  "location_id": "LOC0001",
  "variant": "temporal",
  "parent_id": "SET0001",
  "temporal_layer_id": "TL0001",
  "visual_facts": []
}
```

SET通过temporal_layer_id关联时间来源、parent_id关联之前同LOC使用的布景；适用场次由SC.scene_asset_id反查，SC的source_range保留原文范围。同一层后续出场默认复用该版本，新的大跨度层可再建立版本。某地点首次出现时即使剧情已是“十年后”，也只建立base，不额外创建未用的时间版本；新base可记录该首次出现的temporal_layer_id。temporal的visual_facts可以为空：时间跨度不证明结构、陈设、老化或翻新变化，不能写成自动破败或装修。

原文明示仍是原样时，在该层该LOC的首次出场登记明确基准：

```json
{
  "id": "SC0004",
  "temporal_layer_id": "TL0002",
  "set_reuse": {
    "same_as_scene_id": "SC0002",
    "evidence": "屋里仍和上次回来时一模一样",
    "source_paragraphs": [25]
  }
}
```

基准场须为较早、已核实同LOC的场，证据须来自本次出场的连续原文段落。same_as_scene_id明确是与原始布景相同，还是与上次temporal/decor版相同；仅凭“原样”无法确定对象时审阅上下文，仍有真实歧义才询问用户。build-scenes复用基准场实际SET并传播到该层同地点后续出场，不另造temporal。已有层不能在后面随意补set_reuse以追改先前关联。

含时间层或原样复用元数据时，build-scenes必须传`--source <source.json>`，先校验摘要、场次、连续证据和层引用；render再次验证。相同label若实际属于不同层，导致完整制作名重名时脚本拒绝最终台账。先核实是否同一层；确属不同层时，由用户选择有依据且唯一的区分名，手工登记对应SET，再校验；不静默复用、添加SC后缀或发明年份阈值。旧项目应离线制作映射和绑定迁移方案，不因添加时间层静默改写现有scene_asset_id、资产名、节点或图片。

## 命名预检与形象启用

资产处理阶段一次完成以下表格和预检，分镜阶段只消费已确认结果：

- name是全局唯一、安全的制作/图片名称，不使用SC编号消除所有重名；source_entry（人物）与source_name（道具）保留准确原词，不为制作改名重写剧本或台词。
- 群像名为“原完整人物条目-已核实地点名”，例如“村民-群像-基础形象-林家村祠堂门口”。同LOC、同原条目、同形象默认复用；明确不同队伍需不同形象时填写group_qualifier事件限定或visual_version。地点身份未核实不能合并；跨地点的旧群像ID先拆分。制作名初次由init加地点后缀，normalize在已确认LOC后归并。
- 同名道具默认共享视觉资产，不要求先证明物理上是同一件。明确升级、变形或不同外观才设置visual_version（缺省base），以“source_name-版本”命名；逐场prop_occurrences保留实体id、asset_id、count、持有人/状态等原有字段。count可为null表示未知，不编数量。例如两个“黄绫”可同图但仍是两个实例，不能推成同一件穿越场景。
- 角色身份、世次/当前身份、视觉版本三层分开，参考“陈默-第一世-基础形象”“陈默-第二世-林默-基础形象”“陈默-第二世-林默-富家日常形象”。同世次换装保持面部身份；不同世次身体不能只因旁白仍称陈默而复用旧身体。
- “第二世即将重开”、结算、加点仍用上一世形象；实际重生进入新家庭才启用新世次。发财、获知富家身份或刚到富家认亲不自动换衣；实际换装/洗漱、合理时间跳跃或稳定的新生活阶段才启用新造型。可提前登记候选，但不提前挂到场景character_ids。
- 涉及重生/换装的CHAR登记appearance对象：identity、incarnation、variant、active_from、可选active_until（均为SC）、trigger、basis。trigger只能为source_appearance/rebirth/change_clothes/time_jump；basis须为启用场原文连续短句。active_until为包含该场的末场。生成者核实“即将”与“已经”的语义，程序只检查时段、枚举和出处，不能理解剧情。
- 旧合理服装推定的production_design数组保持兼容，沿用原证据结构且attribution固定“制作设计”，不冒充原文明示。新完整视觉设计放visual_design，按显式设计流程补足必要面容、配色与妆造；不得新增能力、秘密、剧情线索。原文明示服装仍进visual_facts。
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
