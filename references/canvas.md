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

`setup`检测现有官方CLI及画布命令，缺少时用包内`ensure-cli.js`安装到官方CLI缓存；不每次安装或升级。`auth --login`先检查status，已有登录不重复打开授权页。首次提示用户完成浏览器授权；CLI自行保存安全凭据。配置文件只保存CLI/Node路径、版本和公开账号标识，禁止保存密钥、完整status、认证环境变量或浏览器存储。CLI报401/403时先判断账号/权限；仅确认浏览器凭据过期或撤销后使用`auth --login --force`，不将force作为常规操作。

链接支持`https://xyq.jianying.com/novel/detail/canvas?projectId=...&canvasId=...`，以及含`overviewPippitAssetId`的链接。后者通过官方CLI读取overview解析实际canvas ID，不能把overview或project ID直接当canvas ID；无法唯一解析时请用户提供实际画布ID。所有远端ID保持字符串。绑定校验个人canvas类型与编辑权限；没有指定目标时询问画布链接，不自行新建画布。

明确切换目标后用`bind ... --switch`，原绑定及映射归档。当前有效连接保存在`canvas/connection.json`，绑定、节点映射、操作日志及待同步项保存在`canvas/state.json`。每次实际操作核对当前CLI账号，账号变化先恢复或重新绑定，不跨账号复用映射。

## 资产与视频节点

```text
python <skill>/scripts/canvas.py assets --project <project> [--preview] [--decisions <选择.json>]
python <skill>/scripts/canvas.py videos --project <project> --request-id <本批提交ID> [--preview] [--decisions <选择.json>]
python <skill>/scripts/canvas.py videos --project <project> --ids 场景1-分镜1 场景1-分镜2
```

`assets`读取session绑定台账，逐个创建role/scene/image业务节点。角色使用台账完整name，场景使用scene_assets[].name及SET ID，每个SET仅一个节点，SC heading不参与资产建节点，道具使用主name；角色/场景同时设置节点显示名和basicInfo.name，basicInfo.appearanceName为空。视频普通业务节点使用分镜ID作为name/title/caption，完整提示词直接由当前有效单元渲染写入generation.prompt，保留固定抬头、视频风格、换行和人物/场景前缀；不含“时长”后缀的名称。不调用生成命令。

视频新节点自动设置Seedance 2.5、720P和对应单元时长。通过`model list --type video`唯一解析准确key，再用`model describe <key> --type video`核对720P与秒数档位；模型不可用、配置缺失、时长不合法或组合无法确定时停止本批写入并报告，不猜测替代值。生成配置写入`generation.modelKey`、`generation.resolution`及`generation.durationMs`，时长由单元duration秒乘1000；不修改节点已有媒体的实际durationMs。嵌套generation从最新内容合并，保留prompt、references、比例及其他配置，逐字段回读校验。

已有节点的模型或分辨率不符返回`video_settings_changed`及current/desired/fingerprint，由用户选择切换或跳过；同意切换才填本次`overwrite_fingerprint`。已同步的时长保存基线；分镜改时长自动跟随，云端人工改过时长则先处理冲突。单独`references`只刷新引用，不改视频参数。

不传批次或ids时仅选择最近一次提交的单元；指定历史批次仍取这些编号的当前有效版本。不得把所有历史receipt都当成新节点。范围由Codex解析为明确分镜ID数组，不能用一个普通连字符拆开`场景X-分镜Y`。

返回`planned`为预览，`needs_decision`表示同名、缺失或云端修改冲突，无冲突时直接执行已授权范围，不强加一次审批。`existing_name`含候选节点ID及fingerprint，让用户选择复用；后台生成选择文件：

```json
{
  "CHAR0001": {"reuse": "真实候选节点ID", "fingerprint": "本次查询摘要"},
  "场景1-分镜1": {"overwrite_fingerprint": "用户明确选择覆盖的当前节点摘要"},
  "场景1-分镜2": {"skip": true}
}
```

选择文件只能根据用户的实际选择填写；不自动填覆盖。候选或摘要变化重新核对。多个候选不能默认取第一个。已有映射正常复用；绑定节点被删或类型变化时说明实际情况，由用户决定恢复/重绑，不在同名节点间猜测。用户明确要求重建缺失节点时，在该资产选择中填写`{"recreate_missing":"查询确认已缺失的原节点ID"}`；若出现同名候选仍需明确reuse。节点已人工改名、改提示词或改默认形象/视角名称时，先说明冲突再按用户选择处理。

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

上传完成自动刷新已有映射且涉及这些资产的视频节点引用，不新建视频节点、不生成视频。后续新建视频节点自动使用本项目已导入图片的真实引用。待刷新资产保存于pending_image_references；图片已成功但提示词冲突时保留图片和待处理项，按用户选择继续`references`。缺少图片或黑幕继续保留文字@并报告。`status`返回待刷新资产和未完成上传，恢复时先核对。

## 图片引用

```text
python <skill>/scripts/canvas.py black --project <project> --node-id <现有黑幕图片节点ID>
python <skill>/scripts/canvas.py references --project <project> --request-id <本批提交ID> [--preview] [--decisions <选择.json>]
python <skill>/scripts/canvas.py references --project <project> --ids 场景1-分镜1 场景1-分镜2
```

先用images上传图片，或由用户在画布中添加图片并选定主图，再运行references；只处理已映射视频节点，未创建的编号列入unbound_units，不自动创建。黑幕使用用户指定的已有图片节点，不自行生成或冒用其他图片。图片为空时保留`@台账完整名称 `（名称后英文空格）或独立行`@黑幕`，返回pending_references；只报告已实际绑定与待绑定的数量，不把文字@当成实际引用。

转换限定资产行的@位置、镜头说明和台词说话人前缀，使用真实`<node-asset>节点ID</node-asset>`。不替换台词引号内文字、不改原交付TXT。通过官方`xyq.generation.update_prompt`导入引用，回读prompt；检查get_asset的generation.references以及画布reference连线确认。用户改变主图后可再次references刷新。后续普通videos同步及局部修改保留已启用的图片绑定方式。

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

删除请求使用targets，无changes。后台先preview，将查询确定的目标转换为确切ID并将fingerprints原样带回执行；不额外增加用户审批。删除前检查reference连线、正式标签及其他结构化引用；额外影响返回dependents，让用户选择保留、改删范围或接受影响。只有明确接受后才把本次返回的完整dependents数组写入`accepted_dependents`，不可自动填。节点变化会停止。修改/删除前保存本地before快照，CLI支持浏览器检查点时额外创建；不自动恢复整画布。删除已映射节点同时清除其本地映射，保留历史日志；重复删除确切ID时，日志已确认删除的目标计入already_deleted，仅继续其余目标。

## 局部修改同步与结果

director.commit的replace会先保存本地版本并输出TXT，再同步已有映射的视频节点；未绑定的不新建。待同步版本额外保存于`canvas/pending-sync/`，即使其他进程占用操作锁也保留任务；查询汇入pending_units，同步确认后清理对应标记。可用`videos --ids ...`继续；先处理日志未确认或云端冲突。正常同步更新对应节点名称、提示词及分镜时长，核对Seedance 2.5和720P；人工改过的视频参数先处理冲突。保留已有素材及其他参数，不重建节点。

用户可见结果只给简短创建/更新/复用/删除数量、待处理项及画布链接。错误中出现凭据须隐藏；JSON、CLI命令、内部标签、日志与执行分析不在对话输出。所选操作因连接失败中断时说明阻塞项，并保留已经完成的文件与节点。

包内ensure-cli.js来自小云雀xyq-skill的官方CLI检测脚本；实际业务调用由本包canvas.py通过CLI执行，不依赖外部Skill安装路径。

## 全部资产与旧项目升级

assets立即创建全部角色形象、基础布景及经证据核实的少量布景版本、全部主要道具，允许空节点；不因尚未写分镜或尚未准备图片而延迟创建。日夜、天气、光照、拍摄方向与场次排序变化不会新增场景节点。节点和图片都使用SET对应的完整name。

旧schema 2项目不能直接继续assets/videos写入。先离线审核“保留、修改、合并、删除”方案：列出旧SC、目标LOC/SET、现有节点ID、主图、下游引用、拟保留节点及受影响单元；由用户决定后再执行升级。升级台账与新绑定不能自动迁移旧canvas映射，不静默复用或删除任何节点。明确名称引用必须后接英文空格，渲染匹配也检查该边界，禁止把短资产名匹配为长词前缀；独占行@黑幕仅紧随标题下一行。
