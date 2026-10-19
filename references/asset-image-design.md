# 资产美术设计与图片提示词

本文件统一维护事实与制作设计边界、人物/群像/场景/道具版式、作者字段、共享基线与分组协议、编译验收和局部更新。资产筛选、身份/LOC/SET归并和形象启用见[资产台账](../modules/screenplay-standardizer/references/asset-ledger.md)；阶段顺序与交接关闭时点只由[资产主流程](asset-workflow.md)维护。设计只准备 Gpt-image-2 图片提示词，不自动生图，不改变剧情、稳定ID、制作名称或形象启用。命令中的`<module>`指本Skill根目录下`modules/screenplay-standardizer`，执行时使用绝对路径。

按需定位：[事实与风格](#事实设计与风格) · [初稿约束](#初稿前的短约束表) · [作者输入](#制作设计与图片提示词) · [字段职责与继承](#字段职责与继承) · [个体人物](#个体人物) · [群像](#群像) · [场景](#场景) · [道具](#主要道具) · [共享基线与分组](#共享制作基线与设计分组) · [编译验收](#写入编译与验收) · [局部更新](#设计依赖与局部更新)。

## 事实、设计与风格

`visual_facts`、`personality_facts`保留原文明示事实与出处，未知事实留空；完整美术方案另存`visual_design`，标注`attribution: 制作设计`及具体上下文依据。原文缺外观时，依据全剧时代、环境、实际身份、人物关系、可见动作及已确认风格补足必要外观，不把“外观未明示”留作最终图片输入。原文已确认的外貌、年龄、服装、数量、布局、功能和状态是硬约束，由作者逐项核对；程序不理解这些语义。

设计可补足对象实际具备的辨识特征、轮廓、表面、色材、构造及空间关系，不能新增姓名、职业、阵营、身世、能力、剧情道具、隐藏线索或未知文字。伤疤、徽记、铭文等叙事暗示须有原文或用户确认依据。人类、动物及其他形态只使用适用的身体、衣饰和空间特征，不为满足模板添加器官、通道或房间结构。示例只说明字段，不授权向当前剧本添加相同事实；制作设计不回写标准剧本。

设计与编译使用本项目已由`director.py style`保存的非空单行`video-style.txt`：只去除文件首尾空白，保留内部空格和原文，不翻译、缩写或追加风格。资产本色、环境受光与最终调色分开；人物板用中性柔和照明看清肤色和材质，场景光照服从已确认环境及可读性。更换风格只处理用户指定对象，既有设计、图片及绑定不静默重做。

参考图固定已确认的身份、形制与空间结构，不固定后续剧情中的表情、姿态、动作进度和可变环境状态。分镜按当前时点及实际可见范围使用参考，动态状态仍归[连续性规则](continuity-state.md)。只有文字设计时不得声称已建立图像一致性；有用户提供或已采用的真实基础图时，后续造型注明保留的身份及允许变化项。

## 初稿前的短约束表

每项初稿从台账、逐场观察和候选处置汇总下表，随设计交接。引用现有资产/观察/候选ID、事实路径与证据，不另建可独立维护的事实副本。待核观察保持待核，影响设计的未决项回原审阅入口解决；无依据外观保留为明确标注的制作设计。

| 约束 | 初稿及实际编译结果须落实的内容 |
| --- | --- |
| 事实 | 本资产及当前版本的原文明示属性、事实主体和证据。 |
| 交互 | 归入本资产的候选及处置理由；逐项把原动作需要的接触部位、开合/摘戴行程、身体与物件净空、支撑与相对尺度落到已有设计字段。不建独立PROP不等于删除交互要求。 |
| 当前可见范围 | 按本版服装、佩戴和遮挡区分可见与被遮部位；开口大小、连接和材质须共同满足覆盖边界，共享身体特征不要求每版全部展示。 |
| 继承 | 身份锚点、基础服装、关联PROP、LOC/父SET的来源及本版允许变化项。 |

可用`design_inputs.py`汇集既有事实、被吸收候选原记录及继承引用。它是只读派生索引，不自动提炼交互结论或确认pending；使用前check，过期从当前输入重建，不编辑索引或刷新旧摘要。短约束与语义判断写入现有审阅记录并引用`binding_sha256`，索引current仅表示输入有效。

```text
python <module>/scripts/design_inputs.py prepare <标准剧本.txt> <source.json> <候选台账.json> --style-file <video-style.txt> --output <新索引.json>
python <module>/scripts/design_inputs.py check <新索引.json>
```

已有共享设计加`--shared-design <当前共享设计.json>`；选定范围用`--asset-id CHAR0001 SET0001`，自动带入既有依赖闭包，省略则索引全部图片资产。输出保留`facts`、`absorbed_candidates`原记录及继承引用。check重验来源文件、风格、可选共享设计、规则/编译器摘要和完整内容。

写初稿前，将原文相邻动作及参与者状态转成必须成立的物理关系，再选择具体结构、尺度与色材。主代理的交接也只传这些已证实的约束和允许设计的空间；不能把某个动作词直接升级为唯一设施、分区、姿势或路径。未被原文排除的实现方式保留选择余地，制作选择不能反过来要求新增人物移动、动作或剧情事实。

在同一次设计中核对整段交互能否同时成立：接触部位可用，开合或摘戴行程不受阻，各参与者的相对高度、支撑及可达位置兼容，相邻动作之间有连续通路，遮挡开口符合可见范围。只展开当前来源需要的关系，采用能支持整段动作的必要结构，不为解释一个动作额外指定另一处空间或工程尺寸。

约束引用既有OBS/CAND/资产事实，只在设计`basis`或现有审阅记录写其实现字段及必要判断，不另制平行检查表或逐项通过回执。字段分配与继承按下节执行。编译后沿原约束实读实际提示词，集中修订冲突；`basis`声称支持动作不等于输出已实现。

## 制作设计与图片提示词

`design`只接受已核实的schema 3台账。旧schema 2沿原显式迁移流程处理；旧schema 3可在用户授权范围内显式设计，新政策字段不自动补入。全量输入精确覆盖全部现用图片CHAR、SET、PROP，包括只供版本继承的基础SET；`prompt_only`群像保留剧情索引但不参与图片设计、TXT、节点和缺图统计，其旧生图派生字段只在显式design时清除。设计数组不增删资产，脚本不推断外观、不调用模型。

作者JSON顶层保存`image_design_version`、`identity_anchors`和`designs`。新设计默认`gpt-image-2-v2`；已有明确v1按原编译器校验并保留提示词字节，只有显式重新设计并选择v2才升级，需补齐适用的新字段。输入可显式选`gpt-image-2-v1`维持原行为，但新遮面资产不得进入v1露面模板。

每项设计均有`asset_id`、固定值`attribution: 制作设计`、具体`basis`和`reviewed_source_facts: true`。最后一项表示作者确已审阅原文约束，不是程序语义批准。所有视觉文字字段为非空单行；ID关系放结构化字段，视觉文字不泄露后台ID、不写“同SET0002”。

### 字段职责与继承

先确定一段描述属于对象本体、具体造型、使用关系、场次状态还是图片展示，再放入字段；不能靠末尾排除语抵消放错层的内容。共享字段应在全部实际使用方中成立，按消费者核对继承结果，而不只检查提供方单独看是否合理。

| 对象 | 作者字段及归属 |
| --- | --- |
| 稳定人物身份 | `identity_anchor_id`引用共享`age/body/face/hair`，只承载稳定年龄感、体型比例、面容和发型。当前所在环境、坐姿、屈膝、受力、湿润和剧情表情不进入锚点。来源属性采集和归属见[逐场观察](../modules/screenplay-standardizer/INSTRUCTIONS.md#事实粒度与记录格式)。 |
| 具体人物造型 | `styling`描述本版适用衣饰；真实外观变化用已支持的覆盖字段。基础/换装/佩戴各版的事实来源、可见范围及差异见[个体人物](#个体人物)。 |
| 图片群像与共同发声集合 | `group_wardrobe`和`group_members`，不以单人锚点/衣装代替多人设计，见[群像](#群像)。 |
| 空间本体 | SET的`space/materials_palette/furnishings/lighting`承载结构、固定陈设及参考受光；分组中的版本SET用[空间继承协议](#空间版本继承)。具体人物站位、行走过程及当场变化不固化为空间结构。 |
| 道具本体 | PROP的`structure/materials_palette/scale/state`描述结构、色材、相对尺度及本体参考态。`state`可写完好、磨损和部件开合等可见状态，不写被谁持有、是否佩戴或当前使用动作。 |
| 使用关系与场次状态 | 佩戴的贴合/遮挡用`covering`及`visible_identity`，发生时序和持有人由台账轨迹、实体记录及分镜承接。它们不回灌到共享身份或PROP本体；同款外形不合并实体。 |
| 图片展示 | 版式、背景和照明由分类编译器及对应展示字段负责；PROP展示选择用`product_layout/layout_basis/image_presentation`，不混入会被人物继承的本体字段。 |

关联佩戴版继承PROP的四个本体字段，因而这四项既须支持单品图，也须与全部已登记使用关系兼容；单品图的无人展示属于图片版式，不定义物件与使用者的关系。原文确有不同本体状态时，按现有版本和适用范围处理，不把互斥状态写进共用字段。具体继承接口见[遮面与关联佩戴道具](#遮面与关联佩戴道具)，不新增JSON字段。

```json
{
  "image_design_version": "gpt-image-2-v2",
  "identity_anchors": [
    {"id":"identity-a","age":"成年男性","body":"修长体型","face":"偏长脸、平直浓眉、鼻梁平直","hair":"黑色短发"}
  ],
  "designs": [
    {"asset_id":"CHAR0001","attribution":"制作设计","basis":"沿用原文明示的灰蓝夹克，补足活动所需版型","reviewed_source_facts":true,"identity_anchor_id":"identity-a","styling":"灰蓝哑光棉质短夹克，肩背留有活动量，深灰直筒长裤，黑色低帮布鞋"}
  ]
}
```

示例面容与未明示细节均是制作设计，不是可套用的剧情事实。完整初次设计须覆盖当前全量图片资产；代表样本可只提交所选资产及其必要依赖。

## 个体人物

### 16:9头部特写与全身三视板

每个需图片的个体形象使用一张纯白横向16:9参考板。具备相应人体结构时采用以下固定布局；动物和遮面分支另见本节，不向其他形态强加人体结构。

- 左侧完整1/3宽度、贯穿高度：超大正面面部特写，平视前方，面部充分占区，保留必要头部轮廓和颈肩，五官与发型清楚。
- 右侧2/3：依次等距并排完整全身正视、明确90度侧视和后视图，三图等高、头顶脚底对齐，头发、手脚和衣摆完整，彼此分开。
- 四视同一角色、身体、发型、服饰及当前状态；左侧面部大于全身图头部。全身自然静态站姿，表情中性，白底无剧情环境、文字标签或水印。

单人提示词从硬约束到继承事实都只描述当前角色及造型，不夹同场人数、其他角色或共同动作。多人原句原样保留作证据，事实摘要提取对该角色成立的单人状态；关联道具仅继承物件本体特征。不能混入多人描述再靠末尾排除语抵消。

先确定稳定形态、轮廓和少量辨识特征，再设计适用衣饰、材质及配色。人类可具体描述年龄感、身形、脸型、眉眼鼻唇与发型；常同框角色通过可见特征区分，不强求同一审美比例。性格与戏剧功能可辅助设计，瞬时表情和动作不固化为外貌。

`styling`说明影响轮廓和动作的层次、版型、连接、色材与既有新旧状态，适用时展开领口、袖口、鞋袜；不用“高级”等评价替代可见属性。职业称谓不能独自证明制服、徽记、身体习惯或识别物。存在服制/时代混淆风险时写清有依据的可见特征；便装或无制服照实际约束处理，无衣物动物不新增衣饰填字段。

### 身份锚点与造型差异

同一实际身体的不同造型共享锚点。已登记`appearance.identity`与`incarnation`相同的CHAR共享`identity_anchor_id`，不同已知身体不能共用；没有appearance的简单/旧人物由作者审阅，不从名字拆分猜身份。明确进入另一身体时建立独立锚点，不能跨世次用覆盖字段借用旧身体。换装通常只改`styling`，不提前改变形象启用区间或新增人物。

有依据的真实外观变化可用`appearance_overrides`覆盖`age/body/face/hair`，并以`variant_differences`文字数组逐项说明差异与保持项；这不创建CHAR或替代形象启用规则。覆盖`face/body`还须在`override_source_facts`中把相应字段映射到该资产已有、已验证的`visual_facts.text`。程序能核对引用，变化是否被事实证明仍由作者审阅。

### 遮面与关联佩戴道具

遮面板保留分区，左侧改为保持佩戴状态的头部特写，右侧三视也始终佩戴同一遮挡物。不能为露身份摘除、透明化或强露被遮五官/头发。锚点保留真实面容与发型，当前提示词只显示实际可见部分。

对辨识、轮廓保持或摘戴情节有实际作用的遮挡物，按台账`appearance_variant`登记对应造型；制作名保留角色、世次及必要服装限定，再加明确佩戴状态，展示号不进入name。始终佩戴且拆分无制作价值时可直接设计原形象。佩戴版继承对应基础人物的`identity_anchor_id`、`styling`及已审`appearance_overrides`，不得声明不同值；覆盖事实由基础记录提供。可拆下、手持、交换并承担核心操作的遮挡物仍按道具规则审阅，佩戴图不代替实物状态。

| 佩戴依赖 | `covering` | `visible_identity` |
| --- | --- | --- |
| 未关联独立PROP | 非空单行，展开形制、色材、佩戴方式及遮挡范围，不只写“戴口罩”。 | 写实际外露且与锚点相符的特征，如具体眉眼/发型；全头套说明脸与头发均遮挡并保留外露体型等。 |
| `appearance_variant.prop_asset_id`已关联PROP | 只能为`{"fit":"此人的贴合与佩戴关系","coverage":"实际遮挡范围"}`；不加色材/形制字段，不用自由文本。 | 同上，不重新要求画出被遮的完整五官。 |

关联PROP是遮挡物外形的唯一设计来源，继承边界按[字段职责与继承](#字段职责与继承)；多个角色保留各自身份与衣服。PROP事实/设计变化使相关人物板摘要过期，重编读取本次PROP设计。程序拒绝重复结构字段并校验摘要，作者仍须核实`fit/coverage`未暗含改色、换形等越界内容。

### 动物与模板适用性

v2动物个体显式登记`asset_type: animal`与`species`，锚点以`age/body/face/hair`记录适用体态、头部和毛羽/皮肤；`styling`仅填已审配件或无衣物状态。板面左1/3为动物头部特写，右2/3为正面/90度侧面/背面的完整自然身体，不调用人类衣装站姿模板。物种变化使设计过期；未登记普通人物、既有v1及未选项目不因动物名称自动改型。

现有动物模板仍含耳部、肢体及尾部，SET模板仍含出入口与人物通道；不具备这些结构时，在真实预览中指出具体模板冲突，未解决的该资产不能判定为可用提示词，不影响其他已通过资产。不得添加器官/通道或改分类适配模板；本规范不新增编译类型或改变旧版字节兼容。

个体提示词由已选风格、固定布局、当前可见身份、版本服饰/状态、四视一致、中性光照及完整身体组成。

## 群像

需图片的背景群像须通过台账对应政策的入选审阅；`prompt_only`不设计图片。原文明示共同发声的`character_kind: speaking_ensemble`保留集合声源、完整对白及独立审阅，始终制作图片参考，不改成无台词背景群像。两类图片均使用以下多人板。

纯白16:9横板呈现多个外貌可辨的样本，每个只出现一次完整正面全身，彼此分开、不重叠遮挡。适用人体结构时平视、静态站姿、完整头脚；其他形态核验真实模板支持，不新增身体结构。保留身高体型差异，不拉成等高；不用单人面部特写/三视图或“无其他角色”限制。后台样本编号不绘入图中。

先核对已确认的世界设定、形态、地区、年代、年龄、性别和群体身份边界。统一服制须有原文或已确认设定依据，明示等级/任务差异用混合服制，其他情况按制作需要选择不同搭配。不从群体称谓推定制服，也不靠新增身份或叙事标记制造差异。

| v2字段 | 要求 |
| --- | --- |
| `group_wardrobe` | `mode`为`varied/uniform/mixed`；`era_region`写时代地区和群体身份约束，`basis`说明服制依据；uniform/mixed另填`shared_style`。 |
| `group_members` | 至少2个样本，每项有`sample_id/age/body/face/hair`；varied/mixed另填个人`styling`。uniform直接继承shared_style，个人styling可省略或与其完全一致。 |

外貌差异须可见，不能仅换编号、年龄词或衣服；样本数只需展示可用造型并保持可读，不是剧情人数或实体身份。图不确定队形、站位、动作进度、共同发声人数或每人的独立台词，同款样本复用也不证明各场是同一批人。需辨认特定成员时回台账核实是否单列人物，沿原称谓及身份依据，不从群像设计新增姓名/关系。

## 场景

每个SET为自足的无人物单画面空间主视图。按实际空间形态、剧情用途和风格确定建筑/地形、材质配色、层次、主要活动区、连续通路和稳定参照物关系。出入口、建筑构件和家具仅在适用时描述，自然环境不强加房间；相对尺度足够时不编工程尺寸。

| 字段 | 内容边界 |
| --- | --- |
| `space` | 先确定实际稳定参照作为观察基准，再说明方向、纵深、主要活动区及动作通路；影响动作时展开高差、狭窄或遮挡。反打不改变物理关系。 |
| `materials_palette` | 实际材质、本色与表面质感，受光不变成新固定颜色。 |
| `furnishings` | 已确认/合理设计的固定陈设及同一位置体系，不侵占原动作所需空间；是否独立PROP仍按台账审阅。无家具空间不为填字段添家具。 |
| `lighting` | 实际光源方向、色温、亮度、受光和必要暗部可读性；不预设窗/灯，不写“单幅、无人、主视图”或构图指令。 |

例如已有入口、侧窗、长桌的室内，可用“从主入口面向后墙时，侧窗在右墙，长桌在窗下，入口至桌前留连续通路”表达基准及关系；此例不授权给其他空间添加这些物件。前中后景层次及装饰密度服务活动区可读性。

同LOC/SET持续复用空间锚点，日夜、天气、拍摄方向不增SET。decor/temporal只继承仍有效共性并加入已证实变化，时间标签不证明破败或翻修。父链与共用事实按台账规则审阅；分组中的版本继承还须满足[空间版本继承](#空间版本继承)。场景提示词组合为已选风格、建筑/地形及当前状态、空间关系、固定陈设、色材、观察层次/可读光照和无人物单画面。

## 主要道具

只有台账已确认入选的PROP设计独立图片，具体复用/核心连续性门槛及反向覆盖审阅见[资产台账](../modules/screenplay-standardizer/references/asset-ledger.md)。未建PROP仍须在所属人物/场景设计或既有动作描述中承接必要交互；设计中发现缺少核心参考时回资产审阅，不自行增删资产。穿戴物是否独立参考取决于可拆卸、交换、操作和实际参考需求，不能只因“穿戴”排除。

形制、相对尺寸、功能部件、材料、颜色、表面纹理、磨损和当前状态须支持原有抓握、开合、传递、乘坐等动作。`structure`写清正反面/信息面/操作部件相对主体的位置及必要运动关系，`scale`给支持操作的相对尺度，`state`只写当前参考态；未知密码、文字、图案和线索不补造。共享外形不合并物理实体或持有人，静态图不画剧情动作。

默认为居中完整单件，纯净背景、柔和均匀光照、边缘完整留白，不带人物、手部或剧情环境。立体物选择能读清关键部件的角度，扁平文件选择可读表面的视角；有依据的本体文字保留，不加说明标签。

### 物体属性与图片展示分离

`structure/materials_palette/scale/state`仅为本体属性，人物佩戴板继承这四项；`basis`保存设计判断。以下展示字段仅作用于PROP图片，不继承到CHAR：

| v2字段 | 用法 |
| --- | --- |
| `product_layout: multiple_objects`及`layout_basis` | 原资产确需整组参考时显式选择，layout_basis写组合证据、已知数量和展示范围；各实体完整分开，取代该图“单件”要求。默认同款单样本不填，样本与剧情数量区别写入basis；未知总数不由样本推定。 |
| `image_presentation: {"view":"front_left_three_quarter"}` | view只接受`front/front_left_three_quarter/front_right_three_quarter/top`，省略保持原默认。背景、无人物/手等固定构图由编译器生成。 |

程序不从名称、数量词或提及次数自动选组合版式；未选资产、人物及既有v1保持原编译。头套的`scale`可写与已确认角色头部适配的开口和动作活动量，不写“本图只展示一个样本、不带人头”；车辆角度放image_presentation，不放scale。跨资产关系用ID字段，文字写实际色材结构。

草稿保存即运行`python <module>/scripts/design_scope.py <设计草稿.json>`，无需等全部字段/组完成。v2的design-check/design也检查本批实际继承的字段，拒绝已知构图混入和后台ID泄露；允许“头部开口”“单手握持”等物理描述。检查不改事实、ID、旧回执或旧编译摘要，也不能穷尽自然语言冲突，组合预览仍须实读。道具提示词由风格、本体形制/比例/结构、色材/表面/状态、完整展示、照明及必要保持项组成。

## 共享制作基线与设计分组

有共享依赖或大量分组设计时，用`modules/screenplay-standardizer/scripts/design_workspace.py`冻结制作基线；完整候选台账仍保留所有未设计资产。阶段顺序、代表样本及交接时点见[资产主流程](asset-workflow.md#设计与代表组验收)，本节只规定拥有权、数据与验收协议。

主代理统一审阅身份、服装连续性、LOC/SET、群像/道具取舍及佩戴关联，单入口写正式台账。后台设计者只写本组JSON，不改共享基线、台账、其他组、正式TXT或画布。每个尚未冻结的图片ID恰有一个拥有者；未冻结的同身份造型、基础CHAR/佩戴CHAR/关联PROP依赖须同组，或先冻结共享依赖后重建计划。SET版本以冻结父空间为依赖，不能与未冻结父SET并行。只有责任互斥、共享依赖冻结的组才可按宿主并发预留运行；本模块不分派代理或管理并发。

所有命令仅向stdout输出JSON，不改输入/正式台账；输出保存为独立草稿，重定向不得指向输入文件。

```text
python <module>/scripts/design_workspace.py freeze <script.txt> <source.json> <ledger.json> <shared-design.json> <video-style.txt>
python <module>/scripts/design_workspace.py plan <baseline.json> <groups.json>
python <module>/scripts/design_workspace.py verify-group <baseline.json> <plan.json> <group-result.json> --group-id character-a
python <module>/scripts/design_workspace.py merge <baseline.json> <plan.json> <character-a.json> <sets.json> <props.json>
```

`freeze`固定剧本、source、完整台账、共享设计输入、风格五个原文件的绝对路径与原始字节SHA256。计划、验证、合并均重验文件与基线内嵌内容；`baseline_sha256/plan_sha256`绑定完整内容，宿主交接另绑基线/计划文件原始字节hash。来源、风格或共享设计变化须修改作者输入并建新基线/计划，不能替换旧摘要复用审阅。

共享输入沿用[作者字段](#制作设计与图片提示词)，额外增加`identity_assignments`，如`{"CHAR0001":"identity-a","CHAR0002":"identity-a"}`。它精确覆盖全部独立图片CHAR；v2多人群像和prompt_only群体不填。锚点不得闲置或引用未知ID，身体/身份共享规则不变；`designs`只放本轮明确冻结的设计，可为空，不自动借用台账旧设计。

`groups.json`为`[{"group_id":"character-a","owned_asset_ids":["CHAR0002"]}]`一类数组，精确覆盖所有尚未冻结图片ID。计划生成`read_only_asset_ids`与`read_only_dependency_sha256`，后者以`asset:ID/anchor:ID/location:ID`绑定冻结设计、锚点和LOC事实。提交逐字复用计划范围与摘要：

```json
{
  "baseline_sha256":"从基线复制",
  "plan_sha256":"从计划复制",
  "group_id":"character-a",
  "owned_asset_ids":["CHAR0002"],
  "read_only_dependency_sha256":{"asset:CHAR0001":"从计划复制","anchor:identity-a":"从计划复制"},
  "designs":[
    {"asset_id":"CHAR0002","attribution":"制作设计","basis":"当前来源支持的另一套服装","reviewed_source_facts":true,"identity_anchor_id":"identity-a","styling":"本版本具体服装设计"}
  ]
}
```

提交不新增identity_anchors或其他共享字段；漏项、重复/越界ID、依赖不符、旧计划、缺组或重复组均拒绝。候选去向、事实主体、适用版本、基础服装、关联PROP/父SET变化须重查受影响闭包；仅制作显示名变化按impact区分重编译和语义重审，索引不能确认pending或替代事实源。

### 空间版本继承

分组设计的`variant != base` SET引用已冻结直接父SET；无父引用的兼容情况使用唯一基础SET。编译前原样复制父设计`space/materials_palette/furnishings/lighting`，应用明确差异，再展平为原编译器四字段；其他字段保留，正式台账不增加空间协议字段。

```json
{
  "asset_id":"SET0002",
  "attribution":"制作设计",
  "basis":"同一房间，只增加原文明示的红绸",
  "reviewed_source_facts":true,
  "space_reference":{
    "asset_id":"SET0001",
    "design_sha256":"baseline.shared_designs.SET0001的规范JSON摘要",
    "differences":[
      {"field":"furnishings","operation":"append","new":"厅内挂有红绸","source_fact_text":"厅内挂上红绸","basis":"原文明示增加，其余布置沿用"}
    ]
  }
}
```

差异仅选四个空间字段，每字段最多一项。`append`用分号追加；`replace`另有`old`，须在父字段唯一精确匹配且new确实不同。`source_fact_text`精确引用当前SET自身已验证的`visual_facts[].text`，作者审阅事实是否足以支持差异。不能只凭“多年后”加破败/翻修；无明确变化时`differences: []`合法。

不得同时交space_reference和四个完整空间字段。父设计hash变化使引用失效，多级版本沿父链串行冻结；冻结版本时也用此协议并提供冻结祖先，不自由重写父空间。旧项目可继续原完整design路径，本协议不改旧编译器或提示词。

### Python接口与组验收

| 接口 | 契约 |
| --- | --- |
| `freeze_from_files(script, source, ledger, shared_design, style)` | 生产入口，完整原文验收及五文件冻结。 |
| `validate_baseline(baseline, require_files=True)` | 生产交接要求真实输入文件，拒绝纯内存基线。 |
| `plan(baseline, groups)` / `validate_plan(baseline, plan)` / `group_spec(baseline, plan, group_id)` | 建立/验证计划，查询拥有范围和只读依赖。 |
| `verify_group(baseline, plan, group_id, submission)` | 在完整台账编译真实提交，返回展平设计和提示词预览；仅`structurally_valid_group`，`semantic_review=not_performed`、`formal_ready=false`、`bindable=false`。 |
| `merge(baseline, plan, submissions)` | 重新核验实际组，运行原apply_design及完整require_designed验证，返回完整旧格式设计JSON；仍由主代理正常命令写正式台账。全部已冻结时groups/submissions均可为空。 |

纯内存`freeze(script, paragraphs, data, payload, style)`只供已验证解析结果的可信调用者和测试。宿主负责草稿隔离、并发上限、ownership reservation、实际语义验收及交接关闭，本模块不签发语义通过。关闭前不得改变交接输入，也不能把已完成任务记cancelled或刷新摘要绕过校验。

## 写入、编译与验收

设计委派前的`asset_ledger.py preflight <标准剧本> <source.json> <候选台账> --stage candidate`检查候选来源、身份、命名、筛选证据与关联，不要求完整提示词，也不判断候选已找齐。

代表样本按实际字段与依赖选择，不固定数量：有SET纳入代表SET；有佩戴关联保留基础人物＋PROP＋至少一个佩戴版闭包。读取逐场移动空间和佩戴/可拆卸风险，确认风格、完整候选、锚点、服装、PROP、LOC及父链，再预览。样本字段检查见[属性与展示分离](#物体属性与图片展示分离)。

```text
python <module>/scripts/asset_ledger.py design-check <标准剧本.txt> <source.json> <候选台账.json> <本批设计.json> --style-file <video-style.txt> --asset-id CHAR0001 --asset-id SET0001
python <module>/scripts/asset_ledger.py design <标准剧本.txt> <source.json> <候选台账.json> <完整设计.json> --style-file <video-style.txt> --output <新台账.json> --catalog-output <新分类TXT>
```

design-check的`--asset-id`可重复但ID不得重复。它先验证完整台账，再只编译请求ID及闭包，不裁剪台账绕过缺项。基础衣装、同身份组、关联佩戴PROP、同LOC版本/父链须有本批设计或严格有效旧设计，LOC事实保留为依赖；新设计改变旧对象依赖时须重审并提交该对象，不能刷新摘要。旧设计仅在版本、风格、来源/事实/场次、锚点及实际编译结果均有效时复用。

预览只向stdout输出，不改原台账/设计、不建立绑定或正式TXT。报告包含五个输入文件的`input_sha256`、请求ID、依赖闭包、`binding_sha256`与实际提示词；佩戴用`wearable_review_pairs`同时实读PROP单品和人物板。复查可把input_sha256对象另存JSON并传`--expected-hashes <输入摘要.json>`，必须含script/source/ledger/design/style五项；输入变化即重查。报告始终是`partial_preview`、`formal_ready:false`、`semantic_review:not_performed`。

作者沿短约束实读预览，核对事实、身份与服装继承、实际遮挡、空间关系及美术效果，再扩写同类。全量合并仍须原design、render及绑定验收，样本/组结构通过不替代完整交付。JSON是唯一设计源，TXT不得手工维护第二份设计。

编译以原文明示特征为优先约束，组合已选风格、作者设计与分类版式，不把审计出处或“无外观事实”写入prompt。每个图片资产保存`visual_design/image_prompt/image_format/image_design_digest`，设计自动记录`source_fact_digest/source_context_digest`。render确定性重编，核对提示词、版式、设计摘要、事实出处和场次映射；事实/设计/依赖变化须显式design，不手改image_prompt。命令写不同的新文件，保留旧台账/TXT，无设计旧台账沿原事实渲染，不自动补全。

分类TXT只保留角色、场景、主要道具三类，按台账数组顺序各自从`角色01`、`场景01`、`道具01`连续编号，超过99为100，过滤prompt_only后无空号。完成design的主行为`角色01 - 完整资产名：完整image_prompt`（场景/道具用各自前缀）；下一行角色写出现场次，须含仅OS/画外声出场及佩戴形象关联反查，SET/PROP写使用场次，稳定ID反查number后排序去重。展示号不进入稳定ID、name、图片名、节点名或引用，后台ID、证据及性格栏只留JSON。未design旧台账继续显示原文核心可见特征，缺信息保留“未明示”及旧独立production_design行，保留原绑定，不静默补设计；新制作须先design。

实际语义验收覆盖：原文硬约束与当前状态；人物辨识度、色材、动作适配及四视一致；遮面每视真实覆盖；群像差异、服制依据及样本/剧情人数边界；空间层次、动线和固定陈设；道具形制、比例、功能结构及共享依赖。前置候选覆盖和图片入选理由须有效，未建图对象应合理归属，不能只以“重要/保持一致”代替具体参考需求。程序不找齐所有重要物件/群像，也不判断中文语义或已生成图片效果；有真实图片才额外核对美观、身份/空间一致性与完整性。

## 设计依赖与局部更新

保存每轮真实修改前后的作者JSON，直接用现有工作区比较入口定位变化：

```text
python <module>/scripts/design_workspace.py diff <修改前设计.json> <修改后设计.json>
```

diff只读输出`compared`，按`asset_id`与锚点`id`比较新增、删除和字段变化；忽略JSON键与两类ID数组的排列顺序，保留字段内部有序列表的差异。`assets/identity_anchors`各列added/removed/changed，`top_level`列其他字段变化，`direct_anchor_references`给出变化锚点的前后直接引用。用这些结果复看实际变化，不再按数组位置zip或用无序集合生成列表做顺序断言；新增或重复ID仍须核实。

差异报告不确认语义或输入有效性，也不替代依赖闭包检查。编译与验收继续使用design-check；共享PROP、锚点或父SET变化时复看受影响提示词。阶段事件和修订窗口只在[进度计量](asset-workflow.md#实际进度与计量)记录，不另建设计计时表。

作者确认上下文范围可封闭时，每条设计可显式登记：

```json
"source_scope": {
  "kind":"scenes",
  "scene_ids":["SC0001","SC0004"],
  "basis":"所列场次包含该形象全部时代、身份、服装和动作依据，其他场次无额外设计约束"
}
```

scene_ids须覆盖全部出场，并加入未出场但提供时代、地区、身份或关系依据的场次；不能按出场列表自动填充分性套话。无法封闭时省略或用`{"kind":"global","basis":"具体全剧依赖"}`，继续把顶层source_digest纳入source_context_digest。旧设计不因安装更新取得局部声明。脚本只查场次存在、无重复和出场覆盖，不能证明没有漏写语义依赖。

design从当前source生成`design_source_scene_digests`，只读验证对照实际原文，不手改摘要。局部设计绑定所列原文、事实、关联和共享依赖，无关场次变化不使其过期；段落偏移仍须当前出处有效。显示名、场次映射、图像模式、筛选理由、锚点、基础形象或共享PROP变化仍触发对应验证，共享编译结果变化须审阅其依赖者。迁移保留原设计，`design_status`逐项列可复用/待审ID。

```text
python <module>/scripts/asset_ledger.py design <标准剧本.txt> <source.json> <现台账.json> <局部设计.json> --style-file <video-style.txt> --patch --output <新台账.json> --catalog-output <新分类TXT>
```

patch的designs只列变化资产，identity_anchors可省略或仅列变化锚点。未提交项须继续通过原摘要、事实、上下文、身份/共享依赖及实际编译检查，保留原记录和摘要，不自动刷新通过。缺新设计、过期未选项或漏依赖时按具体ID补审，不重交无关项。换风格/设计版本用显式完整design，patch拒绝隐式全量切换；prop/group覆盖等前置审阅仍需有效，新增候选的全局核实不等于重做全部外观。

这些命令生成新材料，不更新既有session；已绑定资产变化先读[绑定变更边界](binding-changes.md)，design --patch不是原位rebind或云端覆盖授权。

方法参考：[Toonflow视觉设计](https://github.com/HBAI-Ltd/Toonflow-app/blob/2c7fb257affb65777110fe4c6e60cd3c09187d9b/packages/skills/workflow/references/methods/visualDesign.md)、[ViMax人物设计与基础图](https://github.com/HKUDS/ViMax/blob/main/agents/character_portraits_generator.py)、[OpenAI Gpt-image-2提示词指南](https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide)。只用于资产设计方法，不改变分镜规则。
