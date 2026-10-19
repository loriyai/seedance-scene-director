# 资产生产主顺序

此页是标准化到资产交付的顺序入口。字段定义沿用模块规则，分镜与画布分别按各阶段参考加载。`<module>`表示`<skill>/modules/screenplay-standardizer`；所有输出写入项目目录，候选版本用新文件。已绑定项目按改稿迁移，不能重新init覆盖历史。下文涉及服装、佩戴、移动载体或时间层的步骤，仅在本稿具有相应对象或依赖时执行；示例不授权新增剧情事实。

## 入口、样本与逐场整理

确认完整剧本可访问，先保存用户已选风格；未选才显示入口菜单并等待。风格之前不解析全文或分派任务。按入口的范围判定初始化交付契约：完整剧本加已确认风格、未限定单一阶段时使用workflow；明确仅要资产使用assets，明确仅要标准稿使用screenplay。当前先执行资产阶段不改变完整任务范围，不增加一次菜单。

```text
python <skill>/scripts/director.py style --project <project> --choice 3 --file <用户风格.txt>
python <skill>/scripts/director.py project-init --project <project> --scope workflow
python <module>/scripts/screenplay_io.py prepare <input.docx> --workdir <workdir>
```

style示例只适用于用户选自定义；入口的其他选项沿用原值。prepare之后先做能覆盖真实异常的少量样本，检查人物槽位、完整source_entry、基础人与佩戴状态、非人物播报、引号/OS兼容及资产解析。样本通过只允许扩大整理，不代表全稿通过。

首次实读原文、逐场整理时同步保存[observations-v1观察](../modules/screenplay-standardizer/references/observations.md)：明确主体的外貌、服装和物件结构按visual_fact候选记录主体、属性及证据，瞬时状态另记；人物身份、地点和时间判断仍沿用对应观察类型。全部引用同一source.json段落与当前场稿摘要，未审仍pending；候选提及不会直接变成PROP，也不凭名称归并地点。无需逐名词建记录或在后续阶段重新抽取全文。

跨场检查先解决会改变人物行、声源或场次边界的问题，再执行全稿review→assemble。按内置规则区分叙事场次、摄影视角和资产：同一地点连续上车、下车、车内外切镜不自动拆场；车辆先作为移动物件候选，需独立车厢空间参考时另审用途，不能从镜头位置反推新SC或SET。真实转场、时间跳跃和实际地理变化仍保留。场次覆盖范围与地点命名分别判断，按[地点称谓与移动过程](../modules/screenplay-standardizer/INSTRUCTIONS.md#地点称谓与移动过程)保留原文主要地点称谓；路径和动作过程写在正文或连续性说明，不拼接成LOC/SET名称。纯制作命名、显示名和是否单独建图的判断留在台账，不能反向改动原文字句。真实矛盾只问必要问题，等待时继续不受影响部分。每场首次语义仍须实读；--previous-report仅复用经过验证且输入未变的检查。

```text
python <module>/scripts/scene_observations.py prepare <观察条目.json> --source <workdir>/source.json --workdir <workdir> --output <观察包.json>
python <module>/scripts/scene_observations.py check <观察包.json> --source <workdir>/source.json --workdir <workdir>
python <module>/scripts/screenplay_review.py <workdir> --report <全稿复核.json>
python <module>/scripts/screenplay_io.py assemble <workdir> --review-report <全稿复核.json> --output <标准剧本.txt>
```

观察包的confirmed由实际审阅填写，prepare不替人确认。只交标准稿时到此交付；其余范围继续。

## 资产身份与决定

```text
python <module>/scripts/asset_ledger.py init <标准剧本.txt> <workdir> --project <project> --observations <观察包.json> --observations-review <全稿复核.json> --output <索引台账.json>
```

init导入当前审阅支持的已确认事实，其余保留观察队列。影响人物身份、场界、声源或资产依赖且尚未解决的决定按既有待核机制阻止交付；普通候选提及不自动设为阻塞。后补目标映射用scene_observations import写新候选台账，不修改原观察摘要或推测ID。

在索引台账上统筹全剧身份、世次、服装、物理LOC与时间层，核实引用和原文事实。合并地点后再归一化制作名称及重复群像/道具；先建立SET，之后决定草稿才绑定最终ID。跨段证据在冻结前用[evidence_draft](../modules/screenplay-standardizer/references/asset-decisions.md)从明确原文范围生成单行证据，原摘录和出处集中保留；不等review-check报错后才改冻结候选。把这一步所得的完整候选保存为固定输入，审阅过程中不边填结论边改底稿。

```text
python <module>/scripts/asset_ledger.py normalize <身份与时间已核实台账.json> --output <归一化台账.json> --report <命名报告.json>
python <module>/scripts/asset_ledger.py build-scenes <归一化台账.json> --source <workdir>/source.json --output <候选台账.json>
python <module>/scripts/asset_ledger.py review-template <候选台账.json> <本批选择.json> --output <决定批次1.json>
python <module>/scripts/asset_ledger.py review-check <标准剧本.txt> <workdir>/source.json <候选台账.json> <决定批次1.json>
python <module>/scripts/asset_ledger.py review-apply <标准剧本.txt> <workdir>/source.json <候选台账.json> <决定批次1.json> --batch <决定批次2.json> --output <已审台账.json> --report <决定落盘对账.json>
```

按[审阅决定工作区](../modules/screenplay-standardizer/references/asset-decisions.md)逐批核对发声集合、背景群像、候选物件及退役项。review-check允许未选范围仍待核；每批真实证据与目标关系仍检查。各批必须同一输入且无责任重叠；全部收齐后一次review-apply严格验收并保存原批回执。不通过更新摘要让旧决定适配新底稿。回执形成后再normalize若影响其依赖会在写入前拒绝；确需改变则生成新候选并重新审阅受影响决定，保留旧版本。

需要独立佩戴参考时才执行variants，依据已审基础人物和独立PROP登记；再校验最终图片资产范围、关联及决定回执。不需要则跳过。variants不能给人物行新增第二个人。群像prompt_only与明确排除道具保留相应证据，后者归档并退役ID，不混在inactive待核区。进入设计前以`validate ... --candidate`检查来源/身份/候选；输出明确不是正式交付。

## 共享设计、样本与分组

写共享基线或单项初稿前，先按[短约束表](asset-image-design.md#初稿前的短约束表)汇总事实、被吸收候选的交互要求、当前可见范围和继承来源。候选最终归入CHAR/SET后，仍沿稳定目标ID读取其证据与处置理由，不能在进入设计时丢掉功能要求。

可用只读设计输入索引汇集已有记录：

```text
python <module>/scripts/design_inputs.py prepare <标准剧本.txt> <source.json> <候选台账.json> --style-file <project>/video-style.txt --output <新索引.json>
python <module>/scripts/design_inputs.py check <新索引.json>
```

已有共享设计时加`--shared-design <当前共享设计.json>`；选定范围用`--asset-id CHAR0001 SET0001`并自动带入既有依赖闭包，省略则索引全部图片资产。输出保留facts、absorbed_candidates原记录及继承引用，不自动生成交互事实。check重验来源文件、风格、可选共享设计、规则和编译器摘要及完整内容；过期须重建，不能编辑索引或替换旧摘要。current仅表示输入有效，短约束表和语义结论仍在现有审阅记录中引用binding_sha256保存。

先冻结身份锚点及分配、适用的基础造型、会被佩戴引用的PROP、同一LOC的空间共性。关联独立PROP的佩戴版本只补贴合关系、遮挡范围和实际可见身份；同一空间版本先继承共性，再填原文支持的差异。未明示的空间补全须标为制作设计，不写成原文事实。

无跨资产继承风险的小项目可直接做完整设计；大量设计采用[设计工作区](design-workspace.md)。出现佩戴PROP、多个同身份形象或父SET继承时，即使总资产少也先预览代表组。代表样本覆盖实际使用的字段类型，有SET时纳入一个代表SET；佩戴依赖闭包仍须完整。草稿保存即运行`design_scope.py <设计草稿.json>`检查字段用途；它允许不完整草稿，不代替原文/身份检查。再做完整候选上的design-check，按短约束表实读并记录实现情况；佩戴组同时看PROP单品板与人物佩戴板，SET检查照明与展示字段分离。共享基线与代表组通过后扩展其余组。

freeze/plan输出JSON由主代理捕获到新UTF-8文件。每组拥有互不重叠的可写ID及显式只读依赖；同身份未冻结变体归同组，跨组仍可变的依赖拒绝。标准化、全剧身份统筹和连续分镜不能借此任意并行。

```text
python <module>/scripts/design_workspace.py freeze <标准剧本.txt> <workdir>/source.json <最终候选台账.json> <共享设计.json> <project>/video-style.txt
python <module>/scripts/design_workspace.py plan <基线.json> <组别.json>
python <module>/scripts/design_workspace.py verify-group <基线.json> <分组计划.json> <组稿.json> --group-id <组ID>
python <module>/scripts/design_workspace.py merge <基线.json> <分组计划.json> <组1.json> <组2.json>
```

所有组由主代理在一个入口合并成原格式完整美术设计JSON。合并与提交都重查原文件摘要，既检查组责任范围，也运行原完整编译器；不能把局部设计预览改名当最终台账。使用子代理时，先verify产物、完成实际验收并close completed，再由主代理正式编译/assemble/bind改变交接输入；未关闭交接会阻止bind，不以cancelled代替已完成的正常关闭。

## 完整交付与变化后的复核

```text
python <module>/scripts/asset_ledger.py design <标准剧本.txt> <workdir>/source.json <最终候选台账.json> <完整美术设计.json> --style-file <project>/video-style.txt --output <正式资产台账.json> --catalog-output <分类视觉描述.txt>
python <module>/scripts/asset_ledger.py validate <标准剧本.txt> <workdir>/source.json <正式资产台账.json>
python <skill>/scripts/director.py bind --project <project> --script <标准剧本.txt> --source <workdir>/source.json --review <全稿复核.json> --ledger <正式资产台账.json>
```

新契约使validate/render/bind自动要求完整设计，不依赖执行者记得附加--require-designed。只有显式--candidate可作候选前检，不能据此交付。旧无契约台账读取兼容，不自动补写契约、重绑定或迁移画布。

assets范围到三份交付即结束，bind保存phase=complete，不主动要求传输、上传或选择分镜范围。workflow范围继续既有资产菜单：已授权传输则排队后进入分镜范围，选择不传输同样进入范围；未给范围才提示输入，已明确给出则直接按[范围定位](storyboard-workflow.md)检查。未获授权不提交画布任务。阶段交付后用户追加分镜请求时，按该工作流的续接分支复用有效绑定，不重做未变资产。

变化前后用`asset_ledger.py impact <旧台账> <新台账>`及可选source/design快照生成直接、依赖与未知影响报告。报告绑定输入，区分语义重审与重编译建议，不自动复制通过结论。原文改变仍按全局未知依赖保守失效；下一步自动增量复用需另行设计验证。

## 实际进度与计量

来源全文保存在source.json；辅助语义记录以场次/资产ID、段落引用、输入摘要、具体判断及必要短引文为主，不在每层交接报告重复拷贝全文。正式review与ledger协议必需的证据字段照常保留。复用公共prepare/check工具及同一证据草稿，避免每个阶段重写一次临时包装脚本；不得为节省记录删除首次实读、待核项或依赖失效检查。

委派阶段直接读取handoff的prepared_at和closed_at，标为“交接准备至关闭”，其中包含分派、等待和主代理验收。手工事件另列，不相加；未记录风格确认、准备或其他前后区间时，全流程耗时记为未知，不把后补的started记录当作实际任务起点。`pipeline_metrics.py status --project <project>`同时显示并集、重叠和空档，不能把等待重复加到同一阶段。

进度用`scripts/pipeline_metrics.py record --project <project> --stage <standardize|assets|design|review|delivery> --task-key <任务ID> --status <状态> --object-id <ID> --input-digest <当前摘要>`记录，status汇总。只登记真实事件；changed输入独立计首检，返工记needs_review及原因。同一对象当前状态只计一次，完成窗口与任务耗时之和分列，避免并行叠加被误报总耗时。宿主未提供实际模型/Token时留空。用户反馈用完成数、剩余范围和具体阻塞，不把这些日志当已证实的提速比例。
