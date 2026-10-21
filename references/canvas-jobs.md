# 后台画布同步

资产、视频、引用刷新和图片上传默认使用 `scripts/canvas_jobs.py`。先完成 `canvas.py setup/auth/bind`，复用已绑定账号与用户指定画布。后台只调用官方CLI，不生成图片或视频。

启用[阶段模型调度](model-routing.md)时，sync子代理仅准备已授权操作及核对本地结果；enqueue、pause、resume、image-choice、report-event、video-followup与正式状态变更由主代理统一执行。云端写入仍由本队列worker独占，不因多个模型参与而增加画布writer。

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

`enqueue`不带`--start`只输出不可变任务JSON；`start`启动隐藏后台Python进程并立即返回，`worker`为该进程内部入口。启动成功后返回，由独立worker继续执行；不得以宿主持续等待进程退出代替后台运行。`status`只读本地状态，在交付分镜、处理节点菜单等交互边界读取即可，不循环查询远端，也不因后台任务未完成而持续等待并阻止当前交互结束。单次恢复查询内，相同依赖摘要和同目录图片清单只核算一次，不跨查询沿用过期内容。

任务排队时即保存total，视频/引用任务按所选单元数计数。新none-v1任务不执行黑幕ID/节点/连线/素材检查；冻结的历史任务保持原模式。视频在准备、逐节点处理和整节点回读通过时保存stage、current_unit、真实ID及updated_at；每个完整节点通过后立即更新completed和node_ids，不等整批返回。创建ACK、名称/时长单步通过、引用未通过均不能推进完整节点计数。恢复执行会重新读取目标并核验节点，本轮completed随实际核验重建；旧回执和旧进度记录仍用于同一ID续接，计数重核不代表重建节点。后台任务终态仍由完整结果决定，不能仅凭completed==total宣称完成。

图片任务的分母仅包含扫描有效、名称唯一匹配且未明确skip的上传目标；未匹配或不支持的文件不计入分母，缺失/重复/无效/冲突另列；终态仍依据原有执行结果及冲突判定，不能只看分母。新任务将目标数冻结到options.image_target_count，旧任务没有此证据时total显示null，待实际执行扫描后更新；不为补历史分母读取已经改变或消失的旧图片目录。完整图片清单摘要仍用于判断输入是否漂移。

需要冲突选择时，把用户明确给出的选择保存为JSON，传`enqueue --decisions <文件>`；修改选择会形成新任务。同一个request_id可补充冲突选择，但来源内容和所选分镜不能改变；新的修订使用新的commit request_id。用户未授权的覆盖选择不得由脚本自动填入。

同画布、同类、同一组所选分镜或图片目录的新任务会替代未完成旧任务，保留旧JSON和操作日志。依赖旧资产任务的视频自动跟随替代任务；新任务完成后才释放依赖。替代不绕过未知写入核对。显式切换画布后，新画布任务不依赖旧画布任务；旧目标任务保留停止原因，不自动跨画布执行。

用户要求暂停或停止画布工作时，立即调用`pause`。它停止派发新操作；已发送的请求收取确认并保留日志后停止。强制终止可能留下未知结果，恢复必须核对原ID。`start`和新任务不会绕过暂停；只有用户恢复后调用`resume`。恢复先核对原操作日志；结果未知时仍停止，不盲目重建、不删除CLI隔离状态。

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

资产定稿且用户同意传输后，提交assets任务并立即进入剧本范围选择。后台脚本新建、核对、记录ID；Codex可同时用本地定稿台账生成分镜。同步进程不修改`session.json`的phase、分镜编号或历史范围。

资产的`completed`表示名称、类型、空形象/视角名、完整image_prompt及ID映射均已核验；角色/场景图片提示词写入appearanceInfo.description，道具写入generation.prompt。空图片节点允许完成，图片就绪另列。成功计数只包括已核验节点，不能把queued/running、已申请ID或单步ACK当完成。

`status.image_choice_available=true`时，在当前分镜交付/交互边界提示“是否上传本地图片到对应资产节点？1.是 2.暂不上传”，不打断正在起草的分镜，也不以补图阻塞分镜或视频空节点。收到选择后调用image-choice：1记录upload_pending并询问目录，已有目录直接提交images；2记录跳过。同一完成任务的菜单不重复询问。需要切换phase时由前台director执行，后台完成事件不能覆盖当前交互位置。

分镜交付后保留“是否链接小云雀，批量创建视频节点？1.是 2.否”。选否直接followup。选是提交该request_id的视频任务；若资产仍未完成，任务保持依赖等待，同时显示：

1.资产节点还在创建中，继续等待
2.选择新的剧本范围，继续生成分镜提示词

选择2仍保留已授权的视频任务，继续本地分镜。等待选项不触发高频远端查询。资产任务成功后，worker自动处理已授权的下游任务；资产失败或未知确认先停止写入，简短报告实际待处理原因，不能一直显示“创建中”。没有资产任务的显式视频请求沿用原资产盘点与补建流程。图片、视频、引用刷新共用同一画布写入队列。

资产、视频、图片与引用任务均持久保存完成或待处理事件。只有全部授权目标经过最终核验并进入成功终态，才能报告本批同步完成；缺图与生成媒体就绪另列，不能把节点同步完成表述为视频生成完成。部分完成、明确拒绝、结果未知与字段冲突报告已核验数量和具体待处理项，沿原任务和原节点ID恢复。恢复成功另发完成事件；已解决或被替代的失败保留历史，不作为新的待处理提示。

主代理在下一可交互边界读取`status.pending_events`，必须报告其中尚未报告的当前画布事件，注明任务范围、完成或待处理状态及数量；实际向用户展示聊天反馈后，逐个调用`report-event --event-id`记录已报告。查询状态、尝试桌面通知或准备了消息均不等于已报告，不得提前消费。重复调用同一报告记录保持幂等。`status.events`只返回当前绑定画布的事件，完整历史仍保存在events.json；切换画布不串报，旧事件缺少目标信息时由原冻结任务确定，不推测目标。

完整核验的视频任务通过`status.video_followups`提供一次后续入口；来源和所选分镜已变化的旧任务不开放该入口。先报告完成事件，再提示“可继续提供下一段剧本范围，或指定需要修改的分镜”；需要选择卡时可列“继续其他范围的分镜／修改已有分镜／本次结束”。实际展示后用`video-followup --action shown`记录，后续查询不重复展示；收到对应选择时用`continue`、`modify`或`finish`记录。用户已经明确下一步时直接续接，以`already-directed`记录，不强制再选菜单。这些入口仅记录已发生的交互，不改变工作阶段、不排队云端操作、不生成图片或视频，也不解除暂停。新用户请求仍按其授权独立执行，不能把菜单消费当作额外审批。

完成和待处理事件均尽力尝试Windows短暂桌面提示，桌面提示与聊天报告独立。仅记录可证实的通知状态：`started_result_unknown`表示提示进程已启动但显示和阅读结果未知，`start_failed`表示进程启动失败，`attempt_outcome_unknown`表示尝试结果无法确认，`not_attempted`表示平台不支持或事件不属于当前画布。通知过程不等待弹窗关闭，不宣称用户已看到，失败不改变同步结果，也不消费待报告事件。宿主没有事件回调时脚本不能主动向聊天追加消息，由主代理依上述交互边界完成回退报告。暂停和替代不产生失败提示，同一完成事件或相同失败内容不重复通知。

## 持久化与正确性

- `canvas/jobs/job-<摘要>.json`：绑定、冻结session输入、所选单元与版本、请求选项、依赖任务及内容摘要。稳定CHAR/SET/PROP/分镜ID沿用，不创建易漂移的临时序号。
- 同名`.state.json`：worker独占的进度、已核验计数、节点映射和结果。完整映射和逐操作日志继续保存在`canvas/state.json`，视频完成回执仍保存在`canvas/video-write-receipts/`。
- `control.json`：暂停、图片菜单选择、按event_id保存的已报告记录与按job_id保存的视频后续入口状态；`worker.json`：进程状态；`events.json`：带稳定事件ID、目标绑定和桌面通知尝试结果的completed与needs_attention事件，完成每任务一次，相同失败内容去重；`worker.log`：后台运行错误。读取旧事件只补足返回视图，不迁移冻结任务或重发系统通知。

追加新范围不使已有资产任务失效。来源、资产台账、风格、目标画布、所选分镜或待上传图片变动时，旧任务停止，重新提交当前内容；不得继续写旧版本。输入文件与进度文件分离，Codex不改正在执行的任务JSON。

同一账号与画布只有一个后台writer，使用跨进程OS锁并复用原画布操作锁。不同项目绑定同一画布时第二个worker停止，不得启动多个worker并发写入同一画布。worker正在写入时不得另行使用canvas.py修改或上传；需要精确编辑或删除时先暂停队列，确认当前已发送请求的结果并保存日志后，再按原接口执行。

资产按小组申请真实ID（默认每组10个），创建前立即保存全部映射；写命令仍串行逐条取得ACK，每组统一回读并逐字段验证全部节点。10是本地保守分组参数，不是官方QPS或最优值。已完整验证的资产从一次盘点中复用；不重新逐个发起写入。视频继续使用已有逐步ACK、整节点核验及业务引用解析逻辑；保留当前模型与分辨率字段，不查询、写入或校验这两项。

不把通用apply_mutations的dryRun当业务验收，不为批量化改用浅层generation补丁写提示词。已注册描述/提示词命令保持官方业务入口；当前接口不启用纯创建批事务或自行实现的MCP写入路径。未知ACK保留allocated/pending/acknowledged/uncertain等操作状态，只有回读证明目标已生效才跳过；未证明的项不能当普通失败自动重试。

## 输入校验的执行边界

worker在任务开始、资产组及视频节点的写入/核验边界执行完整输入校验；组内控制检查对文件/目录元数据作轻量比对，变化立即触发完整内容校验。没有跨任务永久缓存，来源摘要仍是有效性依据。图片实际上传前后原有逐文件内容hash检查、官方ACK和写后回读继续执行；减少本地反复读取不改变未知写入停止、人工冲突保护或用户暂停语义。
