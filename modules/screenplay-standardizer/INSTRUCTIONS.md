---
name: screenplay-standardizer
description: 将非标准中文剧本整理成统一影视剧本 TXT，交付可索引资产台账及分类视觉描述；区分原文事实与制作设计，编译可用于GPT-image-2的资产图片提示词，不改写剧情或调用生图。
---

# 剧本标准格式与资产台账

将原文按场景、人物、动作和台词重新排版。只做格式与必要的断句标点调整；保留剧情、动作细节、台词字词及其顺序。交付标准剧本 TXT、独立后台资产台账 JSON、从同一台账生成的分类视觉描述 TXT。用户只要求其中一项时按请求缩小交付范围。

## 标准剧本固定格式

1. 转场或地点发生变化时按顺序建立场景，新项目标题为 `场景X-日/夜-内/外-地点`。日夜、内外各取一个实际值，例如 `场景1-日-内-昏暗房间`，实际名称不含选项斜杠。有明确夜晚线索才写 `夜`；不能明显判断为夜晚时一律写 `日`。内外景和地点从原文及相邻场景判断，不凭空增加具体地点。标题仅作剧情场次索引，不作为场景图片文件名；为兼容现有标题校验，标题仍不能含 `< > : " / \ | ? *`、控制字符或以空格/句点结尾。布景资产name须通过文件名检查并与图片文件名主体一致，含禁用字符时先核实可用名称，不静默替换。读取兼容旧项目 `场景X：日/夜·内/外·地点`，不自动改名或重绑定；用户明确要求迁移时再按改稿流程处理。
2. 标题下一行固定写 `人物：角色名-形象；角色名-形象；`。形象只写原文明示的服装、造型或明显年纪，例如 `人物：卢筱筱-黑色裙子；邵恩辞-白西装；`。没有明确造型写 `角色名-基础形象`；只有年纪明确时可写 `角色名-基础形象-中年` 等。动作、伤势、表情、道具和性格不得充当形象。无人出场且无人发声的空镜写 `人物：无；`。该场出现的说话人和画外音角色也须列在人物行，使用一致的角色名。
   无名字、无台词的泛称背景人物，只有原文明确给出阵营、身份、人设或职业时，按该称谓归为 `称谓-群像`，如 `百姓-群像-基础形象；建筑工人-群像-基础形象；紫衣门弟子-群像-基础形象；`。资产阶段直接加“-地点名字”制作后缀，如“百姓-群像-基础形象-京城街道”；相同已核实地点、称谓和形象默认复用视觉资产，不按SC逐场创建。明确不同队伍且需区分时加事件限定。图片复用不代表剧情中是同一批实体人物。人物行保留原条目，通过source_entry映射，不改台词标签。不得凭衣着或动作臆造阵营、职业，也不把有台词的泛称角色并入群像。
3. 非台词的背景、动作和剧情描述单独成段，每段以 `△ ` 开头。台词写 `角色名(原文明确的简短情绪/微动作/内心OS/画外音)：台词`；没有修饰时写 `角色名：台词`。长动作仍写成 `△ ` 段，括号不写空值或类别占位词。
4. 保留原有标点。原文靠换行断句时，才按语义补逗号、句号、问号或叹号；特别核对无标点的问句。不得改写台词字词、合并不同人物的台词，或省略动作细节。只把**独立的角色名、独立的 `OS`、独立的 `画外音`**当作纯提示；`握紧拳头os`、`低声交头接耳` 等含有叙事内容的行必须保留内容。无法直接确定说话人的台词才回看上下文，不把“看向某人”的对象当作说话人。

## 独立资产台账

完成并核对标准剧本后，按 [资产台账结构](references/asset-ledger.md) 建立 UTF-8 JSON。资产包括逐场记录scenes、物理地点locations、可复用布景scene_assets、人物和主要道具；后台稳定 ID 与剧本中的可变场景序号分开。已有台账时沿用 ID，不因改名、插场或排序而重编号，不复用已废弃的 ID。用关联 ID 记录人物、地点、道具在各场的出现关系。

- **原文是事实依据**；标准剧本只用于定位场景和确定人物名称。每条视觉、性格及其他资产事实都带原文段落编号与原文证据。visual_facts/personality_facts只记录原文明示的内容；不从行为、台词语气或格式默认值推断稳定属性，未知事实留空。新制作设计独立存visual_design，注明“制作设计”、设计依据和原文约束审阅；允许结合剧本时代、身份、关系和行动需求补足可拍外观，不伪装原文事实，不新增剧情、能力或线索。旧production_design数组保持兼容。标准剧本中的 `基础形象`、未有夜晚线索时默认的 `日`，都不是资产事实。
- 每场记录 `time_basis`：原文明确写出日夜时填 `原文明示` 与对应原文段落；按格式规则默认写 `日` 时填 `格式默认`，不附原文证据。此字段只说明标题日夜字样的依据，不添加新视觉事实。
- 人物name是唯一制作名称；source_entry逐字对应人物行完整条目，未填时兼容以name匹配。源条目不因制作命名调整而改正文；同名源条目可在不同场映射到不同世次/形象CHAR。原说话人不变，必要时以source_speaker登记人物行中的准确声源名。身份、世次/当前身份与视觉版本分开，参见[形象启用规则](references/asset-ledger.md#命名预检与形象启用)。
- 剧情场次scenes的 `heading` 与标准剧本场景标题逐字一致；场景视觉资产独立存入scene_assets，以SET ID及name关联节点和图片，不能逐场新建布景；地点主名称取其关联场景标题中的地点字样，其他确认为同一地点的标题字样放入别名。主要道具source_name取正文原词，name作为制作名称可含必要版本限定；别称放aliases。同原词且没有明确升级/变形/外形变化的道具默认复用一个视觉资产，不因地点或持有人改变拆图。数量、实体身份与持有关系记录在prop_occurrences，不因共用图片合并叙事实体。`render` 在生成 TXT 前检查这些名称及逐场关联。
- 新台账采用asset_policy="visible-reuse-v1"。主要道具同时满足至少2次独立可见使用及确需保持外观一致；每次登记scene_id、event_id、连续原文evidence/source_paragraphs，并说明consistency_reason。对白提及、同一动作中的多个词、一次事件的不同切镜不累计次数。桌床等固定家具归SET；普通锅碗瓢盆、日用品、临时物件排除；日用品成为特殊信物等时说明special_features，但仍须2次独立可见使用。主要道具可为空，不设数量下限或每集硬上限。程序验证声明、证据和独立定位，使用是否实际可见、是否属于特殊物件仍由作者审阅。旧无policy台账不自动筛除或删除资产。
- 人物记录原文明示的外貌、服装、造型、明显年龄及**性格特点**。瞬时情绪、动作和伤势不自动成为性格或固定外观。人物自述或他人评价可记录，但注明说话人，不当作旁白确认的客观事实。
- 地点记录原文明示且各布景版本共用的空间布局、固定陈设、材质和其他可见特征；道具记录原文明示的外形、颜色、材质、相对大小、标记及状态变化。场景中的天气、光线、临时布置等放在逐场事实，不擅自变成地点的永久属性。服装优先作为人物外观，不因出现服装名就自动建道具。decor与temporal分类描述继承同LOC及基础SET中的共用固定元素；temporal的parent只记录版本承接，不把上一版婚堂、废墟等非共性状态自动继承。原文明确被移除或改变的元素不留在共用字段，须放入实际适用的布景版本并人工核对。脚本不从中文描述推断删除或覆盖。
- 同一地点已在较早时间层出场，剧本明示较大的时间跨度（如“3年后”“十年后”）且未明示仍是原样时，保持LOC身份，新增`variant=temporal`的SET，制作名为“地点-时间跨度”，如“陈家老宅-十年后”。较大跨度由原文及相邻上下文审阅确认，不设统一年数门槛，不把对白中的“十年前”误认成叙事跳时。按[时间层元数据](references/asset-ledger.md#时间层与原样复用)登记来源与每场实际所属层，再运行build-scenes；新地点首次出场只建base。同层同地复用；明示原样时明确复用原始版还是上一次版本。跳时本身不能证明破败、装修或陈设变化，未知视觉事实留空。旧映射/绑定与新层不一致时须审核迁移，不能静默重绑。
- 美术设计按[资产图片设计规范](../../references/asset-image-design.md)及[JSON设计结构](references/asset-ledger.md#制作设计与图片提示词)执行。生成者审阅事实、写人物/SET/PROP完整设计JSON，复用项目原样保存的单行style-file，运行design确定性编译image_prompt和image_format，不调用生图模型。人物固定16:9白底：最左1/3超大正面面部特写占满宽度并贯穿画面高度、平视前方；右2/3正面/90度侧面/背面完整全身等高并排。相同结构化身份与世次共享identity_anchor_id，换装保留身份，已有真实变化按明确版本差异覆盖；不同身体/世次分别设计，不靠名字猜测。
- 资产TXT必须由同一份JSON渲染。仅三个分类标题，各类各自从01起按台账顺序编号，如 `01 - 陈默-第一世-基础形象：完整image_prompt`；序号只用于查看统计，不进入name、ID、图片文件名或节点名。design后的条目主行直接显示完整image_prompt，下一行人物写 `出现的场景：场景1，场景3。`，SET及PROP写 `使用的场次：场景1，场景3。`，按关联ID反查场景号并排序去重。旧未design台账继续显示原文特征和旧制作设计，不自动补外观；新制作不能仅以“外观未明示”作为生图交付。出处、原文事实和独立制作设计完整留JSON。

## 可复用流程

1. 对 TXT 或 DOCX 使用 [scripts/screenplay_io.py](scripts/screenplay_io.py) 的 `prepare` 命令，一次提取原文并自动生成内部段落编号。编号只用于追踪，不写进成稿。若输入已在对话中，先保存为任务工作目录的 UTF-8 TXT。
2. 在任务工作目录的 `scenes/` 下逐场保存 UTF-8 中间稿，文件名为 `scene_001_0001-0049.txt`：前一组数字是场景号，后两组是该场覆盖的原文段落编号，端点均包含在内。场景切分可依据明确转场及实际地点变化调整；各场编号范围必须连续、无重叠。每场处理完即保存，出错时只重做该场。
3. 对逐场稿运行 [scripts/screenplay_review.py](scripts/screenplay_review.py)，按 [逐场复核与确认格式](references/review.md) 处理报告。每段原文须对应成稿行，或明确标记为纯格式提示；复核引号及无引号台词的文字、人物行和说话人。漏文、额外文字和文字差异须修改逐场稿；上下文仍无法唯一判断说话人时，记录带原文片段、上下文、成稿台词的人工确认。报告必须为 `passed`。改稿重跑时可复用未变化场景的检查结果，变化或确认失效的场景重新检查。
4. 用同一脚本的 `assemble --review-report` 合并。命令会核对报告与当前原文、逐场稿的摘要和未处理项；文件改动后旧报告不能用于合并。再人工复核补标点、人物行及语义；发现问题时修正对应中间稿，重新运行复核和合并。
5. 从已核对的剧本和source.json建立台账。统一人物、地点、道具身份与别名，登记世次/形象启用、原文事实及逐场关联；对主要道具按新双门槛审阅并记录证据。核实LOC、时间层与原样复用，运行build-scenes；对新未绑定台账normalize完成命名及复用，保留redirects和合并档案。再按美术规范写完整设计JSON，使用项目已保存的style-file运行design，输出新台账和分类TXT；验证名称、事实、设计摘要、图片提示词及版式一致。脚本不设计外观、不调用模型，原文语义、设计是否尊重事实及审美质量由生成者核对。
6. 改稿时保留旧 `source.json`、旧 JSON 台账和新稿工作目录，运行 [scripts/asset_migrate.py](scripts/asset_migrate.py)。原文完全一致且唯一对应的场景自动沿用 ID；改写、拆场、并场或删场须填写新旧场景对应，拆并时指定哪个新场沿用哪个旧 `SC` ID。迁移只自动保留可唯一定位的证据；人物改名显式映射，暂时未出现的人物留在 `inactive_assets` 待核，不自动废弃 ID。地点标题改名不自动作为旧地点别名。逐项处理 `pending_reviews` 和 `inactive_assets` 后才生成资产 TXT；不得静默丢失事实或重用废弃 ID。详见 [改稿迁移](references/asset-ledger.md#改稿迁移)。

```text
python scripts/screenplay_io.py prepare <input.txt|input.docx> --workdir <task-work-dir>
python scripts/screenplay_review.py <task-work-dir> --report <review-report.json> [--decisions <review-decisions.json>] [--previous-report <previous-review.json>]
python scripts/screenplay_io.py assemble <task-work-dir> --review-report <review-report.json> --output <final.txt>
python scripts/asset_ledger.py init <standard-script.txt> <task-work-dir> --output <asset-ledger.json>
python scripts/asset_ledger.py build-scenes <identity-and-time-reviewed-ledger.json> --source <task-work-dir>/source.json --output <sets-draft.json>
python scripts/asset_ledger.py normalize <unbound-ledger.json> --output <normalized-ledger.json> --report <name-audit.json>
python scripts/asset_ledger.py design <standard-script.txt> <task-work-dir>/source.json <normalized-ledger.json> <asset-design.json> --style-file <project>/video-style.txt --output <designed-ledger.json> --catalog-output <asset-catalog.txt>
python scripts/asset_ledger.py render <standard-script.txt> <task-work-dir>/source.json <asset-ledger.json> --output <asset-catalog.txt>
python scripts/asset_migrate.py <old-ledger.json> <old-source.json> <new-script.txt> <new-workdir> --output <migrated-draft.json> --report <migration-report.json> [--mapping <mapping.json>]
```

按用户指定位置交付；未指定时交付三份通过检查的文件并给出链接。逐场中间稿与提取缓存留在工作目录，不作为成稿交付。

## 执行效率

- 一次提取source，一场一次标准化与事实登记；保留说话人决策、来源摘要及已审场缓存。改稿用--previous-report，仅重查变化场。
- 资产显示名、图片名调整只修改台账和映射，不更改逐场正文/人物行，不失效原剧情复核；重新跑台账校验和引用预检即可。事实或形象适用场改变时只复核受影响场；正文变化仍走原改稿复核。
- 资产交付前必须完成全量命名和形象检查；进入分镜后只检查当前范围的关联，不再次全篇盘点或反复询问已决事项。规则可确定的后缀与复用自动处理，只有真实身份/视觉变化歧义才问用户。
- 程序检查名称、ID、时码和关联；重生/换装语义、情绪语速及制作推定仍由生成者审阅，不把启用依据字符串当作语义已经证明。
