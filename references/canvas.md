# 小云雀 CLI 画布接口

本文件仅供后台执行。所有路径用绝对路径，JSON使用UTF-8；用户名称、提示词和链接通过参数数组或文件传入，不拼接shell命令。常规资产/视频/引用/图片任务使用[后台队列](canvas-jobs.md)的`canvas_jobs.py`；配置、绑定、预览和精确编辑/删除继续使用`canvas.py`；不需要另装小云雀Skill或MCP。

## 首次连接与绑定

准备：小云雀账号、本人可编辑的目标画布链接；批量资产操作需要本项目已验证的资产台账，视频节点需要已commit的分镜。图片可后续添加，创建空节点不要求提前准备图片。电脑需Node.js 16或更高版本；未安装时提示安装，已有可用Node.js可传绝对路径。Python使用当前环境提供的解释器。

```text
python <skill>/scripts/canvas.py setup --project <project> [--node <node绝对路径>]
python <skill>/scripts/canvas.py auth --project <project> --login
python <skill>/scripts/canvas.py bind --project <project> --target <画布链接或真实画布ID> [--team-id <空间ID>]
python <skill>/scripts/canvas.py status --project <project>
```

`setup`检测现有官方CLI及本Skill实际调用的命令，缺少时用包内`ensure-cli.js`安装到官方CLI缓存；不每次安装或升级。必要原生命令为status/login、canvas get/allocate/apply/upload及space list；canvas get必须支持--team-id。必要语义命令覆盖快照、权限、apply_mutations、节点创建/更新/删除（含update_asset）、引用连线的create_edge与精确删除引用边的delete_edges、提示词写入、角色/场景描述及角色主图更新。create_edge用于派生角色图片引用及历史黑幕引用；新none-v1视频仍不创建黑幕连线。角色updateAppearance须支持patch.generationPrompt与expected.generationPrompt，以及派生图片引用所需的patch.generation与expected.generation整体配置字段；不能只凭命令名存在认定支持。create_checkpoint只在权限支持时使用，保持可选；不探测本Skill不调用的模型列表/描述、生图、生视频、超分、擦字幕、积分、query-result、logout或canvas create。`auth --login`先检查status，已有登录不重复打开授权页。首次提示用户完成浏览器授权；CLI自行保存安全凭据。配置文件只保存CLI/Node路径、版本和公开账号标识，禁止保存密钥、完整status、认证环境变量或浏览器存储。CLI报401/403时先判断账号/权限；仅确认浏览器凭据过期或撤销后使用`auth --login --force`，不将force作为常规操作。

链接按完整域名与路径共同解析，支持三种已核实的官方入口：`https://xyq.jianying.com/novel/detail/canvas?...`为个人空间候选；`https://xyq.jianying.com/project?...`与`https://www.xiaoyunque.com/project?...`均为团队空间入口，不能仅因域名含`xyq.jianying.com`就选择个人空间。严格校验域名、路径及唯一ID参数。收到完整链接时原样传给bind，由统一解析器保留projectId、canvasId及空间线索；不为绕过链接解析而抽出canvasId改走裸ID入口。入口分类用于选择候选空间，仍须只读核验画布身份与编辑权限后才保存绑定。overviewPippitAssetId须在选定空间后通过官方CLI解析实际canvas ID，不能把overview或project ID当成canvas ID。所有远端ID及TeamID保持字符串。

绑定先复用同账号、同画布已保存的team_id；首次绑定可用`--team-id`明确指定（个人为`0`）。没有明确值时，旧格式链接选择个人候选，新格式链接结合`space list`在只有一个团队时选择该团队候选；裸ID沿用CLI当前空间作为候选。只读核对该候选下的画布身份及编辑权限，通过后保存account_id/canvas_id/team_id和空间选择依据；不从查询响应回显的team_id反推归属。多个团队不能唯一确定时返回SPACE_REQUIRED及选项，失败返回SPACE_LOOKUP_FAILED，不遍历团队、不回退个人、不修改CLI全局默认空间。已绑定画布后续不重复选择。

CLI的原生查询、分配、上传与语义查询、创建、编辑、删除、检查点及回读由同一入口自动附加已绑定--team-id，不由各业务分支猜测参数。新进程恢复也使用该绑定。旧绑定缺少空间时，先对同一目标重新执行bind并核对必要信息，保留节点映射；不得静默采用当前默认空间。绑定验证失败不覆盖原绑定。

明确切换目标后用`bind ... --switch`，原绑定及映射归档。当前有效连接保存在`canvas/connection.json`，绑定、节点映射、操作日志及待同步项保存在`canvas/state.json`。每次实际操作核对当前CLI账号，账号变化先恢复或重新绑定，不跨账号复用映射。

## 新批次无黑幕策略

新提交/选定替换单元由director.commit固定 `black_reference_mode: none-v1`。videos资产盘点仅处理人物、布景和道具；不解析、获取、新建或改名黑幕节点，不新增黑幕reference边，不查询黑幕素材，也不把缺黑幕列为待处理。资产正式标签、时长、逐步ACK、最终回读均保留。已有黑幕及其连线不删除。以下涉及黑幕的说明和命令只用于历史receipt明确保留的旧模式兼容，不是新制作步骤；运行中的冻结任务按原版本继续，安装不迁移任务。

## 资产与视频节点

新建角色role、场景scene及道具image节点时，统一通过`create_biz_node.initialData.generation`设置图片默认参数：`resolution: "2K"`（清晰度2K）、`effort: "high"`（推理强度高）、`ratio: "16:9"`（比例16:9）。这三项属于节点图片配置，不写成提示词文字，也不套用到video节点。资产批量任务、直接assets入口及videos流程补建资产共用这套创建参数；创建后逐项回读，不以创建ACK代替参数验收。

资产的完整`image_prompt`始终以`content.generation.prompt`为验收字段，与外观/资料描述独立。按节点类型使用以下契约：

| 节点 | 写入入口 | 保留要求 |
| --- | --- | --- |
| 角色role | `xyq.role.updateAppearance`的`patch.generationPrompt`；`expected.generationPrompt`使用刚读到的原值 | 保留外观描述、主图及generation其他配置，不用普通图片/视频提示词接口写角色 |
| 场景scene | 新建时使用`create_biz_node.initialData.generation.prompt`；已有节点由`update_asset`写入从最新快照完整合并的generation | 保留其他生成字段和包装层，不提交仅含prompt的浅层generation对象，不虚构scene.updateAppearance |
| 道具image | `xyq.generation.update_prompt` | 保留当前生成配置与引用 |

角色`updateDescription(field=appearance)`与场景`updateDescription(field=description)`只用于明确的描述编辑，不能代替生图提示词写入。三条资产入口——后台assets、直接assets、videos补建——共用canvas.py的资产初始化步骤与验收。创建后完成显示名/basicInfo.name、空形象/视角名和正确提示词回读；SDK工厂默认显示名不是最终名称。仅新建/未完成初始化节点检查上述默认图片参数，ready或采用节点的人工配置保持。所有操作仅配置节点，不触发生成。

派生角色图片按[图片设计](asset-image-design.md)的已编译`asset-image-reference-v1`合同读取基础角色与道具依赖，按依赖顺序创建节点；旧台账没有该合同的独立提示词保持原样。完整制作名引用在写入时转换为查询确认的真实`<node-asset>`标签，并通过官方`create_edge`建立指向派生角色的reference连线。引用名称、节点ID、Pippit素材ID和云端媒体assetId分别核实，不互相代填。

角色图片引用使用`xyq.role.updateAppearance`的`patch.generation`保存从最新快照完整合并的生成配置，同时传`expected.generation`；仅改文字时仍使用上表的`generationPrompt`。`generation.references`保存源节点身份、`type:image`、实际主图的`pippitAssetId/assetId`；基础角色还用`dataReferenceSelection`明确选择该图片，不能混入声音。保留模型、比例、清晰度、推理强度、无关引用、外观描述及当前主图；人工改过的受管理引用不得直接覆盖。不得对角色调用图片/视频专用的`xyq.generation.update_prompt`。

节点与提示词初始化完成、引用连接完成、参考图片输入就绪分别验收。缺图片时仍允许创建节点及写入引用提示词，记录`image_input.status=pending`及缺项，不静默退回独立文生图，也不标记图片输入就绪。只有真实参考节点、所选图片、媒体身份、完整提示词、连线和保存的引用条目全部回读一致时，才记图片输入配置`ready`。这个状态只表示配置已核验，不证明实际生成服务已消费参考图或成图身份一致；最终成图仍须实际检查。已知写入中断按原日志和ID续接，恢复图片后仅在用户授权的资产同步中重新解析输入，不自动恢复暂停队列。

默认参数仅用于新建节点及按原ID续接本版本未完成的初始化。已完成或复用节点保留当前模型、比例、清晰度、推理强度、图片与其他配置；不因Skill升级改写既有节点。历史创建日志没有这些参数时按原证据续接，不把新默认值追加为历史恢复门槛。

```text
python <skill>/scripts/canvas_jobs.py enqueue --project <project> --kind assets --start [--decisions <选择.json>]
python <skill>/scripts/canvas_jobs.py enqueue --project <project> --kind videos --request-id <本批提交ID> --start [--decisions <选择.json>]
python <skill>/scripts/canvas_jobs.py enqueue --project <project> --kind videos --ids 场景1-分镜1 场景1-分镜2 --start
```

`assets`后台任务冻结session绑定台账，创建role/scene/image业务节点并写入完整image_prompt；各节点按本节字段契约写入generation.prompt，描述不作为提示词验收。只有最终字段与ID映射全部核验才算完成。仅需图片的角色使用台账完整name；visual_mode为prompt_only的群像不建节点、不匹配上传图片、不列为缺图，场景使用scene_assets[].name及SET ID，每个SET仅一个节点（包括有证据的时间版本和布置版本），SC heading不参与资产建节点，道具使用主name；角色/场景同时设置节点显示名和basicInfo.name，basicInfo.appearanceName为空。视频普通业务节点的name/title/caption逐字使用对应已提交单元display_name，例如 `A01-场景3-分镜2`；无新名称的旧单元继续使用原ID，不附加时长、镜头标签或布景名称。完整提示词直接由当前有效单元按其输出版本渲染写入generation.prompt，保留固定抬头、视频风格、换行和人物/场景前缀；不调用生成命令。

显式执行`videos`时，先全量读取目标画布一次，用同一份节点索引逐项核对当前台账全部需图片的角色、布景和主要道具，不只核对本批分镜所用资产，不比较节点总数。已保存的真实ID仍在当前画布且完整名称、类型一致时复用，保留该节点当前主图和形象/视角；无映射或原ID已不存在时，按完整name+kind查找，唯一候选自动复用并保存映射，没有候选时先列为缺失。全量盘点结束后统一统计：缺失0至10个可自动补建对应role/scene/image空节点，执行首次初始化并保存CLI真实ID；缺失超过10个，在任何资产补建及视频写入前停止，返回完整missing_assets清单（资产ID、完整名称、类型、原节点ID）和缺失数量，不分批绕过阈值。有多个候选或已绑定资产改名/类型变化时，列入待处理，不自动选择候选或覆盖冲突字段；其余合法项可继续。画布额外节点保留，不改名、不删除。复用阶段1资产传输已保存的ID，不额外按名称重新寻找有效映射；恢复时沿用已确认进度，不重复创建。已完成资产允许用户自行修改生图提示词、图片和图片参数；视频准备不比较或写回这些内容，也不刷新其内部派生图片引用。仅校验真实ID、类型、完整名称以及视频所需的正式引用，不以图片就绪为前置条件。新建或尚未完成初始化的资产仍完成首次配置及回读。新增节点更新本次内存索引，开始时一次全量盘点不代替每次写后的回读。

仅当历史receipt的已保存模式要求黑幕时，才在同一准备阶段使用这份节点索引检查独立“黑幕”视频节点；none-v1不执行本段步骤。有效已保存ID须仍为video且完整显示名为“黑幕”；有效映射优先复用。无有效ID时，按完整名称“黑幕”与video类型唯一匹配并保存；没有候选则分配真实ID、立即保存allocated状态，创建video空节点，在同一ID上把name/title/caption设为“黑幕”，写后回读再保存ready。多个同名video、已绑定video人工改名或改类型列为冲突；用选择文件中的`BLACK`键按真实ID与当前fingerprint解决，不自动选择候选或覆盖冲突字段。黑幕独立于剧本资产与分镜单元，不进入PROP台账、不计分镜数量/时长及分镜节点创建统计；创建和改名不写剧情prompt、不设置分镜时长、不触发视频生成。新建位置避开现有节点。`black_node_id`与`black_node_kind`记录默认节点，`black_video_preparation`记录allocated/created/ready步骤。进程在分配或创建后中断时沿同一ID续接；未确认日志须先只读核对，不能重建或盲目重放。

旧项目默认黑幕为image时，保留原图片节点与连线，将已引用它的现有视频映射固定为各自的`black_node_id`与`black_node_kind=image`，原ID另存`legacy_black_node_id`；仍按历史黑幕模式处理的新增节点使用video默认节点；none-v1不使用黑幕。升级安装不改写旧云端提示词或旧回执，后续引用刷新和局部修改继续使用单元已固定的旧节点；不会把旧image改类型、改名或删除。`references`及自动局部replace只消费已绑定黑幕或唯一可用候选，不创建黑幕节点。

已固定的黑幕video被删除后，显式`videos`可在本次选择范围内把失效video引用更新到已复用或补建的黑幕video；范围外单元不改，旧image引用不迁移。单独`references`或局部replace只报告节点缺失，不补建或迁移。

视频默认编码正式节点引用；空资产节点照常写入真实ID标签，不因缺图片而保留文字占位或阻塞创建。新批量videos从已取得的快照记录缺图/缺视频诊断，不为此补发媒体查询，不因缺媒体阻止节点引用或任务完成。历史串行videos保留media_readiness_checked=false，未检查的缺图数量为null而非0。显式references及上传后刷新仍区分节点引用和媒体就绪，保留原缺图诊断。后台结构化检查结果保存于`canvas/reference-check.json`，用户检查TXT默认与最近receipt交付TXT同目录；不在对话展示内部JSON或标签。失败只CLI回读确认并记录，不清空提示词，不用网页点击、浏览器编辑器或模拟粘贴补救。

黑幕video节点即使没有视频素材也可写入真实节点引用；显式references的媒体诊断独立列“黑幕视频素材待补”，不误记缺图片或引用失败。`missing_video_count`与`missing_image_count`、`reference_issue_count`分别统计；同一个黑幕节点合并列出相关单元。素材存在不等于生成结果已经验收；本Skill不自动制作或上传黑幕视频。旧image黑幕仍按旧图片就绪规则报告。

视频新节点使用小云雀默认的Seedance 2.5和720P；不调用模型列表/描述，不查询、强制写入或校验模型与分辨率，不因为这两项的差异产生切换或跳过确认。历史未保存批量协议的任务继续按以下四步串行执行；新任务采用[本地编译与批量提交](#本地编译与批量提交)：

1. 创建video业务节点，立即保存CLI分配的真实节点ID和本步官方确认；此时不要求默认name/title/caption已等于最终分镜编号。
2. 在该真实ID上改名，name/title/caption逐字设为已提交单元display_name（旧单元无此字段时为原ID）。
3. 设置generation.durationMs，数值为单元duration秒乘1000。新单元的video_ratio必须为16:9或9:16，并随单元及后台任务冻结；新建视频的initialData.generation.ratio使用该值，同一真实ID未完成初始化时继续核对及写入。已完成或复用的视频和没有video_ratio的历史单元保留原比例，不追加比例写入或验收条件。嵌套generation从最新内容合并，保留modelKey、resolution、prompt、references及其他配置，不修改已有媒体的实际durationMs。
4. 新none-v1直接写入已编码图片资产引用的提示词；对历史edge-only-v1单元先按真实黑幕源节点和目标节点检查或建立独立reference连线，记录本次连线写入的官方确认；再从当前有效单元渲染完整提示词，以真实节点ID编码其他资产引用，通过官方提示词命令写入。正文不含黑幕标签，连线子步骤失败或ACK不明须停止后续写入并沿同一真实ID核对续接。

每步记录命令和官方确认；四步全部获得确认后统一读取目标视频，核对name/title/caption、generation.durationMs、本轮初始化适用的generation.ratio、完整generation.prompt、真实节点标签及reference连线，并按下文引用规则核对适用媒体引用条目。单节点JSON回执保存于`canvas/video-write-receipts/`，记录真实节点ID、各步骤确认和名称、时长、提示词、引用的核对结果。最终全部核对通过才保存整节点完成回执；核心名称、时长或提示词未通过时返回`UNCERTAIN_WRITE`并保存`uncertain`回执，核心字段通过但引用尚未通过时保存`partial`回执，不标整节点完成。历史串行videos不检查图片是否就绪；新批量任务及显式references的缺图片单独记录待补，不判定为节点引用失败。各步骤确认不能代替整节点回执。创建阶段不做最终名称回读门槛，最终名称检查在改名、时长和提示词都完成后执行。复用已有视频跳过已确认的创建步骤，在同一真实ID上续接其余步骤。

视频首次写各字段前保存字段级初始化基线，即使节点尚未ready也保留该字段的初始值。恢复时按各字段已确认的写入值或初始化基线核对，不把中断后新读取的人工修改吸收为基线；实际冲突先处理。ready节点的name/title/caption三个名称字段全部受保护，不只检查name。已同步的时长保存基线；分镜改时长自动跟随，云端人工改过时长则先处理实际时长冲突。名称和提示词的云端人工修改也按各自基线处理；模型、分辨率不属于本流程的冲突条件。单独`references`只刷新已有映射视频的提示词引用，不改name/title/caption、时长或其他生成配置，也不创建视频节点。

不传批次或ids时仅选择最近一次提交的单元；指定历史批次仍取这些编号的当前有效版本。不得把所有历史receipt都当成新节点。范围由Codex按完整display_name、A03等短号或原ID解析为明确分镜ID数组；画布映射与选择文件仍使用原后台ID，不能用一个普通连字符拆开`场景X-分镜Y`。

返回`planned`为预览，`needs_decision`表示需要用户处理的同名、缺失或云端修改冲突，无冲突时直接执行已授权范围，不强加一次审批。直接`assets`及视频节点同名候选的`existing_name`含节点ID及fingerprint，让用户选择复用；显式`videos`的资产准备按上一节自动唯一匹配/补建，多个资产候选仍不得自动选择。后台生成选择文件：

```json
{
  "CHAR0001": {"reuse": "真实候选节点ID", "fingerprint": "本次查询摘要"},
  "BLACK": {"reuse": "完整名称为黑幕的真实video节点ID", "fingerprint": "本次查询摘要"},
  "场景1-分镜1": {"overwrite_fingerprint": "用户明确选择覆盖的当前节点摘要"},
  "场景1-分镜2": {"skip": true}
}
```

选择文件只能根据用户的实际选择填写；不自动填覆盖。候选或摘要变化重新核对。多个候选不能默认取第一个，用户选定后使用该候选真实ID及本次fingerprint继续。已有映射正常复用；直接`assets`中绑定节点被删或类型变化时说明实际情况，由用户决定恢复/重绑。用户明确要求重建缺失节点时，在该资产选择中填写`{"recreate_missing":"查询确认已缺失的原节点ID"}`；若出现同名候选仍需明确reuse。显式`videos`的资产准备已获自动唯一匹配及缺失不超过10个时的补建授权，无需逐项确认，但不因此自动填任何云端覆盖选择。资产引用准备保留当前主图及形象/视角名称；直接改写资产名称、描述或默认形象/视角，以及视频节点名称、提示词或参数时，仍检查各自保存基线并处理实际冲突。

大批量操作在浏览器授权支持时先建检查点。新任务按下节批量协议执行；历史未保存该协议的任务保留逐节点串行写入和整组字段回读，创建后有限核对业务资产就绪；明确拒绝记`rejected`，未知ACK记`uncertain`。允许有限重试的拒绝及旧版误分类日志恢复严格按[后台队列的资产恢复规则](canvas-jobs.md#资产就绪拒绝与恢复)执行；未知结果无法核对时返回`UNCERTAIN_WRITE`并停止，不当普通失败重试。视频按创建、改名、时长、正式引用提示词四步取得官方确认后统一回读。ID由CLI真实分配并在创建前保存，可恢复进程在已确认步骤之间中断的情况。视频步骤收到实际拒绝、未知或无法确认的官方ACK，立即停止后续写入并保留真实ID、命令确认和步骤进度；先读取画布核对远端状态，按官方支持的恢复流程解决未确认步骤，再在同一真实节点ID上续接，不重新创建。未确认状态仍无法核对时返回`UNCERTAIN_WRITE`，不得把该节点或整个批次标为完成。不要删除CLI隔离的未确认事务或换request ID盲目重放。项目`operation.lock`及系统临时目录中对应账号/画布摘要的`seedance-canvas-*.lock`只在确认旧进程已退出后才能移除；不批量清理其他项目的锁。`status`可在未知写入后读取节点与未确认操作列表。

仅当用户明确授权重建并填写`recreate_missing=旧真实节点ID`，且当前snapshot确认该视频节点已不存在时，才将旧未确认日志保留为`superseded`并分配新ID；不重放旧写入，不删除历史或CLI隔离状态。

## 本地图片批量上传

主流程取消上传询问；独立上传能力仅在用户明确提出上传请求时使用，届时才询问图片文件夹路径；已有明确路径直接使用。未选择上传时不询问路径、不运行本节命令。PNG/JPG/JPEG/WebP图片可混放，支持子文件夹；文件名主体必须与角色完整name、布景资产完整name或主要道具name逐字一致。建议一项资产一张图片；同名不同扩展名或分散于不同子目录仍视为重名，不默认选第一张。布景资产name含Windows文件名禁用字符时报告并先处理台账命名，不把替换后的文件名静默映射回旧名称。

```text
python <skill>/scripts/canvas.py images --project <project> --folder <图片文件夹绝对路径> --preview
python <skill>/scripts/canvas_jobs.py enqueue --project <project> --kind images --folder <图片文件夹绝对路径> --start [--decisions <选择.json>]
```

先扫描并核对已保存的资产节点映射；未建节点列入unbound_assets，不按同名猜测。重复文件、缺失文件、未匹配文件和文件头错误分别返回duplicates/missing/unmatched/invalid；继续其余合法项。已有图片默认保留，返回retained与existing_image。只有用户明确选定替换后，把该冲突返回的replace_image对象原样写入选择文件；文件摘要或节点摘要变化需重新核对：

```json
{
  "CHAR0001": {"replace_image": {"fingerprint": "本次节点摘要", "file_hash": "本次图片摘要"}},
  "SET0001": {"skip": true}
}
```

串行使用官方`canvas upload --path ...`上传素材，再绑定到已映射节点。上传素材ID与节点ID分别保存，不能混用。角色通过`xyq.role.updateAppearance`的mainImage选主图；场景合并appearanceInfo和sourceInfos并选主图；道具图片节点写入实际pippitAssetId及source。图片来源为Upload，使用CLI实际返回的素材ID，不保存临时下载链接；保留角色/场景默认名称为空、描述及其他字段。

文件SHA-256、上传状态和真实素材ID保存在本项目画布状态中；相同文件复用已上传素材，已绑定且内容未变计入unchanged。CLI返回processing时保存ID，仅用`canvas get --asset-id ...`续查，素材可查询后绑定，不再次上传。未返回ID的上传结果不明返回UNCERTAIN_UPLOAD，先核对个人素材库，不直接重试。绑定写后回读，上传或绑定中断保留已完成进度。

上传完成自动刷新已有映射且涉及这些资产的视频节点引用，不新建视频节点、不生成视频。用户也可在画布中手动添加图片并选主图，随后明确要求运行`references`刷新和校验；不承诺补图后所有媒体引用会自行刷新。后续新建视频节点默认使用真实节点引用。待刷新资产保存于pending_image_references；图片已成功但提示词冲突时保留图片和待处理项，按用户选择继续`references`。缺少图片只记录，不撤销已有正式节点引用；没有映射的图片资产保留文字@并报告；历史edge-only-v1黑幕仅报告引用待处理，不能补回黑幕文字，旧单元继续兼容原标记。`status`返回待刷新资产和未完成上传，恢复时先核对。

素材身份分别保存pippit_asset_id、source_id和media_asset_id；上传响应的旧asset_id属于source/workspace ID，不能写入节点媒体assetId。再次查询素材时核对同一Pippit ID、图片类型及Image.asset_id；云媒体ID未就绪保持processing，只沿同一ID续查，不重新上传。主图回读同时核对Pippit ID、云媒体ID与选择状态。上传缓存按账号、已绑定TeamID和文件hash隔离；旧无空间缓存保留并提示核对，不自动当成新上传重试。

## 本地编译与批量提交

新资产、视频和引用任务冻结 `options.sync_protocol=sdk-local-batch-v1`。CLI适配归 `scripts/canvas.py` 与 `scripts/canvas_cli_batch.js`，分组执行、事务及回执归 `scripts/canvas_batch.py`，队列冻结和调度归 `canvas_jobs.py`、`canvas_job_backend.py`；资产初始化、派生图片引用计划及节点验收仍在 `canvas.py` 共用，不在项目中另建绕行脚本。

先盘点并解决资产身份与冲突；视频准备自动补建的上限为10个，按整次盘点统计，超过10个不补建、不写视频。新旧任务分别按冻结协议处理，未记录批量协议的历史任务保留原3个上限及串行恢复方式，不改旧manifest、ID或回执。

按依赖顺序预申请真实节点ID并持久保存，默认每批10个节点。这是本地分组参数，不是官方上限或实测最优值。通过已安装官方SDK的隔离文档执行业务工厂、各类型字段写入和引用解析；不启动网络同步、不改SDK源码，不自己仿造引用条目。基础资产与派生资产在同组时先在本地完成所有基础节点，再编译派生引用。官方注册命令在本地仍逐项执行完整业务校验，仅将最终SDK文档差异合并为一个事务；不把多个注册命令直接塞进远端apply_mutations，也不把其dryRun当完整业务预演。

每批统一读取官方快照与资产版本，核对目标和引用源未变化。视频本地一次准备名称、毫秒时长、适用比例、完整提示词、实际选材及连线，已有generation从最新内容合并。通过公开 `canvas apply` 提交一个transaction内的多条patch，使用真实资产版本；不用隐藏传输参数、不绕过ACK校验、不发送HTTP私有请求。事务发送前保存 `canvas/batches/` 请求及摘要、原节点ID和逐节点预期字段，整批ACK仅表示事务已确认。

写后统一回读本批，逐节点核验名称、类型、提示词、参数、完整引用条目及reference连线，每个节点仍保存独立回执。节点没有图片时仍建立真实节点引用及连线，只记录missing_image；不阻塞同步、不要求补图、不把节点ID当成云媒体ID，也不宣称图片输入已就绪。有媒体时保留官方图片/声音选材。批次未知确认或最终回读失败立即停止，沿原请求和ID只读核对，不重放整批、不重新分配ID；暂停前确定未发送的事务记cancelled，已发送但未知的仍保留pending/uncertain。只有实际字段与引用全通过才推进完成计数。安装升级不自动切换正在运行的旧worker。

## 节点引用与黑幕

```text
python <skill>/scripts/canvas.py black --project <project> --node-id <完整名称为黑幕的现有video节点ID>
python <skill>/scripts/canvas_jobs.py enqueue --project <project> --kind references --request-id <本批提交ID> [--decisions <选择.json>] --start
python <skill>/scripts/canvas_jobs.py enqueue --project <project> --kind references --ids 场景1-分镜1 场景1-分镜2 --start
python <skill>/scripts/canvas.py references --project <project> --request-id <本批提交ID> --preview
```

`references`只处理已映射视频节点，未创建的编号列入unbound_units，不自动创建。角色、场景及道具资产的节点映射有效时，即使图片为空也编码真实节点标签并尝试正式节点引用；不以先上传图片为前置条件。没有映射或映射冲突的图片资产保留`@台账完整名称 `（名称后英文空格）并记录。edge-only-v1单元的BLACK无论提示词内容如何都属于后台引用清单，黑幕无法确定时报告引用失败但不插入黑幕文字；旧单元保留独立行`@黑幕`的兼容行为。黑幕优先使用该单元已固定的ID，否则使用项目已保存的默认video节点；旧项目既有image绑定兼容保留。没有有效默认ID时可只读解析完整名称恰为“黑幕”的唯一video候选；没有候选或有多个候选则记录，不自行创建或生成。`black`命令仅显式绑定完整名为“黑幕”的现有video，不要求提前有视频素材；绑定前保存旧单元的image引用，不自动刷新任何旧单元。已确认的节点引用、图片待补和黑幕视频素材待补分别计数，不把文字@当成实际引用，也不把缺媒体自动判为节点引用失败。

转换限定资产行的@位置、镜头说明和台词说话人前缀，使用真实`<node-asset>节点ID</node-asset>`。不替换台词引号内文字、不改原交付TXT。图片资产通过官方`xyq.generation.update_prompt`导入引用；历史edge-only-v1黑幕通过官方`create_edge`独立建立`type: reference`、source为黑幕真实节点ID、target为当前分镜真实节点ID的连线，已存在的同源同目标reference边复用，不因提示词去标签而删边。回读prompt、实际源节点与画布reference连线，并按官方SDK的引用类型核对适用的generation.references条目身份、类型和dataReferenceSelection结构。历史edge-only-v1黑幕验收要求正确的源节点与reference连线，以及提示词无黑幕文字引用或节点标签，不要求prompt里出现黑幕；其他资产仍检查正式标签。普通image/scene/video/audio的节点标签可以只通过连线表达引用，不能把合法的空references统一判错；空role同样以真实标签、源节点和reference连线确认节点引用，缺主图另记待补；已有角色媒体时仍检查适用的媒体引用条目，不把SDK沿用的旧素材ID统一判错。此检查不逐一证明人工选材仍存在于当前角色素材中。提示词文字未变也检查引用是否有效，缺失时仍走官方更新命令，回读未通过直接保存失败原因，不清空、不网页补救、不虚报修复成功。用户改变主图后可再次references刷新。后续普通videos默认启用节点引用，局部修改沿用既有绑定方式。

空角色节点后续补图时，既有reference连线不一定自动产生角色媒体条目；手动补图或CLI上传后都须执行`references`刷新校验。若本次范围内角色已具备主图但缺适用媒体条目，用官方CLI仅移除该角色至当前目标视频的失效reference边，再以原节点标签重新导入并回读边及媒体条目；不清空提示词，不改动无关连线、不刷新请求外视频。失败保留待处理记录，不把仅补图视为媒体引用已验证。

显式references结束后写出检查TXT：缺图片部分列资产完整name、类型、真实节点ID与所涉及分镜；黑幕视频素材待补独立列节点ID与相关单元；引用失败部分列分镜编号、资产名称和具体原因，含未映射、候选冲突、节点失效或引用回读失败。只确认节点结构时明确媒体待补，不标为媒体已就绪。用户可照TXT手动上传，或明确授权后提供匹配图片目录，按`images`上传并刷新`references`；黑幕视频素材另行补充到已记录节点。未授权不扫描本地文件、不上传、不生成媒体。


## 指定节点修改与批量删除

```text
python <skill>/scripts/canvas.py edit --project <project> --request <修改.json> [--preview]
python <skill>/scripts/canvas.py delete --project <project> --request <删除.json> [--preview]
```

请求目标可为确切ID、确切name（可加kind），或用户明确指定的整类节点kind。类型用role/scene/image/video/audio/text等；其他画布节点可使用查询返回的完整类型。无筛选条件或同名多节点不执行。例：

```json
{
  "targets": [{"name": "场景1-分镜2", "kind": "video"}],
  "changes": {"prompt": "用户要求的新提示词"}
}
```

`changes`仅支持name、prompt、description、appearanceName、sceneViewName。name适用于角色/场景/图片/视频/音频/文字；prompt适用于角色/场景/图片/视频，按上述各自接口路由；description适用于角色/场景/文字；后两项分别适用于角色/场景，空字符串表示清空。通用patch只更新请求字段，嵌套对象从当前值合并，不覆盖其他字段。其他节点/字段先用官方`canvas command describe/schema`核实支持后执行精确命令并回读，不自造字段。

删除请求使用targets，无changes。后台先preview，将查询确定的目标转换为确切ID并将fingerprints原样带回执行；执行阶段拒绝类型/名称筛选及缺项摘要，摘要键必须完整覆盖本次尚存在的确切目标，不得包含请求范围外ID。幂等续接时，日志已确认删除的ID沿用日志，可附旧摘要或省略该摘要；不额外增加用户审批。删除前使用与回读相同的正式标签解析器，检查含属性标签、reference连线及其他结构化引用；额外影响返回dependents，让用户选择保留、改删范围或接受影响。只有明确接受后才把本次返回的完整dependents数组写入`accepted_dependents`，不可自动填。节点变化会停止。修改/删除前保存本地before快照，CLI支持浏览器检查点时额外创建；不自动恢复整画布。删除已映射节点同时清除其本地映射，保留历史日志；重复删除确切ID时，日志已确认删除的目标计入already_deleted，仅继续其余目标。

## 局部修改同步与结果

director.commit的replace会先保存本地版本并输出TXT，再提交已有映射的视频节点更新任务并启动后台worker；未绑定的不新建，也不触发全台账资产盘点或无关资产补建。冻结输入与版本保存在`canvas/jobs/`，同画布worker忙时任务排队；后台状态只读本地文件，旧pending-sync记录仍兼容。用户明确要求继续批量创建时可用`videos --ids ...`，该显式操作按资产准备流程执行；先处理日志未确认或云端冲突。正常同步在同一真实节点ID上依次更新名称、仅更新分镜时长、写入正式引用编码提示词，各步记录官方确认后统一回读并保存完成回执；不查询、写入或校验模型与分辨率。名称、时长、提示词的人工修改按实际冲突处理。保留已有素材及其他参数，不重建已绑定视频节点。

常规操作结果简要报告创建/更新/复用/删除数量、待处理项及画布链接。错误中出现凭据须隐藏；常规交付不附内部JSON、标签或原始日志。用户要求故障分析时，可以提供必要且已脱敏的命令名称、错误原因和核验依据。所选操作因连接失败中断时说明阻塞项，并保留已经完成的文件与节点。

包内ensure-cli.js由小云雀xyq-skill的CLI检测脚本按本Skill实际需要调整；实际业务调用由本包canvas.py通过CLI执行，不依赖外部Skill安装路径。

## 全部资产与旧项目升级

用户授权执行assets后，创建全部需图片的角色形象、基础布景及经证据核实的必要布景版本、全部主要道具，允许空节点；不因尚未写分镜或尚未准备图片而延迟创建。日夜、天气、光照、拍摄方向与场次排序变化不会新增场景节点。节点和图片都使用SET对应的完整name。

旧schema 2项目不能直接继续assets/videos写入。先离线审核“保留、修改、合并、删除”方案：列出旧SC、目标LOC/SET、现有节点ID、主图、下游引用、拟保留节点及受影响单元；由用户决定后再执行升级。升级台账与新绑定不能自动迁移旧canvas映射，不静默复用或删除任何节点。明确名称引用必须后接英文空格，渲染匹配也检查该边界，禁止把短资产名匹配为长词前缀；旧单元的独占行@黑幕仅紧随标题下一行；历史edge-only-v1单元的黑幕只用独立连线，不写入提示词。
