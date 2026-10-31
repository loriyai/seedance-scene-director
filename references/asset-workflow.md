# 标准化与资产主流程

本页只维护入口、阶段先后、输入输出及交付边界。规则按职责读取，不再加载旧汇编页：

| 当前工作 | 唯一规则来源 |
|---|---|
| 标准格式、原文保真、声源与场界、逐场观察和全稿复核 | [标准化规则](../modules/screenplay-standardizer/INSTRUCTIONS.md) |
| 身份与命名、SC/LOC/SET、人物/群像/道具筛选、候选决定和改稿迁移 | [资产台账](../modules/screenplay-standardizer/references/asset-ledger.md) |
| 事实与制作设计、图片版式、设计JSON、共享依赖、分组和编译验收 | [图片设计](asset-image-design.md) |

`<skill>`为Skill根目录，`<module>`为`<skill>/modules/screenplay-standardizer`，`<project>`为当前任务`work/seedance-scene-director/<项目标识>/`；下文`<workdir>`为prepare输出目录。执行时使用绝对路径。新候选、决定和设计用新文件保存，不覆盖已绑定材料；本流程不自动执行云端操作。

## 风格确认

收到完整剧本后，只检查文件是否存在且可读取（粘贴全文视为已收到），不先解析全文或分析剧情。不可访问时简短报告并请求可访问文件；可访问且本项目尚无已确认风格时，立即暂停工作，显示以下选项并等待用户回答：

请选择视频的风格
1.3D国风动漫,暗黑武侠/超写实仙侠风格
2.3D国风动漫，眷思量风格，东方奇幻唯美风格
3.自定义风格

等待风格期间，不运行依赖检查、prepare、标准化、身份/资产审阅、设计或这些阶段的子代理任务；选择3后也须等到收到有效自定义文本才继续。用户随剧本或在本次会话中已明确提供本项目风格，或本项目已有有效的 `video-style.txt` 时，视为已确认，不重复提问，不跨项目或跨聊天推定风格。

选择1或2用 `style` 将对应文字原样写入 `video-style.txt`。该命令支持在bind前保存风格，尚无session时仅保存文件，bind后纳入项目摘要。选择3提供自定义文本输入；宿主不能把输入框附在该选项下时，选择后立即提示“请输入自定义的视频风格。”并显示输入框，收到后原样保存，不追加词语。直接提供的自定义风格同样用 `style --choice 3 --file <自定义风格.txt>` 保存。风格为非空单行文本；明确要求字幕、文字、BGM或其他固定禁止项时简短指出冲突并请用户调整，不改固定抬头。本步骤只保存风格，保存后立即进入视频比例确认，不解析全文、不检查依赖、不开展资产任务。

后续每次资产设计或分镜生成/修改读取该文件，持续使用所选风格，不重新展示菜单。仅用户明确要求更换时更新；换风格只影响新生成或明确指定修改的资产/单元，已有图片、设计和分镜保留原版。固定抬头中的写实要求始终保留。

## 视频比例确认

风格保存后，当前聊天尚未确认比例时立即显示并等待有效选择：

请选择视频的比例
1.横屏16:9
2.竖屏9:16

用 `director.py ratio --project <project> --choice 1|2` 将纯比例独立保存到项目 `video-ratio.txt`，并将当前聊天的默认值保存在项目父目录 `video-defaults.json`。同一聊天的项目位于同一个专用根目录；不得将该默认文件放入跨聊天共享的目录。已经明确给出比例时直接保存，不重复提问；同一聊天新项目用 `--inherit` 将已确认默认值保存到该项目。仅有按钮预选不算确认。当前项目有已保存比例时直接沿用，其他项目后来的默认值变更不覆盖它。

比例步骤只保存比例。保存成功后立即进入workflow入口记录：先记录分段选择和人数或自定义范围，再记录画布同步选择及可选的画布网址。记录阶段不解析剧本、不生成正式分段、不运行标准化、资产设计或云端写入。首次确认前禁止继续；`intake-status`可只读查询两项设置。新项目初始化记录工作流版本及本次入口记录，旧项目不自动补写或迁移。

## 分段与画布意图记录

比例保存后立即显示：

```text
请选择原剧本分段方式
1.按照人数拆分
2.用户输入自定义范围
```

选择1后立即提示“请输入动画参与人数”，只接收并记录正整数；选择2后立即提示“请输入分段范围”，只接收并记录用户提供的分段起止内容。此时不创建`script-partition.json`，不计算正式切点，不输出正式分段模板。正式分段延后到标准化、资产筛选和美术设计交付完成后，按[剧本分工规则](script-partition.md)执行。宿主的输入框属于界面能力；Skill负责发出提示并校验收到的文本，宿主不支持附加输入框时使用同等文字提示。

完成分段意图记录后立即显示：

```text
请选择画布同步方式
1.自动同步资产和后续分段视频节点
2.暂时不同步
```

选择1只记录本项目的自动同步授权；尚无有效目标时立即提示“请输入画布的网址链接。”并记录网址。网址记录表示同步意图，不提前创建节点；实际CLI登录、画布绑定和资产任务提交须等正式资产交付完成后执行。选择2记录本项目不自动同步，不进行云端操作。意图、人数、范围和网址与当前原文版本绑定，来源或目标变化时不得沿用旧记录。

用户可以在同一条消息中提供风格、比例、分段方式、人数、自定义范围、同步选择和画布网址。已明确且通过校验的字段直接记录；只询问缺失或无效字段，不依赖聊天记忆代替项目记录。

视频比例仅用于视频：本期新分镜标题和新建视频节点按所选比例，新分镜单元同时冻结对应的`landscape-v1`或`portrait-v1`配置；`16:9`沿用现有横屏规则，`9:16`按[竖屏9:16配置](storyboard-rules.md#竖屏9:16配置)落实构图与运镜。画幅配置不改变文本输出模板、资产身份、来源事件、声音时长或历史回执。所有资产节点在两种视频比例下均保持16:9，资产图片版式和2K/high初始化参数不变。比例在正式分镜和后台任务中固定；更改当前默认只影响新请求，重试、重导出及已有节点沿用原批次设置，指定修改按实际授权处理。

## 准备与范围

风格与视频比例均已确认并保存后，使用实际运行本Skill的Python执行`scripts/ensure_dependencies.py`。Codex桌面通过workspace dependency loader取得解释器路径。只在该解释器缺少PyYAML（导入名yaml）时直接安装并重新导入验证，已有可用版本不升级；导入损坏、网络/权限或安装错误报告一次，不循环重试、不提权、不修改其他Python。

`director.py`只调用包内模块，不搜索或回退本机同名Skill。模块规则内的命令相对`<module>`解释；缺失文件或component.json摘要不一致时恢复完整包，不能跳过标准化/复核。模块维护后同步文件清单及摘要；本机独立的screenplay-standardizer不受影响。核心脚本仅用标准库，PyYAML按上述步骤准备；画布另需Node.js与官方CLI，仅在相应任务配置。

根据已明确请求直接运行`project-init`，不增加范围菜单：完整剧本且未限制阶段用workflow，只要资产/图片提示词用assets，只要标准剧本用screenplay。workflow在入口记录完成后继续标准化、资产筛选和美术设计；正式分段和分镜范围仍须等资产交付完成后确定，不把“完整剧本”当作全部分镜授权。

```text
python <skill>/scripts/ensure_dependencies.py
python <skill>/scripts/director.py project-init --project <project> --scope assets|workflow|screenplay
python <module>/scripts/screenplay_io.py prepare <input.docx> --workdir <workdir>
```

以上scope三选一。project-contract.json保存本次交付范围；资产init必须传--project。旧无契约项目保持原读取语义，不因更新自动补写契约。新项目与已绑定项目的状态入口按[恢复与画布](runtime.md#恢复与画布)区分；项目无法唯一定位时才询问目标。

## 阶段证据与验收

来源正文只在source.json维护，观察证据按[标准化协议](../modules/screenplay-standardizer/INSTRUCTIONS.md#逐场观察协议)记录，身份/取舍决定在台账审阅记录维护，制作方案在作者设计JSON维护。后续阶段引用现有ID、路径和出处，只补本阶段新判断与实际变化，不重抄全量事实或再建一份平行清单。协议要求的逐字片段、摘要和不可变回执继续完整保存。

result.json的checks引用实际报告路径、范围及结论，issues集中记录未决问题；语义说明可直接放checks中，已有说明文件则引用它，不要求另写逐资产长表复述JSON。作者首次实读和主代理独立验收仍完成各自判断；机检通过不自动变成语义通过。修订后复看改动及受影响连接/依赖，未变且绑定仍有效的证据与判断直接复用，正式提交仍核对最终版本。

过程报告按实际发生的性质组织，同一事项不在多阶段重复计成问题；无需新增报告文件或机器字段：

| 性质 | 记录依据与边界 |
| --- | --- |
| 实际缺陷与修订 | 曾保存的稿件或已执行交接中确有遗漏、矛盾或错误；写明影响对象、发现依据和实际改动，区分作者自查与主审反馈。主代理给错制作约束也属于实际缺陷。 |
| 必要确认与制作决定 | 声源归属、片段映射、身份/图片资格/空间关系等需要真实判断，但不一定改动稿件；与返修分开记录。机检待确认条数不等于错误数。 |
| 已检查的风险 | 实际核对且未出现的问题，只作为checks的验收依据；不能把“可能出错”包装成已发生又已解决的缺陷。 |
| 计量或工具限制 | 输入不可访问、阶段尚不支持该命令、起点漏记、观察元数据缺失等，说明实际影响与可核实范围，不编造故障、耗时或处理结果。 |

已关闭缺陷可在现有checks或过程说明中保留修订依据；当前未决项仍集中在issues。这些说明描述真实工作，不要求针对每条潜在风险另建台账、重复检查或增加审阅轮次。

## 标准化与观察

prepare → 少量代表样本 → 逐场整理并保存观察 → 处理影响原稿的跨场矛盾 → full review → assemble。样本覆盖本稿实际存在的风险：人物槽位与完整source_entry、声源/集合、引号、内心想法os或画外音、场界、基础与佩戴状态、资产解析；不为凑种类虚构样本。样本通过只允许扩写，不代替全稿通过。

首次实读各场时按[逐场观察协议](../modules/screenplay-standardizer/INSTRUCTIONS.md#逐场观察协议)同步记录后续所需证据，不等全文机检通过后再重读建立观察。影响人物行、声源、场界或依赖的真实跨场矛盾在全稿冻结前处理，纯制作名与图片范围判断留给台账。需要用户裁决时只问必要问题，继续不受影响部分。

```text
python <module>/scripts/scene_observations.py prepare <观察条目.json> --source <workdir>/source.json --workdir <workdir> --output <观察包.json>
python <module>/scripts/scene_observations.py check <观察包.json> --source <workdir>/source.json --workdir <workdir>
python <module>/scripts/screenplay_review.py <workdir> --report <全稿复核.json>
python <module>/scripts/screenplay_io.py assemble <workdir> --review-report <全稿复核.json> --output <标准剧本.txt>
```

confirmed及语义结论由真实阅读者填写，prepare/check不代签。`--previous-report`只复用输入未变且已验证的检查。sample/partial不得正式合并或绑定；完整出口见[复核协议](../modules/screenplay-standardizer/INSTRUCTIONS.md#复核协议)。screenplay范围到标准稿交付结束，不建立资产。

## 资产身份与最终图片范围

```text
python <module>/scripts/asset_ledger.py init <标准剧本.txt> <workdir> --project <project> --observations <观察包.json> --observations-review <全稿复核.json> --output <索引台账.json>
```

init仅导入当前审阅支持的已确认事实，其余保留观察队列；后补映射用scene_observations import写新候选，不能改旧观察摘要或猜ID。普通候选提及不自动阻塞，真正影响主体/声源/场界/依赖的未决事项沿待核机制处理。

统一核实全剧身份、世次、服装、物理LOC与时间层 → 合并已确认别称 → normalize制作名及重复群像/道具 → build-scenes建立SET → 冻结候选 → 审阅基础分类 → 必要佩戴形象 → 最终图片范围。SC场界由标准化规则判断，LOC/SET/移动PROP映射与候选筛选只按[台账规则](../modules/screenplay-standardizer/references/asset-ledger.md)，不在流程另设门槛。

跨段证据在冻结候选前用evidence_draft从明确原文范围准备，保留出处与原摘录。决定草稿绑定最终ID和同一候选输入，不边填结论边改底稿。命令及决定字段见[审阅决定工作区](../modules/screenplay-standardizer/references/asset-ledger.md#审阅决定工作区)。

```text
python <module>/scripts/asset_ledger.py normalize <身份与时间已核实台账.json> --output <归一化台账.json> --report <命名报告.json>
python <module>/scripts/asset_ledger.py build-scenes <归一化台账.json> --source <workdir>/source.json --output <候选台账.json>
python <module>/scripts/asset_ledger.py review-template <候选台账.json> <本批选择.json> --output <决定批次1.json>
python <module>/scripts/asset_ledger.py review-check <标准剧本.txt> <workdir>/source.json <候选台账.json> <决定批次1.json>
python <module>/scripts/asset_ledger.py review-apply <标准剧本.txt> <workdir>/source.json <候选台账.json> <决定批次1.json> --batch <决定批次2.json> --output <已审台账.json> --report <决定落盘对账.json>
```

各批责任不得重叠；review-check允许未选范围待核，全部收齐后一次review-apply严格验收并保存原批回执。回执形成后若normalize影响依赖会拒绝；需变化时另建候选并重审受影响决定，不刷新旧摘要。只在需要独立佩戴参考时依已审基础人物和PROP登记variants；最终核对图片范围、关联及决定回执。排除项带证据归档并退役ID，不留作无解释的inactive，也不删除历史。

```text
python <module>/scripts/asset_ledger.py validate <标准剧本.txt> <workdir>/source.json <最终候选台账.json> --candidate
```

候选身份、来源、命名及引用检查通过后才进入设计；candidate只是前检，不是正式图片提示词交付。preflight是兼容入口，不另重复运行同一检查。

## 设计与代表组验收

先按[短约束表](asset-image-design.md#初稿前的短约束表)汇总事实、已吸收候选的交互、可见范围与继承；可用design_inputs准备只读索引，但语义判断仍须实际阅读。先确定共享身份、基础造型、关联PROP和同LOC空间共性，再写差异项。

无跨资产继承风险的小项目可直接设计；存在佩戴PROP、多形象或父SET继承时，先预览覆盖实际字段类型的代表组，有SET则含代表SET，并保留佩戴依赖闭包。草稿保存即design_scope检查，再在完整候选上design-check并实读编译结果；字段用途通过不代替事实与美术验收。共享基线和代表组通过后扩展其余组，最后统一合并。

索引、freeze/plan/verify-group/merge、设计JSON及编译接口全部见[图片设计](asset-image-design.md)。只在冻结基线、责任ID互斥且无跨组可变依赖时受控并行；标准化和全剧身份统筹仍为单写者。全部正式文件由主代理写入。委派时先verify产物 → 实际验收 → close completed → 正式写入/编译/bind，不能以cancelled绕过未关闭交接。

## 完整交付与变更

```text
python <module>/scripts/asset_ledger.py design <标准剧本.txt> <workdir>/source.json <最终候选台账.json> <完整美术设计.json> --style-file <project>/video-style.txt --output <正式资产台账.json> --catalog-output <分类图片提示词.txt>
python <module>/scripts/asset_ledger.py validate <标准剧本.txt> <workdir>/source.json <正式资产台账.json>
python <skill>/scripts/director.py bind --project <project> --script <标准剧本.txt> --source <workdir>/source.json --review <全稿复核.json> --ledger <正式资产台账.json>
```

新契约使validate/render/bind自动要求完整设计；不能用候选前检代替。TXT只由同一最终JSON编译，不另维护一套设计。交付到任务outputs或用户指定位置：标准剧本TXT、正式资产台账JSON、分类图片提示词TXT，使用实际绝对路径给出文件链接；只报告实际数量、检查与具体阻塞，不输出后台JSON或把结构通过称为语义通过。

assets范围到三份交付结束，bind保存phase=complete。workflow在标准化、资产筛选、美术设计和正式图片提示词全部完成后，读取入口阶段已记录的画布同步意图。

入口记录选择自动同步时，复用已记录的目标，完成必要连接和绑定后提交一个整体`assets`任务；核实任务已启动或已进入有效队列后，继续正式分段，不等待资产任务完成。入口记录选择暂时不同步时，不进行云端操作，立即继续正式分段。当前阶段不重复显示同步选择，也不主动询问图片上传；显式上传请求仍走独立画布接口。已有明确授权不重复询问，后台异常只暂停后续自动入队，不覆盖前台本地位置。



## 正式剧本拆分与范围入口

资产交付完成后，workflow读取入口记录并执行正式分段。选择1时，入口记录中的人数作为分段数量，先按原文总字数计算目标切点，再在各目标点附近做局部事件连续性审查，前后生成候选并整体择点，按[剧本分工规则](script-partition.md)生成连续、非空且完整覆盖原稿的正式分段计划及起止摘录。该流程尽量均衡但不要求严格均分，不对整个剧本预先建立事件单元表，也不把正式分镜单元提前生成。人数超出合法候选切点数量或局部连续性约束无法满足时，说明实际限制并停止正式计划，不能擅自减少人数或切断事件。

入口记录中的自定义范围按当前原文、标准稿和资产交付版本逐段核对；全部范围必须唯一、连续、非空、无重叠、无遗漏。正式计划或自定义范围确认后，按[分镜工作流](storyboard-workflow.md)进入分镜范围。分段编号、整段重做轮次、段内续号由分镜交付模块维护，拆分模块不占用分镜号。入口阶段只保存意图字段；正式计划和正式范围完成后不得回写为旧菜单。

已绑定材料发生变化按[绑定变更边界](binding-changes.md)生成可追溯新版本；当前没有原位rebind，不手改session。纯制作名不改正文，真实剧情改动按标准化和台账迁移重审；未变报告/设计仅在依赖仍有效时复用。impact只给直接、依赖、未知影响与重编译/重审建议，不自动搬用通过结论。冻结是版本基线，不能靠刷新摘要接受新输入；旧项目、节点和图片不自动迁移。

## 实际进度与计量

### 计量对象与边界

标准化、资产、设计及旧交接统一由`scripts/pipeline_metrics.py`维护项目`pipeline-events.jsonl`，用status读取或派生报告，不维护平行计时状态。阶段时间、首稿/检查里程碑、具体处理窗口是不同尺度：stage-handoff-v2的阶段起止自动取prepare/close，包含分派、等待与验收；写者用`--handoff`追加本次交接内的真实事件，不另记started/finished。没有交接的准备或交付工序才使用`--project/--stage/--task-key`记录真实阶段起止。

每阶段记录实际首稿保存与检查结果；只有发生确认或修订时才开处理窗口，不逐资产、逐观察或逐诊断打点。事件版本用真实`revision-file`计算；机器检查只登记structure/source/package/inputs，semantic由实际阅读者登记。未通过按真实状态记needs_review或failed；缺少首检保持未知，不当作零错误率。

### 处理窗口的起止与分类

先判断本次工作属于哪类，再在开始处理反馈、重新阅读、编辑正文或编写修改脚本**之前**记录work_started。完成本轮修改和受影响范围复验后记work_finished；不能先准备好修改脚本，再把一次运行脚本的亚秒区间称为全部返修耗时。一次集中处理可涵盖多个相关对象，不把每条诊断算作一轮返工。

| category | 以触发工作而非发现阶段判断 |
| --- | --- |
| confirmation | 补归属、片段映射或其他决定，稿件内容不变。 |
| author_revision | 作者自行发现并修改稿件，即使发现于作者自己的复核阶段也属于此类。 |
| review_revision | 收到独立主审或用户的具体反馈后修改稿件；主代理修正自己先前传入的错误约束也按该实际反馈记录。 |
| tool_repair | 命令、临时脚本或运行故障的实际修复；不是普通待确认诊断，也不代替内容修订。 |

同一窗口使用相同work_id、category及parent-work-id；下一轮另开work_id。同一修订内的工具修复可用parent-work-id标识嵌套，区间按并集汇总，不能重复累计。若已经开始而漏记起点，保持完整窗口耗时未知，可记录仍能核实的结束或执行片段并注明覆盖局限；不根据文件时间、版本或事后回忆补造起点，不用短执行片段代表全部判断与编辑成本。

```text
python <skill>/scripts/pipeline_metrics.py record --handoff <handoff.json> --status draft_saved --revision-file <本批实际主稿或已有输入清单.json>
python <skill>/scripts/pipeline_metrics.py record --handoff <handoff.json> --status checked --check-kind structure --revision-file <该次检查的实际输入.json>
python <skill>/scripts/pipeline_metrics.py record --handoff <handoff.json> --status work_started --work-id <事项ID> --category <按上表选择的类别> --reason <实际处理原因>
python <skill>/scripts/pipeline_metrics.py record --handoff <handoff.json> --status work_finished --work-id <同一事项ID> --category <相同类别> --revision-file <处理后实际输入.json>
python <skill>/scripts/pipeline_metrics.py status --project <project>
```

### 汇总与历史

关联事件并入对应交接，独立手工任务单列；区间之和、并集、重叠和未覆盖时段分别展示，未结束窗口不伪造完成。阶段时间不能当作模型纯计算时间，处理窗口不能当作净可节省工时；模型和Token仅登记可信宿主元数据。

旧事件与交接原样保留。发现历史分类或覆盖范围记错，在当前过程说明中注明原标签、实际性质及可核实范围，不改旧事件来制造更好结果。报告按[阶段证据与验收](#阶段证据与验收)区分缺陷、确认、风险和限制；本节不另定义一套问题清单。
