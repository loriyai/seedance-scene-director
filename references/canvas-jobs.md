# 后台画布同步

资产、视频、引用刷新和图片上传默认使用 `scripts/canvas_jobs.py`。先完成 `canvas.py setup/auth/bind`，复用已绑定账号与用户指定画布。后台只调用官方CLI，不生成图片或视频。

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
```

`enqueue`不带`--start`只输出不可变任务JSON；`start`启动隐藏后台Python进程并立即返回，`worker`为该进程内部入口。不得用宿主反复等待进程退出来模拟后台。`status`只读本地状态，在交付分镜、处理节点菜单等交互边界读取即可，不循环核对远端或占住对话等待。

需要冲突选择时，把用户明确给出的选择保存为JSON，传`enqueue --decisions <文件>`；修改选择会形成新任务。同一个request_id可补充冲突选择，但来源内容和所选分镜不能改变；新的修订使用新的commit request_id。用户未授权的覆盖选择不得由脚本自动填入。

同画布、同类、同一组所选分镜或图片目录的新任务会替代未完成旧任务，保留旧JSON和操作日志。依赖旧资产任务的视频自动跟随替代任务；新任务完成后才释放依赖。替代不绕过未知写入核对。显式切换画布后，新画布任务不依赖旧画布任务；旧目标任务保留停止原因，不自动跨画布执行。

用户要求暂停或停止画布工作时，立即调用`pause`。它停止派发新操作；已发送的请求收取确认并保留日志后停止。强制终止可能留下未知结果，恢复必须核对原ID。`start`和新任务不会绕过暂停；只有用户恢复后调用`resume`。恢复先核对原操作日志；结果未知时仍停止，不盲目重建、不删除CLI隔离状态。

## 并行工作与菜单

资产定稿且用户同意传输后，提交assets任务并立即进入剧本范围选择。后台脚本新建、核对、记录ID；Codex可同时用本地定稿台账生成分镜。同步进程不修改`session.json`的phase、分镜编号或历史范围。

资产的`completed`表示名称、类型、空形象/视角名、完整image_prompt及ID映射均已核验；角色/场景图片提示词写入appearanceInfo.description，道具写入generation.prompt。空图片节点允许完成，图片就绪另列。成功计数只包括已核验节点，不能把queued/running、已申请ID或单步ACK当完成。

`status.image_choice_available=true`时，在当前分镜交付/交互边界提示“是否上传本地图片到对应资产节点？1.是 2.暂不上传”，不打断正在起草的分镜，也不以补图阻塞分镜或视频空节点。收到选择后调用image-choice：1记录upload_pending并询问目录，已有目录直接提交images；2记录跳过。同一完成任务的菜单不重复询问。需要切换phase时由前台director执行，后台完成事件不能覆盖当前交互位置。

分镜交付后保留“是否链接小云雀，批量创建视频节点？1.是 2.否”。选否直接followup。选是提交该request_id的视频任务；若资产仍未完成，任务保持依赖等待，同时显示：

1.资产节点还在创建中，继续等待
2.选择新的剧本范围，继续生成分镜提示词

选择2仍保留已授权的视频任务，继续本地分镜。等待选项不触发高频远端查询。资产任务成功后，worker自动处理已授权的下游任务；资产失败或未知确认先停止写入，简短报告实际待处理原因，不能一直显示“创建中”。没有资产任务的显式视频请求沿用原资产盘点与补建流程。图片、视频、引用刷新共用同一画布写入队列。

## 持久化与正确性

- `canvas/jobs/job-<摘要>.json`：绑定、冻结session输入、所选单元与版本、请求选项、依赖任务及内容摘要。稳定CHAR/SET/PROP/分镜ID沿用，不创建易漂移的临时序号。
- 同名`.state.json`：worker独占的进度、已核验计数、节点映射和结果。完整映射和逐操作日志继续保存在`canvas/state.json`，视频完成回执仍保存在`canvas/video-write-receipts/`。
- `control.json`：暂停与图片菜单选择；`worker.json`：进程状态；`events.json`：每个成功任务仅一条完成事件；`worker.log`：后台运行错误。

追加新范围不使已有资产任务失效。来源、资产台账、风格、目标画布、所选分镜或待上传图片变动时，旧任务停止，重新提交当前内容；不得继续写旧版本。输入文件与进度文件分离，Codex不改正在执行的任务JSON。

同一账号与画布只有一个后台writer，使用跨进程OS锁并复用原画布操作锁。不同项目绑定同一画布时第二个worker停止，不启用3～8路同画布并发。后台忙时不要另开直接canvas.py修改/上传；需要精确编辑或删除时先暂停队列并等当前请求收尾，再按原接口执行。

资产按小组申请真实ID（默认每组10个），创建前立即保存全部映射；写命令仍串行逐条取得ACK，每组统一回读并逐字段验证全部节点。10是本地保守分组参数，不是官方QPS或最优值。已完整验证的资产从一次盘点中复用；不重新逐个发起写入。视频继续使用已有逐步ACK和整节点核验逻辑，保持模型参数与业务引用解析。

不把通用apply_mutations的dryRun当业务验收，不为批量化改用浅层generation补丁写提示词。已注册描述/提示词命令保持官方业务入口；首版不启用纯创建批事务或自写MCP。未知ACK保留allocated/pending/acknowledged/uncertain等操作状态，只有回读证明目标已生效才跳过；未证明的项不能当普通失败自动重试。

完成事件会尝试在Windows显示短暂桌面提示。通知失败不影响已保存结果；宿主没有事件回调时，脚本不能主动向聊天追加消息，Codex在下一交互边界读取本地事件后简短报告。只有全部任务目标核验成功才提示完成，不能把部分成功计为整批完成。
