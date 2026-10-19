> **历史结构化稿专用（legacy）**：本文件仅用于读取或维护已有 reference-v3 / strict-draft-v1 数据。v0.19 新起草及明确重做使用 [文字稿工作流](storyboard-workflow.md)；不得把这里的字段、放行或回执要求加到 scene-text-v1 起草与交付。旧脚本保留兼容，不代表新稿仍须执行。

# 精确修订长期接口

起草中的字段修改使用 `scripts/draft_revision.py`；正式项目的指定修改、范围授权和提交仍走 `director.py`。本接口读取一个已严格装配的请求，保存新的请求文件，不修改 `session.json`、生产文件或已有稿件。

先按稳定单元与镜头身份提取当前值：

```text
python scripts/draft_revision.py inspect --input draft.json --unit draft-... --shot shot-... --path /camera
```

返回绑定输入摘要的 `exact-draft-revision-v1` 计划。将该 JSON 保存为计划文件，只修改 `value`；需要多个字段时增加不重叠的明确 edit。`old` 是实际旧值，不能为了通过检查而修改。`--shot` 省略时，path相对指定单元。路径采用JSON Pointer，例如 `/continuity/initial/driver/facts/位置`；键名中的 `/` 用 `~1` 表示，`~` 用 `~0`。

```json
{
  "policy": "exact-draft-revision-v1",
  "input_sha256": "inspect返回的摘要",
  "edits": [
    {"unit_id": "draft-...", "shot_id": "shot-...", "path": "/camera", "op": "replace", "old": "当前机位原文", "value": "作者决定的新机位"}
  ]
}
```

```text
python scripts/draft_revision.py apply --input draft.json --plan revision.json --output draft-next.json
```

脚本逐项核对文件版本、稳定身份、路径和旧值；全部成功才输出新稿。新请求自动建立新的 `request_id`，也可用计划的 `output_request_id` 明确指定不同身份，防止命中已提交旧回执。`add` 仅能创建不存在的对象字段，`remove` 须给旧值；数组内容可以作为一个完整权威计划精确替换，不支持按索引增删镜头。拆并镜头属于重新调度，作者须明确保留旧身份或建立新身份，再严格装配；不能删身份后自动认回原镜。

状态与声音有单源计划时，必须改权威计划。`dialogue_parts` 由 `audio_plan` 派生；动作单源模式下 `changes/final/start_state/end_state` 由动作效果编译。脚本清除受影响派生字段后重新装配，不能通过手改派生状态制造双重事实。没有动作单源协议的旧式状态修订仍从 `initial/changes` 重放末态，不替作者猜自由文本的动作或位置。

受改单元的 `staging_review` 失效；只有声音/动作输入实际变化时才移除 `audio_preflight`。相机单独变化保留未变声音判断。外置场次报告的旧摘要由既有冻结和最终门再次核对；本脚本不生成任何 `passed/reviewed` 判断。修改后使用 [装配接口](draft-assembly.md)、同轮相关差异展示和[场次审阅](scene-review-workflow.md)完成实际语义核对。

遇到版本变化、身份丢失、旧值不匹配、路径重叠、未知协议或装配矛盾时停止，不做全局字符串替换、不静默覆盖。输出文件必须不存在，保留输入文件和修订计划供追溯。
