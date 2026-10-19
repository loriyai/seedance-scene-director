# 规则职责与摘要

新文字分镜只在[工作流](storyboard-workflow.md)、[创作规则总集](storyboard-rules.md)和[文本语法](storyboard-text-format.md)维护职责。场级报告绑定最终文件及当前项目上下文，不使用下述旧规则哈希图或逐镜复用证据。规则变化时按真实影响阅读/修订，不自动重做未受影响镜头。

## 旧JSON规则摘要实现

下文仅记录保留的legacy实现，用于明确旧格式与历史证据兼容；不作为scene-text-v1交付门槛。

正式首轮和增量审阅均通过 `scene_review.rules_digest()` 取得当前规则摘要。其实现委托 `creative_rules.rules_manifest()`，当前策略为 `storyboard-rule-domains-v1`。摘要绑定实际文件和代码投影，不以人工版本号代替内容校验。

## 创作权威

旧JSON数据字段由[旧分镜契约](storyboard-contract.md)维护；仍适用的实质创作规则已归入[创作规则总集](storyboard-rules.md)，流程归入[分镜工作流](storyboard-workflow.md)。SKILL.md负责入口和路由，runtime.md负责命令与恢复；不要只在这两个混合文件的运营段增加新的创作要求。

所有包内文件默认进入创作规则摘要，包括尚未分类的新文件。新 `state_effects.py`、装配、精确修订和未来验证辅助器不依赖维护者记得补一份固定白名单。缺少固定抬头、核心验证入口或本契约等必需文件时直接报错。文件删除、重命名、未知代码与未知参考修改均保守失效。

## 明确的运行部分

运行差异不自动要求全稿重新作语义审阅，但仍受实际交接输入、命令检查及权限边界约束。当前排除限定如下：

- `creative_rules.OPERATION_FUNCTIONS` 明确列出的纯运行函数，仅函数体不进入创作摘要。函数名称、参数、默认值、装饰器，以及文件中的导入、常量、未列函数和其他顶层代码仍进入摘要。不能把新的验证或渲染逻辑放入已分类运行函数来规避绑定；职责改变时须先移出该分类。
- `director.py` 只整体排除 `save` 与 `sync_canvas_replacements` 的函数体。`set_style` 仍参与；其中仅对 `state['phase']` 的无副作用赋值和纯阶段分支作运行投影。风格读取、文本处理、验证、`style_hash`、渲染与未知调用仍参与摘要。其他 director 函数，包括 `validate`、`validate_unit`、`render_unit`、`render_receipt`、`commit`、`main` 全部参与。
- 明确列出的包版本、README、组件打包清单、模型路由配置及测试运行入口不构成创作权威。测试源码采用 `test_*.py` / `test_*.js` 命名，只作回归，不得被生产路径用作语义规则模块。缓存目录不属于规则；仅根 `logs/` 下 `.log`、`.jsonl`、`.txt` 是运行日志，`logs/` 下未知代码仍参与摘要。工作数据和日志仍应保存在项目工作区。

运行函数名单与元数据名单本身保存在 `creative_rules.py`，该文件全文进入摘要。更改分类需要真实规则版本变化与测试，不能只刷新旧报告的摘要。上述排除不等于允许忽略实际来源、风格或当前请求变化。

## 混合文档的显式运营块

只允许以下文件和块名：

| 文件 | 已登记块名 |
|---|---|
| SKILL.md | `entry-routing`、`style-intake`、`local-runtime`、`asset-pipeline`、`stage-routing`、`delivery` |
| references/runtime.md | `command-reference`、`recovery`、`canvas-operations`、`dependencies` |

维护者只把已确定的运营段落包在成对标记中：

```html
<!-- rule-domain:operations:start local-runtime -->
既有本地运行步骤。
<!-- rule-domain:operations:end local-runtime -->
```

块名可用于多个分开的段落，不能嵌套。标题放在标记之外；块内出现标题时，该标题及随后内容保守进入摘要，避免把新章节悄悄归为运营。块外内容和未知标记默认保留；已知块中出现未知标记、错配或未关闭标记时报错。标记只说明维护者已审阅的职责，不是自动判断文字是否属于创作规范的工具。

## 历史与校验

历史 `RULE_FILES` 清单和旧快照记录保留原读取与完整性校验；不重写历史 `rules_sha256`。当前策略切换后，旧规则摘要与当前不同，新提交按当前规则准备待审，已交付 receipt 仍按原证据读取与幂等导出。

测试须实际修改隔离副本中的创作文件、明确运行函数、phase 分支及未知代码，分别验证是否改变摘要；不能只 mock 返回版本字符串证明职责拆分。纯运行改动不失效的测试也必须验证同文件中的风格、渲染或验证改动仍会失效。分类不承诺理解自然语言或判定改稿语义；真实语义判断仍由作者与独立主审完成。
