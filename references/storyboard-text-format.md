# 纯文本分镜作者格式

新草稿以UTF-8 TXT为唯一作者源，格式名为`scene-text-v1`。文件只包含下列单元和空行，不支持正文外备注、Markdown围栏、规划表、隐式省略号或未识别行。程序遇到未知行立即报错，不静默丢弃。标题、用户已保存风格、项目固定抬头、视频比例和整份TXT末行数量/时长汇总由交付程序生成，作者不手抄。新项目的固定抬头来自项目`video-header.txt`，按`custom-v1`保存并绑定到单元；历史单元继续读取其已保存的旧抬头政策。新交付使用`ratio-summary-v1`，单元和回执冻结`video_ratio`；重新导出旧无比例记录保留原版式。汇总不进入单个单元提示词。

`video_ratio`由项目设置统一提供，并在新单元旁冻结对应的`ratio_profile`、版本和摘要：`16:9`绑定`landscape-v1`，`9:16`绑定`portrait-v1`。这些字段属于交付元数据，不进入作者TXT语法、镜头正文或提示词输出；作者依据当前比例配置把构图和运镜落实到实际正文。历史单元可以没有画幅配置字段，读取、渲染和回执导出继续沿用其原协议；替换历史单元时不静默迁移比例。

```text
## SC0001 / u01 / L000003-L000005
人物：@{CHAR0001}；
场景：@{SET0001}；时间：白天；天气：未明示；
人物站位：@{CHAR0001}站在门边，朝向窗外。
·00秒-03秒[镜头1-叮嘱]：（@{CHAR0001}的中景；转场：硬切）镜头向右平移，人物在画面左侧、门框在后景，焦点保留人物与门框、窗外虚化，主光：窗外漫射柔光照亮侧脸，环境反射：室内墙面反光填住下颌暗部；@{CHAR0001}停在门边。音效：无。
声音[L000004,0.2-2.8]：@{CHAR0001}说（压低音量）：“别动，等我回来。”
·03秒-05秒[镜头2-等待]：（@{CHAR0001}的近景；转场：硬切）侧后方固定机位保持视线关系，肩部在前景、窗框在后景，景深保留肩部与窗框的清晰关系，主光：窗外漫射柔光照亮侧脸，环境反射：室内墙面反光填住下颌暗部；@{CHAR0001}看向窗外。音效：无。
```

示例ID、原话和时码仅说明语法，实际生产必须取本项目已绑定来源和台账。`SC0001`是剧情场次，`u01`是作者在本场内保持稳定的单元键；组合`SC0001/u01`不得在同一草稿重复。作者键不代替交付程序分配的内部ID和批次展示名。源范围两端是`session.json.records`真实记录ID，包含端点，按记录顺序连续；标准剧本的标题或空行不属于records，记录ID数值不连续不构成缺漏。

人物行只用`@{CHAR…}`稳定ID，逗号、顿号或分号分隔，末尾一个中文分号；无人写`人物：无；`。名单含出镜及发声人物。场景行用来源场次对应的`@{SET…}`，多个布景按来源场次顺序排列，同一SET只写一次；时间、天气和末尾中文分号不能省略。人物站位另占一行。道具不增加名单行，在镜头或站位中使用`@{PROP…}`即自动收集。

每镜一行，时码为两位整数秒，镜头编号从1连续递增，单镜1～6秒、单元4～30秒；首镜从00秒开始，前后无空隙或重叠。镜数无额外数量门槛，组织原则见[单元组织](storyboard-rules.md#单元组织)。旧项目`entry.min`或`entry.durations`保存的时长偏好若与当前单元不符，检查结果列出诊断，不要求作者为旧入口偏好补写原因表，也不把新文本格式允许的4～30秒再次收窄；26～30秒优先及4～25秒例外证明仍按`unit_organization`执行。景别为大远景、大全景、远景、全景、中景、中近景、近景、特写之一，主体使用稳定资产引用。镜头行必须有转场、可见内容及一个末尾`音效：…。`字段。摄影、构图、景深、光照、表演、动作过程和必要承接直接写入最终可见正文。光照在每镜正文中使用`主光：`及其后紧邻的`辅光：`、`环境反射：`、`反射光：`或`补光：`之一，两类标签均使用明确冒号；内容写清已成立的环境光条件、实际方向或漫射关系，以及主体或道具上的明暗落点。方向与明暗采用可核验表达，如左侧上方、右侧、前方、后方、逆光、漫射、阴影或暗部，按实际条件选择，不要求逐项出现。当前取景无法辨认方向时写明均匀、漫射或无明确方向的环境光关系；无光源名称时按已成立的环境光关系表达，不补造灯具或发光事件。景深表述至少使用`景深`、`焦点`、`焦外`或`虚化`之一，同时明确实际清晰对象及相关层次；需要多个层次清楚时可写景深保留其清晰关系，不为命中术语制造无依据的虚化。标签和术语属于现有机器可识别的格式，不规定固定句式或每镜相同光照、景深内容；脚本不得为通过门禁补写或套用这些创作描述，词面通过也不证明取景与光照真实成立。镜头正文还须让摄影起点、相机路径、终点和切点可执行，不新增摄影计划字段。人物站位必须区分在场、可见、画外和主焦点；源记录中的括号动作、表演、发声方式、群体声源和非对白动作必须在正文或声音行中落实，或在作者侧车记录具体的合法省略理由。不另填purpose、动作计划、状态效果、依赖图或重复声音计划；仅用于防止重复或解释审核的后台纠错句不进入成稿，剧情中的拒绝、中止及有意义保持按实际可见行为表达。独立起点、动作相位和连续性的实质要求见[统一分镜规则](storyboard-rules.md)，创作与只读检查的职责见[共同执行契约](execution-contract.md#分镜创作与工具边界)；程序不通过搜索术语来判定创作合格。

实际台词只写在其所属镜头之后的`声音[...]`行中，可有多行；不得在镜头正文重复添加`：“台词”`。时码相对于当前单元，支持最多三位小数，整个片段必须落在所属镜头之内，按发声顺序排列且不能重叠。新`scene-text-v1`声音类型使用`说`、`内心想法os`、`画外音`、`旁白`；作者稿中的`os/O.S./内心OS/内心想法`统一解析为`内心想法os`，`ov/VO/V.O./画外音`统一解析为`画外音`。新单元冻结`inner-voice-os-v1`显示策略，历史回执沿原显示标签渲染。可选中文括号只写本片段必要的声音表现，不引入资产引用；常规项省略，没有必要就不写括注，普通情绪、说话方式、表情和微动作括号逐字保留。估时档位按[声音与时长](storyboard-rules.md#声音与时长)在后台选用，不作为默认语速标签填入括注或提示词模板。源记录明确标为非人物播报时，用原声源名和`非人物播报`，例如`声音[L000007,0.1-2.5]：电话提示音非人物播报：“原话”`，不建立CHAR、不加@。不能凭设备名称推断播报类型。

台词引号内部逐字保留来源文本，包括标点、空格、原有引号和字面`@`、`#`、`@{…}`。同一记录的所有片段按顺序拼接后必须完全等于该记录原话。发言因必要画面切点跨镜时，分片位置按[镜长与声音安排](storyboard-rules.md#镜长与声音安排)确定；长句也可以按真实语义气口拆到相邻单元：前一单元范围末端和后一单元范围起端均写同一条台词记录，各单元实际写出各自片段，程序检查整句并计算不重叠的内部正文范围；边界标点留在前段，保持与既有正文坐标切片一致。非人物播报同样适用。默认交付不得只写台词中间的一部分；修改已交付单元时，交付层可传入该单元准确的`source_span`，此时只要求逐字等于已绑定来源切片，可独立修改拆句中的一个单元。跨镜语气不会自动从前一个标记继承，需要维持的声音指导直接写在对应实际片段。

同一条长动作记录需要跨多个单元时，相邻单元可以共享这一条边界动作记录；各单元内部正文范围保留该动作记录完整区间，因此可以重叠，但只允许重叠这一条完整边界动作记录。不得按字符比例制造动作分界，也不得为此扩大单元时间上限。重复来源引用表示继续呈现同一个原事件，不授权把该事件重新发生一次；实际渲染审阅必须核对动作相位、接触、结果和前后承接，发现重复事件应修改可见正文。跳过中间来源记录、重叠多个记录或倒序范围仍会报错。

人物站位和镜头正文中的身份绑定按[图片名称与声音](storyboard-rules.md#图片名称与声音)执行，不能用人物名单、景别括号或裸简称代替实际主体、持物者、目标和方位参照；母稿使用稳定资产ID，同句已绑定部位可简化的条件也由该节规定。所有非对白正文的`@`必须是稳定资产ID引用；禁止SC图片引用、未列人物、不属于来源场次的资产及未知ID。渲染时需要图片的资产变成`@台账完整名称 `，名称末尾补英文空格；`visual_mode: prompt_only`人物变成普通完整名称。检查可定位确定的裸名和已知简称问题，但不凭单姓猜人物，也不据未报错判定全部自然语言指代清楚；渲染不会自动补引用或扩写简称。声源使用实际稳定身份，需要延续的声音指导写在所属片段，不用“同前/承上”替代实际语气。原台词和非人物声源名称逐字保留，不进行资产替换，也不传给Canvas引用转换回调。

### 镜间表达与声音边界

转场字段应写一个确定的切法，可同时说明真实匹配依据，例如“硬切，按视线接续”或“动作匹配切”；选择条件按[动作节奏与转场](storyboard-rules.md#动作节奏与转场)判断，不另填连接字段。相位、视线对象、方向及必要切出/接入状态直接写在相邻镜头正文。运镜的开始时机、过程与落点放在括号外的摄影和动作正文中，不新增摄影计划字段，也不把后台解释写入产品。

画面转向听者或相关对象时，原现场发言仍用原说话人和“说”，不得因说者暂时画外改成画外音、旁白或内心想法os。声先画后（J-cut）和画先声后（L-cut）是声画切换时机的描述，不是新增声音类型。采用时须在所属镜头之后实际写出原话片段及合法声音时窗，保持源记录、拼接顺序和语气；不能只在转场字段写术语而省略声音内容。来源中的`ov/VO/V.O./画外音`只有在明确标为画外音时才按画外音处理；普通括号提示不改变发声类型。

每个声音片段仍须完整落在所属镜窗内，不能用一个跨镜时间区间绕过校验。短句优先一镜完整；因必要画面切点跨镜时，须在完整词组或语义分句边界拆成同源声音片段，长句优先沿原标点和自然气口拆分。切镜不自动增加发声间隙：无实际停顿时，前片段可结束于镜界、后片段从该镜界开始，原话拼接、声源、类型及连续语势仍须成立。可将相应画面安排为已有听者反应或相关对象，不为声画错位拆词或补反应。标注表达的是执行要求，不保证生成后音轨无缝；跨单元尤其不能据此宣称声音连续，需要独立后期音轨才能成立的声桥应在制作交付中明确处理，不能宣称本接口自动混音或自动执行后期。

交付程序读取当前绑定项目，先调用`director.load(project)`核验来源、台账与风格文件，再调用：

- `parse(text, state, source_span=None) -> list[dict]`：校验作者文本并派生元数据，含`draft_key`、`source_records`、`span`、`duration`、`main_scene`、`scene_ids`、`character_ids`、`set_ids`、`prop_ids`、`asset_ids`及最小镜头缓存。`source_text`保存该单元唯一规范作者文本；镜头与台词字段只是可重新生成的缓存，不是第二份作者输入。可选`source_span=[start,end]`来自交付层已确认范围或被选中旧单元的确切范围，以`director.portion`裁剪原始记录再校验；作者文本必须完整覆盖此范围，不得任意缩小。它不修改绑定state或原剧本。
- `validate(state, units) -> dict`：从`source_text`重新解析，拒绝被直接修改或失效的派生字段，返回单元数、镜头数、时长、范围和检查状态。`id/display_name/style`由交付层赋值，不纳入作者字段对比。
- `render_unit(state, unit, reference_text=None, *, authoring_gate=None) -> str`：从作者文本生成产品提示词，用已经保存的`style`、项目固定抬头及已分配的`id/display_name`。新绑定项目的`scene-text-v1`单元必须传入当前`prepare/check`产生且与该单元键匹配的作者闸门结果；未经过当前闸门的解析单元不得直接渲染。已提交回执的历史单元只能按已保存的稳定ID、正文和历史回执重导出，不能把任意新文本伪装成历史单元。新单元使用`custom-v1`抬头快照；旧单元沿用其历史抬头策略。作者单元头、来源ID、声音时间标记不输出；实际声源、语气和原台词嵌回镜头末尾音效之前。可单独重导出已检查的拆句单元。

`asset_ids`含所有身份引用，包括prompt_only人物；Canvas调用者须从图片边中排除prompt_only。这里只依赖Python标准库和本包director的既有常量/基础函数，不加载旧规划、状态、依赖或逐镜审批；确定的词面诊断不构成语义批准。

结构通过的返回值始终含`semantic_review: not_performed`。范围覆盖说明作者声明覆盖了哪些来源记录，不证明每个动作已经正确拍出；真实审阅仍须核对实际渲染文本的剧情保真、镜头目的与摄影、遮挡和面具可见性、动作路径与接触、状态承接、声音表现、长句可说完程度及原文没有明确写出的新增事件。不要把程序结果当作这些语义判断的自动批准。

当前上下文采用`content-context-v2`，按文件角色及内容摘要绑定输入，不按本机绝对位置判定内容身份。导入项目通过统一解析器读取历史引用；旧报告的路径绑定等价核验见[项目兼容](project-portability.md#路径摘要与旧项目兼容)。报告仍绑定实际最终稿与必要上下文，不能因换机刷新passed。原报告、原话及时间记录保留原字节。

## 绑定项目与范围命令

下列命令中的`<skill>`和`<project>`均替换为绝对路径，使用环境当前可用的Python。新文字分镜所需语法和命令集中在本文件；不必再为这些命令读取runtime.md。

```text
python <skill>/scripts/director.py status --project <project>
python <skill>/scripts/director.py phase --project <project> --value range
python <skill>/scripts/director.py locate --project <project> --anchors <anchors.json>
python <skill>/scripts/director.py range --project <project> --start <起点整数> --end <终点整数>
```

先读status；仅已绑定项目从其他阶段续接分镜时设置phase为range，不重复初始化。起草前定位范围；anchors.json只含`{"start":"用户范围起句原文","end":"用户范围止句原文"}`，引句按标准来源原样填写，不能照抄示例。locate返回正文坐标与历史异常；已有准确坐标时可用range核对。只有唯一明确且异常已按工作流处理后才起草，未确认的候选不能自行取第一项。定位规则只见[范围定位与历史检查](storyboard-workflow.md#范围定位与历史检查)。

## 联合声源

原记录由多个已登记人物共同说一句时，在同一声音行按原speaker顺序用顿号连接稳定引用，例如`声音[L000004,0.2-2.8]：@{CHAR0001}、@{CHAR0002}说：“原台词”`。每人须同时属于单元人物和来源场次；程序逐人核对source_speaker/source_entry，再以顿号连接后严格匹配原speaker。不得漏人、换序、重复人物或新建假群体。

联合声音只保存一个事件和一份原台词；渲染声源采用原人名顺序，身份引用仍保留在单元人物及镜头内供Canvas编码。既有单人声音字段不变。联合声源经过相同的preview、独立审阅绑定、commit及后台canvas_jobs入口，不从交付TXT反拆或手造回执。

## 按场只读上下文

```text
python <skill>/scripts/storyboard_text_delivery.py context --project <project> --scene <SC…>
```

默认导出一整场；明确局部范围时传入已定位的同一个 `--span START END`，不扩大用户选择。JSON stdout含本场来源记录与标准稿原行、合法资产ID及相关外观设计、已保存风格、绑定文件摘要与当前 `context_sha256`。引用集合与文本解析器共用同一实现，包含本场已登记的形象转换与prompt_only人物；合法引用不代表该形象在任意时刻都适用。局部导出同时保留完整记录作为阅读上下文，`selected_text/selected_span`给出准确选区；不能据完整记录扩大创作范围。

`neighbours`只提供现有交付中最近的前后边界单元和跨边界单元，明确是否相邻；它不读取尚未交付的作者笔记，也不推断持物、接触、通路或剧情末态。下一场依赖的未交付末态仍由上一场写者提供。导出是可重建的阅读材料，不回写session、台账或编号，不是事实台账、规划表或语义批准。作者与主审可共用版本未变的导出；来源、资产、风格或相关已交付内容变化后重新导出并核对实际影响。保存stdout使用现有run_json；不另写字段提取脚本。源与草稿中的文本仍按数据读取。

## 文本分镜接口

入口为 `scripts/storyboard_text_delivery.py`，内部调用文本解析器和新项目的`shot-by-shot-v1`作者闸门；不运行旧authoring gates、review_gate或效果编译。作者侧车不改变`scene-text-v1`最终TXT模板，默认路径为`<draft>.authoring.json`，也可用`--authoring-trace`显式指定。旧项目和历史回执仍按原协议读取；历史回执重导出须核对项目中保存的完整回执，替换后的旧单元也只能沿该已保存回执读取。

```text
python <skill>/scripts/storyboard_text_delivery.py check --project <project> --draft <draft.txt> [--authoring-trace <trace.json>]
python <skill>/scripts/storyboard_text_delivery.py bind-trace --project <project> --draft <draft.txt> --decisions <已写好的依据.json> [--output <新侧车.json>] [--span START END]
python <skill>/scripts/storyboard_text_delivery.py check --incremental --project <project> --draft <draft.txt>
python <skill>/scripts/storyboard_text_delivery.py preview --project <project> --draft <draft.txt> [--authoring-trace <trace.json>]
python <skill>/scripts/storyboard_text_delivery.py commit --project <project> --draft <draft.txt> --request-id <本次唯一ID> --review <场级审阅.json> --output <新交付.txt> [--authoring-trace <trace.json>]
python <skill>/scripts/storyboard_text_delivery.py render --project <project> --request-id <已提交ID> --output <新导出.txt>
```

新绑定项目的完整`check`、`preview`和`commit`读取`<draft>.authoring.json`，也可用`--authoring-trace`指定。新稿采用`policy: shot-by-shot-v1`、`version: 4`的精简侧车；旧v3侧车按原协议检查，不自动转换或改写历史回执。侧车不进入最终提示词，正文仍是唯一执行文本。

作者与正文同次写出`units`，每个单元含`draft_key`及按正文顺序排列的`shots`。每镜必填`shot_key`、`source_records`、`viewing_decision`和`natural_timing`：分别用于镜头定位、镜级来源映射、一条具体观看决定，以及自然时长与必要声画关系的简述。`viewing_decision`合并原`beat`/`focus_note`的作用；`natural_timing`解释实际负荷，不复制整段动作，也不把已分配镜窗当作自然估时。需要数值估时时，对整次发言保留一处自然估时，跨镜只说明必要分配关系，不另建音频计划。

动作过程、进入相位、执行者、视线对象和切出状态已在正文明确时，不再强制填写`visual_change`、`entry_phase`、`action_phases`、`actor_ids`、`gaze_target`或重复`cut_reason`。必要时可补`cut_reason`；保持、等待或关键变化后仍需观看时，使用一条`continuation_reason`说明对象、仍在发展的内容和结束条件，替代重复的`hold_reason`/`tail_load`。复杂且依赖具体时间的位置可填写`timing_constraints: [{"label":"具体动作或发言阶段","start":0,"end":1.5}]`，时间相对于本镜起点，须完整落在镜窗内；这些区间由作者决定，程序不推断或生成。普通镜头不强制填写动作秒数、观察秒数、小数完成点或同期布尔值，不以观察数值未单列判定没有观看负荷，也不用三项全部相加或取最大值批准真实同期关系。

源记录括号中的动作、表演和发声要求以每镜`source_cues: ["L000019:笑了笑"]`定位，cue逐字引用原括号要求，记录必须属于本镜`source_records`；实际落实在正文或所属声音行中。必要合法省略放顶层`source_cue_omissions: [{"source_record":"L000019","cue":"原cue","reason":"具体合法依据"}]`，由主审核实，不自动批准省略。非对白动作仍保留镜级来源映射。删除重复的全场`source_cue_coverage.status`自证段；程序只核验定位和声明是否齐全，不能证明实际画面已落实。

`unit_organization`使用`policy: prefer_26_30_at_unit_boundaries_v2`，其`units`按正文顺序列`draft_key`。正常26～30秒单元不要求重复时长、分类或达到目标的理由；4～25秒单元填写`reason_code`及具体`reason`。原因代码沿用`scene_boundary`、`selected_range_end`、`merge_exceeds_30`、`incompatible_generation`、`semantic_cut`、`candidate_not_compatible`；存在26～30秒相邻直接组合候选而未选用时，须用后两类说明实际不兼容条件。只读诊断也列出在既有镜间边界移动单元切点的26～30秒时长可能性，这些不表示自然切点已获批准；作者和主审按[单元组织](storyboard-rules.md#单元组织)判断合法性，不只检查原单元能否整体合并。程序不改镜长、不选切点、不重组单元。

`draft_sha256`、`context_sha256`和`source_span`由`bind-trace`依据实际稿件及项目生成。作者将已经写好的v4依据保存到独立JSON后运行该命令，另存到新的侧车路径；命令不改正文、原依据、session或审阅，也不生成观看决定或时间依据。默认输出为`<draft>.authoring.json`，已有路径须另存并通过`--authoring-trace`使用；局部范围传同一个`--span`。单镜/单元时长、声源签名及单元时长分类由正文派生，不再重复维护。绑定后仍须完整`preview`与独立审阅，不能以重新绑定摘要代替实际修改和阅读。高度集中镜长或统一转场确需解释时，保留一次场级`timing_distribution_basis`或`transition_distribution_basis`，不常规复述逐镜依据。

check返回统计、`draft_sha256`、`context_sha256`及`semantic_review: not_performed`。`observations`提供单元时长分布、26～30秒目标摘要、同场来源连续且合计不超过上限的相邻组合候选，以及按镜长分组的摄影措辞统计；候选列表仍是非阻断观察，不自动合并或改稿，但完整作者闸门会要求每个单元提交对应的组织决定，并阻断未证明的短单元或未关闭的可合并候选。摄影统计仅识别镜头开头摄影分句的词面，含混或未识别项明确保留，不能将主体动作、对白或词频当作相机运动的语义证据。preview已包含同版check，同时保存实际排版预览，不占正式编号；不要在前后紧邻重复check。只查错误/摘要时单独check即可。默认覆盖所选完整场景；用户明确局部范围时，check/preview/commit传同一个 `--span START END`，使用现有locate/range所得正文坐标，不猜范围。

`check --incremental`用于作者逐单元写作期间的只读检查：草稿须从当前场的第一条来源开始，按来源顺序包含已写完的单元或完整镜头及所属声音行前缀，不能跳过前部记录挑选容易通过的片段。明确授权局部修改并提供`--span START END`时，从该范围起点检查已写前缀，仍不代表覆盖整个指定范围。标题中的来源范围须对应已写到的位置；末条来源台词尚未写完时，只允许已经写出的片段按顺序逐字拼成该条原话的前缀；前面的台词仍须完整，不能漏词、改字、换声源、倒序或把非末条台词截短。只有末单元处于书写中时可暂不足4秒，完整交付仍须符合4～30秒。未写出的后续范围明确保留为未完成，不能由脚本补齐或据此前缀宣称整场覆盖。该模式不修改作者稿、来源、项目状态或审阅，不生成预览、编号、receipt、成功事件或passed；确定错误由写者处理，语义仍为not_performed。完整成稿必须回到默认完整检查/preview后再送主审和提交，增量结果不能替代它。

完整检查和增量检查均执行逐镜台词容量硬门禁：同镜全部原话片段合并计算`字数÷6 + 逗号/顿号数量×0.1 + 句末标点数量×0.2`，字数按Unicode字母和数字计，逗号/顿号计`，、,`，句末标点计`。！？!?`。镜长小于该最低值时报`DIALOGUE_CAPACITY`；无台词镜头跳过。该值仅证明镜窗是否低于机械下限，不核准自然语速、语气或动作安排，不以计算结果自动配秒、拆镜或回写稿件；实际取舍见[镜长与声音安排](storyboard-rules.md#镜长与声音安排)。

作者闸门返回错误时，相关镜头/单元为`needs_rewrite`。写者先按根因修正实际正文及受影响连接，再更新匹配侧车并完成机械检查及独立复看；未改变的有效阅读按实际差异复用，不增加第三轮逐镜审阅。具体闭环见[集中修订与结论](storyboard-workflow.md#集中修订与结论)。错误未关闭前，不得宣告预览成功、commit或同步画布。

preview默认在项目的 `text-storyboards/previews/` 内按稿件及上下文摘要分配版本路径，返回实际 `output`、`output_sha256` 和 `preview_event_ids`。同一运行中，只有实际产物逐字一致、绑定摘要相同且各场成功事件仍有效时才复用；`preview_reused: true` 表示复用已核验版本，不重新记录成功。损坏、撤销、未登记或其他运行的同名产物均保留，自动选择新路径。需要指定位置时可传 `--output <新预览.txt>`，已有路径仍报错，不能覆盖作者稿、来源、状态、审阅或历史版本。作者交接使用返回的路径和摘要，不凭文件名里的v2/v3推定同版。

预览的解析、渲染、写入及字节核验全部成功后，程序才为实际涉及场次记录 `previewed`，携带作者稿、上下文和输出文件摘要；该事件只证明机械预览完成，不证明语义通过。命令失败或事件写入不完整时必须先读取实际错误和产物状态，不得继续宣告成功，不得把调用预览和手写成功事件串成无条件后续命令。`run_json`仍负责捕获一次CLI的退出码和JSON，不改变成功条件；其结果/日志同样使用新路径。稿件变化后生成匹配的新预览，保留旧预览，不以旧预览配新摘要。命令若提示文件已生成但事件记录不完整，保留文件核对日志，再运行自动路径预览，不手补成功或覆盖文件。

指定修改给check/preview/commit传 `--replace-ids <原ID…>`，保留每个原单元的来源范围与场景。分散的非连续选择各自提交局部草稿，不把中间未选内容混入。此入口可以明确替换选定旧单元，未选历史不迁移；不得仅因升级擅自转换旧稿。范围异常需实际用户选择后，commit再传已有 `--range-confirmation <token>`。

独立主审完成实际阅读并关闭问题后，使用最终check/preview的摘要写一个场级报告。新绑定项目还必须绑定作者闸门摘要、侧车摘要、镜头数量，并列出已实际阅读的全部镜头键；`semantic_review`必须明确为`performed`。主审须逐镜核对进入相位、关键变化完成点、实际同期关系、机械过程是否省略以及完成后的镜尾负荷，不能只依据侧车中的自报秒数。`basis`必须替换为本次实际阅读/处理的说明，不照抄例句：

```json
{
  "draft_sha256": "最终文件摘要",
  "context_sha256": "当前上下文摘要",
  "reviewer": "实际独立审阅者",
  "semantic_review": "performed",
  "authoring_trace_sha256": "逐镜作者侧车摘要",
  "authoring_gate_digest": "逐镜作者闸门摘要",
  "shot_count": 1,
  "reviewed_shots": ["SC0001/u01#1"],
  "scenes": [
    {"scene_id": "SC0001", "status": "passed", "basis": "本次实际审阅及问题关闭说明", "issues": []}
  ]
}
```

scenes按草稿实际场景顺序完整列出。报告不含逐镜passed、字段证据或依赖哈希；摘要不匹配先核对真实变化及影响，不直接刷新摘要造通过。流程详见[场次审阅](storyboard-workflow.md#场次审阅)。

commit使用现有session保存稳定ID、展示名、范围、版本和receipt，正文仍是唯一作者源。有已确认拆分方案时使用`partition-round-v1`分段编号，并冻结计划身份、分段代号、轮次、轮内序号和场内序号；跨分段请求逐段编号，任何单元跨段即报错。preview只展示预计编号，不消费编号；commit在锁内重新核对会话版本后保存。局部续写沿当前轮累计，完整重做新开一轮，旧单元和节点保留；局部修订与同request_id重试继承原身份。没有拆分方案时继续使用共享批次分配器。导出失败用render重建。正式输出必须是新路径，不能覆盖输入/审阅、来源、风格、比例、session或Skill文件，也不放项目内部scenes/canvas状态目录。主代理单写，预览可由写者写其草稿目录。

文字commit不自动排队Canvas。已明确授权的节点任务按[画布流程](canvas-workflow.md)使用支持文本回执的适配入口。旧receipt继续 `director.py render --project <project> --request-id <旧ID>`；混合项目不要用旧director check/commit提交文字单元。

## 事件计时

只记录实际发生的开始、场次里程碑和结束，不为每镜打点，也不手写另一份进度状态。

```text
python <skill>/scripts/storyboard_text_delivery.py event --project <project> --name start
python <skill>/scripts/storyboard_text_delivery.py event --project <project> --name draft_started --scene <SC…> --coverage full
python <skill>/scripts/storyboard_text_delivery.py preview --project <project> --draft <当前场草稿.txt>
python <skill>/scripts/storyboard_text_delivery.py event --project <project> --name review_started --scene <SC…> --coverage full
python <skill>/scripts/storyboard_text_delivery.py event --project <project> --name review_finished --scene <SC…>
python <skill>/scripts/storyboard_text_delivery.py event --project <project> --name complete
python <skill>/scripts/storyboard_text_delivery.py timing --project <project>
python <skill>/scripts/storyboard_text_delivery.py invalidate-event --project <project> --event-id <错误阶段事件ID> --note <事实与撤销原因>
```

任务开始记一次start、全部工作结束记一次complete，中间按实际需要记少量事件。起草起点应在读取当前场原文、构思与写稿之前，不能到落盘前才记开始并称完整创作耗时；若已迟记，使用 `--coverage partial` 并用note说明，未知起点不回填。`full`是记录者声明，工具不证明其真实性；未声明的旧记录显示unspecified。preview成功自动记各场previewed，禁止使用event手工记录该成功名称；修订或合并预览在没有待闭合起草窗口时只作产物里程碑，不制造缺少起点的第二段起草计时。commit自动记本批delivered，不能把每批交付当全任务结束。

同一event日志同时承担过程问题和计时，不另建状态：实际修订使用 `revision_started/revision_finished`，复看使用 `recheck_started/recheck_finished`，工具修复使用 `tool_repair_started/tool_repair_finished`。需单独测交接工作才使用 `handoff_started/handoff_finished`；preview到主审开始的间隔已自动派生，不必重复打点。重复轮次按先后配对；同场同阶段存在并行或交错工作时给各窗口相同起止 `--activity-id <唯一标记>`。开始端声明coverage。

实际问题在对应开始事件或单独issue事件中用 `--issue-kind author_revision|review_revision|tool_repair|confirmation --note <实际说明>` 分类，分别表示作者自修、主审退回、工具修复、必要确认；不要把所有检查算作返工。旧revision/tool_repair开始事件的note仍可展示，但明确标为旧事件名分类，不能据此自动判断缺陷已关闭。结束事件只说明测量窗口结束，不充当语义通过。

每条新事件有event_id；旧日志的行在读取时派生稳定legacy ID，不重写原字节。发现错误阶段事件时，先从timing结果定位准确ID，再用invalidate-event追加带原因的撤销记录。它只撤销阶段计时证据，不改稿件、审阅结论、正式回执、start/complete或画布状态。自然语言note中的“误记、替代”不会自动改变统计，也不能直接编辑/删除旧行。需要替代成功预览时，应实际生成匹配预览，由程序记录当前成功时刻；未知原始成功时刻不回填。该更正接口不授权修改暂停的生产项目日志。

timing只读派生最近start至首个complete的总墙钟、按场/阶段窗口、交接间隔、问题说明、重叠并集与缺失端点/覆盖范围提示；原始事件及撤销原因均保留，窗口与交接间隔只使用仍有效的事件。完成后追加的明确撤销仍影响对应阶段派生值，不扩大原任务墙钟；旧无产物证据的记录只作历史记录，不升级为已验证成功。缺少任务或阶段起止时相应耗时未知；缺口含未打点工作，不称空转。嵌套工具修复已包含在修订内，阶段之和不等于总耗时，不称纯模型时间。旧资产交接计时继续使用pipeline_metrics；同次画布任务可能重叠，分开展示，不把云端时间加到分镜总墙钟。

