# 后台命令索引

所有JSON/TXT使用UTF-8。`<skill>`为实际Skill目录，`<project>`为独立项目目录，实际执行使用绝对路径。新文字分镜只读[工作流](storyboard-workflow.md)、[创作规则总集](storyboard-rules.md)及[文本语法](storyboard-text-format.md)，本页只索引命令，不另维护创作规则。

## 准备与阶段入口

完整剧本风格确认前只检查文件可访问，不运行依赖检查或分析。先由`director.py style`保存风格，再由`director.py ratio`独立保存视频比例，两项设置完成后才运行`scripts/ensure_dependencies.py`与`project-init`。比例已有当前聊天确认默认值时使用`--inherit`，不重复询问；设置命令不解析剧本或分派生产。依赖准备仅缺失PyYAML时在同一解释器安装并验证，已有可用版本不升级，失败报告一次，不提权/循环尝试。

资产完整顺序及命令唯一见[资产主流程](asset-workflow.md)。内置标准化脚本以 `<skill>/modules/screenplay-standardizer/` 为根，不搜索其他同名Skill，缺文件/摘要损坏时恢复完整包；项目产物不写安装目录。标准化、资产和设计交接按[模型调度](model-routing.md)保留prepare/verify/close；新文字分镜直接交接路径、版本和范围。

```text
python <skill>/scripts/director.py intake-status --project <project>
python <skill>/scripts/director.py style --project <project> --choice 1
python <skill>/scripts/director.py ratio --project <project> --choice 1
python <skill>/scripts/director.py ratio --project <project> --inherit
python <skill>/scripts/director.py status --project <project>
python <skill>/scripts/director.py locate --project <project> --anchors <anchors.json>
python <skill>/scripts/director.py locate --project <project> --segment A
python <skill>/scripts/director.py range --project <project> --start <起点整数> --end <终点整数>
```

设置命令按实际选择执行，不同时执行示例中的两种ratio保存方式。`--choice 1`为16:9，`--choice 2`为9:16；比例保存后由统一画幅配置绑定`landscape-v1`或`portrait-v1`，新分镜单元冻结该配置，历史回执仍按原格式读取。风格选项及自定义命令见主流程。分段计划的准备、确认、摘要与精确范围接口统一见[剧本分工规则](script-partition.md)。范围和异常确认按[工作流](storyboard-workflow.md#范围定位与历史检查)。原位rebind未实现，来源/台账变化用[绑定变更边界](binding-changes.md)，不能手改session摘要。

## 文本分镜接口

新文字分镜的绑定/范围命令、check/preview/commit/render和场级报告，唯一维护在[文本母稿格式与接口](storyboard-text-format.md#文本分镜接口)；本页不重复接口内容。

## 事件计时

新文字任务的event/timing命令及计时口径见[事件计时](storyboard-text-format.md#事件计时)。旧资产交接计时继续使用pipeline_metrics。

## 恢复与画布

跨电脑交接、导入已上传项目包和本机路径重定位统一使用[项目迁移](project-portability.md)中的`director.py project`接口。恢复后先读取`project resume`；绑定内容未变的位置迁移不走原位rebind，不手改会话、原审摘要或批次编号。

先按项目文件选择入口。已有`session.json`的绑定项目运行`director.py status`；尚未bind的新项目先用`intake-status`核对风格和比例，再读取`project-contract.json`、已保存来源/阶段产物及`pipeline_metrics.py status --project <project>`，不能把缺少session当作损坏或因此重复初始化。status的session/recovery接口不承担未绑定项目的阶段诊断。workflow-settings.json只记录新入口版本，不迁移旧项目；分工计划与视频比例均由各自保存接口维护，不能手改session中的摘要或编号。

绑定项目以`status.recovery`中的session前台阶段、当前输入对应队列及提交记录为准；`intake-state.json`仅是历史快照，不用其旧计数、next_step或失败描述决定续接。查询只读、不连接云端、不改变phase、不解除暂停。phase只表前台位置，不代表外部授权；后台worker状态独立，已排队或单步ACK不计完整节点完成，完成事件不覆盖正在创作的phase。

资产/图片/视频队列、完成反馈、后续入口、真实节点ID、字段ACK/回读、UNCERTAIN_WRITE及续接只在[画布流程](canvas-workflow.md)、[接口](canvas.md)和[队列](canvas-jobs.md)维护。节点事件的查询、实际报告后消费及视频后续选择使用队列接口，不把桌面通知启动当作用户已看到。只在已授权画布工作时读取；本地分镜不依赖登录，后台失败不抹掉本地交付。同画布单writer，未知写入先回读且沿原ID，人工修改冲突保护和暂停边界保留。

只有旧JSON的既有替换commit保留原自动局部同步行为；scene-text-v1以本页的显式节点操作为准。none-v1不创建或检查黑幕；历史黑幕回执和冻结任务按原协议读取，不因安装改节点/连线。

## 历史回执兼容

历史reference-v1/v2/v3及scene-text-v1回执按保存格式读取和重导出，安装不迁移项目或刷新历史审阅摘要。已有回执使用下列命令；输出须为新路径，具体保护沿原接口执行。

```text
python <skill>/scripts/director.py render --project <project> --request-id <已保存请求ID> --output <新导出.txt>
```

新创作和用户明确采用文字路径的指定修改使用[文本接口](storyboard-text-format.md#文本分镜接口)，不进入旧装配、状态效果、逐镜依赖或旧审阅链。新场级报告不能传给旧review_gate，旧JSON请求也不能传给文本入口。

用户明确继续编辑旧JSON时，保留原稿结构、来源范围和历史版本，依据实际保存的请求及相应脚本--help使用兼容接口；这些实现仍被历史操作及回归调用，不是新文字稿前置。旧流程可能返回原有问题；兼容保留不表示已修复，例如scene_review.verify_reuse_evidence仍按列表顺序比较复用警告键。不能为通过旧检查重写历史证据或自动切换生产格式。

新写或明确修订的reference-v3有声单元在预览前登记`speech_render_policy: explicit-cues-v1`，声音括注仅渲染实际指定的声音表现，不从估时档位附加语速标签。现有`dialogue_layout.attach`及显式装配入口为工作副本登记该策略，手写请求须同样声明；未知策略报错。该标记参与原有声音审阅绑定，不改变声音继承协议。无此标记的历史回执按原渲染方式重导出，读取与导出不登记新策略；新文字路径不增加此字段。估时规则统一见[声音与时长](storyboard-rules.md#声音与时长)，兼容数值由现有`dialogue_layout`实现供调用方复用。

## 本地验证

`python <skill>/scripts/self_check.py`为完整Python回归入口，与unittest discovery二选一，不重复累计。CLI能力测试为`node <skill>/scripts/test_cli_capabilities.js`，只用mock；Skill元数据另运行quick_validate。验证只用隔离样本，不读改真实项目或写画布，结构/离线通过不证明语义质量或真实生成效果。
