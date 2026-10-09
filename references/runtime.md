# 后台命令与数据接口

只在后台使用；不向用户输出本文件示例、JSON或程序检查报告。所有JSON/TXT使用UTF-8。`<skill>`是实际Skill目录，`<project>`是本剧本独立工作目录。脚本只用Python标准库；bind默认加载`<skill>/modules/screenplay-standardizer/scripts/`中的现有函数验证输入，不依赖另装的同名Skill。

## 内置剧本标准化模块

转换/提取资产/改稿时读取[内置规则](../modules/screenplay-standardizer/INSTRUCTIONS.md)。该文件保留原标准化Skill的完整规则，其相对链接以模块目录为根。运行其中示例命令时，将`scripts/文件.py`解析为`<skill>/modules/screenplay-standardizer/scripts/文件.py`，工作产物仍放项目工作目录。完整包包含规则、两份参考文件、四份Python脚本和`component.json`摘要清单。

默认仅加载包内版本；同级目录、用户主目录或CODEX_HOME中同名Skill均不会被自动调用。缺失/损坏时停止并提示恢复完整包，不回退外部版本。兼容旧调用的`--standardizer <目录>`仅在用户明确指定外部版本时使用，不作为常规命令，也不因旧session记录了外部路径而自动沿用。旧session的来源、编号、范围、版本数据格式仍兼容，无须因本次内置升级重新转换未变剧本。

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

phase只记录交互位置，不代表外部操作授权。bind后为asset_choice；用户无论选1/2均phase→style，再展示风格菜单。style后自动为range；commit后为nodes_choice；用户无论选1/2均phase→followup并显示两条续接提示。恢复时status读当前阶段，避免重复菜单。没有小云雀网络操作或工具依赖。

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
| short_reason | <26秒必填：scene_cut、range_end、script_end；程序验证真实边界 |
| count_reason | ≥26秒偏离默认镜数时后台记录自然表达依据；范围8～13镜 |
| character_ids | 本单元所有出镜或发声的CHAR ID，不列本单元没出现的人 |
| prop_ids | 作为镜头主体的主要道具PROP ID；其他正文提到的道具仍用台账主名 |
| time / weather | 中文；时间按标准标题、天气未知填未明示 |
| global | 单段中文全局光照/色彩/层次/景深/站位/构图/已有环境描述 |
| start_state / end_state | 后台中文承接状态，含已有站位、朝向、持物、伤势、动作进度、发声接续 |
| shots / audio | 见下表 |

镜头字段：

| 字段 | 内容 |
|---|---|
| start / end | 相邻整数秒，单镜1～5秒，全单元从0连续至duration |
| scene_id | 当前实际场景SC ID，属于scene_ids |
| framing | 大远景、大全景、远景、全景、中景、中近景、近景、特写之一 |
| angle | 正面、斜侧、侧拍、背拍、俯拍、仰拍、第一人称、客观之一 |
| subjects | 本镜明确主体CHAR/PROP ID数组；正文必须含每个主体的台账完整主名 |
| closeup_reason | 近景/特写必填：情绪峰值、核心道具、动作高光、剧情伏笔之一 |
| face_closeup | 脸部特写时true；首镜不能true |
| environment_evidence | subjects为空时，填本单元标准正文中的原句，生成者确认确为原有无人/无核心道具环境镜头 |
| connector | 第2镜起必填单行自然连接词；不代替运镜 |
| text | 中文单行摄影/动作/环境/副反应/音效描述，包含声明的景别与主体全名；不要手写台词标签与对白引号 |
| dialogue_parts | 无对白可省略/空数组；有则为`[{"record":"L000004","text":"原台词连续片段"}]`，程序补完整人物名、发声标签和引号 |

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
| overlap_evidence | 与其他声音事件重叠时必填标准剧本原句，并人工确认确属同时发声 |

同一声音事件可以跨镜头，但各dialogue_parts按镜头顺序拼接必须逐字等于该事件text，包括标点。每片段所在镜头必须与事件时间相交；生成者还须核对片段长度与该镜实际发音时间，不能把长台词全部放到最后一镜。声音连续期间的反应镜仍安排原句的连续片段，标注自然续声。

## 执行与恢复

写完第一个单元，用相同顶层span和当前units前缀执行check --partial；通过并人工检查后追加下一单元，再检查。partial不要求覆盖选定范围尾部，但已写部分仍满足全部单元规则。最后完整check后commit；partial结果不能作为全批交付依据。

程序检查：来源与台词、归属、资产关联、场景硬切、短段边界、镜数/镜时长/声音合法性、特写数量/累计比例、相邻景别、范围异常确认、版本与固定抬头。语速估计超档仅返回warnings；应后台复核，明确实际不自然时修正，不把估计误差当确定错误。

摄影目的、环境依据、人物微动作、具体化是否新增事件、实际口型、动作/声音是否同期、完整事件覆盖均需生成者人工复核。来源span完整覆盖不等于语义已完整呈现，程序不作此承诺。

commit先原子保存session，再渲染交付；同request_id、同内容重试不会重复编号或范围记录。交付失败用render重建已提交文件。指定新输出名以保存历史；不能覆盖来源、风格、session或Skill文件。渲染旧receipt得到当批原版本，当前有效版本以session.units为准。

状态通过单一session.json原子提交。单项目顺序执行，不并发写同一项目；读取/校验不会改变历史。程序运行产生的JSON只供后台查看。
