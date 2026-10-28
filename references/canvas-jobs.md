# 后台画布同步

资产、视频、引用刷新和图片上传默认使用 `scripts/canvas_jobs.py`。新剧本前期只保存用户的同步授权、目标网址和绑定状态，不创建节点；资产设计验收完成后才提交assets任务，分镜正式commit后才提交videos任务。先完成 `canvas.py setup/auth/bind`，复用已绑定账号与用户指定画布。视频入口尚未绑定时先提示“请输入画布的网址连接。”，接收地址并完成必要登录及绑定；已绑定不重复询问。绑定或权限核对失败时立即提示“1.重新绑定  2.暂时不绑定，所有节点暂时不同步”，同时显示两分钟倒计时；选择1后重新接收网址并重新开始两分钟倒计时，倒计时结束前未收到有效选择，默认执行选项2并将同步状态设为`disabled`。选择2后不提交资产、视频、引用或图片任务，直到用户明确重新授权。后台只调用官方CLI，不生成图片或视频。

启用[阶段模型调度](model-routing.md)时，sync子代理仅准备已授权操作及核对本地结果；enqueue、pause、resume、image-choice、report-event、video-followup与正式状态变更由主代理统一执行。云端写入仍由本队列worker独占，不因多个模型参与而增加画布writer。

## 迁移后的画布任务

[项目导入](project-portability.md)保留原绑定、节点映射、任务及未知写入证据，重新建立本机运行状态，队列默认暂停。旧PID、锁及连接配置不恢复；本地分镜不要求登录。不得因导入成功或原任务已授权而自动重放写入。

用户继续已授权画布工作时，先在本机配置CLI与登录，再运行`director.py project resume-canvas --project <project>`。该入口只读核验原账号、目标画布、映射节点与已保存提示词身份；缺失、外部修改或未确认写入须按本页和[画布接口](canvas.md)保留现场处理，不自动重建或回滚。核验成功只解除迁移保护，队列仍暂停；随后按现有resume接口明确恢复原队列。未经核验的导入项目不得启动worker，也不能把本地成果恢复当作云端完成。

## 提交、状态与控制

```text
python <skill>/scripts/canvas_jobs.py enqueue --project <project> --kind assets --start
python <skill>/scripts/canvas_jobs.py enqueue --project <project> --kind videos --request-id <commit返回的ID> --start
python <skill>/scripts/canvas_jobs.py enqueue --project <project> --kind references --ids <分镜ID...> --start
python <skill>/scripts/canvas_jobs.py enqueue --project <project> --kind images --folder <已明确授权的图片目录> --start
python <skill>/scripts/canvas_jobs.py status --project <project>
python <skill>/scripts/canvas_jobs.py pause --project <project>
python <skill>/scripts/canvas_jobs.py resume --project <project>
python <skill>/scripts/canvas_jobs.py image-choice --project <project> --choice 1
python <skill>/scripts/canvas_jobs.py image-choice --project <project> --choice 2
python <skill>/scripts/canvas_jobs.py report-event --project <project> --event-id <已在聊天报告的事件ID>
python <skill>/scripts/canvas_jobs.py video-followup --project <project> --job-id <视频任务ID> --action shown
python <skill>/scripts/canvas_jobs.py video-followup --project <project> --job-id <视频任务ID> --action already-directed
```

`enqueue`不带`--start`只输出不可变任务JSON；`start`启动隐藏后台Python写入进程和只读退出监护进程后立即返回，`worker`与`monitor`分别为内部入口。启动成功或任务进入有效worker队列后，前台立即继续本地流程，不等待同步完成；不得以宿主持续等待进程退出代替后台运行。`status`只读本地状态，在交付分镜、处理节点菜单等交互边界读取即可，不循环查询远端，也不因后台任务未完成而持续等待并阻止当前交互结束。单次恢复查询内，相同依赖摘要和同目录图片清单只核算一次，不跨查询沿用过期内容。

任务排队时即保存total，视频/引用任务按所选单元数计数。新none-v1任务不执行黑幕ID/节点/连线/素材检查；冻结的历史任务保持原模式。视频在准备、批次处理和整节点回读通过时保存stage、current_unit、真实ID及updated_at；每个完整节点通过后立即更新completed和node_ids，不等整批返回。创建ACK、名称/时长单步通过、引用未通过均不能推进完整节点计数。恢复执行会重新读取目标并核验节点，本轮completed随实际核验重建；旧回执和旧进度记录仍用于同一ID续接，计数重核不代表重建节点。后台任务终态仍由完整结果决定，不能仅凭completed==total宣称完成。

图片任务的分母仅包含扫描有效、名称唯一匹配且未明确skip的上传目标；未匹配或不支持的文件不计入分母，缺失/重复/无效/冲突另列；终态仍依据原有执行结果及冲突判定，不能只看分母。新任务将目标数冻结到options.image_target_count，旧任务没有此证据时total显示null，待实际执行扫描后更新；不为补历史分母读取已经改变或消失的旧图片目录。完整图片清单摘要仍用于判断输入是否漂移。

需要冲突选择时，把用户明确给出的选择保存为JSON，传`enqueue --decisions <文件>`；修改选择会形成新任务。同一个request_id可补充冲突选择，但来源内容和所选分镜不能改变；新的修订使用新的commit request_id。用户未授权的覆盖选择不得由脚本自动填入。

同画布、同类、同一组所选分镜或图片目录的新任务会替代未完成旧任务，保留旧JSON和操作日志。依赖旧资产任务的视频自动跟随替代任务；新任务完成后才释放依赖。替代不绕过未知写入核对。显式切换画布后，新画布任务不依赖旧画布任务；旧目标任务保留停止原因，不自动跨画布执行。

用户要求暂停或停止画布工作时，立即调用`pause`并完全暂停项目heartbeat。它停止派发新操作；已发送的请求收取确认并保留日志后停止。强制终止可能留下未知结果，恢复必须核对原ID。`start`和新任务不会绕过暂停；只有用户恢复后调用`resume`并明确恢复heartbeat。恢复先核对原操作日志；结果未知时仍停止，不盲目重建、不删除CLI隔离状态。

## 资产就绪、拒绝与恢复

创建ACK只证明创建命令已确认。新角色/场景在写入名称与描述前，用官方快照检查同一ID的节点类型、业务content及basicInfo；最多核对3次，间隔0.5秒，未就绪返回`ASSET_NOT_READY`并保留映射。它不是图片就绪检查，不要求主图。暂停在每次查询、等待及写命令前检查。

明确`ok:false`保存公开的code/message/revision并记`rejected`；超时、无效输出、执行/保存异常及其他无确定确认的错误记`uncertain`。未知写入不自动重试。仅`xyq.scene.updateDescription`明确返回`INVARIANT_FAILED / scene asset not found`时，可在官方只读核对业务资产就绪、原ID及名称/视角/描述/媒体字段未变化后，总共尝试最多3次，间隔0.5秒；不重建节点。若回读证明目标已生效则记录核验，发生人工变化停止。其他拒绝立即停止。CLI隔离/未确认事务仍按官方流程核对，不能清理后重跑。

旧版把明确拒绝误记为uncertain时，`resume`本身不能解除日志阻塞。以下入口只读核对及修改本地日志，不发送业务写命令、不启动worker、不解除pause：

```text
python <skill>/scripts/canvas_jobs.py recover-assets --project <project> --operation-id <原操作ID>
python <skill>/scripts/canvas_jobs.py recover-assets --project <project> --operation-id <原操作ID> --apply --fingerprint <预览返回的摘要>
```

仅接受本队列旧场景改名记录：完整明确拒绝JSON、唯一原映射、同ID已确认创建、名称与初始数据对应，且无其他未知操作。原业务资产必须已就绪；名称只能是原目标名或工厂默认名，视角/描述为空且无图片/其他素材。节点有人工内容、证据缺失、摘要变化或CLI查询失败时不修改日志，转为具体待处理项。apply保留原错误与操作ID，追加恢复依据、核对时间和摘要，将误分类改为rejected；回读证明目标改名已生效则记verified，不重放该步骤。恢复入口使用原账号/画布的writer锁；其他项目writer活跃时拒绝操作。

用户授权恢复后，再调用`resume`。正常队列会核对原ID、已确认步骤与拒绝前字段基线，仅补未完成字段并整组核验；新成功ACK关联原拒绝记录，保留历史证据。已核验节点不重写，已创建但未核验的节点不分配新ID。暂停授权与日志修复是两件事，不能在后续分镜请求中静默恢复队列。

## 并行工作、完成反馈与菜单

资产设计和验收完成且用户已保存自动同步授权后，提交assets任务；后台启动或进入有效worker队列后立即进入本地分镜流程，不等待资产任务终态。后台脚本新建、核对、记录ID；Codex可同时用本地定稿台账生成分镜。同步进程不修改`session.json`的phase、分镜编号或历史范围。资产同步失败或结果未知时必须生成失败事件并在下一交互边界提示用户，但不停止本地分镜。

资产的`completed`表示名称、类型、空形象/视角名、完整image_prompt及ID映射均已核验；新建节点还须核对[资产图片默认参数](canvas.md#资产与视频节点)。提示词按[资产字段契约](canvas.md#资产与视频节点)核验generation.prompt，描述一致不能代替；回执记录generation-prompt-v2及prompt_hash，旧description_hash不作为提示词证据。派生角色同时按该节的图片输入合同处理引用，任务结果分别报告节点初始化和参考图片输入状态；节点计数不能代替引用配置验收。空图片节点允许完成初始化，缺少的参考图片另列待补，不宣称图片输入已就绪或图像一致性已建立。成功计数只包括已核验节点，不能把queued/running、已申请ID或单步ACK当完成。

主流程不再询问是否上传图片；status.image_choice_available恒为false，不因资产完成触发上传菜单、索取目录或扫描文件。独立images命令仍仅在用户明确要求上传时使用，旧image-choice接口仅为兼容已发生的交互保留。需要切换phase时由前台director执行，后台完成事件不能覆盖当前交互位置。

每段分镜正式commit后，若同步状态为`authorized`，提交该段的`request_id`视频任务；后台启动或进入有效worker队列后立即进入下一段本地分镜，不等待同步完成。若同步状态为`disabled`，不提交画布任务并直接进入下一段。资产任务尚未完成时，视频任务保持依赖等待；资产或视频任务启动失败、明确拒绝或结果未知时，立即生成失败事件并提示用户，前台仍继续下一段本地分镜。单段视频任务若回读确认节点已创建但没有任何业务字段写入，只允许尝试一次删除并重建；重建失败则标记该段`skipped`并记录原节点ID、重建节点ID、request_id和原因，后续不重复提交，项目交付时集中报告。结果未知、已写入部分字段或存在人工修改时不得删除或重建，沿真实ID核对和续接。图片、视频、引用刷新共用同一画布写入队列。

资产、视频、图片与引用任务均持久保存完成或待处理事件。只有全部授权目标经过最终核验并进入成功终态，才能报告本批同步完成；首次资产初始化和显式图片输入同步按[图片输入合同](canvas.md#资产与视频节点)报告；普通videos不检查或报告媒体就绪，显式references和上传刷新保留媒体诊断，不能把节点同步完成表述为图片输入就绪或视频生成完成。部分完成、明确拒绝、结果未知与字段冲突报告已核验数量和具体待处理项，沿原任务和原节点ID恢复。恢复成功另发完成事件；已解决或被替代的失败保留历史，不作为新的待处理提示。

主代理在下一可交互边界读取`status.pending_events`，必须报告其中尚未报告的当前画布事件，注明任务范围、完成或待处理状态及数量；实际向用户展示聊天反馈后，逐个调用`report-event --event-id`记录已报告。查询状态、尝试桌面通知或准备了消息均不等于已报告，不得提前消费。重复调用同一报告记录保持幂等。`status.events`只返回当前绑定画布的事件，完整历史仍保存在events.json；切换画布不串报，旧事件缺少目标信息时由原冻结任务确定，不推测目标。

完整核验的视频任务通过`status.video_followups`提供一次后续入口；来源和所选分镜已变化的旧任务不开放该入口。先报告完成事件；新主流程已在任务启动后提示下一范围，完成事件不得再次强制弹出同一范围菜单。只有尚未提供下一步入口的历史任务，才提示“请继续输入分镜剧本范围”。实际展示后用`video-followup --action shown`记录，后续查询不重复展示；收到对应选择时用`continue`、`modify`或`finish`记录。用户已经明确下一步时直接续接，以`already-directed`记录，不强制再选菜单。这些入口仅记录已发生的交互，不改变工作阶段、不排队云端操作、不生成图片或视频，也不解除暂停。新用户请求仍按其授权独立执行，不能把菜单消费当作额外审批。

完成和待处理事件均尽力尝试Windows桌面提示，桌面提示与聊天报告独立。成功提示8秒自动关闭；失败及需处理提示保留至用户关闭，提示运行在独立隐藏辅助进程，绝不阻塞writer或前台。worker启动失败、可捕获运行异常均持久化needs_attention事件；独立只读monitor按PID及启动标识监视worker，发现非预期退出或被强制终止后保存进度和待处理事件，不重启任务、不重放命令。主动暂停或正常结束不误报失败，重复检查同一事件不重复通知。监护启动失败需如实返回并在后续交互核对本地状态；系统退出、整个进程树被终止或桌面不可用时不保证即时显示。仅记录可证实的通知状态：`started_result_unknown`表示提示进程已启动但显示和阅读结果未知，`start_failed`表示进程启动失败，`attempt_outcome_unknown`表示尝试结果无法确认，`not_attempted`表示平台不支持或事件不属于当前画布。通知过程不等待弹窗关闭，不宣称用户已看到，失败不改变同步结果，也不消费待报告事件。宿主没有事件回调时脚本不能主动向聊天追加消息，由主代理依上述交互边界完成回退报告。暂停和替代不产生失败提示，同一完成事件或相同失败内容不重复通知。

## 持久化与正确性

- `canvas/jobs/job-<摘要>.json`：绑定（含已确认team_id）、前置同步授权与`disabled`状态、资产字段契约版本、冻结session输入、所选单元与版本、请求选项、依赖任务及内容摘要。稳定CHAR/SET/PROP/分镜ID沿用，不创建易漂移的临时序号。
- 同名`.state.json`：worker独占的进度、已核验计数、节点映射和结果。完整映射和逐操作日志继续保存在`canvas/state.json`，视频完成回执仍保存在`canvas/video-write-receipts/`。
- `control.json`：暂停、图片菜单选择、按event_id保存的已报告记录与按job_id保存的视频后续入口状态；`worker.json`：writer PID、启动标识、生命周期和monitor启动结果；`events.json`：带稳定事件ID、目标绑定和桌面通知尝试结果的completed与needs_attention事件，完成每任务一次，相同失败内容去重；`worker.log`：后台运行错误。视频节点已创建但无业务字段时的一次删除重建尝试、重建节点ID、`skipped`结果及最终待处理原因与对应request_id一并写入任务状态和事件日志。读取旧事件只补足返回视图，不迁移冻结任务或重发系统通知。

追加新范围不使已有资产任务失效。任务字段契约与当前版本不一致时停止，保留原JSON、日志与暂停状态，核对后重新提交当前契约的任务；不自动升级冻结任务或按旧description目标继续写入。来源、资产台账、风格、目标画布、所选分镜或待上传图片变动时，旧任务停止，重新提交当前内容；不得继续写旧版本。输入文件与进度文件分离，Codex不改正在执行的任务JSON。

同一账号与画布只有一个后台writer，使用跨进程OS锁并复用原画布操作锁。不同项目绑定同一画布时第二个worker停止，不得启动多个worker并发写入同一画布。worker正在写入时不得另行使用canvas.py修改或上传；需要精确编辑或删除时先暂停队列，确认当前已发送请求的结果并保存日志后，再按原接口执行。

新assets/videos/references任务冻结sdk-local-batch-v1，按[本地编译与批量提交](canvas.md#本地编译与批量提交)执行，默认每批10个节点、一个writer。整批ACK不推进完成数；统一回读后逐节点验收并保存各自回执。缺图片只记录，不判定为节点引用失败。无该协议的历史任务保持原串行路径和3个缺失资产补建上限，不因升级重写manifest、迁移ID或自动切换运行中的worker。新视频任务的缺失资产补建上限为10个。批次数量是本地分组参数，不是官方QPS或最优值。

不把通用apply_mutations的dryRun当业务验收，不为批量化改用浅层generation补丁写提示词。已注册描述/提示词命令在本地隔离文档中保持官方业务入口，最终差异通过公开canvas apply提交；不自建MCP或私有HTTP写入路径。未知ACK保留allocated/pending/acknowledged/uncertain等操作状态，只有回读证明目标已生效才跳过；未证明的项不能当普通失败自动重试。

## 输入校验的执行边界

worker在任务开始和每个资产/视频批次的写入及核验边界执行完整输入校验；历史串行任务保留逐节点边界。组内控制检查对文件/目录元数据作轻量比对，变化立即触发完整内容校验。没有跨任务永久缓存，来源摘要仍是有效性依据。图片实际上传前后原有逐文件内容hash检查、官方ACK和写后回读继续执行；减少本地反复读取不改变未知写入停止、人工冲突保护或用户暂停语义。
