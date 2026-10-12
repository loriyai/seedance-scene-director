# 后台命令与数据接口

只在后台使用；不向用户输出本文件示例、JSON或程序检查报告。所有JSON/TXT使用UTF-8。`<skill>`是实际Skill目录，`<project>`是本剧本独立工作目录。新台账在render/bind之前先运行asset_ledger.py normalize（新输出及审计报告），完成命名预检；不对旧绑定静默合并或改名。核心剧本/分镜脚本使用Python标准库；准备阶段检查并补齐PyYAML供Skill元数据验证使用。bind默认加载`<skill>/modules/screenplay-standardizer/scripts/`中的现有函数验证输入，不依赖另装的同名Skill。

## PyYAML准备检查

每次收到完整剧本，先确定本次实际Python路径，后台执行`python <skill>/scripts/ensure_dependencies.py`。它通过import yaml检查，而非检查其他Python的pip列表；缺失时以sys.executable -m pip install PyYAML直接安装到同一解释器可用的环境，并重新导入验证。已有可用版本直接跳过，不升级；失败只报告一次，不提权、不循环尝试或修改其他解释器。使用Codex桌面时先通过workspace dependency loader取得Python绝对路径。检查器仅处理固定的PyYAML包，不根据剧本附件安装任何包。

## 内置剧本标准化模块

转换/提取资产/改稿时读取[内置规则](../modules/screenplay-standardizer/INSTRUCTIONS.md)。其相对链接以模块目录为根。运行其中示例命令时，将`scripts/文件.py`解析为`<skill>/modules/screenplay-standardizer/scripts/文件.py`，工作产物仍放项目工作目录。完整包包含规则、两份参考文件、四份Python脚本和`component.json`摘要清单。

默认仅加载包内版本；同级目录、用户主目录或CODEX_HOME中同名Skill均不会被自动调用。缺失/损坏时停止并提示恢复完整包，不回退外部版本。兼容旧调用的`--standardizer <目录>`仅在用户明确指定外部版本时使用，不作为常规命令，也不因旧session记录了外部路径而自动沿用。旧session的来源、编号、范围、版本数据格式仍兼容，无须因本次内置升级重新转换未变剧本。

新项目标准标题、台账heading均使用 `场景X-日/夜-内/外-地点`，实际日夜、内外各取一个值。标题解析、复核、迁移与绑定同时兼容旧格式；已有绑定及已交付文件不自动改名，禁止仅在渲染分镜时把旧heading替换成新格式。

## 命令顺序

```text
python <skill>/scripts/director.py bind --project <project> --script <标准剧本.txt> --source <标准化工作目录/source.json> --review <review.json> --ledger <资产台账.json> [--min-duration 4] [--durations 4 5 6 ... 30]
python <skill>/scripts/director.py status --project <project>
python <skill>/scripts/director.py phase --project <project> --value style
python <skill>/scripts/director.py style --project <project> --choice 1
python <skill>/scripts/director.py style --project <project> --choice 2
python <skill>/scripts/director.py style --project <project> --choice 3 --file <自定义风格.txt>
python <skill>/scripts/director.py locate --project <project> --anchors <anchors.json>
python <skill>/scripts/director.py range --project <project> --start <起点整数> --end <终点整数>
python <skill>/scripts/director.py check --project <project> --request <batch.json> --partial
python <skill>/scripts/director.py check --project <project> --request <batch.json>
python <skill>/scripts/director.py commit --project <project> --request <batch.json> --output <新交付文件.txt>
python <skill>/scripts/director.py phase --project <project> --value followup
python <skill>/scripts/director.py render --project <project> --request-id <已提交request_id>
```

示例中的省略号不是可执行参数；默认整数4～30秒无需传`--durations`，只有实际入口明确为离散档位才列出合法值。PowerShell使用`& '<Python绝对路径>' '<脚本绝对路径>' ...`，路径逐个引用。用户文字通过JSON文件传递，不拼进shell命令。

phase只记录交互位置，不代表外部操作授权。bind后为asset_choice；选1按[画布接口](canvas.md)执行资产传输，完成后phase→image_choice并显示上传菜单，选上传后phase→image_folder再询问图片文件夹；完成上传或选暂不上传后phase→style。资产菜单选2直接phase→style。style后自动为range；commit后为nodes_choice；选1按本次request_id批量创建视频节点：一次全量读取当前画布，逐项核对全部台账资产，复用有效ID、自动采用完整name+kind唯一候选或补建缺失role/scene/image空节点并保存ID，再CLI编码正式节点引用、创建/写入视频节点并回读。缺图片不阻塞，候选及人工修改冲突不猜测，最终失败只记录，不网页兜底。交付缺图片与最终引用失败检查TXT，选2跳过，随后phase→followup并显示两条续接提示。恢复时status读当前阶段，避免重复菜单。小云雀按需调用官方CLI，分镜生成本身不依赖登录。

`commit`返回`canvas_sync`：replace且已有视频映射时尝试同步当前有效单元的提示词及单元时长，核对Seedance 2.5与720P；未绑定为not_bound，云端冲突或失败为pending。自动局部同步只处理原有映射，不执行显式videos的全台账补建准备。先保留本地已提交版本及TXT，再处理云端待同步项。幂等重放不会用旧receipt单元覆盖更晚的有效版本。TXT渲染默认不含小云雀内部标签；画布引用绑定使用同一render_unit的reference_text入口，仅转换资产位置，保留台词引号内原文。默认videos使用真实节点标签，空资产节点的缺图单独记录；节点引用回读与媒体就绪检查分别判定。

后台引用检查通过`write_reference_report(project, session, state, doc, units, failures=(), output=None)`保存，结构化记录位于`canvas/reference-check.json`，用户TXT默认与最近receipt的交付TXT同目录；可显式指定合法交付路径。报告区分缺图片资产和最终引用失败，列完整资产名、类型、真实节点ID及相关分镜、原因，不将正式节点引用成功写为图片已齐。交付TXT中的`@资产完整name `原样保留，失败不清空画布提示词、不执行网页粘贴或生成命令。

## 范围坐标

`session.json.records`逐条记录标准剧本正文行、场景ID、说话人/声音类型、原文字串及`start/end`。坐标为正文保留字母数字后的全局位置，半开区间`[start,end)`；不含场景标题、人物行、动作前缀或对白标签。字词/标点检查仍用原始正文，不用归一化内容替换成稿。

`anchors.json`：`{"start":"用户提供的开始片段","end":"用户提供的结束片段"}`。locate返回status及candidates，每个候选含span、标准正文、是否仅能近似到整行、历史issues和confirmation_token。

- located且唯一、approximate=false、issues为空：直接使用该span。
- needs_selection、近似边界、未找到或倒序：给用户必要候选确认。用户选定整行边界后采用对应span；用户修正句内边界则重新locate。
- 历史issues非空：显示具体原句与继续/重发选项，得到“继续”后才将token写入请求range_confirmation。
- token与当前状态版本及范围绑定。phase/style/commit更改后需重新range获取当前token；已有明确继续授权且范围、异常片段未变时可后台刷新，不重复询问。异常内容变化则重新确认。
- 所有正文都必须逐字映射到绑定标准剧本。候选太多时请求更长锚点，不任取第一个。

## 请求结构

append请求含以下顶层字段：

| 字段 | 内容 |
|---|---|
| mode | `append` |
| request_id | 本批唯一字符串；失败重试沿用，内容改变使用新ID |
| span | locate/用户明确确认的整个范围，两个整数 |
| range_confirmation | 仅存在历史异常且用户选继续时填当前token |
| units | 按来源顺序排列的完整单元数组 |

replace请求：mode=`replace`，新的request_id，units列出要替换的原ID；不需要顶层span，不使用`--partial`。每单元来源span、main_scene必须与原有效版本相同。程序保留编号、原batch_id、前序关系和旧版本。

单元字段：

| 字段 | 内容 |
|---|---|
| id | append不填，程序分配；replace填原“场景X-分镜Y” |
| span | 本单元连续标准来源区间；append各单元相邻并完整覆盖顶层span |
| main_scene | 主场景稳定SC ID，属于来源涉及场景 |
| scene_ids | 全部涉及SC ID，按标准来源出现顺序、不重复 |
| cross_scene_reason / cross_scene_evidence | 多场时必填：回忆或想象，以及标准剧本中逐字存在的依据；生成者必须确认其确实授权跨场 |
| duration | 合法整数总秒数，默认4～30 |
| short_reason | <26秒必填：scene_cut、range_end、script_end或natural_beat；前三种验证真实边界，natural_beat须另填short_basis说明完整动作/语义自然结束 |
| count_reason | ≥26秒偏离默认建议镜数时后台记录自然表达依据；范围8～13镜，不以默认镜数铺固定摄影序列 |
| character_ids | 本单元所有出镜或发声的CHAR ID，不列本单元没出现的人 |
| prop_ids | 本单元作为主焦点或明确参与的主要道具PROP ID；其他正文提到的道具仍用台账主名 |
| time / weather | 中文；时间按标准标题、天气未知填未明示 |
| output_format | 新生成或指定修改必填`reference-v3`；旧reference-v1、reference-v2及更早receipt保留原版渲染，不重写未选单元 |
| global | 必填单行中文起始人物站位，仅含必要的左右、前后、朝向及坐席关系；无人物时填“人物站位：无。”；资产名称使用显式@引用；不填光照、色彩、层次、景深、构图、地点氛围、持续环境或声源/口型规则。reference-v3在场景行后渲染独立“人物站位：”行；仅画外发声者不强迫入画，位移写对应镜头text；旧记录保留原版读取 |
| start_state / end_state | 后台中文承接状态，含已有站位、朝向、持物、伤势、动作进度、发声接续 |
| shots / audio | 见下表 |
| dialogue_layout | 新生成/修改的有声单元必填`semantic-v2`；旧交付保留读取和渲染，不要求重做未选中的单元 |

镜头字段：

| 字段 | 内容 |
|---|---|
| start / end | 相邻整数秒，单镜1～5秒，全单元从0连续至duration |
| scene_id | 当前实际场景SC ID，属于scene_ids |
| framing | 大远景、大全景、远景、全景、中景、中近景、近景、特写之一 |
| angle | 正面、斜侧、侧拍、背拍、俯拍、仰拍、第一人称、客观之一 |
| label | reference-v3必填单行中文短叙事标签，贴合本镜原动作或情绪落点，渲染为`[镜头序号-label]`；不固定标签顺序，不写目的解释或新增高潮 |
| transition | reference-v3必填单行中文具体切法，渲染在景别后、同一中文外括号内；不放机位运镜，不写备选方案，提到资产名时使用显式@引用 |
| subjects | 本镜参与资产CHAR/PROP ID数组，含主焦点及其他明确参与者；每项须在景别、四项摄影字段或正文有显式@引用，不要求全部成为同等景别主体；旧格式沿原正文引用规则 |
| focus | 主焦点CHAR/PROP ID数组，是subjects的子集；reference-v3有参与主体时必填且非空，景别短语由这些资产与framing生成；参与者通过可信空间关系说明。有依据的无人无主要道具环境镜允许为空；旧单元不强制补填 |
| camera | reference-v3必填非空单行中文机位与运镜描述；两项均须明确，固定机位也明确写出。放在景别/转场括号外，不重复景别；四项摄影字段均拒绝裸值“同前/同上/无/电影感”及纯标点，全句具体说明沿用的摄影状态可用 |
| composition | reference-v3必填非空单行中文构图描述；只说明本镜画面组织和既有参与者关系，不新增实体 |
| depth_of_field | reference-v3必填非空单行中文景深描述；说明清晰区域与前后层次，可承接已知状态 |
| lighting | reference-v3必填非空单行中文光照描述；已有光源明确时保持其方向与明暗连续，未明示时可指定制作照明方向与对比，但不冒充原文事实、不增加剧情中的灯具、火光、月光或天气 |
| purpose | 新生成/替换reference-v3每镜必填非空单行中文意图，用一句话具体说明本镜新增的信息或情绪、观看重点及摄影选择依据；拒绝裸“同前/同上/无/电影感/推进剧情/体现情绪/展示人物”及纯标点。程序只能校验字段，不能据此保证内容有意义；不渲染到提示词，旧receipt不强制补填 |
| closeup_reason | 可选兼容字段；原四项白名单不再限制新提交。近景/特写的具体依据写在purpose，可以是重要反应、表演变化、关系变化、动作细节或核心道具，不只限情绪峰值 |
| face_closeup | 沿用已有可选布尔字段，明确面部是本镜观看主焦点时建议true，覆盖面部中近景/近景/特写；不是只按framing=特写填写。首镜允许true，返回审查提醒，不禁止重要开场反应。不新增面部打卡字段 |
| environment_evidence | subjects为空时，填本单元标准正文中的原句，生成者确认确为原有无人/无核心道具环境镜头 |
| connector | 可省略或空字符串；有明确动作、视线或构图切点时填单行衔接，不代替运镜，不逐镜复写通用连接词；reference-v3并入对应镜头正文，不单列；提到资产名时使用显式@引用 |
| text | 中文单行原剧情动作描述；只写原动作、必要站位、已有环境状态、副反应及声源/口型归属，不重复四项摄影字段，不写后台排错说明。reference-v3的景别由focus与framing生成，不在text复写景别栏；音效用独立字段，不在text重复；不要手写台词标签与对白引号。旧格式沿原正文规则 |
| sound_foreground / sound_environment | 可省略、留空或填“无”的单行中文前景/环境音效，仅写原动作或已有环境支持的声音；资产名使用显式@引用。两项都有内容用+连接，只有一项只写该项，两项均无依据渲染`音效：无。`；不为补栏新增音效 |
| dialogue_parts | 新单元为`[{"record":"L000004","text":"原文完整话语或人工语义分句","start":5,"end":9.5}]`，起止是单元内实际发声秒数，最多三位小数；程序补声源、标签和引号，start/end只存后台；每段必须完整落在所在镜头。semantic-v2非末片段没有原文标点切口时，须另填`breath_basis`：非空、单行且含中文的人工自然气口依据，解释为何此处是完整语义/表演停顿；只存后台，不插入台词或渲染 |
| dialogue_continuations | 仅兼容旧semantic-v1；semantic-v2禁止此字段，以各镜dialogue_parts明确实际台词 |

声音字段：每个本单元涉及的标准台词record恰好一个事件，不按镜头重复登记。跨单元时分别填该单元来源截取的准确片段。

| 字段 | 内容 |
|---|---|
| record | session.records中的标准台词行ID |
| character_id | 对应标准说话人、对应原场景形象的CHAR ID |
| voice | 说、内心想法、画外音、旁白，必须与标准记录一致 |
| text | 本单元所覆盖该行的逐字原台词，含原标点 |
| start / end / pause | 单元局部秒数，至多三位小数；pause为总停顿秒数 |
| speed | 常速、快节奏、情绪慢速 |
| units | 按实际念法展开后的可发音单位，正整数；生成者核算 |
| timing_basis / recording_evidence | 可选；默认text_estimate。使用recording时须记录非空的录音与测量依据，仅存后台，不渲染估时/实配音标签；程序不验证录音真实性，生成者核实 |
| overlap_evidence | 与其他声音事件重叠时必填标准剧本原句，并人工确认确属同时发声 |

同一声音事件可以跨镜头，各dialogue_parts按顺序拼接仍逐字等于event.text，包括标点。各段必须有实际时码，首尾覆盖声音事件、无倒序/重叠，段间停顿纳入event.pause。短句完整落一镜；长句按原文完整分句/自然气口分到各镜，每镜写本镜实际说出的片段且时码完整落在本镜。禁止全文挤入一镜、重复贴全文或用抽象续声说明代替实际台词。人工分段只能在原文完整分句/自然气口，禁止从词句中间切字、孤立句尾语气词、纯标点段。程序检查可计算条件，标点切口的语义完整性及自然表演仍逐句人工复核。

台词布局工具：通过`director.dialogue_layout.attach(unit, plans)`登记；plans省略某条record时使用整条声音事件，不自动切文字。任一片段跨切点即报错，必须提供自然分句计划或重排镜头/发声起点。旧reference-v1读取时仍可登记semantic-v1续声，不能用于新提交。人工分句plans为record到`[{text,start,end}]`的映射，text保持原文；semantic-v2无原标点的非末片段可另填`breath_basis`人工依据，attach原样携带，validate核对字段及原文拼接，语义完整性仍人工复核。不得给纯标点或孤立尾词加依据绕过检查。

文本估时步骤必须调用`director.dialogue_layout.estimate(text, units, rate)`：先按实际念法核算正整数units（数字/英文不按原字符数硬估），按角色语气在所选speed档内确定rate，得到包括原文标点停顿的基线；计算`基线 - pause_seconds(text) + audio.pause`作为本次含情绪气口的总发声估时，再排镜头与同期动作。`audio.pause`是人工登记的总停顿，包含原文停顿及额外情绪气口，不重复叠加。`pause_seconds`先合并全部连续标点，混合簇也只计一次，含句末符时按句末级别处理。check在text_estimate路径实际调用estimate，用speed档上下限与登记pause计算时长范围，超出范围仅给后台估时警告；recording路径保留独立实测校验，不使用文本估时取代录音。不得通过拉长短句绕开单镜要求。新提交缺少semantic-v2会拒绝；旧receipt不会被升级或批量重新编号。

新规划先确定原文动作、情绪和声音落点，再指定主焦点与参与者，最后选择摄影，并逐镜填写purpose。删除固定景别排序及默认机位优先级；不按镜头索引轮换，也不用固定比例替代观看重点。有目的的同景别正反打、反应、动作衔接及相邻近景/特写允许，单元特写超过两镜、累计特写超过15%只触发审查，不拒绝提交；低于参考值也不是质量合格证明。具体表演变化、重要反应、人物关系、动作细节和道具都可以支持近景，不要求全是峰值。面部镜需要让观众读取具体表演变化，不能把对白、OS或情绪句自动变成正脸怼脸；同人物相似面部看点重复时评估关系构图、动作细节或空间信息，不只修改angle或framing标签。

真实威胁出现、情绪反转、动作爆发及关键信息揭示处主动比较快推、突拉、快速跟随、细节或空间展开，按剧情决定是否使用，写清运动方向、速度、落点及最终焦点，不设快镜头数量配额。短镜头一般只执行一个明确主要运动。静态镜头在能保留表演或空间信息时有依据地使用。动作匹配须明确同一持续动作及切点阶段，视线匹配须写视线方向和所看对象；已完成动作切到新动作不能只贴“动作匹配”标签。无法写清匹配依据时使用具体普通切换。涉及离开、追逐等持续位移时，下一镜明确“继续走远”等动作进度；容易混淆的画面说明相机、人物和物体的前中后景关系，人物的“身后”不能代替相机的“前景/后景”。

常规单元在26～30秒、8～13镜范围内按内容选择，单镜仍为1～5秒；26～30秒仅优选；4～25秒自然完成单元可用natural_beat并填写short_basis，不补动作凑时长。普通起身转身离开可用1～2秒动作链/匹配蒙太奇，重要次数与情绪停顿保留。参照模板不增加固定镜数、固定时长、不同景别轮换、强制高潮或环境破坏。旧单元的subjects继续按原有主体声明读取；没有output_format/focus/purpose及四项摄影字段的旧receipt不强制迁移，替换仅作用于用户选定单元。

reference-v3逐镜渲染为`·00秒-03秒[镜头1-label]：（@主焦点完整名 的framing；转场：transition）camera，composition，depth_of_field，lighting；text [本镜对白]。音效：前景+环境。`。每镜使用中文外括号，括号内不写“景别：”、不放运镜；四项摄影字段按固定顺序放在括号外。镜头时码为两位整数秒，编号由程序从1递增。群像焦点用多个@主名连接，空focus原有环境镜使用相应环境景别。global紧随场景行渲染为独立“人物站位：”行，connector并入本镜正文，purpose不输出。label是可见短标签，不等于后台purpose。音效缺一项省略空项和+，两项均空写“无”。

旧reference-v2的render_unit继续输出完整heading、无外括号的`景别：…；转场：…，`及旧text，不读取四项摄影字段；旧reference-v1保留其景别括号与估时/续声，更早无output_format的receipt保留原布局。新提交仅接受reference-v3，有声单元仍必须semantic-v2。replace可将选中的旧v1/v2单元改为v3，但来源span、main_scene和原编号不变；不重新校验或升级未选中的旧单元。

新对白只渲染“@完整人物名 说/内心想法/画外音/旁白：‘本镜原台词片段’”（成稿使用中文双引号）。不渲染发声估时、实配音或同句续声文字。audio事件起止仍覆盖整条声音，各镜片段首尾/停顿在后台校验。旧reference-v1的估时和续声渲染保持原样，重渲染receipt不会升级。

## 引用与统计

global、shots[].camera/composition/depth_of_field/lighting、text、connector、transition、sound_foreground与sound_environment中的人物、场景、主要道具名称使用 `@台账完整主名称 `（名称后必须有英文空格），或后台稳定ID形式 `@{CHAR0001}`、`@{SET0001}`、`@{PROP0001}`；程序只解析显式引用，按ID输出 `@完整名称 `，自动补齐名称后的英文空格，不自动替换裸词或台词内的人名、道具词。人工复核所有资产名称均已标记@且使用主名称，不使用别名或简称；程序检查主体引用、ID归属、重名、未知引用与重复@。全局唯一名称在资产render/bind前预检；分镜asset_text只检查本单元相关目录，范围外旧问题不阻塞本地有效单元。全资产传输及全量图片目录仍检查完整目标；本范围重名不得猜图片。

reference-v3人物行渲染为 `人物：@完整name ，@下一完整name ；`，逗号分隔，只有行末一个分号，不重复name@name；无人物为 `人物：无；`。场景行渲染为 `场景：@所关联SET完整name ；时间：…；天气：…；`，删除完整heading，多场景逐项连接；heading仍留在绑定台账中用于来源定位。旧receipt无output_format时人物行及其他布局保持旧版，旧v1/v2场景行保留heading。原音频名单行替换为紧随标题下一行、独占一行的 `@黑幕`，无分号及空格；不新增镜头或时长，不写入剧本道具。audio结构与校验不变；对白说话人标签加@，dialogue_parts.text原样保留。

commit、幂等重试及render成功后返回unit_count、total_seconds、minutes、seconds、summary。统计仅使用该receipt.units：unit_count为单元数，total_seconds为duration求和，minutes与seconds为divmod(total_seconds,60)。append的summary为 `本次生成分镜共N个，总时长x分x秒。`；replace为 `本次修改分镜共N个，总时长x分x秒。`。首次交付、重试及局部修改均不累计其他批次或旧版本，只有文件写入成功才向用户显示统计与文件链接。

## 执行与恢复

写完第一个单元，用相同顶层span和当前units前缀执行check --partial；通过并人工检查后追加下一单元，再检查。partial不要求覆盖选定范围尾部；完整check与commit仍须覆盖所选完整范围。单元超过两镜特写、累计特写超过15%及摄影重复均只返回warnings，partial、完整check和commit一致允许提交，不再禁止相邻近景/特写。15%是诊断参考，不是数量配额，不用补宽镜头稀释比例，也不因低于参考值免除人工检查。partial结果不能作为全批交付依据。

程序硬检查：来源与台词、归属、资产关联与引用、focus/subjects合法性与主焦点绑定、重名及引用归属、reference-v3所需label/transition/focus/purpose、四项摄影字段的非空单行与引用归属、音效字段类型、场景硬切、短段边界、镜数/镜时长/声音合法性、范围异常确认、版本与固定抬头。不把purpose文字当作摄影质量证明。语速估计超档仅返回warnings；应后台复核，明确实际不自然时修正，不把估计误差当确定错误。

摄影软审查：连续三镜framing、focus、angle和camera相同，提示检查新增看点；连续两镜明确的正面面部近景/特写，以及同人物连续三镜正面面部中近景/近景/特写，提示审查表演变化、空间与关系。审查延伸到来源连续的单元/批次边界，不把重复渲染批次拼接成故事。首镜面部近景、数量和累计比例也仅提醒。面部识别要求单一CHAR主焦点，依赖face_closeup=true或构图/标签中有限的明确面部描述，再结合正面angle；道具focus不会当作脸，普通“微笑/眼神”动作文字或只有CHAR+正面+近景不足以确定脸主导。缺少明确信号不代表没有怼脸画面，程序不分析图像、不承诺识别所有面部镜。换成斜侧或改景别标签后仍重复看脸的情况必须人工复核；合法近景正反打与重要反应允许保留，不靠警告数或配额验收。

摄影目的、环境依据、人物微动作、具体化是否新增事件、实际口型、动作/声音是否同期、完整事件覆盖均需生成者人工复核。来源span完整覆盖不等于语义已完整呈现，程序不作此承诺。

commit先原子保存session，再渲染交付；同request_id、同内容重试不会重复编号或范围记录。交付失败用render重建已提交文件。指定新输出名以保存历史；不能覆盖来源、风格、session或Skill文件，也不能把交付写进绑定的逐场稿scenes目录或本项目内部canvas状态目录。使用任务outputs或明确的交付目录。渲染旧receipt得到当批原版本，当前有效版本以session.units为准。

状态通过单一session.json原子提交。单项目顺序执行，不并发写同一项目；读取/校验不会改变历史。程序运行产生的JSON只供后台查看。

## 可复用布景

新绑定使用schema 3台账。unit.scene_ids、main_scene及shot.scene_id仍是SC剧情场次，保持来源定位、编号、时间与边界判断；图片引用目录使用CHAR、SET、PROP，不能拿SC当图片ID。每个SC的scene_asset_id指向对应SET，允许多SC复用同一SET；镜头只能引用该SC所属的SET。节点清单包含全部视觉资产，不根据当前单元筛选。图片上传后刷新引用及缺图报告也按SET判断。

本地回归检查：`python <skill>/scripts/self_check.py`。新增回归含命名归一化、道具实体记录、形象启用、semantic-v2逐镜台词与新旧模板兼容。只使用临时目录内的虚构剧本样本，覆盖布景复用/版本、名称空格边界、标题后黑幕、固定抬头、图片文件名、缺图报告及改稿迁移，不连接CLI或画布，不处理用户当前项目。

内置asset_ledger.py init产生草稿；地点身份核实及别称归并后运行build-scenes，按确认的LOC归组，不按标题字符串/日夜猜测归并。真实布景版本需手动添加decor、parent_id及visual_facts原文证据，具体字段见内置资产台账参考。旧schema 2可供读取审计，但画布资产和视频写入须先通过离线方案升级，不复用SC映射冒充SET。
