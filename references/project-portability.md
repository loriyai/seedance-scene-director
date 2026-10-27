# 分镜项目导出、导入与重定位

本模块负责已绑定分镜项目的文件依赖、位置迁移及恢复入口。`scripts/project_bundle.py`负责交接包与原子导入，`scripts/project_paths.py`负责逻辑引用与本机位置，`scripts/project_continuation.py`负责检查点及本地交付登记。原文、资产、创作判断与审阅仍由原负责模块维护。交接包恢复项目制作状态，不恢复聊天进程或代理身份，不创建新的审阅通过结论。

正常项目以现有绑定、正式回执、来源范围和可选检查点为默认恢复路径，不要求存在`storyboard-AF`、本地交付或关联项目。只有实际存在这些历史时才加载相应兼容分支；不得把特殊项目布局或专用辅助脚本设为普通迁移的前置条件。目标电脑需具备兼容的本Skill及Python运行环境，依赖准备沿[后台接口](runtime.md#准备与阶段入口)执行。

## 用户入口与恢复顺序

用户请求导出、上传项目交接包、换电脑继续或重定位时，使用本模块；不能将项目包当作新剧本重新初始化。收到ZIP先运行`project inspect`；格式不是`portable-project-v1`时说明不兼容，不能猜测它是完整项目。导入到当前任务的全新项目目录，再运行`project resume`。检查通过且下一范围已明确时直接继续；未明确时仅询问实际缺失的范围，不重问已保存风格、比例或分段名称。

```text
python <skill>/scripts/director.py project export --project <project> --output <新交接包.zip> --handoff
python <skill>/scripts/director.py project inspect --bundle <交接包.zip>
python <skill>/scripts/director.py project import --bundle <交接包.zip> --target <全新项目目录>
python <skill>/scripts/director.py project verify --project <导入项目>
python <skill>/scripts/director.py project resume --project <导入项目>
python <skill>/scripts/director.py project relocate --project <原项目> --target <全新项目目录>
```

普通备份省略`--handoff`，导出为只读快照；换机交接采用`--handoff`，在成功导出后登记本机停止写入。先让写者与主审在当前安全检查点停止，并确认后台写者已退出；导出程序拒绝活跃画布写者，并在读取前后核对所有打包字节，发现变化则不交付混合版本。不得为打包擅自终止正在进行的未知云端写入。旧版工具不会执行新的交接写入保护，因此不能让旧工具继续写源项目。

`relocate`创建验证后的新副本，保留原项目；它不删除原目录、不改变原机队列。恢复旧机继续须明确执行`project release --project <原项目>`，之后两份成果作为分叉处理，不自动合并。交接标记是本机保护，不宣称离线文件包提供跨电脑的分布式锁。

## 包内容与依赖完整性

包内`manifest.json`登记格式、稳定项目ID、源修订、每个文件的大小与SHA-256、原位置到包内相对位置的映射、检查点、正式及本地交付记录、关联项目与运行版本。包ID绑定整份清单。项目文件保留原字节；路径映射不改写台词、原审报告、场次ID、资产ID、编号或历史时间。

导出收集项目目录、绑定来源及逐场稿、正式交付输出、已关闭本地交付的稿件/预览/原审/上下文与声明依赖、检查点文件及固定的项目工具快照。外部标准化工作目录保持来源与`scenes/`的相对关系。项目外另外需要的文件或目录用重复的`--include <路径>`登记；属于同一来源记录的早期正式项目用重复的`--related-project <项目目录>`登记。关联项目必须与当前项目的来源记录一致，只补充历史覆盖和原回执，不将其单元写入当前正式会话。

本地交付按已有`storyboard-AF/<分段>/bundle-result.json`及`final-review.json`成对发现，核验原场报告、实际TXT、连续范围及原始依赖。只有候选文件而没有关闭终审的，不计为已交付；应登记其检查点。其他项目布局先按相同契约整理显式登记材料，不凭成品TXT推断已审核。复制原工具代码仅保留为证据，不自动执行ZIP内代码；续作使用本机安装且兼容的Skill。

项目凭据、CLI连接配置、环境密钥文件、缓存、进程锁、worker与控制文件不作为可恢复运行状态打包。包内文件摘要证明清单与字节一致，不证明来源可信或原审阅读确实发生。

## 路径、摘要与旧项目兼容

导入保留旧会话和报告中的历史位置，通过统一解析器定位新文件；新保存的位置使用`project://<项目ID>/<包内位置>`逻辑引用。来源位置、回执输出、批次作用域和预览事件均经所属读取模块解析，不对所有文本全局替换路径。未知且未收录的外部原机引用须报错，不回退读取原电脑文件。新电脑的Python、标准化模块与CLI位置分别由本机已安装环境提供，不采用旧机器可执行路径。

新的`content-context-v2`使用文件角色及内容摘要绑定来源，结合记录、台账、必要邻接内容、规则及拆分方案；本机位置不构成内容变化。更改内容、范围、台账或规则仍须按实际影响复核。

旧版待交付审阅只有在能够重建其原上下文摘要，且创作规则、稿件与报告身份一致时，才能建立独立的路径绑定等价记录。原报告不改字，不刷新摘要冒充通过。无法证明等价或创作规则已有变化时，`resume`标记`historical_context_requires_impact_check`，由原审阅流程核对实际影响；不能自动批准，也不因此重审未受影响的已交付历史。已正式交付与本地已审成果分别保留，任何迁移均不补造正式receipt或official checked。

## 检查点与继续制作

```text
python <skill>/scripts/director.py project checkpoint --project <project> --stage writing --scene SC0001 --draft <draft.txt> --context <context.json> --note <必要承接事实和未决项>
python <skill>/scripts/director.py project checkpoint --project <project> --stage needs_revision --scene SC0001 --draft <draft.txt> --context <context.json> --review <原审报告.json> --note <集中修改项>
python <skill>/scripts/director.py project checkpoint --project <project> --stage delivered --next-segment B
```

阶段为`writing`、`awaiting_review`、`needs_revision`、`passed_pending_delivery`、`delivered`。未交付检查点须绑定真实草稿及上下文；返修和待交付还须保留匹配原审。`passed_pending_delivery`不等于正式提交；仍须通过当前绑定与原有commit核验。只记录既有任务状态、必要持续事实与未决项，不记录私有思考，不新增逐镜表单或审批轮次。

`resume`同时展示当前正式成果、本地交付、关联正式项目与检查点。范围覆盖检查纳入所有已验证成果，避免把本地交付或早期项目误认为未制作。场次上下文提供相邻本地成果的原实际文字供承接；程序不据此生成持物、接触或通路结论。下一范围未给出或已经全部完成时如实说明，不自动重复生成。导入后继续写作仍执行写者同次检查、独立整场审阅及集中修订流程。

## 原子恢复与验证边界

导入先核验清单及全部文件，再恢复到目标目录旁的临时目录，核验绑定、关联来源、回执和本地原审身份后才启用新目录。目标已存在则拒绝覆盖；同包重复导入仅在现有全部原文件仍与该包一致时返回`already_imported`。有后续修改的项目不能被旧包覆盖。路径越界、跨平台重名、符号链接、加密条目、缺失/多余文件、摘要不匹配及超出大小/数量限制的包均拒绝。

功能验收须包含原目录不可访问时的context、正式重导出与幂等重试、待审/返修检查点、本地范围覆盖、再次迁移、编号不重置，以及损坏包失败不留下半恢复项目。跨平台路径规则不等于已经在每种操作系统实测；交付报告明确实际运行环境。

画布本机配置、只读核验和队列恢复边界见[画布队列](canvas-jobs.md#迁移后的画布任务)。本地导入、检查和继续分镜不要求云端登录。
