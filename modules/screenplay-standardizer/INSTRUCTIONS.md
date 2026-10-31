---
name: screenplay-standardizer
description: 将非标准中文剧本整理成保留原文内容的统一影视剧本 TXT，核对逐场覆盖、人物与声源，记录可追溯的逐场观察供资产审阅使用。
---

# 剧本标准化、复核与逐场观察

本文件统一维护标准稿写法、复核协议和观察交接。阶段顺序、范围、准备与交付入口见[资产主流程](../../references/asset-workflow.md)；资产身份、筛选、命名与台账字段见[资产台账](references/asset-ledger.md)，图片设计见[资产美术设计](../../references/asset-image-design.md)。本模块不改写剧情，不调用生图。

原文是内容与事实依据；标准稿保留剧情、动作细节、台词字词及顺序，只调整格式、必要断句与标点。规则中的姓名、时代、身体部位和叙事情形只是条件示例，不能成为新增设定；按当前原文与对象实际形态选择适用分支。下文命令相对本模块目录，执行时使用脚本绝对路径，项目数据保存在任务工作区。

## 标准剧本固定格式

1. 新标准稿标题固定为 `场景X-日/夜-内/外-地点`；原稿标题不符合时转换为该格式，日夜、内外各取一个实际值，如 `场景1-日-内-昏暗房间`。有明确夜晚线索才写“夜”，否则按格式默认写“日”；内外与地点依据原文和相邻场景确定。连续载体内外取景的同场按主要叙事空间取单个内/外值，具体位置放动作段。标题仅作剧情场次索引，不是图片文件名；仍须满足现有校验，不能含 `< > : " / \ | ? *`、控制字符或以空格/句点结尾。历史已绑定稿的旧 `场景X：日/夜·内/外·地点` 继续读取，不因新稿规则自动改名或重绑定。新prepare登记`format_policy=standard-screenplay-v1`，新稿校验据此强制当前标题格式。
2. 下一行固定为 `人物：角色名-形象；角色名-形象；`。原稿人物行用作出场人物参考，并结合实际正文核对，不原样沿用不合标准的格式，也不因原名单遗漏而删去正文中出现或发声的角色。形象只写原文明示的服装、造型或明显年纪；未知写“基础形象”，仅年纪明确时可写“基础形象-中年”。动作、伤势、表情、道具和性格不充当形象。无人出场且无人发声写 `人物：无；`；人物对白、内心想法os和画外音的声源都列入人物行。一个源人物在同场只有一个槽位，基础/佩戴/换装状态不列成多个人；保留不同匿名源名，不自动合并“记者1”“记者2”“记者一”。
3. 无名无台词背景人物，仅原文明示阵营、身份、人设或职业时按该称谓写“称谓-群像-形象”，如 `百姓-群像-基础形象`；不凭服装或动作推断职业，不把有台词泛称角色并入群像。人物行始终保留原称，图片资格与制作后缀在[群像图片筛选](references/asset-ledger.md#群像图片筛选)处理，不能因不建图片删去剧情人物。
4. 非台词的背景、动作和剧情单独成段。原稿已经使用含义等同于`△`、且与台词明确区分的特殊行首符号时，保留原符号，不重复添加`△`；未标识的非台词段才以`△ `开头。须在首次实读中确认原符号承担动作标识作用，正文内部符号、台词引号和普通标点不能据形状自动当作动作标识。台词为 `角色名(原文明确的简短情绪/微动作/内心想法os/画外音)：台词`；没有提示时写 `角色名：台词`，不写空括号或类别占位词，长动作仍独立成段并使用对应动作标识。`os`、`O.S.`、`内心OS`和`内心想法`表示同一声音类型，标准标签统一写为`内心想法os`；`ov`、`OV`、`V.O.`、`VO`和`画外音`表示同一声音类型，标准标签统一写为`画外音`。仅当这些词作为完整声音标签出现时才按别名处理；`低声os`、`握紧拳头os`等叙事内容仍按原文保留。普通情绪、说话方式、表情和微动作括号不属于声音类型，须原样保留。
5. 保留原有标点。原文靠换行排版时，先按完整动作或话语的句法组织相邻原段，再依据停顿与语气补标点；原段号保留作来源定位，不充当句号位置。主谓、动宾、条件与后续动作未完整时继续衔接，不先逐行补句号再合并，也不统一逗号拼接或凭疑问关键词一律补问号。组织段落时保持字词、顺序和声音类型，不合并不同人物台词或省略动作。仅独立角色名、独立内心想法os、独立画外音可作纯提示；“握紧拳头os”“低声交头接耳”等叙事内容须保留。疑似笔误原样保存；只有实际影响主体、声源、场界或依赖且无法判定时才设阻塞待核，其他情况用非冲突观察说明。

原动作符号只在prepare后的本项目工作目录登记，不改变`source.json`中的原文字词。标识由`source_compat.action_content`统一解析；标准稿校验、复核和分镜绑定使用同一清单。清单绑定原文摘要并进入复核指纹，原文或清单改变后重新核对，不能沿用旧通过报告。未登记其他符号的旧项目继续使用`△`。

```text
python scripts/screenplay_io.py action-markers <workdir> --marker "◆" --reason "实读确认原文的◆仅在动作行首使用，并与角色台词明确区分。"
```

示例符号和理由不得直接套用；只登记当前原文实际存在且已判断的标识。多个标识用重复`--marker`传入，不把无标识段改成其他原符号。动作标识是格式，不进入正文词数或台词。

### 人物与声源

声源分为人物、明确共同发声集合、非人物有语义播报和无语义音效；不能从人类/非人形态或“系统”“电话”等名称直接判类别。归属明确时沿用原声源，复杂语境回看上下文，不把动作主语或“看向某人”的对象自动当作说话人。

原文明示集合共同发声时保留集合称谓与完整对白；无法唯一确定全部成员，不编姓名、人数或个人分工，资产按[发声群体资产](references/asset-ledger.md#发声群体资产)登记，不能当无台词背景群像或设 `prompt_only`。全部成员由原文唯一确定时可用共同声源标签，保留原称与成员映射。

有确切证据的非人物播报使用 `原声源名(非人物播报)：原话`，这是声源列入人物行的明确例外，不建立CHAR；须通过[声音分类决定](#非人物有语义播报)，不能借此省去人物内心想法os或画外音、可见集合。普通音效保留在原动作/环境中。

### 地点称谓与移动过程

标题优先沿用原文明示的主要叙事地点，不要求列全行动范围。上车、启动、行驶、驶离及当前位置变化放动作或连续性说明，不把路径、转场解释或覆盖范围拼进地点名；原文本身的完整道路、路线或多空间称谓正常保留。

原文未命名时，依据明示空间采用最小、中性概括；同名不同地点只加有来源依据的所属主体、方位等消歧限定，不编街道或建筑。实际到达新地点、跳时或独立事件仍按场界判断；不能为了保留原名把新地点写回旧处，也不能因车辆移动推定另一条道路。后文对白、通话或定位补出当前地点时，回查此前的离开、行进、到达及事件发生位置，区分当前所在、目的地和仅被提及的地点；当前地点证据可回溯确定前面已发生事件的位置，不必等到出现地名的台词才切场。LOC/SET沿用经过审阅的地点称谓，不传播场次覆盖说明作为物理地点名。

### 场界先于机位与资产分类

起草先核对主要叙事地点、时间承接与实质转场。连续事件的附属空间、载体内外、取景方向变化本身不构成新场；“转场”字样也须判明是实质叙事变化还是切镜。正文保留原位置变化，不以复用标题代替空间核对，也不扩写地点名掩盖真实转场。SC场界、LOC身份、SET/PROP参考用途分别审阅，不相互倒推。

先判断实际叙事空间，再应用同地点的机位/载体内外规则。同一人物、车辆、出行或因果事件持续，不足以证明仍在同一场；主要空间已经改变时，不能用“动作连续”“没有另写转场”“没有证明是另一条道路”作为合场依据。同一道路也可能包含不同主要叙事空间。驶离路口后在另一弯道发生事故，应结合位移、事故发生空间及后文当前地点回指判断新场；单纯转弯、车辆移动或事故关键词本身也不自动拆场。切点落在新空间/事件开始处，保留中间行进与对白的原顺序，不新增到达动作。

首次逐场判断和独立复核均须同时核对多拆与少拆：既问已有边界是否只是取景变化，也问每个场稿内部是否容纳了已改变的主要地点。对有位移或后文地点回指的实际风险，检查者从原文重新判断，不以作者的“连续事件”结论代替复核；沿现有观察及语义报告记录证据、切点或保留理由，无须另建一套逐段场界台账。影响SC的地点归属在full/assemble前处理，不能留给资产命名阶段再决定。

代表样本须覆盖原稿实际存在的边界风险；确认只是机位拆场时合并场稿、调整连续源范围与场次、核对人物行，再复核及重新prepare观察。确需保留时按[场界复核协议](#移动载体场界复核)留下绑定原文与相邻场稿的依据。

## 复核协议

当前 `review_version=9`。先校准代表样本，再检查已保存批次，最后完成full与assemble；每场首次语义判断均须实读，结构通过不等于标点、归属或场界判断正确。

```text
python scripts/screenplay_review.py <workdir> --scope sample --scenes 1 3 8 --report <sample-review.json>
python scripts/screenplay_review.py <workdir> --scope partial --report <partial-review.json> [--previous-report <prior-review.json>]
python scripts/screenplay_review.py <workdir> --report <review.json> [--decisions <decisions.json>] [--previous-report <old-review.json>]
python scripts/screenplay_io.py assemble <workdir> --review-report <review.json> --output <script.txt>
```

| 范围 | 有效出口 | 用途与限制 |
|---|---|---|
| sample | `sample_passed` | 检查指定实际场号，可不连续；范围合法、无重复或重叠，不改source.json |
| partial | `partial_passed` | 检查已保存场稿；报告保留实际覆盖场次 |
| full（默认） | `passed` | 连续覆盖全部原文，禁止 `--scenes`；唯一允许正式assemble/bind的范围 |

sample/partial均带 `scope`、检查场次和 `coverage_complete: false`。每场先做与assemble相同的标题、人物行及正文确定性检查；局部通过不代表全文通过。final full可按当前算法与输入哈希复用有效场结果，但必须重新验证完整范围；assemble另核当前源和场稿摘要。

### 覆盖、决定与增量复用

报告 `paragraph_map` 记录每个原段对应的场稿行号及纯角色名、独立声音提示、空段等未进正文的原因；必须处理 `coverage_missing`、`coverage_extra`。`speaker_items` 包括原文引号及无引号成稿对白，列源片段、成稿行、声源提示与待核原因。自动提示仅接受明确标签或姓名紧接明确发声谓语的简单句，多角色、多引号、复杂从句或无法唯一归属的情形人工确认。

已知人物名后的括号提示与冒号可识别，中英文括号/冒号兼容；覆盖不把角色标签算台词，但会核对原情绪、微动作和声音类型是否保留。`os`、`O.S.`、`内心OS`和`内心想法`仅按声音类型别名规范化为`内心想法os`；`ov`、`OV`、`V.O.`、`VO`和`画外音`仅按声音类型别名规范化为`画外音`。普通情绪、说话方式、表情和微动作括号必须逐字保留，包括括号形式和内部文字，不得套用声音类型规范化。人物行已逐字列出全部成员时，顿号连接并紧接冒号/对白引号的联合标签可识别；未知或重复成员、动作句不据此推断联合发声。

同一 `decisions.json` 保存以下决定；均复制报告提供的完整模板再填写，不以示例片段替代模板：

| 决定 | 保存位置与补充字段 | 边界 |
|---|---|---|
| 说话人 | 报告的 `decision_key`；在 `decision_template` 增加 `speaker` | 只确认归属，不豁免漏文、额外文字或对白差异 |
| 引号不是台词 | 相同入口，增加 `classification: 非台词` | 仅叙述中的引用，且尚未匹配成稿台词 |
| 纯格式段 | `paragraphs[decision_key]`，模板加具体 `reason` | 不得跳过动作、台词或情节 |
| 跨目标片段 | `fragments[fragment_key]` | 见下节，不与同段纯格式决定并用 |
| 保留场界 | `boundaries[decision_key]` | 见场界协议 |
| 非人物播报 | 声音分类项的 `decision_key` | 见声音分类协议 |

说话人决定保留 `source_text`、`context_before`、`context_after`、`draft_text`，逐字绑定原片段、上下文与成稿。决定键依据片段、相邻上下文及场标题内容，不依赖段号；仅内容与上下文不变时可复用。纯格式决定也按源与上下文失效。

`--previous-report`只复用同算法版本且整场原文、场稿和该场相关决定均未变的结果。普通声源/段落决定允许内容相同的段号平移；显式片段映射还要求原坐标一致。算法升级或绑定内容改变时重新核对，不能刷新旧hash冒充重审。v7/v8报告不得用于新装配、新观察导入或新项目绑定；历史观察中的不可变快照仍按原版验证，不自动重写已绑定项目或历史回执。

### 源片段跨目标覆盖

默认按源顺序核对连续正文。一个原段拆到标题、动作、台词或声音标签时，复制报告完整 `fragment_template`，在 `decisions.json.fragments[fragment_key]` 增加具体 `reason` 与非空 `fragments`。模板绑定 `policy: source-fragments-v1`、原 `paragraph`、逐字 `source_text`、前后文、整场 `source_hash` 及 `draft_hash`。

每片段含 `source_range`、逐字 `source_text` 与 `target`；target含 `kind`、实际文件 `line`、`range`、逐字 `text`。行号从1开始，字符范围为Python字符串零基半开区间，保留空白，不按字节或压缩文字定位。目标范围按保存文件整行计，包含实际动作前缀（`△ `或已登记的原符号）或人物标签的占位，不假定前缀固定为两个字符；选中内容须落在对应正文、地点或声音标签内部。

例：源段1 `阿明垂眸os`、段2 `“我知道。”`；场稿第3行为 `△ 阿明垂眸。`，第4行为 `阿明(内心想法os)：我知道。`。以下字段并入该段完整模板：

```json
{
  "reason": "实读确认末尾os属于紧邻的阿明内心台词，垂眸动作保留在正文。",
  "fragments": [
    {"source_range": [0, 4], "source_text": "阿明垂眸", "target": {"kind": "action", "line": 3, "range": [2, 6], "text": "阿明垂眸"}},
    {"source_range": [4, 6], "source_text": "os", "target": {"kind": "cue", "line": 4, "range": [3, 9], "text": "内心想法os"}, "speaker": "阿明", "dialogue_source": {"source_range": [2, 2], "source_lines": ["“我知道。”"]}}
  ]
}
```

`dialogue_source.source_range`是1基、两端包含的原段号范围，与字符范围不同；可覆盖紧邻多段台词，`source_lines`逐段逐字匹配。台词仍需独立通过speaker_items归属核对。

片段必须遵守：

- 除解析器已识别的格式前缀，原段全部字词恰好消费一次，不重叠、不倒序；目标沿全场源序消费，段内跨段都不重复占用标题、正文或声音范围。
- 动作、时间、台词不删改词，沿用标点/空白比较规则，语气仍实读；新增和遗漏正文继续报coverage问题。
- 标题只消费场首转场段的实际地点片段，不能消费固定的“场景”“日”“内”等字。转场段的时间内容保留于动作等适用位置，不要求地点再在动作重复。
- `cue`源仅接受段末完整声音标签 `os/O.S./内心OS/内心想法` 或 `ov/VO/V.O./画外音`；目标分别为规范标签`内心想法os`或`画外音`，并逐字绑定声源和紧邻原台词，不能跨另一动作/对白借用，紧邻台词已有明确人物标签时须一致。“低声”、普通情绪或微动作括号、动作句、`os拔刀`不在该迁移语法内，不能借cue删除。别名规范化只适用于声音类型标签，不适用于普通括号提示。
- 报告保留实际片段及范围；映射增删改进入该场 `decision_hash`。原文、上下文、段号或场稿任何位置变化后，用当前模板重新核对；不能靠修改hash复用旧映射。无法证明声源归属时保留待核或原文。

### 移动载体场界复核

场界实质判断与[地点命名](#地点称谓与移动过程)按前文执行。`boundary_items`对相邻标题的载体内部等附属取景空间提示风险，sample/partial也检查已存在的直接相邻场稿；未处理项计入 `counts.scene_boundary`，报告为 `needs_review`。有限词表只提供提示，不自动合场、不改标题、不建资产，未报风险也不能证明所有场界合理。

`internal_boundary_risks`另检查所选场稿内部的原文位移及当前地点话语，给出段号、逐字片段与场内上下文，供人工发现少拆和后文地点回指。该扫描为有限词表启发式，`blocking:false`，不推断当前地点、切点或LOC，不自动拆场，也不进入`counts.scene_boundary`。相关风险须在本次真实语义阅读中作出判断；`passed`及场界待核0只表示机械检查通过，不是场界语义批准。未命中词表的地点变化仍由阅读者负责。

决定保留时，在 `boundaries[decision_key]` 的完整 `decision_template` 上增加：

```json
{
  "outcome": "retain",
  "reason_kind": "location_change",
  "source_paragraphs": [12],
  "evidence": "车驶入金荣街。",
  "reason": "原文已离开前场路口进入金荣街，场界依据是实际地点变化。"
}
```

`reason_kind`允许 `explicit_transition`、`time_change`、`location_change`、`event_discontinuity`、`independent_vehicle_space`；最后一项仅指原文确立的独立载体内部叙事空间。证据位于相邻两场范围、段落连续且逐字匹配；模板绑定两侧标题、源范围、源/场稿摘要和邻接上下文，任一改变需重审。程序验证绑定，不证明理由语义成立；不得批量填通用理由放行。场界应在样本/逐场出口解决，观察的confirmed不能替代本决定。

### 原文解析兼容与差异诊断

`source_compat.py`只创建解析视图，原文逐字保留。它识别成对 `「」`、`『』`、`“”`、`‘’`及ASCII双引号，保留嵌套正文，支持已知声源的 `角色os：`、`OS 角色：`、`角色 O.S.：`、`角色ov：`、`角色VO：`、`角色 V.O.：`及相应中文规范标签等明确语法。独立`os/O.S./内心OS/内心想法`前缀须成为`内心想法os`声音提示；独立`ov/VO/V.O./画外音`前缀须成为`画外音`声音提示。普通括号提示仍保留原文，不因包含相似字母而改作声音标签。错闭、孤立闭引号、未闭合只报边界问题，不猜配对；`parsing_transforms`记录类型、原文及位置，不表示源已改写。

独立“第十章”“第12集”等纯编号可作格式提示；“第十章 林舟回家”等含文字的行须保留，或通过源/上下文绑定的paragraphs决定确认纯提示性质。纯编号已作为成稿台词保留时按正文处理。

`issue_counts`分别统计源格式、过期决定、文字对齐、人物契约、真实声源待核和声音分类；`counts.format_hints`单列已识别格式提示，speaker总数是待处理记录数，不代表同等数量错误台词。首轮报告提供待填决定模板；先区分真实正文错误、待确认归属和格式/片段映射，再处理对应项。仅补决定且稿件未变属于确认工作，实际改字词、断句或归属才记相应修订；不以退出码或报告条数直接推算返工次数。协议版本不是修订轮数，不放宽归属规则压低计数。

`punctuation_diagnostics`提供可直接对齐的台词/动作标点与空白差异，不自动改字，也不把有依据的补标点一律判错。“不，是他。”与“不是他。”等语义差别须实读。冻结场稿前完成标点、人物槽位和声音分类核对，再full/assemble；交付核验不能替代此出口。

### 非人物有语义播报

仅在原文及上下文确认自动播报且无人物视觉身份时采用该分类，原话连标点逐字保留，不添加“声效”等正文。第一次review产生分类待核项；复制其完整 `decision_template`，保留 `source_text`、前后文、`draft_text`及完整 `source_lines`，再增加：

```json
{
  "classification": "非人物播报",
  "source_label": "电话提示音",
  "reason": "原文明示来自电话自动提示，无人物视觉身份"
}
```

以该项decision_key保存，不只写示例三项。原文同段声源名、上下文或成稿改变时重新确认；决定不豁免文字/标点差异，声源仍列人物行时也不得放行。播报进入专门声音记录，后续逐字分句、排声、估时，不因无CHAR而丢失文本。真实无法确定的声源保持待核。

## 逐场观察协议

`observations-v1`在首次逐场实读时记录人物、服装、地点、佩戴、声音、物件与跨阶段依赖的源证据，供全剧统筹直接复用。可早于full收集；收集、结构检查与sample通过均不等于身份/资产语义确认。只修改实际受人物身份、声源、人物行或场界决定影响的场稿。

观察不是第二份剧本：`source.json`保持正文来源，观察只存段号、短逐字证据与场次去向。场次清单保存源/场稿摘要、标题、段落范围；导入审阅快照只存校验回执，不复制全文、paragraph_map或对白正文。首次实读同步写入这一份观察记录，后续按稳定OBS标识补主体、目标和结论；不再另写一套人物/物件事实摘要。跨阶段如何引用和验收统一见[阶段证据与验收](../../references/asset-workflow.md#阶段证据与验收)。

### 事实粒度与记录格式

首次实读按“主体→属性→适用层→出处”登记，不按名词扫描：先分清句中哪些词描述稳定外貌、体型比例、衣饰或物件结构，哪些词只描述当前动作；前者拆为 `visual_fact` 候选，后者留在正文或相应事件观察。记录 `tentative_subject`、原段号、短证据及 `fact.key`，不能因属性嵌在动作句中就整句只记作物件提及。

联合主语须按原句分别判断属性归属。原文明确共同属性时，为每个已确定主体各存一条适用的属性摘要，可共用原始证据；句中另有动作不取消该属性，也不能据此把属性分配给未被说明的人。摘要不夹带其他主体、人数或共同动作。归属、主体或版本无法确认时保留pending，不猜ID；同主体同属性只保留必要证据，不把普通动作或每个名词都扩成观察。

按适用层分配记录：稳定身体或物件结构归对应资产，衣饰归实际造型，临时受光、湿润、位置及动作进度归场次/状态，佩戴或持有归事件关系。只记录原文明示的信息；首次书写中核对已提到的属性是否在正文、事实或事件中各有合理去向，不额外增加一次全文扫描。全剧统筹在既有身份审阅中确认目标及适用范围，来源事实仍只引用实际出现的场次；设计如何共享身份及继承字段见[图片设计的字段职责与继承](../../references/asset-image-design.md#字段职责与继承)。LOC合并、佩戴身份、群像计数与PROP筛选仍归资产审阅。

先写列表 `records.json`，由prepare计算摘要，不手填或刷新旧摘要：

```json
[
  {
    "observation_id": "OBS0001",
    "scene_number": 1,
    "kind": "visual_fact",
    "tentative_subject": "陈默",
    "source_paragraphs": [1],
    "evidence": "穿灰色布衣",
    "status": "pending",
    "requires_decision": false,
    "basis": "原文写出服装，待统筹确认主体及适用形象",
    "target": {"category": "characters", "source_entry": "陈默-基础形象"},
    "fact": {"field": "visual_facts", "text": "灰色布衣", "attribution": "叙述", "key": "服装"}
  }
]
```

`observation_id`为稳定OBS标识，重导入保留。`source_paragraphs`连续且在所指场内，`evidence`为对应原文逐字片段，跨连续段保留段间换行；不以改写句或别场证据补本场事实。

| kind | 允许行为 |
|---|---|
| `visual_fact` | 可导入 `fact.field=visual_facts` |
| `personality_fact` | 只向人物导入 `fact.field=personality_facts` |
| `location_identity` | 地点身份及固定空间归属的观察；不因场内有人移动就改为mobile_space |
| `mobile_space` | 实际移动载体、附属空间及其场界/参考用途关系的观察，不自动决定SC、LOC或PROP |
| `candidate_mention`、`identity`、`costume_state`、`wear_event`、`sound_source`、`scene_boundary`、`asset_dependency` | 仅观察/决定索引，不夹带fact自动改资产；location_identity/mobile_space同样不自动导入fact |
| `source_conflict` | 必须pending且requires_decision=true；解决后保留ID，改为对应观察类型并写依据 |

事实含 `text`、`attribution`；可选 `key`显式标记属性，同场同目标同字段同key的矛盾值拒绝导入。工具不靠相似度识别语义矛盾，作者仍需统筹。`source_conflict`仅用于无法判定且影响主体、声源、场界或依赖的真实冲突；无此影响的疑似笔误保留原词，在相应非冲突类型说明basis并设requires_decision=false。

目标target定位规则：

- `category + asset_id`：已有稳定ID，必须关联当前场。
- 人物初始化前：`category=characters + source_entry`，逐字匹配本场人物行完整条目且唯一，可重复填写相同scene_number，不跨场猜身份。
- `category=locations/scenes`：由scene_number唯一定位已关联LOC/SC。
- SET/PROP必须明确asset_id；观察工具不创建道具、合并地点或推断次数。

`pending`永不写事实，即使已填target/fact；普通候选默认requires_decision=false，只入索引。真实未决事项设true，生成 `pending_reviews.kind=逐场观察待核`。允许导入时明确填写confirmed、target、fact、basis并提供匹配review。不能为增加导入量批量确认；主体、场界或状态变化即使文字未变，也须重审其影响。

### 观察检查与导入

```text
python -B scripts/scene_observations.py prepare <records.json> --source <workdir>/source.json --workdir <workdir> --output <observations.json>
python -B scripts/scene_observations.py check <observations.json> --source <workdir>/source.json --workdir <workdir> --review <review.json>
python -B scripts/scene_observations.py import <observations.json> --source <workdir>/source.json --workdir <workdir> --review <review.json> --ledger <assets.json> --output <assets-with-observations.json>
```

也可由 `asset_ledger.py init --observations ... --observations-review ...` 导入。prepare只绑定当前源/场稿，不改确认状态；check无需完整review，`ready_to_import_facts=false`时仅继续收集/检查。事实导入要求对应场次在当前算法的passed/sample_passed/partial_passed报告中，源、段落范围、场稿摘要完全匹配；局部报告只授权其覆盖场次，不能代替最终full、assemble或台账validate。

起草同步登记真实跨阶段依赖：附属空间/载体先判SC，再判移动物件、LOC和独立SET参考用途；可摘戴遮挡物记录物件、佩戴者、状态事件及源范围，ID稳定后关联PROP/佩戴CHAR。这些是适用风险分支，不要求每稿都有；不为参考图先拆场建重复地点，不把图片展示要求写进物理属性。

check的 `risk_diagnostics`只用有限车辆/遮挡物词表提示原段、短原句及已有观察ID，标记 `risk_scan=heuristic_not_complete`；不确认观察、不选择图片、不保证找齐，不因物件出现自动阻塞。实读补观察，实际未决依赖才设requires_decision=true。场界观察引用同一来源和处理依据，不能替代boundaries决定；局部审阅涉及相邻场稿时还核对 `boundary_drafts`，相邻稿变更后重查。

导入后保存不可变 `observations.source_payload`、`payload_digest`，imports记录观察ID、初始/当前稳定目标、SC归属及实际写入事实。重复导入不复制事实；完整观察重导入只替换本模块原摘要对应的待核，不清其他pending。validate核对原证据、场稿摘要、目标及事实仍在台账，始终声明 `semantic_review=not_performed`，摘要通过不代表语义通过。

经确认的资产合并由normalize处理redirects后调用remap，保留原bundle与摘要，记录initial_target_id→target_id链；旧ID须退役、同类别、无循环，事实仍在当前目标。不能静默删除旧观察或已导入事实；需要变更时先归档交接、人工处理事实，再按当前源审阅。

下列函数均不落盘，review参数可为JSON对象或路径；import/remap返回深拷贝，不改原台账：

```python
prepare_observations(source_path, workdir, observations, scene_numbers=None) -> bundle
validate_observations(payload, source_path, workdir, review=None, ledger=None) -> report
import_observations(data, payload, source_path, workdir, review=None) -> (new_data, report)
validate_imported_observations(data, paragraphs, script=None) -> report
remap_observation_targets(data, redirects) -> new_data
```

旧台账无observations时返回 `not_registered`；常规validate必须传parse_script的script以验证人物行/正文，不传时仅为 `source_and_facts_checked`，不能称完整通过。

### 改稿迁移与历史重审

`asset_migrate.py`保留旧source、台账、场稿、delivery_contract及全部观察历史。源、场稿、场次去向与导入事实仍一致时可保留观察；任一绑定变化则整体归档到 `observations_history`，保留原摘要和迁移后仍存在的旧事实，生成 `逐场观察迁移待复核`。不能只关闭待核而遗留错误事实，也不能清其他待核。

人工重审后用当前源/场稿prepare。新bundle的 `reconciliations`仅消解指定历史：

```json
{
  "reconciliations": [{
    "history_digest": "迁移待核项的精确历史摘要",
    "basis": "已逐项核对当前原文、场稿与旧事实",
    "resolutions": [{"observation_id": "OBS0001", "outcome": "reconfirmed", "new_observation_id": "OBS0001", "basis": "重审确认服装事实仍成立"}]
  }]
}
```

每个旧observation_id恰有一个处理结果：

- `reconfirmed`：指向当前confirmed新观察并有当前该场review，旧事实由本次实际导入的新事实确认或已人工移除。
- `retained_pending`：指向当前pending新观察，旧事实先移除；真实未决事项继续由新pending阻塞。
- `withdrawn`：说明撤回依据，旧导入事实已移除。

import只消解匹配history_digest的迁移项，历史追加处理回执，旧bundle/摘要不变。未知历史、漏项、未通过当前review或仍存在未确认旧事实均拒绝；其他迁移项走各自流程，最后完整validate，不提供自动语义重基线。

## 向资产阶段交接

影响原人物行、声源、场界的跨场问题须在full/assemble前解决；制作名称、图片资格和稳定资产ID只在台账处理，不反写剧情。标准稿的“基础形象”和默认“日”不是原文事实。后续复用当前source、已核场稿/决定和观察索引，不重建同一证据。

资产阶段按[主流程](../../references/asset-workflow.md)完成身份/LOC/时间层归并、SET、候选审阅、必要佩戴版本与设计依赖；按[台账规则](references/asset-ledger.md)保留稳定ID、原文事实与制作设计分层，按[美术设计](../../references/asset-image-design.md)编译图片提示词。正式交付遵从项目范围和delivery_contract，候选检查不能冒充完整设计交付；资产改稿及绑定变更沿用对应流程，不自动迁移旧项目。
