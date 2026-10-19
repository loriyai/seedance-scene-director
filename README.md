# Seedance 场景分镜导演

本 Skill 将完整中文剧本整理成标准稿，筛选并设计可复用人物、场景和主要道具，输出 Gpt-image-2 图片提示词，再按用户指定范围生成 Seedance 2.5 分镜提示词。通过明确授权的小云雀CLI操作画布，不自动生成图片或视频。当前版本为 **v0.19**，更新日期 **2026-10-19**，以 `VERSION` 为准。

本次将新分镜改为 **scene-text-v1 文字母稿**：范围简报一次，当前场短节拍，直接写完整提示词；作者边写边检查，每场由一位独立主审完整阅读一次，集中修订后只复看改动与受影响连接。场末只记后续需要的事实，范围末只核覆盖和场次连接。脚本负责原话、时轴、引用、编号、导出和事件计时；真实表演、摄影与连续性仍由作者和主审判断。

新稿只需[分镜工作流](references/storyboard-workflow.md)、[创作规则总集](references/storyboard-rules.md)和[文本母稿格式](references/storyboard-text-format.md)三份资料；命令另由[后台接口](references/runtime.md)索引。所有本次移除/退出新路径的机制集中记录在[分镜重构移除记录](references/storyboard-removal-record.md)。旧脚本和回执仍保留读取及明确旧格式修改用途；安装不会迁移真实项目、资产或Canvas状态。

## 使用

1. 发送完整剧本及风格。尚无本项目风格时先选择并保存，已有有效风格不重复问。
2. 完成标准化、来源复核、资产筛选与美术设计，交付标准剧本TXT、最终资产台账JSON、分类图片提示词TXT。内置screenplay-standardizer，无需另装。
3. 仅标准稿/资产任务到相应交付结束；完整workflow继续取得分镜范围。明确全部或起止时直接定位，范围异常才取得必要选择。
4. 按场完成文字分镜和独立审阅，正式交付TXT链接、本次数量与总时长。指定编号可局部修改，其他单元保持原版本。
5. 用户选择后再通过画布适配入口建节点、写提示词或上传图片。节点操作与图片/视频生成分别授权；停止/暂停不会因安装或恢复状态查询自动解除。

格式继续使用视觉抬头、保存的风格、实际资产名与现有镜头版式。单元组织、时长与摄影选择统一见[创作规则总集](references/storyboard-rules.md)，不在入口另设规则。保留原台词、声源、事件因果及动作结果，不以镜数、时长或表现配额新增剧情。

## 安装与运行

需要支持本地Skill的Codex和Python 3.10+。完整剧本风格确认后才检查当前Python的PyYAML，缺失时安装并验证，已有可用版本不升级。画布操作另需Node.js、npm及官方CLI，按需授权。

安装到当前CODEX_HOME下的skills/seedance-scene-director；已有安装先在Skills目录外备份，确认没有活跃Canvas写者，再完整替换并核对VERSION和文件摘要。不混用不同包的模块，不写真实项目到Skill目录。公开版本从[项目仓库](https://github.com/loriyai/seedance-scene-director)选择明确tag，本地修订不会自动发布。

Python回归入口为 `scripts/self_check.py`，Skill元数据另用quick_validate检查。结构、离线测试和来源覆盖不证明实际视频效果或整体提速；耗时以本次实际事件为准。
