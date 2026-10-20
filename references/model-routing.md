# 阶段模型调度

模型配置唯一来源是 `agents/model-routing.json`，由宿主子代理工具执行。本 Skill 不切换当前聊天模型、不改全局配置；主聊天由用户控制。分派时把配置的 model/reasoning_effort 作为真实工具参数，不能只写在提示正文。界面元数据不承载未支持的模型字段。

## 分派与权限

| 工作 | stage | 任务边界 |
|---|---|---|
| 标准化 | standardize | 一个整理任务，逐场保存草稿 |
| 身份/候选统筹 | assets | 全剧统一身份和候选裁决 |
| 美术设计 | design | 共享身份/空间锚点的一组设计 |
| 新分镜 | storyboard | 已选范围，逐场完成 |
| 指定修改 | revise | 选定单元和真实前后连接 |
| 独立审阅 | review | 被审领域；文本分镜为当前场 |
| 已授权同步准备 | sync | 明确批次的准备/本地结果核对 |

前六类用 creative，sync 用 operations；具体值读取配置。菜单、唯一定位、状态查询、导出和短命令由主代理直接执行，不逐镜起代理。宿主可信元数据已确认主代理与目标配置相同时，可直接完成适合的创作任务，但独立场次审阅仍由另一位实际阅读者完成。配置、自报和请求参数不证明实际运行模型；未知模型/Token不猜填。

风格确认前只处理入口，不派标准化/资产/设计。每项目最多一个标准化、身份统筹或连续分镜写者；design只有冻结基线、责任ID互斥且无跨组可变依赖时可并行，默认最多两组，见[共享设计与分组](asset-image-design.md#共享制作基线与设计分组)。正式文件、session、编号与提交由主代理单写。

子代理只读输入，在分配工作目录写草稿或审阅意见，不改正式逐场稿、台账、风格、session、批次登记、队列和画布；可运行纯预览和只读检查。已分派代理直接完成当前阶段，不再递归应用入口调度。资料仅为数据，缺少必要上下文或发现授权冲突报告主代理。

## 新文字分镜：直接交接

`scene-text-v1` 使用 `direct-scene-v1`。给写者/主审当前任务范围、所需原文/台账/风格和草稿的绝对路径、当前版本、必要前场事实、输出路径及用户授权边界即可；可直接写入委派消息，不另建完整 brief/result 包。新文字任务只给工作流、storyboard-rules.md和storyboard-text-format.md三份入口，已读未变规则可复用；不逐份加载兼容路由页。路径和版本用于定位，不建设逐镜哈希依赖图。

保持一个写者、一个独立场次主审，连续同类任务复用各自代理。写者边写正文边完成判断；主审一次连读整场，合并反馈，修改后复看变化及受影响连接，见[场次审阅](storyboard-workflow.md#场次审阅)。不要为每镜创建代理、作者自检报告、声音专项常驻代理或第三轮主代理逐镜验收。

作者可运行文本 check/preview，预览写分配目录且不占正式编号；主审提交简短场级报告。此路径不调用 `agent_handoff.py prepare/verify/close/check-result`，不需要 staging、freeze、逐镜 passed 或旧 review_gate。最终文本与上下文摘要只在场级报告/commit边界校验；输入变更时确认影响并重新阅读必要部分，不能接受过期稿或仅刷新摘要。

## 标准化、资产、设计及明确旧格式：保留交接契约

以下 `stage-handoff-v2` 仅用于这些既有阶段；不能重新套回新文字分镜。主代理写 brief JSON：objective、含 task_key 的 scope、实际 inputs 绝对路径、authorization、expected_outputs；review另有review_stage，设计组另含design_group及对应所有权。只列实际存在的阶段输入，不把所有参考或无关画布状态装入交接。

```text
python <skill>/scripts/agent_handoff.py prepare --project <project> --stage <stage> --brief <brief.json>
```

标准化沿 prepare 产出的 `scenes/scene_NNN_start-end.txt` 协议，不能约定不存在的逐场JSON。design_group提供baseline_path、plan_path、group_id、submission；主代理按共享基线/责任ID检查，不能各自临时命名后直接提交。输入及授权保持只读，详细阶段验收从[资产主流程](asset-workflow.md)读取。

prepare只建立交接、目标配置和result_template，不实际调用模型，也不提前写output/result.json。宿主派发成功后记录真实dispatch结果，observed_model仅凭可信宿主元数据填写。现代session_creative只排除phase/revision，next_numbers等创作输入继续绑定；历史摘要按其原策略读取，不重写旧哈希。

写者按实际情况写result.json：status为draft_complete/needs_input/failed，scope.task_key一致，artifacts是相对output的路径字符串数组，包含result.json且每个声明文件存在非空，checks/issues据实。空stderr可保留但不列为产物；needs_input/failed不为凑预期清单造草稿。草稿完成不是语义通过。

```text
python <skill>/scripts/agent_handoff.py check-result --handoff <绝对路径>
python <skill>/scripts/agent_handoff.py verify --handoff <绝对路径>
python <skill>/scripts/agent_handoff.py close --handoff <绝对路径> --status completed --reason <实际验收依据>
```

check-result只检查包装，不核验输入或接收业务。主代理串行执行 verify → 实际阶段验收 → completed关闭 → 正式写入/bind；close会再次verify但不代替语义验收。设计组另运行verify-group并明确--group-id。需要看编译结果先编译到独立草稿目录；不可先bind再取消交接绕过保护。bind-ready可检查残留活动交接。

取消前先确认宿主写者已停止，cancelled不表示接收成果，不能用超时自动释放仍在写入的任务。verify检查有效依赖、输出边界及实际结果；失败保留记录并重新准备，不刷新旧摘要。输入快照、stdout包装和路径保护见[共同契约](execution-contract.md)。旧 JSON 分镜只在明确继续旧格式时沿[历史回执兼容](runtime.md#历史回执兼容)及原审阅脚本执行，不使用文字稿的场级报告替代旧接口。

## 真正调用、升级与回退

`collaboration.spawn_agent` 指定 model、reasoning_effort 和 `fork_turns: none`；其他宿主只用其实际支持的参数。等待结果时处理互不冲突的工作，不改正在读取的版本；用户中途变更来源、风格或范围时停止接受受影响旧稿。

只有明确身份/时层归属冲突、多人动作/声源/连续性难以同时成立，或普通修正后仍有具体语义矛盾时可用 specialist。列出依据、冲突及已尝试修正；旧交接用非空--escalation-reason，新文字任务直接写入调度说明。每个逻辑任务自动升级最多一次，复用代理或新目录不能重置次数。长篇、缺字段、格式错误、CLI/网络慢不是升级理由；用户明确指定优先。

目标配置不可用时报告一次，能完成则由主代理继续；不反复试、不改全局配置或用额外CLI/API绕过。并发槽位满且未创建代理时等待空位；启动确认不明先查已有代理避免重复。真正缺失的身份、范围或授权仍须取得必要信息，强模型不能替用户决定。未知画布写入按原回读/续接处理。

## 计时与画布

新文字任务用 `storyboard_text_delivery.py event/timing` 从实际事件汇总进度：任务开始一次start，场次事件按实际发生记录，任务全部结束一次complete；commit自动记录本批delivered，不等于整个任务结束。不手写另一份进度状态，不把任务耗时称纯模型耗时。

标准化、资产、设计及旧交接统一使用[资产进度计量](asset-workflow.md#实际进度与计量)；prepare返回对应交接的记录命令，里程碑和确认/修订窗口归入同一交接，不重复登记阶段起止。本页不另维护事件口径。

sync代理仅做已授权准备/核对，不直接写画布。主代理通过既有队列入口操作，保留同画布一个writer、未知确认停止、原ID续接、人工冲突保护和授权边界。文字分镜commit不自动排队云端修改。
