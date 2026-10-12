# 小云雀 CLI 画布接口

本文件仅供后台执行。所有路径用绝对路径，JSON使用UTF-8；用户名称、提示词和链接通过参数数组或文件传入，不拼接shell命令。运行入口为`<skill>/scripts/canvas.py`；不需要另装小云雀Skill或MCP。

## 首次连接与绑定

准备：小云雀账号、本人可编辑的目标画布链接；批量资产操作需要本项目已验证的资产台账，视频节点需要已commit的分镜。图片可后续添加，创建空节点不要求提前准备图片。电脑需Node.js 16或更高版本；未安装时提示安装，已有可用Node.js可传绝对路径。Python使用当前环境提供的解释器。

```text
python <skill>/scripts/canvas.py setup --project <project> [--node <node绝对路径>]
python <skill>/scripts/canvas.py auth --project <project> --login
python <skill>/scripts/canvas.py bind --project <project> --target <画布链接或真实画布ID>
python <skill>/scripts/canvas.py status --project <project>
```

`setup`检测现有官方CLI及本Skill实际调用的命令，缺少时用包内`ensure-cli.js`安装到官方CLI缓存；不每次安装或升级。必要原生命令为status/login、model list/describe、canvas get/allocate/upload；必要语义命令覆盖快照、权限、节点创建/更新/删除、精确删除引用边的delete_edges、提示词写入、角色/场景描述及角色主图更新。create_checkpoint只在权限支持时使用，保持可选；不探测本Skill不调用的生图、生视频、超分、擦字幕、积分、query-result、logout或canvas create/apply。`auth --login`先检查status，已有登录不重复打开授权页。首次提示用户完成浏览器授权；CLI自行保存安全凭据。配置文件只保存CLI/Node路径、版本和公开账号标识，禁止保存密钥、完整status、认证环境变量或浏览器存储。CLI报401/403时先判断账号/权限；仅确认浏览器凭据过期或撤销后使用`auth --login --force`，不将force作为常规操作。

链接支持`https://xyq.jianying.com/novel/detail/canvas?projectId=...&canvasId=...`，以及含`overviewPippitAssetId`的链接。后者通过官方CLI读取overview解析实际canvas ID，不能把overview或project ID直接当canvas ID；无法唯一解析时请用户提供实际画布ID。所有远端ID保持字符串。绑定校验个人canvas类型与编辑权限；没有指定目标时询问画布链接，不自行新建画布。

明确切换目标后用`bind ... --switch`，原绑定及映射归档。当前有效连接保存在`canvas/connection.json`，绑定、节点映射、操作日志及待同步项保存在`canvas/state.json`。每次实际操作核对当前CLI账号，账号变化先恢复或重新绑定，不跨账号复用映射。

## 资产与视频节点

```text
python <skill>/scripts/canvas.py assets --project <project> [--preview] [--decisions <选择.json>]
python <skill>/scripts/canvas.py videos --project <project> --request-id <本批提交ID> [--preview] [--decisions <选择.json>]
python <skill>/scripts/canvas.py videos --project <project> --ids 场景1-分镜1 场景1-分镜2
```

`assets`读取session绑定台账，逐个创建role/scene/image业务节点。角色使用台账完整name，场景使用scene_assets[].name及SET ID，每个SET仅一个节点（包括有证据的时间版本和布置版本），SC heading不参与资产建节点，道具使用主name；角色/场景同时设置节点显示名和basicInfo.name，basicInfo.appearanceName为空。视频普通业务节点的name/title/caption逐字使用对应已提交单元ID `场景[序号]-分镜[序号]`，例如 `场景3-分镜2`，不附加时长、镜头标签或布景名称。完整提示词直接由当前有效单元按其输出版本渲染写入generation.prompt，保留固定抬头、视频风格、换行和人物/场景前缀；不调用生成命令。

显式执行`videos`时，先全量读取目标画布一次，用同一份节点索引逐项核对当前台账全部角色、布景和主要道具，不只核对本批分镜所用资产，不比较节点总数。已保存的真实ID仍在当前画布且完整名称、类型一致时复用，保留该节点当前主图和形象/视角；无映射或原ID已不存在时，按完整name+kind查找，唯一候选自动复用并保存映射，没有候选自动补建对应role/scene/image空节点并立即保存CLI真实ID。有多个候选或已绑定资产改名/类型变化时，列入待处理，不猜测或覆盖；其余合法项可继续。画布额外节点保留，不改名、不删除。复用阶段1资产传输已保存的ID，不额外按名称重新寻找有效映射；恢复时沿用已确认进度，不重复创建。新增节点更新本次内存索引，开始时一次全量盘点不代替每次写后的回读。

视频默认编码正式节点引用；空资产节点照常写入真实ID标签，不因缺图片而保留文字占位或阻塞创建。节点存在、节点引用成功和图片就绪分别记录，不能将缺图片等同引用失败。后台结构化检查结果保存于`canvas/reference-check.json`，用户检查TXT默认与最近receipt交付TXT同目录，分别列缺图片资产及最终引用失败；不在对话展示内部JSON或标签。失败只CLI回读确认并记录，不清空提示词，不用网页点击、浏览器编辑器或模拟粘贴补救。

视频新节点自动设置Seedance 2.5、720P和对应单元时长。通过`model list --type video`唯一解析准确key，再用`model describe <key> --type video`核对720P与秒数档位；模型不可用、配置缺失、时长不合法或组合无法确定时停止本批写入并报告，不猜测替代值。生成配置写入`generation.modelKey`、`generation.resolution`及`generation.durationMs`，时长由单元duration秒乘1000；不修改节点已有媒体的实际durationMs。嵌套generation从最新内容合并，保留prompt、references、比例及其他配置，逐字段回读校验。

已有节点的模型或分辨率不符返回`video_settings_changed`及current/desired/fingerprint，由用户选择切换或跳过；同意切换才填本次`overwrite_fingerprint`。已同步的时长保存基线；分镜改时长自动跟随，云端人工改过时长则先处理冲突。单独`references`只刷新引用，不改视频参数。

不传批次或ids时仅选择最近一次提交的单元；指定历史批次仍取这些编号的当前有效版本。不得把所有历史receipt都当成新节点。范围由Codex解析为明确分镜ID数组，不能用一个普通连字符拆开`场景X-分镜Y`。

返回`planned`为预览，`needs_decision`表示需要用户处理的同名、缺失或云端修改冲突，无冲突时直接执行已授权范围，不强加一次审批。直接`assets`及视频节点同名候选的`existing_name`含节点ID及fingerprint，让用户选择复用；显式`videos`的资产准备按上一节自动唯一匹配/补建，多个资产候选仍不得自动选择。后台生成选择文件：

```json
{
  "CHAR0001": {"reuse": "真实候选节点ID", "fingerprint": "本次查询摘要"},
  "场景1-分镜1": {"overwrite_fingerprint": "用户明确选择覆盖的当前节点摘要"},
  "场景1-分镜2": {"skip": true}
}
```

选择文件只能根据用户的实际选择填写；不自动填覆盖。候选或摘要变化重新核对。多个候选不能默认取第一个，用户选定后使用该候选真实ID及本次fingerprint继续。已有映射正常复用；直接`assets`中绑定节点被删或类型变化时说明实际情况，由用户决定恢复/重绑。用户明确要求重建缺失节点时，在该资产选择中填写`{"recreate_missing":"查询确认已缺失的原节点ID"}`；若出现同名候选仍需明确reuse。显式`videos`的资产准备已获自动唯一匹配和补建缺失节点授权，无需逐项确认，但不因此自动填任何云端覆盖选择。资产引用准备保留当前主图及形象/视角名称；直接改写资产名称、描述或默认形象/视角，以及视频节点名称、提示词或参数时，仍检查各自保存基线并处理实际冲突。

大批量操作在浏览器授权支持时先建检查点。每个节点串行创建、改名和写提示词，写后回读再记为完成。ID由CLI真实分配并在创建前保存，可恢复进程在已确认步骤之间中断的情况。写入失败或未知ACK时先读取画布；能核对目标已生效则继续，否则返回`UNCERTAIN_WRITE`并停止。不要删除CLI隔离的未确认事务、换request ID盲目重放或把整个批次标成成功。项目`operation.lock`及系统临时目录中对应账号/画布摘要的`seedance-canvas-*.lock`只在确认旧进程已退出后才能移除；不批量清理其他项目的锁。`status`可在未知写入后读取节点与未确认操作列表。

## 本地图片批量上传

用户明确选择上传后才询问图片文件夹路径；已有明确路径直接使用。未选择上传时不询问路径、不运行本节命令。PNG/JPG/JPEG/WebP图片可混放，支持子文件夹；文件名主体必须与角色完整name、布景资产完整name或主要道具name逐字一致。建议一项资产一张图片；同名不同扩展名或分散于不同子目录仍视为重名，不默认选第一张。布景资产name含Windows文件名禁用字符时报告并先处理台账命名，不把替换后的文件名静默映射回旧名称。

```text
python <skill>/scripts/canvas.py images --project <project> --folder <图片文件夹绝对路径> --preview
python <skill>/scripts/canvas.py images --project <project> --folder <图片文件夹绝对路径> [--decisions <选择.json>]
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

上传完成自动刷新已有映射且涉及这些资产的视频节点引用，不新建视频节点、不生成视频。用户也可在画布中手动添加图片并选主图，随后明确要求运行`references`刷新和校验；不承诺补图后所有媒体引用会自行刷新。后续新建视频节点默认使用真实节点引用。待刷新资产保存于pending_image_references；图片已成功但提示词冲突时保留图片和待处理项，按用户选择继续`references`。缺少图片只记录，不撤销已有正式节点引用；没有映射的资产或黑幕保留文字@并报告。`status`返回待刷新资产和未完成上传，恢复时先核对。

## 图片引用

```text
python <skill>/scripts/canvas.py black --project <project> --node-id <现有黑幕图片节点ID>
python <skill>/scripts/canvas.py references --project <project> --request-id <本批提交ID> [--preview] [--decisions <选择.json>]
python <skill>/scripts/canvas.py references --project <project> --ids 场景1-分镜1 场景1-分镜2
```

`references`只处理已映射视频节点，未创建的编号列入unbound_units，不自动创建。角色、场景及道具资产的节点映射有效时，即使图片为空也编码真实节点标签并尝试正式节点引用；不以先上传图片为前置条件。没有映射、映射冲突或黑幕无法确定时保留`@台账完整名称 `（名称后英文空格）或独立行`@黑幕`并记录。黑幕优先使用已保存或用户指定的图片节点；没有有效ID时可复用画布中完整名称恰为“黑幕”的唯一image节点并保存ID。没有候选或有多个候选则记录，不自行创建、生成或冒用其他图片。已确认的节点引用与图片待补分别计数，不把文字@当成实际引用，也不把缺图片自动判为节点引用失败。

转换限定资产行的@位置、镜头说明和台词说话人前缀，使用真实`<node-asset>节点ID</node-asset>`。不替换台词引号内文字、不改原交付TXT。通过官方`xyq.generation.update_prompt`导入引用，回读prompt、实际源节点与画布reference连线，并按官方SDK的引用类型核对适用的generation.references条目身份、类型和dataReferenceSelection结构。普通image/scene/video/audio的节点标签可以只通过连线表达引用，不能把合法的空references统一判错；空role同样以真实标签、源节点和reference连线确认节点引用，缺主图另记待补；已有角色媒体时仍检查适用的媒体引用条目，不把SDK沿用的旧素材ID统一判错。此检查不逐一证明人工选材仍存在于当前角色素材中。提示词文字未变也检查引用是否有效，缺失时仍走官方更新命令，回读未通过直接保存失败原因，不清空、不网页补救、不虚报修复成功。用户改变主图后可再次references刷新。后续普通videos默认启用节点引用，局部修改沿用既有绑定方式。

空角色节点后续补图时，既有reference连线不一定自动产生角色媒体条目；手动补图或CLI上传后都须执行`references`刷新校验。若本次范围内角色已具备主图但缺适用媒体条目，用官方CLI仅移除该角色至当前目标视频的失效reference边，再以原节点标签重新导入并回读边及媒体条目；不清空提示词，不改动无关连线、不刷新请求外视频。失败保留待处理记录，不把仅补图视为媒体引用已验证。

结束后写出检查TXT：缺图片部分列资产完整name、类型、真实节点ID与所涉及分镜；引用失败部分列分镜编号、资产名称和具体原因，含未映射、候选冲突、节点失效或引用回读失败。只确认节点结构时明确图片待补，不标为媒体已就绪。用户可照TXT手动上传，或明确授权后提供匹配图片目录，按`images`上传并刷新`references`；未授权不扫描本地图片、不上传、不生成媒体。

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

`changes`仅支持name、prompt、description、appearanceName、sceneViewName。name适用于角色/场景/图片/视频/音频/文字；prompt适用于图片/视频；description适用于角色/场景/文字；后两项分别适用于角色/场景，空字符串表示清空。通用patch只更新请求字段，嵌套对象从当前值合并，不覆盖其他字段。其他节点/字段先用官方`canvas command describe/schema`核实支持后执行精确命令并回读，不自造字段。

删除请求使用targets，无changes。后台先preview，将查询确定的目标转换为确切ID并将fingerprints原样带回执行；执行阶段拒绝类型/名称筛选及缺项摘要，摘要键必须完整覆盖本次尚存在的确切目标，不能夹带请求外ID。幂等续接时，日志已确认删除的ID沿用日志，可附旧摘要或省略该摘要；不额外增加用户审批。删除前使用与回读相同的正式标签解析器，检查含属性标签、reference连线及其他结构化引用；额外影响返回dependents，让用户选择保留、改删范围或接受影响。只有明确接受后才把本次返回的完整dependents数组写入`accepted_dependents`，不可自动填。节点变化会停止。修改/删除前保存本地before快照，CLI支持浏览器检查点时额外创建；不自动恢复整画布。删除已映射节点同时清除其本地映射，保留历史日志；重复删除确切ID时，日志已确认删除的目标计入already_deleted，仅继续其余目标。

## 局部修改同步与结果

director.commit的replace会先保存本地版本并输出TXT，再同步已有映射的视频节点；未绑定的不新建，也不触发全台账资产盘点或无关资产补建。待同步版本额外保存于`canvas/pending-sync/`，即使其他进程占用操作锁也保留任务；查询汇入pending_units，同步确认后清理对应标记。用户明确要求继续批量创建时可用`videos --ids ...`，该显式操作按资产准备流程执行；先处理日志未确认或云端冲突。正常同步更新对应节点名称、提示词及分镜时长，核对Seedance 2.5和720P；人工改过的视频参数先处理冲突。保留已有素材及其他参数，不重建已绑定视频节点。

用户可见结果只给简短创建/更新/复用/删除数量、待处理项及画布链接。错误中出现凭据须隐藏；JSON、CLI命令、内部标签、日志与执行分析不在对话输出。所选操作因连接失败中断时说明阻塞项，并保留已经完成的文件与节点。

包内ensure-cli.js由小云雀xyq-skill的CLI检测脚本按本Skill实际需要调整；实际业务调用由本包canvas.py通过CLI执行，不依赖外部Skill安装路径。

## 全部资产与旧项目升级

assets立即创建全部角色形象、基础布景及经证据核实的少量布景版本、全部主要道具，允许空节点；不因尚未写分镜或尚未准备图片而延迟创建。日夜、天气、光照、拍摄方向与场次排序变化不会新增场景节点。节点和图片都使用SET对应的完整name。

旧schema 2项目不能直接继续assets/videos写入。先离线审核“保留、修改、合并、删除”方案：列出旧SC、目标LOC/SET、现有节点ID、主图、下游引用、拟保留节点及受影响单元；由用户决定后再执行升级。升级台账与新绑定不能自动迁移旧canvas映射，不静默复用或删除任何节点。明确名称引用必须后接英文空格，渲染匹配也检查该边界，禁止把短资产名匹配为长词前缀；独占行@黑幕仅紧随标题下一行。
