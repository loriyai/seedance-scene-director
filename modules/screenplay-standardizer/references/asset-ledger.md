# 资产分析、台账与审阅协议

本页是资产事实、身份与命名、图片入选、地点/时间版本、审阅决定及改稿迁移的唯一规则来源。生产顺序和项目契约见[资产主流程](../../../references/asset-workflow.md)；图片设计字段、参考板版式、设计分组及编译接口见[资产美术设计](../../../references/asset-image-design.md)。示例仅说明字段，不为当前项目新增人物、道具、时代、身体结构或剧情。

后台主文件使用UTF-8 JSON，分类TXT由最终台账生成。SC为叙事场次、LOC为物理地点、SET为可复用布景，CHAR/PROP为人物/道具视觉资产；稳定ID用于长期关联，展示序号可变。首次用init建索引，可导入当前全稿复核支持的已确认观察事实；已有台账走[改稿迁移](#改稿迁移)，不重新初始化或重编号。所有审阅都基于实际原文、已确认对象形态和图片用途；程序的结构、出处及摘要检查不能代替语义判断。

按需要查阅：
- [字段与事实](#json-字段)、[命名和形象启用](#命名预检与形象启用)
- [发声集合](#发声群体资产)、[背景群像](#群像图片筛选)、[道具](#主要道具筛选)、[候选覆盖](#候选反向覆盖审阅)
- [地点与布景](#物理地点与布景版本)、[移动载体](#移动载体与地点映射)、[时间层](#时间层与原样复用)、[佩戴形象](#同一人物的佩戴形象与同场切换)
- [证据与决定工作区](#审阅决定工作区)、[交付门槛](#待核退役与新项目交付门槛)、[变更影响](#变更影响清单)

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

`init`默认建立场次、人物和待核地点索引，scene_assets为空且scene_asset_id为null，不自行推断事实或主要道具。带`--observations <观察包> --observations-review <全稿复核>`时，导入当前审阅支持的已确认事实，其余保留观察队列，不能把pending当confirmed。地点按场景先分配独立ID，核实同一地点后再合并；明确姓名的角色按完整人物条目初步归并。`角色甲-基础形象`与`角色甲-黑色裙子`分别建立ID，同一完整条目在多场身份明确时复用；泛称有台词角色先核实身份。群像草稿加地点后缀，确认LOC后normalize按同地点同条目/形象归并，再审图片筛选，不逐SC建图。群像仅用于无姓名、无台词且原文明示阵营、身份、人设或职业的背景人物；init暂记prompt_only并不表示已经审阅。相同条目指不同实体时分ID并按场关联。新ID只追加，废弃ID进retired_ids且不回收；场景number/heading可随改稿变化，id保持稳定。

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

## 命名预检与形象启用

资产阶段完成全局名称、身份和启用区间，后续阶段只消费已确认结果。

- `name`为全局唯一、安全的制作/图片名称，不用SC编号掩盖身份歧义。人物`source_entry`逐字对应人物行完整条目，缺省时兼容以name匹配。可选的`source_speaker`一旦登记，须与该条目解析出的原人物槽位逐字一致，不能填写制作名或自行删去群像标记；无台词背景群像直接省略，不从称谓自动生成声源。旧台账已有且精确匹配槽位的字段保持兼容，字段存在本身不证明有台词。发声集合另按[发声群体资产](#发声群体资产)核对实际台词。制作名称不反写正文或台词，同一源条目可按实际身份在不同场映射不同CHAR。
- 新未绑定人物名省略姓名后独立“今生”段，保留前世、明确第几世、当前身体身份、服装/年龄版本及必要地点限定；`appearance.incarnation`、`identity`、source_entry和证据均保留。normalize遇重名须按真实身份消歧，不自动合并，也不迁移已有绑定。
- LOC主名对应至少一个关联场标题中的地点原词；仅已确认同一物理地点的称谓可进入aliases。群像name为“原完整人物条目-已核实地点名”；同LOC、同原条目、同形象默认复用。确有不同队伍造型时用`group_qualifier`事件限定或`visual_version`；跨地点旧群像先拆分，不能未核实地点就归并。
- PROP的`source_name`须原样出现于至少一个关联场正文，每个关联场须出现source_name或显式aliases，缺省兼容name。同原词默认复用视觉资产，不要求是同一实物；明确升级、变形或不同外观才用`visual_version`（缺省base），制作名为“source_name-版本”。逐场`prop_occurrences`独立保存实体id、asset_id、count及持有人/状态；count=null表示未知，图片复用不合并数量或持有人。
- 身份、原文明示的世次/当前身体与视觉版本分别登记。仅原文具有世次区分才使用对应限定；同身体换装共享身份锚点，不同已知身体不因称谓相同而共用。候选可先登记，但形象只从实际状态成立处挂到`character_ids`；预告、愿望、认亲、财富或资源变化不证明已重生/换装。
- 涉及重生/换装的CHAR填写`appearance: {identity, incarnation, variant, active_from, active_until?, trigger, basis}`。起止均为SC，active_until包含该末场；trigger仅为`source_appearance/rebirth/change_clothes/time_jump`，basis为启用场连续原句。作者判断“即将”或“已经”，程序只查区间、枚举及出处。
- 原文明示服装归visual_facts；旧合理服装推定的`production_design`数组保持原证据结构与`attribution: 制作设计`。新完整方案归visual_design，不变成原文事实，也不新增能力、秘密或剧情线索。
- normalize只写新未绑定台账及新报告；核实后合并保留首个稳定ID，旧ID进入retired_ids，报告保留redirects与完整被合并记录并重指SC/实体引用。明确视觉版本须先登记，程序不判断事实语义冲突。有决定回执时另执行[归并保护](#归并与回执的前置保护)。
- 纯显示名调整不改source或逐场稿、不重跑剧情复核，重做台账/映射预检及必要重编译；事实、启用区间或正文变化按实际影响复核，未变场可用`--previous-report`。全量render/bind和资产传输仍查全部目标；分镜只查当前范围，旧项目无关范围问题不阻止有效本地输出。

形象字段示例（其他既有必填字段仍保留）：

```json
{"id":"CHAR0002","name":"陈默-第二世-林默-基础形象","source_entry":"林默-基础形象","source_speaker":"林默","appearance":{"identity":"陈默","incarnation":"第二世","variant":"基础形象","active_from":"SC0005","trigger":"rebirth","basis":"他重生成为林默"}}
```

若原文仍称陈默，source_entry/source_speaker仍按该场真实人物行及台词填写，不能为制作身份改变说话人。

## 发声群体资产

仅在原文明示集合共同发声、且不能唯一展开为全部已知成员时，按独立集合人物保留CHAR与原集合称谓。新增`character_kind: speaking_ensemble`、`visual_mode: image_asset`及逐字对应标准台词标签的`source_speaker`；source_entry保留完整集合条目，不加背景群像的`-群像-`标记。该类型不参加背景群像筛选，不允许prompt_only，也不创建个人佩戴变体。原文能唯一确定全部成员时可沿用多声源标签，不为了工具格式强拆不确定集合或新增成员。

`ensemble_review`含`kind: speaking_ensemble`、`reviewed_full_script: true`、当前`source_digest`及具体`identity_basis`、`speech_basis`、`consistency_reason`。`visible_uses`至少一项，使用现有scene_id/event_id/evidence/source_paragraphs结构，给出集合实际出场或共同发声的连续来源证据；图片参考需要实际可见的集合，纯音频且无视觉身份的声效另按声音信息处理，不虚构人脸。脚本核对证据、关联、当前摘要及对应场次确有集合台词标签；原文是否明确集合、成员边界及视觉需求由作者审阅。

图片版式及group_members/group_wardrobe见[资产图片设计](../../../references/asset-image-design.md)。样本不确定剧情人数或说话分工，原集合对白逐字完整保留。集合身份审阅变化会使设计过期，须重新核实编译。此类型仅显式登记时启用，不改变旧台账、普通人物或背景群像策略。

## 群像图片筛选

人物行与characters/CHAR是完整剧情索引；是否制作图片由群像visual_mode决定。此筛选仅适用于无姓名、无独立台词且原文明示群体称谓的背景群像，具名角色、有独立台词的匿名角色以及SET/PROP不能用prompt_only规避图片规则。新台账group_policy="visual-continuity-v2"要求每个群像完成全剧审阅，图片入选有复用与核心连续性两条路径；文字足够时仍可prompt_only。不要因群像只参与一次剧情事件，就认定不需要跨镜头固定外观。

先normalize同LOC、同称谓、同形象的候选，再按完整剧本登记visible_uses。一次事件切成多镜、不同文句重复提名或对白提及不累计为独立出场；跨场次的同一连续事件仍只算一次，同场真实独立的再次出场可以累计。不要把所有行为笼统合并为“一次活动”后否决核心连续性需求，也不为达到复用次数门槛拆分同一事件。群像图片只固定服饰、轮廓、色材等制作外观，不据此认定是同一批实体人物；剧情人数、队形、站位、动作进度和成员位置仍由原文及分镜状态管理。需要特定成员辨识时，先核实独立人物身份和原称谓，不用一张群像图替代个体参考，也不补造姓名或关系。

每个群像必填extraction_review：kind仍为"background_group"、reviewed_full_script=true、source_digest与台账当前摘要一致、identity_basis说明原文明示群体称谓且无姓名/独立台词。v2另在extraction_review内填写selection_basis和requires_reference，其组合为：

| visual_mode | selection_basis | requires_reference | 原文可见出场与具体理由 |
| --- | --- | --- | --- |
| image_asset | reusable | true | 至少2次独立可见出场；consistency_reason说明跨镜头或单元需保持的外观及参考必要性。 |
| image_asset | core_continuity | true | 至少1次可见出场；story_role说明具体关键行动、互动或视觉反差，consistency_reason说明为什么需要固定群体外观参考。 |
| prompt_only | prompt_only | false | 至少1次可见出场；exclusion_reason说明排除图片的依据，text_sufficiency_reason说明文字为何足以表达实际画面用途。 |

不得只用“同一次事件”作为prompt_only的全部依据，也不能只填“重要”“保持一致”就认定核心图片需求。多次出场但无需固定外观仍可prompt_only；一次事件承担核心作用也须证明实际参考需求，不能自动提取全部一次性群像。prompt_only保留source_entry、事实、CHAR和逐场character_ids，不含visual_design/image_prompt等生图派生字段；分镜保留人物与动作，渲染普通名称，不输出@引用，不生成图片、资产条目、画布节点或缺图报告项。

旧group_policy="visible-reuse-v1"保留图片至少2次独立可见出场的原门槛和原字段；旧无group_policy且无visual_mode时沿用原图片资产行为，已有visual_mode按旧规则校验。未明确选定的项目、绑定、图片和节点不迁移、不自动升降模式。原文可见性、独立事件、背景身份、剧情作用和文字是否足够仍由生成者审阅；程序只检查字段组合、审阅声明与摘要、连续原文证据、唯一定位及关联，不能自动找全遗漏或判断重要性。

可在normalize后的候选填写上述字段；新项目经[审阅决定](#审阅决定应用与落盘对账)集中应用。旧的独立群像审阅JSON保持兼容：顶层含可选group_policy和groups数组；每项为`{character_id, visual_mode, extraction_review, visible_uses}`，visible_uses沿用scene_id/event_id/evidence/source_paragraphs。

```text
python scripts/asset_ledger.py normalize <sets-draft.json> --group-review <group-review.json> --source <source.json> --output <group-reviewed-ledger.json> --report <group-review-report.json>
```

groups须覆盖归并后全部群像CHAR，不填非群像；--source必需且须为同一原文摘要。独立审阅JSON顶层group_policy可选：省略时保留台账现有策略，旧台账无策略时采用legacy visible-reuse-v1；不会因为使用新版脚本自动升级。明确选择升级时才传group_policy="visual-continuity-v2"并完整填写v2审阅，null及未知策略拒绝；不能不经审阅将v2字段直接用于旧项目。

命令先执行命名归并，再应用审阅，保留原输入；若尚不知最终ID，先不带审阅参数normalize并读取redirects。合并前的群像筛选决定不能自动代表合并后的全剧群像，合并后须重审；可见证据会保留供审阅。design/render及bind再次校验，发现群像称谓的独立台词时阻止按背景群像处理。改稿迁移保留模式与旧审阅，只迁移可唯一定位的出场；v2来源、身份、关联场次或证据定位改变时，把审阅标为未完成并加入待核项，不自动刷新旧摘要或升降模式；最低出场次数按对应路径核查，不把单次core_continuity误判为未达两次。旧未选项目不改绑定、删图或删除节点。

进入分镜后只复核当前范围的群像画面作用。v2纯文字群像在至少2镜作为subjects，或任一镜作为focus时，程序提示“群像图片需求需复核”；对应场次的完整已审visible_uses证据落入本单元动作记录但未登记该群像为本单元角色时，提示复核覆盖与画内/画外参与。以上均为人工判断入口，不机械拒绝、升级图片或把两镜计成两次独立事件。image_asset群像被精确制作名、source_entry或显式别名确定，却登记为空asset_ids的临时实体时，程序阻止提交；共用一句动作证据不能证明同一实体。生成者仍须发现未登记群像和审阅真实参考需求，程序不能自动完成语义查漏。

## 主要道具筛选

新init默认asset_policy="visual-continuity-v2"，两条入选路径均须原文可见证据与具体外观连续性需求：

- `special_reusable`：至少2次独立可见使用，`consistency_reason`说明需固定的轮廓、色材、细节或结构。
- `core_continuity`：至少1次原文明示的可见使用；`story_role`具体说明物件承担的关键动作、视觉反差或结构交互，`consistency_reason`说明跨镜头或生成单元需要固定参考的部位和原因。单次连续剧情也可有充分的制作连续性需求，不能因只有“一次出行/争执/碰撞”就直接排除；也不能只写“很重要”“保持一致”或以车辆等类别自动入选。

词频、对白提及和同一事件拆镜不能冒充独立使用。审阅`special_reusable`时按实际动作与因果关系划分独立事件，不能忽略动作与因果关系合并不同事件，也不能为达到次数门槛拆分同一事件；`core_continuity`单独判断参考需求，不靠扩大事件计数取得资格。普通固定家具通常归SET，普通日用品和临时小物件由分镜动作表达；有具体核心功能时再按上述路径审阅。服装通常归CHAR，可拆卸、交换或操作且承担核心功能的穿戴物件不能仅因可穿戴而一律排除。日用品经`special_reusable`入选时另填`special_features`说明原文依据与可辨特征，普通家具仍不走这条路径；`core_continuity`统一在`story_role`和`consistency_reason`中说明具体依据，不按家具、日用品或穿戴名称直接拒绝。

旧asset_policy="visible-reuse-v1"继续保持至少2次独立可见使用的原规则；旧台账缺少该字段仍按原规则读取，不自动升级、删PROP、改绑定或动下游节点。显式升级先按新规则复核并补全候选覆盖审阅，再重新校验和建立明确绑定。props可为空，但新策略仍须完成全剧候选覆盖审阅。相同source_name且无明确视觉变化默认复用一个PROP图片资产，不因地点/持有人变化另建。物理实体由prop_occurrences独立保存，不因共用图片推定为同一实体。

入选PROP保留`id/name/source_name/aliases/visual_facts`，填写`extraction_review`与`visible_uses`。前者的kind取上述路径，按路径提供consistency_reason及必要的story_role/special_features；每条visible_uses为：

```json
{"scene_id":"SC0001","event_id":"操作并开匣","evidence":"按下机械匣的卡扣，转动齿轮，匣盖弹开","source_paragraphs":[3]}
```

event_id是同场实际可见事件标识，不是镜头号。每条evidence须唯一定位关联场内连续原文段落；同(scene_id,event_id)不重复，证据范围不重叠。source_paragraphs连续、按顺序且无重复；special_reusable至少两条，core_continuity至少一条。程序不做词频计数、不自动发现独立事件，也不能理解证据是否实际可见、story_role是否成立或参考是否必要；作者必须逐条核实，不能通过改kind、改名或未说明具体依据的理由绕过语义审阅。

## 候选反向覆盖审阅

新策略要求顶层`prop_coverage_review`；init只建立`reviewed_full_script:false`的空草稿，未审阅不得design/render或绑定。先逐场阅读原文中的可见动作、关键物件交互和视觉反差，收集需要作出分类决定的候选，再与最终台账反向核对。包括被排除候选，尤其是核心事件主体、明确外观/结构、可拆卸穿戴和跨镜头持续使用的物件；不要求把环境中的每个普通名词都变成独立资产。不能仅从最终props倒推一份全部“已覆盖”的表。

```json
{"prop_coverage_review":{"source_digest":"当前台账source_digest","reviewed_full_script":false,"reviewed_scene_ids":[],"candidates":[{"candidate_id":"PC0001","name":"木桌","aliases":[],"scene_ids":["SC0001"],"source_evidence":[{"scene_id":"SC0001","evidence":"木桌靠着窗边","source_paragraphs":[4]}],"disposition":"set","target_id":"SET0001","reason":"固定陈设的位置与外观由房间设计统一，不另建道具图","requires_reference":false}]}}
```

示例字段需按实际剧本填写，不能复制示例证据。`source_digest`与当前原文摘要一致，`reviewed_scene_ids`须覆盖全部现用SC；`reviewed_full_script:true`表示生成者已完成逐场阅读与判断。每个候选ID唯一，可选`aliases`文字数组记录原文明示别称，`scene_ids`列出实际可见关联，`source_evidence`覆盖其中每场并能在该场原文段落定位。需要补齐候选时追加ID，不把候选展示序号当资产ID。

`disposition`只取`asset`、`character`、`set`、`prompt_only`，依次指向PROP、CHAR、SET或普通分镜描述。前三者的`target_id`必须存在且确实关联候选列出的每场；最后一种为null。`reason`说明实际分类依据，`requires_reference`记录是否需要图片参考；true不能归入prompt_only。穿戴件可用CHAR形象满足参考需求，固定陈设可由SET统一，但需核实对应设计确实包含该物件，不只挂一个目标ID。每个现用PROP都必须有asset候选覆盖，指向该PROP的候选场次合计与该PROP实际关联场次一致。

程序检查审阅声明、摘要、ID、场次覆盖、证据定位和目标关联，不能从空候选列表推知全剧没有物件，也不能证明所有核心物件都被发现。候选是否找齐、分类是否合理、证据是否证明可见和参考必要性，都由生成者通读后审阅。即使props为空，也不得跳过这项工作。全剧来源改变、资产归并或场次关联变化后重新核对覆盖审阅，不能只改摘要让旧声明通过。

分镜规划时仅复核当前范围：从原文与计划镜头的关键交互反查候选及目标资产。多镜头持续依赖某物件的轮廓、色材、结构或交互位置，而台账将其遗漏或仅写普通描述时，回到两条路径审阅；确认需要独立资产后，补齐其证据、设计、逐场关联及覆盖记录，再按明确重绑定流程提交。保留旧ID和交付，不重盘无关范围或静默更改画布；不能用“台账没有”作为无需参考的理由。 对已登记为需要图片参考的PROP候选，单元覆盖完整可见使用证据却未引用时输出复核提醒；若名称、制作名或显式别名能确定是该物件，却将其登记为空asset_ids的临时实体，则阻止提交。共用一句动作证据不等于同一物件，不能据此强行绑定。上述检查不能发现未登记候选，也不替代逐场语义复核。

## 物理地点与布景版本

scenes保留按标准化规则确定的全部剧情场次及heading，不删减原文叙事；scene_assets存储要创建节点的布景资产。先按标准化规则确定主场归属，再结合原文实际存在的所属主体、地理位置、建筑或空间关系核实LOC身份。同一主场内的附属取景共用主场LOC/SET，不因观察方向、载体内外或登离载体的动作自动另建LOC/SET。此类附属内部视角不单独生成场景图片或节点；载体本体与内部结构归相应已审道具，取景、主体位置与动作归分镜。原文确立独立内部叙事空间时，按[移动载体与地点映射](#移动载体与地点映射)保留该分支；不将附属取景限制扩大为一律禁止内部SET。别名和同名不等于同一物理空间；确认同一实体才合并LOC并重指场次，废弃LOC ID写入retired_ids。填写identity_status="confirmed"和非空identity_basis说明核实依据；无法确认保持pending，最终render及bind阻止通过。不同地点的泛称相同时，用有原文依据的所属主体或空间限定区分布景name，禁止编造地点。

build-scenes是确定性索引脚本：先确认并合并LOC、登记时间层及原样基准，对新台账normalize消除逐场init产生的同名群像/道具候选，再调用build-scenes；该命令执行完整制作名称预检，不能带着未归并的重复CHAR名称进入。它按已确认LOC ID建立每个地点唯一的base SET，再依据人工审阅登记的时间层及原样基准建立或复用SET，填写各场scene_asset_id；不按名称字符串自动合并、不扫描全文猜测跳时、不提取或杜撰视觉事实。无时间层元数据的schema 3仍按既有base/decor规则处理；已有SET ID及有效关联保留，新增ID高于现用及废弃SET。旧SET关联与新时间层不一致时报告映射/绑定迁移需求，不静默覆盖。输出为新文件，再补充原文事实、完成群像筛选与美术设计并完整render校验。身份待核、重复基础布景、无效关联均阻止后续阶段。

日夜、天气、光照、取景方向、场次号与排序仅属于SC或分镜，不能作为增加SET的理由。明示真实布景变化可建立少量版本：variant="decor"，parent_id指向同LOC基础SET，name为“地点-有依据的布景状态”，visual_facts须有原文证据并关联确实使用该版本的SC。明示大跨度跳时另用temporal版本，规则见下节。同一视觉状态及同一时间层复用对应SET，不逐场建立新版本；基础布景与LOC事实只承载所有版本共用的固定元素，decor/temporal分类描述继承这些共性，再追加本版本事实。temporal的parent_id可指向同LOC的base、decor或temporal；这是版本承接关系，不表示前一版婚堂、废墟或其他非共性状态延续。已移除物件或已改变状态必须从共用字段移出，分别登记于实际适用的版本，避免被继承至无此元素的版本。现有schema不含结构化删除/覆盖字段，程序不猜测自由中文描述中的删除语义；共用性及移除关系须人工核对。程序验证结构、同LOC及无循环parent链和证据出处，语义上是否真实改变布景仍须人工核实。

旧schema 2可读取或按旧版改稿迁移，不能直接传输画布。升级须先离线给出旧SC→LOC/SET及节点的保留/修改/合并/删除方案，保留旧文件和映射快照，由用户抉择后再建立schema 3新绑定。不得自动把所有旧SC改为SET、静默合并图片或删除节点。schema 3改稿迁移保持SET ID、时间层、原样基准及可定位证据；原文变动、时间层起始场/基准场对应不明确、版本关联不唯一或版本未使用进入pending_reviews，保存旧记录以供审核，不静默丢弃temporal_layers。处理后才能生成最终台账。

## 移动载体与地点映射

现有schema 3每个SC仍绑定一个主要叙事LOC及其SET，不新增一SC多地点或第二张车内图协议。同一主要叙事地点的连续事件沿用所属SC，其heading、location_id和scene_asset_id保持经审阅的地点与布景关联，prop_ids按实际使用引用已审载体PROP；载体内部结构关系写入该PROP的structure/scale。取景位置、动作及人物出场完整保留。

连续行进的路径与当前位置变化写入动作或连续性说明；场景标题优先保留原文明示的主要地点，遵循[地点称谓与移动过程](../INSTRUCTIONS.md#地点称谓与移动过程)，不为覆盖移动范围自行拼接地名。LOC及SET的名称只沿用已审阅的地点称谓或有依据的必要消歧限定，不将移动过程登记为新地点或别名。实际到达另一独立地点或发生实质转场时按证据拆分；未经核实为同一实体的道路、区域或建筑不得仅为减少场次合并成同一LOC。原文把载体内部确立为独立叙事空间时，可按证据建立对应SC/LOC/SET；例如持续在列车包厢发生的独立段落。载体是否另需PROP由外观参考用途决定，不以载体类别一律限制资产归属。

单独内景参考需求先放`asset_dependency`观察：ID已稳定时，`target`沿用`{"category":"props", "asset_id":"PROP0001"}`；ID未确定时不填target，用`tentative_subject`保留原文车辆称谓。`basis`写车辆及需要稳定的座椅/出入口关系，附真实段落引用。该记录只保留制作需求；当前单品导出不会自动生成附加内景图，也不能借此伪造新SC。道具内饰本体设计仍写物理属性，展示选择见[图片设计](../../../references/asset-image-design.md#物体属性与图片展示分离)。

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

`id`是台账内唯一时间层标记，不能仅按相同label合并；`label`取连续证据中的时间跨度原词，并通过图片文件名检查；`start_scene_id`为实际启用该层的首场，该场须登记相同`temporal_layer_id`。evidence/source_paragraphs必须在该起始场原文范围中连续且逐字出现，review_basis记录语义审阅依据。起始场可属于另一地点，后续场通过明确的层ID承接这一来源，不能把同一跳时证据表述为后续每场分别发生的新跳时。

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

形象启用与图片派生分别管理：`appearance_variant`决定已审佩戴资产和剧情使用区间，不扩充为任意换装事件接口。同一身体的换装或其他已成立状态差异，在既有CHAR设计的`visual_design.image_reference`中登记基础图片来源、允许变化和保持项；不能借图片引用创建角色、改变启用区间或跨身份/世次借用另一身体。佩戴版的基础及道具依赖沿已有appearance_variant建立，普通派生由作者显式选择，不从名称猜测。引用必须指向现用图片资产且无自引用或循环，基础和道具变更只使实际依赖者待复核。作者字段、差异编译与历史兼容统一见[图片设计](../../../references/asset-image-design.md#身份锚点与造型差异)，不在台账另存一份完整提示词规则。

`scenes.character_ids`始终逐项对应原人物行，不能把基础和佩戴版本同时追加为两个人。独立的`character_appearances`数组登记本场合法形象及其初始状态、切换链；切换引用本场连续原文段落，event_id不重复，from/to依次衔接。同场无摘戴动作但已佩戴出场时，initial_asset_id直接用佩戴版本，initial_evidence说明本场状态，transitions可为空；不能用后文戴上事件证明前文已佩戴。程序检查引文、链条与摘要，原文是否真的证明该人物佩戴、最初状态以及参考必要性仍由作者核实。

分镜continuity中基础与佩戴版本属于同一个entity，其asset_ids列出本单元会使用的合法版本，以asset_id状态切换，不单独登记为两个人。初始形象按本单元之前已发生的事件继承，不能提前引用后文版本；changes的evidence使用对应事件完整动作引文，使其能在本场标准正文唯一定位。来源边界不能截断该切换动作证据；换镜不是重复佩戴的理由。未选范围之外的事件不需要在本单元重演。形象动作发生镜中可同时引用切换前后两图，后续镜头只使用当前形象。

同一entity还可以包含明确登记了相同appearance.identity与incarnation的其他服装基础形象及其佩戴版本；只有名称或source_speaker相同不证明同一实体。各服装基础的本场佩戴轨迹仍分别验证，不合并from/to链。回忆、想象或有来源的切场进入另一服装/既定佩戴状态时，按普通continuity登记进入场次证据及形象变化，不虚构一次戴上/摘下事件；同场实际摘戴仍须匹配本场事件。单位开始时的形象必须是本范围已经成立的状态，不能把后续造型当作初始状态。

现场说话的逐镜人物引用应与本镜当前或实际达到的佩戴造型一致，佩戴版本仍继承同一原说话人。检查摄影、动作和声音标签时，不让基础形象的图像引用隐式摘掉头套。一句话跨越摘戴时仍只登记一个完整audio事件，逐镜引用按实际状态复核；内心声、画外音与画内造型分别判断，不因声音来自同一人物就强迫画内嘴部动作或改变造型。

迁移保留佩戴ID与一对一可定位的本场轨迹，不让新形象参与原人物槽位匹配。原文版本、原人物映射或事件证据变化时保留旧审阅并登记待核；拆并场轨迹不得猜分配，待作者重审后重新运行variants。不能直接刷新摘要或删除待核项代替实际审核。分类TXT的出现场次按基础关联及本场形象轨迹共同反查。

## 审阅决定工作区

在完整标准稿和来源上确认身份、服装、LOC/时间层，归并命名并建立SET后，冻结候选台账；分类判断可提前，正式决定必须绑定归并后的稳定ID。基础候选审阅完成后才登记必要的variants，再核对最终图片范围及依赖。主顺序只在[资产主流程](../../../references/asset-workflow.md)维护。

### 冻结候选前准备证据

跨段、段内换行或制表符证据须先适配现有单行字段，不能等候选冻结后再替换证据。选择真实连续范围：

```json
[{"evidence_id":"EV001","source_paragraphs":[10,11]}, {"evidence_id":"EV002","source_paragraphs":[18],"quote":"实际读到的逐字摘录"}]
```

```text
python scripts/evidence_draft.py prepare <source.json> <evidence-selections.json> --output <evidence-draft.json>
python scripts/evidence_draft.py check <source.json> <evidence-draft.json>
```

省略quote取段落全文；指定quote须逐字且唯一存在，跨段摘录以换行连接并实际覆盖首尾段。适配器只将连续空白压成一个空格，不改字词、标点或说话人；拒绝不连续、重复、越界段号、改写或移除空白后仍定位不唯一的证据。输出保留`raw_evidence`、原始段落、`source_ref`、全剧`source_digest`及绑定摘要；任一来源变化均须重建，check重新对照实际原文。

`prepare_evidence_draft(paragraphs, selections, source_ref=...)`与CLI相同；`evidence_fields(paragraphs, draft, evidence_id)`先验来源，再返回台账所需evidence/source_paragraphs。适配器不推断场次/分类、不写台账或旧审阅、不填reviewed_full_script，固定返回`semantic_review: not_performed`、`formal_ready: false`。作者确定事件、场次和分类后才冻结候选；已冻结材料确需改变，重建受影响决定，不能刷新旧摘要。

证据草稿集中保存一次，辅助笔记引用文件、binding_sha256和evidence_id并写具体判断；正式事实/决定协议所需实际证据、场次及摘要仍须完整，不用“见报告”替代。

## 审阅决定应用与落盘对账

本协议固定为`asset-decisions-v1`，只应用作者已核实的分类与退役决定，不推断身份或参考必要性。逐项按稳定ID对账，数量相等不代表对象相同。先准备本批选择：

```json
[{"kind":"speaking_ensemble","asset_id":"CHAR0007"},{"kind":"background_group","asset_id":"CHAR0012"},{"kind":"candidate_disposition","candidate_id":"CAND0004"}]
```

```text
python scripts/asset_ledger.py review-template <frozen-ledger.json> <review-selections.json> --output <decisions-01.json>
python scripts/asset_ledger.py review-check <standard.txt> <source.json> <frozen-ledger.json> <decisions-01.json> --batch <decisions-02.json>
python scripts/asset_ledger.py review-apply <standard.txt> <source.json> <frozen-ledger.json> <decisions-01.json> --batch <decisions-02.json> --output <reviewed-ledger.json> --report <decision-match-report.json>
python scripts/asset_ledger.py review-verify <standard.txt> <source.json> <reviewed-ledger.json> <decisions-01.json>
```

`--batch`可重复，单批省略。template从当前台账生成`input_ledger_digest/source_digest`及各项`expected_record_digest`；台账/记录摘要为排序键、UTF-8、不转义中文的紧凑JSON SHA256。模板保留证据，重置原审阅声明为未完成，缺字段为null；须实审填写，不能直接充当通过报告。

决定文件顶层为`review_policy/input_ledger_digest/source_digest/scope/decisions`。scope固定列`character_ids/candidate_ids/archive_ids`，无项为空数组，决定ID与范围集合相等且无重复。每项带kind及以下固定字段，拒绝任意JSON patch或清空待核：

| kind | 标识及完整决定值 |
| --- | --- |
| speaking_ensemble | asset_id、expected_record_digest、values；values含character_kind=speaking_ensemble、visual_mode=image_asset、source_speaker、ensemble_review、visible_uses。源条目须已保留真实集合称谓，不能直接改背景群像名冒充集合。 |
| background_group | asset_id、expected_record_digest、values；values含visual_mode、extraction_review、visible_uses，目标须为已核实背景群像。 |
| candidate_disposition | candidate_id、expected_record_digest、values；values仅为disposition、target_id、requires_reference、reason，证据/场次仍取现记录。 |
| archive_inactive | asset_id、category、expected_record_digest、具体reason；仅归档既有inactive，不删除活动资产。原记录进入asset_archive并登记retired_ids，无活动引用的误留退役项也可显式归档。 |

archive仅可用`resolved_pending_digests`逐条精确匹配迁移产生的同ID、`kind: 人物可能改名或退场`待核，将其保存在归档`resolved_pending_reviews`；其他pending不自动清除。

各批必须基于同一完整冻结台账、同一来源，责任ID互斥；人物修改与归档也不能跨批作用同一ID。review-prepare先检查所选背景群像不能通过决定修改的声源字段，避免填完决定才发现冻结输入不合法。review-check只读，核对摘要、原值、scope、允许字段、真实人物槽位和场次、相关场次人物的声源字段、集合声源及连续来源、背景群像无独立台词、候选覆盖及目标关联、PROP入选路径、归档理由与活动引用。声源与槽位的字段约束由同一校验函数供准备、前检和完整验证复用。允许不相关pending/inactive留存，但本批目标或候选目标自身待核时拒绝；未知全局问题仍交完整前检。

check报告固定`status: draft_checked`、`formal_ready: false`、`full_ledger_validation: not_performed`、`semantic_review: author_judgment_required`，列实际ID、剩余待核数量及绑定标准稿/来源/台账/各批的binding_sha256。只证明选定决定可计算地一致，不证明作者找齐候选或语义正确。

收齐后一次review-apply，先拒绝重叠、不同输入、旧摘要或原值冲突，再深拷贝并从新副本重建索引、应用固定操作、执行完整来源/身份/生命周期/政策/设计校验。任一pending/inactive未解决或已设计类型改变使设计失效，均不写正式结果；不得删待核、遗漏资产或临时换政策让小批通过。输出及报告为不同新文件，不覆盖输入/旧产物。

台账以`asset_decision_receipts`保存每批原决定及摘要；后续追加时同ID以最新显式决定为准，未选ID保留此前有效决定。validate/design/render/bind自动核对当前真实字段，review-verify可分别用原批文件只读对账。逐批正式应用时，下批必须从上次新台账重新prepare；同旧输入的多批应集中应用，不能手改input_ledger_digest或expected_record_digest使旧决定适配新底稿。

## 归并与回执的前置保护

normalize在写出前验证旧回执有效，再核对归并后的决定字段及相关身份、事实、LOC/SET和场次依赖。合并已审ID、改变分类或影响决定依据时，拒绝写出并列ID；仅制作name变化且决定字段及依赖未变时允许，并报告`name_only_ids`，设计仍需摘要检查与重编译。

保护不重签回执、不自动历史化或刷新摘要。确需改变结构时保留旧版本，生成新候选并实审受影响决定；未关联回执的归并仍须遵守身份与命名规则。

## 待核、退役与新项目交付门槛

`pending_reviews`为未解决问题，`inactive_assets`为待定去向资产（characters/props/locations/scene_assets/scenes）；任一非空都阻止完整validate/design/render/bind。明确排除不得留inactive作归档。`asset_archive`保存稳定ID、类别、具体原因与原记录，ID须在retired_ids且无活动资产/引用；旧retired_ids继续读取，不为旧合并ID编造原因，新显式排除由归档决定保留证据。

新项目契约自动要求全部图片资产有有效完整设计、prompt_only无图片派生字段、来源与设计摘要有效；`validate --candidate`仅为候选前检。无契约旧台账保持事实读取兼容，不能据此声称新设计完整；显式`--require-designed`及design最终校验执行同一完整设计门槛。正式交付与bind顺序见[资产主流程](../../../references/asset-workflow.md)。

## 变更影响清单

```text
python scripts/asset_ledger.py impact <before-ledger.json> <after-ledger.json> --before-source <before-source.json> --after-source <after-source.json> --before-design <before-design.json> --after-design <after-design.json>
```

source/design快照参数可省略；命令只读，stdout报告绑定实际输入与`binding_sha256`，不修改来源、设计、审阅或session。`direct/dependent/unknown`分别列直接变化、真实依赖及触发ID、需保守扩大的未知影响，区分`requires_semantic_review/requires_recompile`并汇总ID。

- 仅制作name变化、身份及其他作者字段未变：`name_only`建议重编译，不要求重新理解全剧，也不签发旧设计可复用。
- 事实、分类、关联或设计变化：沿共享身体/身份锚点、基础服装→佩戴形象、PROP→关联佩戴人物、LOC→SET及父SET链传播。
- source_digest或实际来源、全局风格/政策、未知字段变化：全局/未知扩大；仅给一侧来源或设计也报告输入不完整，不能推测其他资产无影响。
- 归档、待核、审阅历史单列行政检查，不当成新设计事实。

报告固定`formal_ready: false`、`automatic_review_reuse: false`，用于安排复核，不能自动复制通过结论。建议重编译不表示已经编译/审阅/恢复绑定；现有全局来源摘要保护及design/render/bind门槛保持。已有绑定的材料变更另按[绑定变更边界](../../../references/binding-changes.md)，当前没有原位rebind。

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

旧人物source_entry（缺省name）没有在新剧本人物行出现时，完整记录先放入 `inactive_assets.characters`，同时加入 `pending_reviews`；这表示“可能改名、换形象或退场”，并不立即废弃人物 ID。若实际改名或原有形象条目更名，在映射中用 `characters` 指定旧 ID 对应的新人物行完整条目后重新迁移；迁移更新source_entry及已登记的source_speaker，保留独立制作name与原ID。若出现新形象，则分配新的 `CHAR` ID，不将旧形象 ID 改名挪用。若确认退场，使用带稳定ID及证据的archive审阅决定，将完整旧记录移入asset_archive并登记retired_ids；核对无现用引用后逐项消解相应pending，不清空其他待核或直接删除旧记录。详见[审阅决定工作区](#审阅决定工作区)。旧版 `schema_version: 1` 可迁移，但每场 `time_basis` 都须复核。

## 制作设计与图片提示词

全部图片设计字段、制作事实边界、身份锚点、版式和编译验收统一见[制作设计与图片提示词](../../../references/asset-image-design.md#制作设计与图片提示词)。本页只确定哪些资产和版本需要图片，不在两处维护设计规范。

### 设计依赖与局部更新

source_scope、上下文摘要、共享依赖和`design --patch`统一见[设计依赖与局部更新](../../../references/asset-image-design.md#设计依赖与局部更新)。已有session的变更仍受[绑定变更边界](../../../references/binding-changes.md)约束。

## 核对

`render` 检查：原文摘要、场景与段落范围、ID 引用、每场人物source_entry（缺省name）与人物行完整条目逐项、逐字、按顺序匹配、场景标题及地点名称与剧本一致、道具source_name和别名在关联正文中出现、全目录制作名称唯一及安全、形象启用区间、资产事实的场景出现关系、证据在原文段落中出现。新策略还检查道具入选路径、使用证据及prop_coverage_review的声明、全部SC覆盖、候选证据与目标关联、全部PROP覆盖。群像按group_policy检查模式、入选路径、参考需求、对应最低可见出场次数及审阅理由字段，v2纯文字模式还须说明文字足够的理由。`pending_reviews` 或 `inactive_assets` 任一分类未处理完时不能渲染。它无法判断摘要是否正确表达证据、候选是否语义完整或“性格”是否真的被原文明确写出；这些仍需人工逐条核对。

分类TXT的准确前缀、编号、人物与SET/PROP关联场次、旧事实台账兼容和导出格式统一见[写入、编译与验收](../../../references/asset-image-design.md#写入编译与验收)。JSON保存原文事实、出处、制作设计与格式元数据，TXT由同一台账渲染。
