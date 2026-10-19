# 后台命令索引

所有JSON/TXT使用UTF-8。`<skill>`为实际Skill目录，`<project>`为独立项目目录，实际执行使用绝对路径。新文字分镜只读[工作流](storyboard-workflow.md)、[创作规则总集](storyboard-rules.md)及[文本语法](storyboard-text-format.md)，本页只索引命令，不另维护创作规则。

## 准备与阶段入口

完整剧本风格确认前只检查文件可访问，不运行依赖检查或分析。风格已明确时先由 `director.py style` 保存，再用当前实际Python运行 `scripts/ensure_dependencies.py`；仅缺失PyYAML时在同一解释器安装并验证，已有可用版本不升级，失败报告一次，不提权/循环尝试。

资产完整顺序及命令唯一见[资产主流程](asset-workflow.md)。内置标准化脚本以 `<skill>/modules/screenplay-standardizer/` 为根，不搜索其他同名Skill，缺文件/摘要损坏时恢复完整包；项目产物不写安装目录。标准化、资产和设计交接按[模型调度](model-routing.md)保留prepare/verify/close；新文字分镜直接交接路径、版本和范围。

```text
python <skill>/scripts/director.py status --project <project>
python <skill>/scripts/director.py locate --project <project> --anchors <anchors.json>
python <skill>/scripts/director.py range --project <project> --start <起点整数> --end <终点整数>
```

范围和异常确认按[工作流](storyboard-workflow.md#范围定位与历史检查)。原位rebind未实现，来源/台账变化用[绑定变更边界](binding-changes.md)，不能手改session摘要。

## 文本分镜接口

新文字分镜的绑定/范围命令、check/preview/commit/render和场级报告，唯一维护在[文本母稿格式与接口](storyboard-text-format.md#文本分镜接口)；本页不重复接口内容。

## 事件计时

新文字任务的event/timing命令及计时口径见[事件计时](storyboard-text-format.md#事件计时)。旧资产交接计时继续使用pipeline_metrics。

## 恢复与画布

先按项目文件选择入口。已有`session.json`的绑定项目运行`director.py status`；尚未bind的新项目读取`project-contract.json`、已保存来源/阶段产物及`pipeline_metrics.py status --project <project>`，不能把缺少session当作损坏或因此重复初始化。status的session/recovery接口不承担未绑定项目的阶段诊断。

绑定项目以`status.recovery`中的session前台阶段、当前输入对应队列及提交记录为准；`intake-state.json`仅是历史快照，不用其旧计数、next_step或失败描述决定续接。查询只读、不连接云端、不改变phase、不解除暂停。phase只表前台位置，不代表外部授权；后台worker状态独立，已排队或单步ACK不计完整节点完成，完成事件不覆盖正在创作的phase。

资产/图片/视频队列、上传菜单、真实节点ID、字段ACK/回读、UNCERTAIN_WRITE及续接只在[画布流程](canvas-workflow.md)、[接口](canvas.md)和[队列](canvas-jobs.md)维护。只在已授权画布工作时读取；本地分镜不依赖登录，后台失败不抹掉本地交付。同画布单writer，未知写入先回读且沿原ID，人工修改冲突保护和暂停边界保留。

只有旧JSON的既有替换commit保留原自动局部同步行为；scene-text-v1以本页的显式节点操作为准。none-v1不创建或检查黑幕；历史黑幕回执和冻结任务按原协议读取，不因安装改节点/连线。

## 明确旧格式的接口

以下仅供用户明确继续旧JSON稿或读取旧回执。旧字段权威见[旧分镜契约](storyboard-contract.md)，装配、动作效果、修订分别见[draft-assembly](draft-assembly.md)、[state-effects](state-effects.md)、[draft-revision](draft-revision.md)。它们不是新文字稿交付步骤。

```text
python <skill>/scripts/director.py check --project <project> --request <旧格式batch.json> --require-workflow
python <skill>/scripts/review_gate.py prepare --project <project> --request <旧格式batch.json> --report <旧格式最终复核.json> --require-workflow
python <skill>/scripts/director.py commit --project <project> --request <旧格式batch.json> --review <旧格式最终复核.json> --output <新交付.txt> --require-workflow
```

沿原协议和脚本--help确定旧报告/冻结接口，不把新场级报告传给旧gate。不补改历史rules_sha256，不自动迁移已交付receipt。旧PROD001问题本轮未修，边界见[移除记录](storyboard-removal-record.md#旧缺陷与兼容边界)。

## 历史锚点索引

### 范围坐标

新文字范围见[文本语法](storyboard-text-format.md)，旧JSON见[旧范围坐标](storyboard-contract.md#范围坐标)。

### 请求结构

新稿只交文本；旧JSON见[旧请求结构](storyboard-contract.md#请求结构)。

### 表演母稿与关键动作

新稿见[创作规则总集](storyboard-rules.md)，旧字段见[旧契约](storyboard-contract.md#表演母稿与关键动作)。

### 台词表现字段delivery

新稿声音写文本标记；旧delivery只见[旧字段](storyboard-contract.md#台词表现字段delivery)。

### 最终稿复核与估时诊断

新稿见本页文本接口；旧报告见[旧契约](storyboard-contract.md#最终稿复核与估时诊断)。

### 引用与统计

新稿引用/统计由文本适配器派生；旧字段见[旧契约](storyboard-contract.md#引用与统计)。

### 可复用布景

SC/LOC/SET身份及版本见[资产主流程](asset-workflow.md)，旧字段接口见[旧契约](storyboard-contract.md#可复用布景)。

## 本地验证

`python <skill>/scripts/self_check.py`为完整Python回归入口，与unittest discovery二选一，不重复累计。CLI能力测试为`node <skill>/scripts/test_cli_capabilities.js`，只用mock；Skill元数据另运行quick_validate。验证只用隔离样本，不读改真实项目或写画布，结构/离线通过不证明语义质量或真实生成效果。
