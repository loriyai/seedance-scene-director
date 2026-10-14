# 资产台账结构

后台主文件使用 UTF-8 JSON；分类视觉描述 TXT 由它生成。标准剧本的场景序号用于显示，`SC`（场次）、`LOC`（物理地点）、`SET`（布景）、`CHAR`、`PROP` ID 用于长期关联。首次建立台账可用 `asset_ledger.py init` 生成无事实的索引骨架；若已有台账，先用 `asset_migrate.py` 迁移并审核，不重新初始化或重编号。`init` 生成的 `time_basis: 待核` 是草稿，须逐场确认后才能渲染。

```text
python scripts/asset_ledger.py init <standard-script.txt> <task-work-dir> --output <asset-ledger.json>
python scripts/asset_ledger.py normalize <identity-and-time-reviewed-ledger.json> --output <normalized-ledger.json> --report <naming-audit.json>
python scripts/asset_ledger.py build-scenes <normalized-ledger.json> --source <task-work-dir>/source.json --output <sets-draft.json>
python scripts/asset_ledger.py normalize <sets-draft.json> --group-review <group-review.json> --source <task-work-dir>/source.json --output <group-reviewed-ledger.json> --report <group-review-report.json>
python scripts/asset_ledger.py design <standard-script.txt> <task-work-dir>/source.json <group-reviewed-ledger.json> <asset-design.json> --style-file <project>/video-style.txt --output <designed-ledger.json> --catalog-output <asset-catalog.txt>
python scripts/asset_ledger.py render <standard-script.txt> <task-work-dir>/source.json <asset-ledger.json> --output <asset-catalog.txt>
```

## JSON 字段

```json
{
  "schema_version": 3,
  "asset_policy": "visible-reuse-v1",
  "group_policy": "visible-reuse-v1",
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
  "props": [],
  "retired_ids": [],
  "inactive_assets": {"characters": []},
  "pending_reviews": []
}
```

`init` 只建立场次、人物和待核地点索引，scene_assets为空且scene_asset_id为null，不自动提取资产事实或主要道具。地点按场景先分配独立 ID，核实是同一地点后再合并；明确姓名的角色只按完整人物条目初步归并。`卢筱筱-基础形象` 与 `卢筱筱-黑色裙子` 分别建立稳定 ID；同一完整条目在多场出现且身份明确时复用 ID。泛称有台词角色先按身份核实；群像草稿直接加地点后缀，在地点核实后normalize按同地点同原条目/形象归并候选，再审阅图片筛选，不逐SC建图。群像只用于无姓名、无台词且原文明示阵营、身份、人设或职业的背景人物。新init的群像暂记visual_mode="prompt_only"但未完成审阅，不能直接交付；最终模式须依据全剧证据确认。若相同条目指向不同实体，拆成不同 ID，并用逐场关联指向各自实体。新 ID 只追加，废弃的 ID 放入 `retired_ids`，不回收。场景的 `number`、`heading` 可随剧本修改；`id` 保持稳定。

原文用词与制作名称分开：人物source_entry对应人物行完整条目，未填则兼容以name匹配；人物name是唯一制作名称，可加入世次、当前身份、形象及地点限定；场景 `heading` 对应完整标题；地点主名称对应至少一个关联场景标题中的地点字样，其他已确认同一地点的标题字样可放入 `aliases`；道具source_name须原样出现在至少一个关联场景正文，每场出现source_name或aliases；未填兼容以name匹配。name用于唯一图片引用，可使用有依据的版本限定，例如“惊堂木-京城茶馆”；不把制作名写进原台词，不把剧情说明当外形事实。

新项目heading统一为 `场景X-日/夜-内/外-地点`，例如 `场景1-日-内-昏暗房间`，实际名称不含选项斜杠，并通过文件名字符检查。读取兼容旧项目的冒号与间隔点标题，保持绑定剧本与台账场次标题一致；图片文件名使用scene_assets的name，不自动迁移旧项目。

`time_basis.kind` 只能是 `原文明示` 或 `格式默认`。前者的 `source_paragraphs` 必须列出该场原文明示时间的段落；后者须为空数组。标题中默认写 `日` 不代表原文明确是白天。脚本生成的待核值不能直接通过 `render`。

每条 `visual_facts` 或 `personality_facts` 采用以下结构。`text` 是简短事实，`evidence` 是原文中的连续原句或词组；证据跨段时，可引用各段共有的短词组，或拆成多条事实。`source_paragraphs` 使用 `prepare` 生成的原文段落编号，不能重复或引用关联场景之外的段落。`scene_ids` 只列证据所在场景，程序分别验证每场所引连续段落包含该证据，不跨场景或跨段落缺口拼接证据；此字段不表示该特征在未明示的后续场景仍持续。

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

新init默认asset_policy="visible-reuse-v1"；旧台账缺少该字段仍按原规则读取，未经显式审阅不迁移或删除旧PROP。新主要道具必须有至少2次独立可见使用，并且确需固定形制以避免前后不一致。词频、对白提及和同一事件的多个镜头不累计；普通桌床等固定家具归SET，普通日用品和一次性小物件不提取，props可为空。相同source_name且无明确视觉变化默认复用一个PROP图片资产，不因地点/持有人变化另建。物理实体由prop_occurrences独立保存，不因共用图片冒认同一件。

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

event_id是同场独立可见事件标识，不是镜头号。每条evidence须唯一定位关联场内连续原文段落；同(scene_id,event_id)不重复，证据范围不重叠，至少两条。source_paragraphs连续、按顺序且无重复。程序不做关键词计数，不自动发现事件，也不能理解使用是否可见；作者逐条核实。extraction_review.kind只能为special_reusable；普通日用品、家具、穿戴、临时物件均不成立。手机、水杯等日用品成为特殊信物时另填special_features说明可辨形制，但仍须2次独立可见使用。程序对明确桌床等名称做辅助拒绝，对部分日用品要求特殊说明，不能覆盖中文全部语义，不能通过改kind、改名或泛泛理由绕过审阅。

## 群像图片筛选

人物行与characters/CHAR是完整剧情索引；是否制作图片由群像visual_mode决定。新台账group_policy="visible-reuse-v1"要求每个群像完成全剧审阅，至少2次独立可见出场并有具体外观一致性需求才取image_asset；其余取prompt_only。具名角色、有独立台词的匿名角色以及SET/PROP不能用prompt_only规避图片规则。旧台账无group_policy且群像无visual_mode时沿用原图片资产行为，不自动筛除；显式填写模式即接受此校验。

先normalize同LOC、同称谓、同形象的候选，再按完整剧本登记visible_uses。一次事件切成多镜、不同文句重复提名或对白提及不累计；跨场次的同一连续事件仍只算一次，同场的真实独立再次出场可以累计。群像图片复用只固定制作外观，不据此认定是同一批实体人物。程序校验连续原文证据、唯一定位、事件与证据不重复，背景身份、出场是否独立/实际可见及一致性必要性由作者审阅。

每个群像必填extraction_review：kind="background_group"、reviewed_full_script=true、source_digest与台账当前摘要一致、identity_basis说明原文明示群体称谓且无姓名/独立台词。image_asset另填consistency_reason并登记至少2条visible_uses；prompt_only另填exclusion_reason，保留至少1条可见出场证据。未达两次不能靠泛泛一致性理由提取；多次但无需固定外观仍可prompt_only。后者保留source_entry、事实、CHAR和逐场character_ids，不含visual_design/image_prompt等生图派生字段；分镜保留人物与动作，渲染普通名称，不输出@引用，不生成图片、资产条目、画布节点或缺图报告项。

可直接在normalize后的候选JSON填写上述字段，或用独立审阅JSON：

```json
{
  "groups": [
    {
      "character_id": "CHAR0002",
      "visual_mode": "prompt_only",
      "extraction_review": {
        "kind": "background_group",
        "reviewed_full_script": true,
        "source_digest": "当前台账source_digest",
        "identity_basis": "原文明示围观村民，无姓名和独立台词",
        "exclusion_reason": "全剧只有一次围观事件，在镜头文字中描述即可"
      },
      "visible_uses": [
        {"scene_id": "SC0001", "event_id": "门前围观", "evidence": "村民站在门前围观", "source_paragraphs": [3]}
      ]
    }
  ]
}
```

```text
python scripts/asset_ledger.py normalize <sets-draft.json> --group-review <group-review.json> --source <source.json> --output <group-reviewed-ledger.json> --report <group-review-report.json>
```

groups须覆盖归并后全部群像CHAR，不填非群像；--source必需且须为同一原文摘要。命令先执行命名归并，再应用审阅，保留原输入；若尚不知最终ID，先不带审阅参数normalize并读取redirects。合并前的群像筛选决定不能自动代表合并后的全剧群像，合并后须重审；可见证据会保留供审阅。design/render及bind再次校验，发现群像称谓的独立台词时阻止按背景群像处理。改稿迁移保留模式和证据、只迁移可唯一定位的出场；全文改变或不足复用门槛加入待核项，不自动升降级、删图或删除节点。

分类TXT按实际图片资产顺序输出角色01、场景01、道具01，三类分别计数，超过99自然为100；prompt_only不占号。编号不进入name、ID、图片文件名、节点名或引用。

## 制作设计与图片提示词

原文visual_facts和personality_facts保持事实及出处，未知事实仍留空；完整美术方案另存visual_design，不写回事实字段。生成者结合剧本约束和[资产图片设计规范](../../../references/asset-image-design.md)写设计JSON，脚本只校验并编译，不设计外观、不调用生图模型、不改变ID/名称/形象启用。design覆盖全部图片CHAR/SET/PROP，排除prompt_only群像；其旧生图派生字段仅在显式design时清除。design只接受已核实schema 3台账，旧schema 2须按原显式迁移流程处理；旧schema 3可在用户授权后显式执行design，新政策字段不自动添加。normalize与群像筛选先于design执行。

输入design JSON必须覆盖全部现用CHAR、SET、PROP；即使某基础SET只供版本继承也须设计。设计数组不增加或删除资产。每项attribution固定“制作设计”，basis记简短设计依据，reviewed_source_facts=true表示作者已审阅所有原文明示约束，不代表程序能理解或自动修正语义冲突。

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

SET的space必须写主要活动区、出入口和固定相对关系；materials_palette/furnishings/lighting提供可拍材质、陈设与基础受光。普通家具进入SET，单幅无人物主视图；父SET共用事实继承规则保持原规定，不把temporal时间跨度推成自动破败。PROP的structure/materials_palette/scale/state只描写物件本体，不编造未知密码、铭文或剧情线索；单件完整、纯净白底、不夹手部或剧情环境。

style-file使用项目原样保存的单行video-style.txt；只去除文件首尾空白，不折叠内部空格、不重新翻译或缩写。编译后顶层新增image_design_version="gpt-image-2-v1"、visual_style={text,digest}、identity_anchors。每个现用资产新增visual_design、image_prompt、image_format、image_design_digest；visual_design中自动记录source_fact_digest及source_context_digest。角色image_format固定model="gpt-image-2"、aspect_ratio="16:9"、background="pure_white"、layout="left_1/3_large_front_face_right_2/3_front_side90_back_full_body"。最左面部特写占满1/3宽并贯穿高度，右2/3正面、90度侧面、背面完整全身等高并排。SET和PROP记录单幅主视图/完整单品版式，不伪造模型API参数或已生成图片。

编译将原文明示可见特征作为优先约束，结合既定风格、已写设计和分类构图，不把审计出处或“无外观事实”等说明塞入生图提示词。render再次确定性编译并核对保存的提示词、版式、设计摘要、事实完整证据及场次映射摘要。source_context_digest同时包含台账顶层source_digest：制作设计可能依赖全剧上下文，因此任意原文版本变更均保守地要求重新审阅设计，即使heading、range、visual_facts保持不变也不能沿用旧提示词。改原文、事实、证据、适用映射、设计或审阅依据、名称、风格后须审阅并显式重新design，不能只手改image_prompt；改稿迁移保留旧设计并登记制作设计待复核。旧无设计台账不受此检查影响。设计命令输出不同的新文件，不覆盖旧台账或旧TXT。重新design可读取旧设计台账，按当前事实结构核实后重编译全部完整方案。

## 核对

`render` 检查：原文摘要、场景与段落范围、ID 引用、每场人物source_entry（缺省name）与人物行完整条目逐项、逐字、按顺序匹配、场景标题及地点名称与剧本一致、道具source_name和别名在关联正文中出现、全目录制作名称唯一及安全、形象启用区间、资产事实的场景出现关系、证据在原文段落中出现。`pending_reviews` 或 `inactive_assets.characters` 未处理完时不能渲染。它无法判断摘要是否正确表达证据，也无法证明“性格”是否真的被原文明确写出；这些仍需人工逐条核对。

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

旧人物source_entry（缺省name）没有在新剧本人物行出现时，完整记录先放入 `inactive_assets.characters`，同时加入 `pending_reviews`；这表示“可能改名、换形象或退场”，并不立即废弃人物 ID。若实际改名或原有形象条目更名，在映射中用 `characters` 指定旧 ID 对应的新人物行完整条目后重新迁移；迁移更新source_entry及已登记的source_speaker，保留独立制作name与原ID。若出现新形象，则分配新的 `CHAR` ID，不将旧形象 ID 改名挪用。若确认退场，人工把 ID 放入 `retired_ids`，移除暂存记录，并核对其旧事实是否需要保留为历史记录。处理所有待核项后才清空 `pending_reviews` 并渲染。旧版 `schema_version: 1` 可迁移，但每场 `time_basis` 都须复核。

## 物理地点与布景版本

scenes保留全部剧情场次及heading，不删场、不合并叙事；scene_assets存储真正要创建节点的布景资产。先结合原文的所属主体、建筑、楼层、具体空间核实LOC身份。别名和同名不等于同一物理空间；确认同一实体才合并LOC并重指场次，废弃LOC ID写入retired_ids。填写identity_status="confirmed"和非空identity_basis说明核实依据；无法确认保持pending，最终render及bind阻止通过。不同地点的泛称相同时，用有原文依据的所属主体或空间限定区分布景name，禁止编造地点。

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
