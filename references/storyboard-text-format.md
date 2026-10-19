# 纯文本分镜作者格式

新草稿以UTF-8 TXT为唯一作者源，格式名为`scene-text-v1`。文件只包含下列单元和空行，不支持正文外备注、Markdown围栏、规划表、隐式省略号或未识别行。程序遇到未知行立即报错，不静默丢弃。标题、用户已保存风格与固定抬头由交付程序生成，作者不手抄。

```text
## SC0001 / u01 / L000003-L000005
人物：@{CHAR0001}；
场景：@{SET0001}；时间：白天；天气：未明示；
人物站位：@{CHAR0001}站在门边，朝向窗外。
·00秒-03秒[镜头1-叮嘱]：（@{CHAR0001}的中景；转场：硬切）镜头向右平移，人物在画面左侧、门框在后景，人物与门框清晰，窗外柔光照亮侧脸；@{CHAR0001}停在门边。音效：无。
声音[L000004,0.2-2.8]：@{CHAR0001}说（压低音量）：“别动，等我回来。”
·03秒-05秒[镜头2-等待]：（@{CHAR0001}的近景；转场：硬切）侧后方固定机位保持视线关系，肩部在前景、窗框在后景，窗框清晰，沿用窗外柔光；@{CHAR0001}看向窗外。音效：无。
```

示例ID、原话和时码仅说明语法，实际生产必须取本项目已绑定来源和台账。`SC0001`是剧情场次，`u01`是作者在本场内保持稳定的单元键；组合`SC0001/u01`不得在同一草稿重复。作者键不代替交付程序分配的内部ID和批次展示名。源范围两端是`session.json.records`真实记录ID，包含端点，按记录顺序连续；标准剧本的标题或空行不属于records，记录ID数值不连续不构成缺漏。

人物行只用`@{CHAR…}`稳定ID，逗号、顿号或分号分隔，末尾一个中文分号；无人写`人物：无；`。名单含出镜及发声人物。场景行用来源场次对应的`@{SET…}`，多个布景按来源场次顺序排列，同一SET只写一次；时间、天气和末尾中文分号不能省略。人物站位另占一行。道具不增加名单行，在镜头或站位中使用`@{PROP…}`即自动收集。

每镜一行，时码为两位整数秒，镜头编号从1连续递增，单镜1～6秒、单元4～30秒；首镜从00秒开始，前后无空隙或重叠。镜数无额外数量门槛，组织原则见[单元组织](storyboard-rules.md#单元组织)。旧项目`entry.min`或`entry.durations`保存的时长偏好若与当前单元不符，检查结果列出诊断，不要求作者补写原因表，也不把新文本格式允许的4～30秒再次收窄。景别为大远景、大全景、远景、全景、中景、中近景、近景、特写之一，主体使用稳定资产引用。镜头行必须有转场、可见内容及一个末尾`音效：…。`字段。摄影、构图、景深、光照、表演、动作过程和必要承接直接写入最终可见正文，不另填purpose、动作计划、状态效果、依赖图或重复声音计划。摄影和表演的实质规则见[统一分镜规则](storyboard-rules.md)；程序不通过搜索术语来判定其合格。

实际台词只写在其所属镜头之后的`声音[...]`行中，可有多行；不得在镜头正文重复添加`：“台词”`。时码相对于当前单元，支持最多三位小数，整个片段必须落在所属镜头之内，按发声顺序排列且不能重叠。声音类型必须与绑定记录一致：`说`、`内心想法`、`画外音`、`旁白`。可选中文括号只写本片段必要的声音表现，不引入资产引用；没有必要就不写括注。源记录明确标为非人物播报时，用原声源名和`非人物播报`，例如`声音[L000007,0.1-2.5]：电话提示音非人物播报：“原话”`，不建立CHAR、不加@。不能凭设备名称推断播报类型。

台词引号内部逐字保留来源文本，包括标点、空格、原有引号和字面`@`、`#`、`@{…}`。同一记录的所有片段按顺序拼接后必须完全等于该记录原话。长句可以按真实语义气口拆到多个镜头，也可以拆到相邻单元：前一单元范围末端和后一单元范围起端均写同一条台词记录，各单元实际写出各自片段，程序检查整句并计算不重叠的内部正文范围；边界标点留在前段，保持与既有正文坐标切片一致。非人物播报同样适用。默认交付不得只写长台词中间的一部分；修改已交付单元时，交付层可传入该单元准确的`source_span`，此时只要求逐字等于已绑定来源切片，可独立修改拆句中的一个单元。跨镜语气不会自动从前一个标记继承，需要维持的声音指导直接写在对应实际片段。

同一条长动作记录需要跨多个单元时，相邻单元可以共享这一条边界动作记录；各单元内部正文范围保留该动作记录完整区间，因此可以重叠，但只允许重叠这一条完整边界动作记录。不得按字符比例制造动作分界，也不得为此扩大单元时间上限。重复来源引用表示继续呈现同一个原事件，不授权把该事件重新发生一次；实际渲染审阅必须核对动作相位、接触、结果和前后承接，发现重复事件应修改可见正文。跳过中间来源记录、重叠多个记录或倒序范围仍会报错。

所有非对白正文的`@`必须是稳定资产ID引用；禁止SC图片引用、未列人物、不属于来源场次的资产及未知ID。渲染时需要图片的资产变成`@台账完整名称 `，名称末尾补英文空格；`visual_mode: prompt_only`人物变成普通完整名称。原台词和非人物声源名称不进行资产替换，也不传给Canvas引用转换回调。

交付程序读取当前绑定项目，先调用`director.load(project)`核验来源、台账与风格文件，再调用：

- `parse(text, state, source_span=None) -> list[dict]`：校验作者文本并派生元数据，含`draft_key`、`source_records`、`span`、`duration`、`main_scene`、`scene_ids`、`character_ids`、`set_ids`、`prop_ids`、`asset_ids`及最小镜头缓存。`source_text`保存该单元唯一规范作者文本；镜头与台词字段只是可重新生成的缓存，不是第二份作者输入。可选`source_span=[start,end]`来自交付层已确认范围或被选中旧单元的确切范围，以`director.portion`裁剪原始记录再校验；作者文本必须完整覆盖此范围，不得任意缩小。它不修改绑定state或原剧本。
- `validate(state, units) -> dict`：从`source_text`重新解析，拒绝被直接修改或失效的派生字段，返回单元数、镜头数、时长、范围和检查状态。`id/display_name/style`由交付层赋值，不纳入作者字段对比。
- `render_unit(state, unit, reference_text=None) -> str`：从作者文本生成产品提示词，用已经保存的`style`及已分配的`id/display_name`，沿用现有固定抬头常量。作者单元头、来源ID、声音时间标记不输出；实际声源、语气和原台词嵌回镜头末尾音效之前。可单独重导出已检查的拆句单元。

`asset_ids`含所有身份引用，包括prompt_only人物；Canvas调用者须从图片边中排除prompt_only。这里只依赖Python标准库和本包director的既有常量/基础函数，不加载旧规划、状态、依赖、逐镜审批或关键词语义门。

结构通过的返回值始终含`semantic_review: not_performed`。范围覆盖说明作者声明覆盖了哪些来源记录，不证明每个动作已经正确拍出；真实审阅仍须核对实际渲染文本的剧情保真、镜头目的与摄影、遮挡和面具可见性、动作路径与接触、状态承接、声音表现、长句可说完程度及原文没有明确写出的新增事件。不要把程序结果当作这些语义判断的自动批准。

## 绑定项目与范围命令

下列命令中的`<skill>`和`<project>`均替换为绝对路径，使用环境当前可用的Python。新文字分镜所需语法和命令集中在本文件；不必再为这些命令读取runtime.md。

```text
python <skill>/scripts/director.py status --project <project>
python <skill>/scripts/director.py phase --project <project> --value range
python <skill>/scripts/director.py locate --project <project> --anchors <anchors.json>
python <skill>/scripts/director.py range --project <project> --start <起点整数> --end <终点整数>
```

先读status；仅已绑定项目从其他阶段续接分镜时设置phase为range，不重复初始化。起草前定位范围；anchors.json只含`{"start":"用户范围起句原文","end":"用户范围止句原文"}`，引句按标准来源原样填写，不能照抄示例。locate返回正文坐标与历史异常；已有准确坐标时可用range核对。只有唯一明确且异常已按工作流处理后才起草，未确认的候选不能自行取第一项。定位规则只见[范围定位与历史检查](storyboard-workflow.md#范围定位与历史检查)。

## 文本分镜接口

入口为 `scripts/storyboard_text_delivery.py`，内部调用文本解析器；不运行旧authoring gates、review_gate、逐镜证据或效果编译。

```text
python <skill>/scripts/storyboard_text_delivery.py check --project <project> --draft <draft.txt>
python <skill>/scripts/storyboard_text_delivery.py preview --project <project> --draft <draft.txt> --output <新预览.txt>
python <skill>/scripts/storyboard_text_delivery.py commit --project <project> --draft <draft.txt> --request-id <本次唯一ID> --review <场级审阅.json> --output <新交付.txt>
python <skill>/scripts/storyboard_text_delivery.py render --project <project> --request-id <已提交ID> --output <新导出.txt>
```

check返回统计、`draft_sha256`、`context_sha256`及`semantic_review: not_performed`。`observations`提供单元时长分布、同场来源连续且合计不超过上限的相邻组合候选，以及按镜长分组的摄影措辞统计；均为非阻断观察，不自动合并、改稿或判定摄影优劣。摄影统计仅识别镜头开头摄影分句的词面，含混或未识别项明确保留，不能将主体动作、对白或词频当作相机运动的语义证据。preview已包含同版check，同时保存实际排版预览，不占正式编号；不要在前后紧邻重复check。只查错误/摘要时单独check即可。默认覆盖所选完整场景；用户明确局部范围时，check/preview/commit传同一个 `--span START END`，使用现有locate/range所得正文坐标，不猜范围。

指定修改给check/preview/commit传 `--replace-ids <原ID…>`，保留每个原单元的来源范围与场景。分散的非连续选择各自提交局部草稿，不把中间未选内容混入。此入口可以明确替换选定旧单元，未选历史不迁移；不得仅因升级擅自转换旧稿。范围异常需实际用户选择后，commit再传已有 `--range-confirmation <token>`。

独立主审完成实际阅读并关闭问题后，使用最终check/preview的摘要写一个场级报告。示意结构如下，basis必须替换为本次实际阅读/处理的说明，不照抄例句：

```json
{
  "draft_sha256": "最终文件摘要",
  "context_sha256": "当前上下文摘要",
  "reviewer": "实际独立审阅者",
  "scenes": [
    {"scene_id": "SC0001", "status": "passed", "basis": "本次实际审阅及问题关闭说明", "issues": []}
  ]
}
```

scenes按草稿实际场景顺序完整列出。报告不含逐镜passed、字段证据或依赖哈希；摘要不匹配先核对真实变化及影响，不直接刷新摘要造通过。流程详见[场次审阅](storyboard-workflow.md#场次审阅)。

commit使用现有session及共享批次分配器，保存稳定ID、展示名、范围、版本和receipt，正文仍是唯一作者源。重试同request_id不重复编号，导出失败用render重建。正式输出必须是新路径，不能覆盖输入/审阅、来源、风格、session或Skill文件，也不放项目内部scenes/canvas状态目录。主代理单写，预览可由写者写其草稿目录。

文字commit不自动排队Canvas。已明确授权的节点任务按[画布流程](canvas-workflow.md)使用支持文本回执的适配入口。旧receipt继续 `director.py render --project <project> --request-id <旧ID>`；混合项目不要用旧director check/commit提交文字单元。

## 事件计时

只记录实际发生的开始、场次里程碑和结束，不为每镜打点，也不手写另一份进度状态。

```text
python <skill>/scripts/storyboard_text_delivery.py event --project <project> --name start
python <skill>/scripts/storyboard_text_delivery.py event --project <project> --name scene_reviewed --scene SC0001
python <skill>/scripts/storyboard_text_delivery.py event --project <project> --name complete
python <skill>/scripts/storyboard_text_delivery.py timing --project <project>
```

任务开始记一次start、全部工作结束记一次complete，中间按实际需要记少量事件。commit自动记本批delivered，不能把每批交付当全任务结束。timing以最近start划分当前运行；缺少起止则总耗时未知。事件包含阅读、等待、修订和提交等墙钟时间，不称纯模型时间，阶段重叠不相加。旧资产交接计时继续使用pipeline_metrics。

