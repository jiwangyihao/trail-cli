# Trail CLI 输出协议重构设计

## 背景

`trail-cli` 当前默认把所有命令结果打印为同一套 JSON envelope，固定顶层字段为：`ok`、`data`、`screenshot`、`timing`、`warnings`、`references`、`debug`、`error`。

这套设计在项目早期有两个明显优点：

1. transport、daemon、命令层共用同一结果形状，测试容易写。
2. 任意命令都能“完整返回”，不用单独设计输出协议。

但随着命令面增多，这套输出已经出现了明确问题：

1. 对 Agent 不够友好。Agent 需要先穿过大量顶层容器，才能找到真正影响下一步动作的事实。
2. token 浪费明显。成功路径也总要携带整套字段名和空容器，和 `rtk` 的“按命令压缩呈现”相比，冗余很高。
3. 高信息密度命令表达不自然。`ocr.read`、`guide.list.cw`、`cw.shop.status` 这类命令的真实重点是“识别到了什么、分页还有没有、商店里有哪些关键项”，而不是 `data.result/items/list` 这种中间容器。
4. 默认输出与调试输出没有足够清晰的分层。对最终用户设备上的 Agent 来说，默认输出应当是行动信号；详细 trace 更适合开发/排障。

用户已经明确选择以下方向：

1. 可以破坏现有 JSON envelope 兼容性。
2. 最终 CLI 输出改为文本优先，不再默认输出 JSON。
3. 基本语法需要统一，但每类命令的 body 必须按真实信息结构定制，不能所有命令都走同一种 body。
4. 截图和状态都必须保留。
5. 高信息密度命令也应优先做 `rtk` 风格紧凑文本，只有文本会明显失真时才回退到 YAML。
6. 默认输出尽量减少信息损失；如果结果体量过大，应优先通过分页、筛选、搜索控制，而不是在 renderer 里偷偷裁剪。
7. `--verbose` 是开发调试层，不假定最终用户设备上的 Agent 会依赖它，也不要求它为了 token 专门压缩。

## 目标

1. 将 CLI 默认输出从固定 JSON envelope 改为 Agent 友好的紧凑文本协议。
2. 所有命令共享统一首行语法，让 Agent 可以快速扫读命令结果。
3. 针对不同命令族设计专门的 body 模板，优先表达“下一步决策真正需要的事实”。
4. 在默认文本协议中稳定保留截图路径、错误码、关键状态、分页与定位信息。
5. 提供 `--format yaml` 作为近乎无损的结构化兜底输出。
6. 提供统一的 verbose/debug helper，把详细 trace 收敛到开发调试层。
7. 在不大改 daemon transport 的前提下完成迁移，优先把压缩放在 CLI 输出边界。

## 非目标

1. 本次设计不重写 `guide`、`cw`、`ocr` 等业务语义本身。
2. 本次设计不把默认输出改成自然语言长句解释器；默认输出仍然是稳定、短行、可预测的协议文本。
3. 本次设计不把 `--verbose` 也做成强压缩协议；它首先是开发和排障能力。
4. 本次设计不要求 daemon RPC 响应立刻脱离当前结构化对象；第一阶段允许继续在内部传递结构化结果，再由 CLI 渲染。
5. 本次设计不通过“渲染层截断”来解决列表过大问题；优先补查询参数。

## 设计原则

1. 文本优先。默认输出必须优先是短小、稳定、可扫读的文本，而不是结构化序列化产物。
2. 统一语法。所有命令都必须先输出统一首行：`<ok|fail> <command> <核心事实...>`。
3. 专门 body。首行统一，body 不统一；body 必须服从命令实际信息结构。
4. 低信息损失。只能去掉样板和低价值容器，不能静默丢掉会影响 Agent 决策的事实。
5. 显式分层。默认输出、YAML 兜底、verbose 调试必须是三个清晰层级。
6. 信息顺序稳定。同一种命令的核心字段顺序应固定，避免 Agent 每次重新学习布局。
7. 统一键值。除首行状态词与行前缀外，所有机器需要消费的事实一律使用 `key=value` 表达，不再混用位置参数。
8. 名称冻结。命令名、字段名、前缀词必须在 spec 中冻结成唯一词表，禁止不同 renderer 为同一语义起不同名字。
9. 查询优先于裁剪。如果结果可能过大，优先分页、筛选、搜索，而不是返回全量后再截断显示。
10. 只省略“语义缺失”，不省略 `0/false` 这类会影响决策的值。

## 输出层级

### 1. 默认模式

- 默认模式输出紧凑文本。
- 这是 Agent 常规消费层。
- 每条输出由“统一首行 + 可选正文行”组成。
- 默认模式不输出调试 trace。

### 2. `--format yaml`

- 用于默认文本难以无损表达的复杂结果。
- 这是一种结构化兜底，而不是默认主通道。
- 即便进入 YAML 模式，仍保留统一首行摘要。
- YAML 仅输出当前命令的结构化内容，不附带旧 envelope 风格的样板字段集合。

### 3. `--verbose`

- `--verbose` 是开发/排障层，不是常规 Agent 层。
- 它用于输出执行过程中的详细 trace、中间判断、内部调试上下文。
- 这部分不以 token 节省为首要目标。
- 需要新增统一 verbose/debug helper，让命令实现以相同方式记录和渲染调试信息。
- 该 helper 的使用约定写入项目级 `AGENTS.md`。

## 输出模式矩阵

| 模式 | 首行 | 额外元信息 | 正文 | 调试信息 |
| --- | --- | --- | --- | --- |
| 默认成功 | 必须有 | 按需输出 `shot` | 命令族文本行 | 不输出 |
| 默认业务失败 | 必须有，至少含 `code` | 若有截图则输出 `shot` | `why`、`warn`、`ref` 等文本行 | 不输出 |
| 默认 control-plane / 结果未知失败 | 必须有，至少含 `code`；如有 taint 则首行附 `tainted=1` | 必须输出 `request id=<id>`；若有截图则输出 `shot` | `why`；仅当当前失败显式携带恢复链路时才输出 `recover action=daemon.request_status request=<id>` | 不输出 |
| `--format yaml` 成功 | 必须有 | `request id=<id>` / `shot` 规则与默认模式一致 | 统一首行后追加 YAML 块 | 不输出 |
| `--format yaml` 失败 | 必须有，至少含 `code` | `request id=<id>` / `shot` 规则与默认模式一致 | 统一首行后追加 YAML 块；`why` 仍需可见 | 不输出 |
| `--verbose` | 与默认模式一致 | 与默认模式一致 | 默认文本正文不变 | 追加 verbose/debug 块 |
| `--format yaml --verbose` | 与 `--format yaml` 一致 | 与 `--format yaml` 一致 | YAML 块保留 | 追加 verbose/debug 块 |

补充规则：

1. 默认模式绝不输出 YAML。
2. YAML 只在显式指定 `--format yaml` 时出现。
3. 结果未知、显式可恢复、或下一步动作依赖 `trail daemon request-status --request-id <id>` 的场景，默认模式必须显式暴露 `request id=<id>` 和 `recover`，不能只藏在 `--verbose`。仅有 `request id` 不等于当前失败一定可恢复；bootstrap/manifest/transport 这类本地 control-plane failure 可以只输出 `request id=<id>` 供排障。
4. `--verbose` 只是追加调试层，不改变默认文本协议的事实集合与顺序。

## 默认文本协议

### 首行协议

所有命令都必须输出首行，格式固定为：

`<ok|fail> <command> <核心事实...>`

示例：

- `ok cw.stage.detect stage=shop stale=0`
- `ok cw.shop.status count=5 reroll=2`
- `ok guide.list.cw count=10 more=1 next=token-2`
- `fail ocr.read code=OCR_NO_RESULT`

首行规则：

1. 只能放最高价值事实。
2. 字段顺序固定。
3. 只省略语义缺失字段；schema-fixed 的 `0/false/count` 仍然输出。
4. 标识型字段优先于描述型字段。
5. 不使用 `data=`、`error=`、`warnings=` 这类 envelope 容器名。

### 命令名规范

1. 默认文本中的 `<command>` 一律使用内部 canonical command name。
2. canonical command name 采用当前 RPC method / 本地控制命令形式，不使用 CLI path 风格的连字符变体。
3. 例如：
   - 输出 `cw.shop.buy_slot`，不输出 `cw.shop.buy-slot`
   - 输出 `daemon.request_status`，不输出 `daemon request-status`
   - 输出 `session.create`、`window.attach`、`screen.shot`

### 正式语法

第一版冻结如下行级 grammar：

1. 首行：`<ok|fail> <command> <fact>...`
2. 正文行：`<prefix> <fact>...`
3. `fact` 统一为 `key=value`
4. 除首行的 `ok|fail` 和第二个位置的 `<command>` 外，不再允许位置参数

这意味着以下写法是允许的：

- `ok ocr.read hits=3`
- `shot path=.trail/shots/req-1.png`
- `text rank=1 value=点击进入 score=0.98 box=122,88,74,20`
- `guide id=abc carry=希儿 hard=1 change_equip=0 expert=0`

以下写法禁止继续出现：

- `item 1 ...`
- `guide abc ...`
- `slot front:0 ...`
- `warn WINDOW_NOT_FOREGROUND ...`
- `ref trail/scenes/... sim=0.97`

### 行前缀协议

正文行通过稳定前缀表达事实类型。第一版固定支持以下前缀：

- `request id=<request_id>`：恢复/排障链路需要的请求标识。
- `shot path=<path>`：当前命令结果对应截图。
- `why msg=<message>`：失败或关键判断原因。
- `warn code=<code> msg=<message>`：警告。
- `ref path=<path> sim=<value>`：参考图命中。
- `text ...`：OCR 单条文本结果。
- `item ...`：列表/商店/通用条目。
- `guide ...`：攻略条目。
- `slot ...`：槽位信息。
- `opt ...`：选项信息。
- `recover action=daemon.request_status request=<request_id>`：告诉 Agent 下一步应进入恢复查询面；只在当前失败明确可恢复时出现。

允许后续按命令族增加新前缀，但必须满足：

1. 前缀词稳定。
2. 单行表达单条事实。
3. 不依赖长句解释才能理解。

### 字段名冻结表

第一版冻结以下高频字段别名，禁止同义漂移：

| 原始语义 | 默认文本字段名 |
| --- | --- |
| session_id | `session` |
| next_page_token | `next` |
| 是否还有更多页 | `more` |
| similarity | `sim` |
| confidence / score | `score` |
| bbox / rect | `box` |
| 条目顺序号 | `idx` |
| 槽位地址 | `pos` |
| 人类可读消息 | `msg` |
| 路径 | `path` |
| support_hard | `hard` |
| has_change_equip | `change_equip` |
| has_expert | `expert` |

补充约束：

1. `cw.stage.detect`、`cw.stage.wait` 等阶段类命令默认输出 `stage=<value>`，不输出 `value=<value>`。
2. `guide.list.cw` 必须使用 `id`、`carry`、`hard`、`change_equip`、`expert` 这些冻结字段名。
3. `box` 统一表示 `left,top,width,height`，单位为当前命令截图/窗口坐标系中的整数像素。

### 正文顺序规则

默认文本正文按以下顺序输出，不允许各命令族自定义顺序：

1. `request id=<request_id>`
2. `shot`
3. 领域实体行，例如 `item`、`guide`、`slot`、`text`、`opt`
4. `why`
5. `warn`
6. `ref`
7. `recover`

只要该命令存在截图，正文中的 `shot` 必须出现，并且必须位于第一条领域实体行之前。

### 值编码规则

为了兼顾紧凑和可读：

1. 默认使用 `key=value`。
2. 没有歧义的标量优先裸写，例如 `stage=shop`、`slot=2`、`sim=0.97`。
3. 含空格、引号、反斜杠、换行、等号或逗号歧义的值使用双引号，例如 `msg="OCR 无结果"`。
4. 双引号内统一使用反斜杠转义 `\"`、`\\`、`\n`。
5. 坐标与 bbox 优先压成短标量，例如 `box=122,88,74,20`。
6. 布尔值优先 `0/1`，减少 token。
7. Windows 绝对路径或包含空格的路径必须写成 `path="C:\\Users\\name\\My Folder\\a.png"` 这种显式 quoted 形式。

### 出现性规则

1. 只允许省略语义上缺失的 `null`、空对象、空列表。
2. 属于命令固定 schema 或会影响下一步动作的布尔值、计数值，即使是 `0/false` 也必须输出。
3. 分页类命令必须始终输出 `count` 与 `more`；只有 `more=1` 时才输出 `next`。
4. 空结果必须显式可见，例如：
   - `ok guide.list.cw count=0 more=0`
   - `ok ocr.read hits=0`
5. 字段缺失只能表示“不适用”或“源数据中不存在”，不能同时兼任 `0`、`false` 或“没有更多页”。

## 命令族渲染规则

### 1. `detect / mutation / transition`

目标：让 Agent 一眼看到“做了什么，结果到了哪”。

示例：

- `ok cw.enter mode=new difficulty=current battle=standard`
- `ok cw.stage.detect stage=shop stale=0`
- `ok cw.shop.buy_slot slot=2 got=希儿`
- `ok cw.invest.choose option=2 stage=battle stale=0`

规则：

1. 首行直接表达动作结果。
2. 如果命令天然导致状态迁移，迁移后的状态优先进入首行。
3. 如果有截图，按全局顺序固定输出 `shot path=...`。
4. 默认不再输出“包一层 data 再包一层 value/result”的结构。

### 2. `status / read`

目标：保留信息密度，但压成可扫读的短行事实。

示例：

- `ok cw.shop.status count=5 reroll=2`
- `item idx=1 slot=1 name=希儿 cost=2 carry=1`
- `item idx=2 slot=2 name=停云 cost=1 support=1`

- `ok cw.slots.read front=3 back=2 hand=4`
- `slot pos=front:0 name=希儿 star=4`
- `slot pos=hand:1 empty=1`

规则：

1. 不输出 `items/options/result/list` 这类容器名。
2. 每个实体一行，实体类型由前缀表达。
3. 同类实体字段顺序固定。
4. 优先保留可驱动下一步动作的字段，例如槽位、费用、是否主 C、是否空位。
5. 只要该命令有截图，就必须按全局顺序输出 `shot path=...`。

### 3. `list / guide search`

目标：把分页和筛选结果压成摘要 + 条目行，同时不依赖渲染截断。

示例：

- `ok guide.list.cw count=10 more=1 next=token-2`
- `guide id=abc idx=1 carry=希儿 hard=1 change_equip=0 expert=0`
- `guide id=def idx=2 carry=银狼 hard=0 change_equip=1 expert=1`

规则：

1. 默认输出代表“当前请求的完整结果页”，而不是被渲染层截断的视图。
2. 分页、筛选、搜索应由命令参数控制，不由 renderer 偷偷裁剪。
3. 默认只展示真正影响选择的字段，长尾字段留给 `--format yaml`。
4. `count`、`more`、`next` 这类继续翻页所需信息必须稳定保留。
5. `guide.list.cw` 默认文本的最小保留字段集合冻结为：`id`、`idx`、`carry`、`hard`、`change_equip`、`expert`。
6. 当源数据包含 `final_role_cards` 或其他高价值角色摘要时，默认文本应优先通过紧凑字段或补充实体行表达，而不是默默丢掉。

### 4. `ocr / image`

目标：让 Agent 快速知道“看到了什么、能不能点、应该点哪”。

示例：

- `ok ocr.read hits=3`
- `text rank=1 value=点击进入 score=0.98 box=122,88,74,20`
- `text rank=2 value=开始挑战 score=0.93 box=410,502,120,36`

- `ok image.locate name=start.png box=122,88,74,20`
- `ok image.wait name=confirm.png elapsed=842 box=410,502,120,36`

规则：

1. 不输出 OCR 原始数组结构。
2. 文本内容、置信度、定位框优先保留。
3. 如果命令本质是“定位到某个可点击对象”，首行直接表达定位结果。
4. 如果截图存在，按全局顺序固定输出 `shot path=...`。
5. `ocr.read` 必须冻结命中排序规则；第一版默认按源结果顺序输出，并显式输出 `rank=`。

### 5. `error / warning / reference`

目标：失败时仍短，但能立即支撑下一步恢复动作。

示例：

- `fail ocr.read code=OCR_NO_RESULT`
- `shot path=.trail/shots/req-ocr-read.png`
- `why msg="OCR 无结果"`
- `warn code=WINDOW_NOT_FOREGROUND msg="输入后窗口可能未在前台"`
- `ref path=trail/scenes/cw/references/1-1.png sim=0.97`

规则：

1. 失败首行必须保留 `code`。
2. 人类可读原因进入 `why` 行。
3. 警告与引用按各自前缀单独输出。
4. 默认模式不输出 debug 结构。
5. 结果未知、显式可恢复、或后续动作依赖查询面时，必须额外输出：
   - `request id=<request_id>`
   - `recover action=daemon.request_status request=<request_id>`

### 6. `--format yaml` allowlist

默认模式不允许输出 YAML。只有显式指定 `--format yaml` 时，以下命令允许提供 YAML 视图：

1. `state.dump` 这类天然嵌套且面向排障/检查的快照命令。
2. `daemon.status` 这类控制面快照命令。
3. `guide.config.cw` 这类动态枚举与配置命令。
4. 少数攻略详情命令，如果其默认文本版本已经定义完成且 `--format yaml` 只是显式扩展视图。
5. `--verbose --format yaml` 联动下需要完整展示调试树时。

YAML 模式规则：

1. 仍先输出统一首行。
2. 再输出最小必要 YAML 块。
3. YAML 必须从原始结构化结果生成，不能从压缩后的 `summary_facts/body_items` 反推。
4. 默认文本已经要求的字段名，在 YAML 中也应优先保持同名或有明确映射。

## 信息保留约束

“尽量减少信息损失”在本设计中是硬约束，而不是实现时的自由裁量。

### 默认模式必须保留的信息

1. 成功/失败状态。
2. 命令名。
3. 下一步动作最依赖的状态字段，例如 `stage`、`stale`、`slot`、`box`、`count`、`next`。
4. 截图路径。
5. 失败错误码。
6. 列表/搜索继续翻页所需 token。
7. OCR/定位结果中的排序、文本、置信度、bbox。
8. 需要恢复链路时的 `request id=<id>`。

### 不允许默认静默丢失的信息

1. `stale` 这类真假影响决策的状态位。
2. `next_page_token` 这类翻页控制位。
3. 错误码。
4. 截图路径。
5. 引用匹配的相似度。
6. 需要恢复链路时的 `request id=<id>` / `recover`。

### 可以从默认模式后移的信息

1. 调试 trace。
2. 次级诊断上下文。
3. 只对人工深查有意义、但不影响下一步动作的长尾字段。
4. 复杂嵌套对象的完整原样结构。

## 列表体量控制策略

本设计明确拒绝“renderer 默认截断”作为主要体量控制方案。

### 规则

1. 列表类命令优先使用分页。
2. 搜索与筛选优先在命令参数层完成。
3. renderer 输出当前请求结果页的完整视图。
4. 如果某个高密度命令未来可能爆量，应先补查询参数，再上线默认文本格式。

### 顺序与完整性

1. 列表实体默认按源 payload 顺序输出；如果业务命令已有更强语义顺序，则在该命令族 renderer 中显式冻结。
2. `guide.list.cw` 条目顺序与源 payload 顺序一致。
3. `cw.shop.status` 默认按槽位顺序输出，并显式输出 `slot=`。
4. `ocr.read` 默认按源命中顺序输出，并显式输出 `rank=`。

## 恢复链路与控制面命令

### 默认恢复协议

凡是满足以下任一条件，默认文本必须暴露恢复链路：

1. side effect 已应用但结果未知。
2. session 进入 tainted，需要 `daemon.request_status` 或 `daemon.reconcile_session` 才能继续判断。
3. payload 已显式标记当前 `request_id` 可由 `daemon.request_status` 查询。

最小输出要求：

1. 首行保留 `code`。
2. 正文必须有 `request id=<request_id>`。
3. 只有当前失败显式可恢复时，正文才输出 `recover action=daemon.request_status request=<request_id>`。
4. 如果当前返回包含 `tainted`，首行必须显式附带 `tainted=1`。

补充说明：

1. bootstrap / manifest / auth / transport 等本地 control-plane failure 默认仍保留 `request id=<id>` 供排障。
2. 这类本地失败如果当前无法证明 `daemon.request_status` 可查询，则默认不输出 `recover`，避免把 Agent 引到必然失败的查询面。

### 控制面命令 renderer

第一版必须显式定义以下控制面命令的 renderer，不允许临时拼接：

1. `daemon.install`
2. `daemon.start`
3. `daemon.status`
4. `daemon.stop`
5. `daemon.logs`
6. `daemon.request_status`
7. `daemon.reconcile_session`

其中：

1. `daemon.request_status` 默认文本必须稳定保留 `request id=<id>`、`final_state`、`last_visible_stage`、`tainted`。
2. `daemon.reconcile_session` 默认文本必须稳定保留 `session` 与 `tainted`。

## 命令覆盖与 renderer 映射

第一版必须为当前 README 中已有命令面显式归类，避免实现阶段自由发挥：

| 命令组 | renderer 家族 | 备注 |
| --- | --- | --- |
| `session.create` | session/action | 默认文本输出 `session`、窗口标识 |
| `window.attach` / `window.launch` | action | 保留窗口与截图事实 |
| `screen.shot` | capture | 默认文本 + `shot` |
| `ocr.read` / `image.locate` / `image.wait` | vision | 见 `ocr / image` 规则 |
| `input.*` | action | 动作事实 + `shot` |
| `guide.fetch.cw` / `guide.list.cw` | guide | fetch 走文本摘要；list 走分页文本 |
| `guide.config.cw` | config | 默认文本摘要；`--format yaml` allowlist |
| `state.dump` | snapshot | 默认文本摘要；`--format yaml` allowlist |
| `daemon.*` | control | 控制面 renderer，不走临时拼接 |
| `cw.*` | action / status / vision / list | 按子命令归入对应家族，但命令名保持 canonical form |

### YAML allowlist 命令的默认摘要冻结

即便这些命令支持 `--format yaml`，默认模式仍必须输出稳定文本摘要，第一版冻结如下：

1. `daemon.status`
   - 首行至少保留：`state`、`pid`、`endpoint`、`protocol`
   - 若存在启动失败信息，首行或 `why` 行必须显式保留 `last_error`
2. `state.dump`
   - 首行至少保留：`session`、`tainted`
   - 正文或首行摘要中至少还要体现：当前主场景、`last_stage`，以及存在时的关键 stale/error 摘要
3. `guide.config.cw`
   - 首行至少保留：`season`、`sub_season`、`big_version`
   - 正文摘要至少保留：`lineup_levels`、`traits`、`roles`、`role_tags` 的计数信息

这些默认摘要字段属于第一阶段 must-keep 事实，不能因为命令支持 YAML 就被省略。

### 对现有命令的影响

1. `guide.list.cw` 已有 `page/limit/next_page_token`，应直接纳入新的文本协议。
2. 其他可能膨胀的读取类命令，如果当前参数面不足，后续实现中优先补查询能力。

## 内部架构调整

### 输出边界分层

第一阶段保持 daemon/client/handler 继续生产结构化结果对象，但不再把它们原样打印给用户。

CLI 侧新增三层能力：

1. 结果适配层
   - 把现有结构化响应适配成统一的内部结果对象。
2. 渲染策略层
   - 按命令名或命令族选择对应 renderer。
3. 输出层
   - 根据默认模式、`--format yaml`、`--verbose` 决定最终打印文本。

### 第一阶段不变项

为了保证“先改输出边界，不重写 transport”，第一阶段明确冻结以下内部契约：

1. daemon RPC 响应的内部结构化 shape 继续保留。
2. `SessionService` 持久化 `last_envelope` 与 `last_result` 的行为继续保留。
3. duplicate terminal replay 继续基于内部 envelope 回放结果。
4. client 在 `--verbose` 或 control-plane 失败时把 `request_id` 注入 `debug` 的行为继续保留。
5. 现有 capture / warning / reference / debug trace 采集链路继续保留，第一阶段不另起第二套事实源。

第一阶段真正替换的是统一输出出口：用 `render_output(command, payload, format, verbose)` 取代 `print_json(...)` 的最终打印行为。

### 建议的内部结果骨架

内部骨架服务于渲染，不对用户直接暴露。字段只保留渲染必需信息：

1. `command`
2. `ok`
3. `request_id`
4. `summary_facts`
5. `body_items`
6. `raw_payload`
7. `screenshot`
8. `warnings`
9. `references`
10. `error`
11. `debug_events`

补充约束：

1. `raw_payload` 必须保留当前页完整结构化结果，供 `--format yaml` 与信息保留测试使用。
2. 默认文本只读取 `summary_facts/body_items` 的子集。
3. `--format yaml` 必须从 `raw_payload` 生成，而不是从默认文本的压缩视图反推。
4. `request_id` 不允许在适配层丢失。

实现初期允许从现有 response dict 适配出这套骨架；稳定后再逐步把命令实现迁移到更直接的内部对象。

## Verbose / Debug Helper

本设计要求新增统一 helper，而不是继续让每个命令自由拼接 debug 结构。

### 目标

1. 统一记录调试事件。
2. 统一控制默认模式与 `--verbose` 的显示边界。
3. 统一约束哪些信息可以进入默认输出，哪些只能进入 verbose。
4. 统一 `debug.request_id`、`debug.trace`、`debug.detail` 等现有调试载荷的承载方式。

### 使用原则

1. 默认模式绝不直接打印 helper 中的 trace。
2. `--verbose` 时，helper 产物可以以更接近原始调试视角的文本或 YAML 输出。
3. helper 的使用说明写入项目级 `AGENTS.md`，作为今后命令开发的约束。
4. helper 必须冻结事件 schema，至少包含：`kind`、`step`、`msg`、`context` 四类标准字段。
5. 未来新增命令在实现时必须声明：
   - 所属 renderer 家族
   - 默认模式必出事实
   - verbose 事件类型
   - 是否允许 `--format yaml`
   - 需要补充的 README 示例与测试项

## CLI 开关与兼容语义

### 开关

第一版新增或重定义：

1. 默认输出：紧凑文本。
2. `--format yaml`：结构化兜底。
3. `--verbose`：开发调试输出。

### 兼容语义

1. 本次允许破坏旧 JSON envelope 输出兼容。
2. 重点是输出协议变更，不要求同步改变业务失败时的命令退出码语义。
3. daemon transport 与命令业务的结构化处理中间态第一阶段可以继续存在，但不再默认透出给用户。

## 验证策略

### Golden Tests

输出测试从“解析 JSON 断言字段”改为“对默认文本/YAML 进行 golden 验证”。第一批必须覆盖：

1. `cw.stage.detect`
2. `cw.shop.status`
3. `guide.list.cw`
4. `ocr.read`
5. 一个失败命令
6. 一个 `--format yaml`
7. 一个 `--verbose`

### 测试迁移矩阵

第一阶段不把所有旧测试都粗暴改成 golden，而是分层迁移：

| 测试层 | 代表文件 | 第一阶段策略 |
| --- | --- | --- |
| CLI 文本渲染 | `tests/test_atomic_commands.py`、`tests/test_guide_rpc_contracts.py`、`tests/test_cw_rpc_contracts.py` | 从 `json.loads(result.stdout)` 迁到文本/golden 断言 |
| transport / daemon 协议 | `tests/test_daemon_protocol.py` 的 transport 与 server 部分 | 继续保留结构化协议断言 |
| session / journal 持久化 | `tests/test_output_envelope.py`、`tests/test_daemon_session_service.py` | 继续保留结构化结果断言 |
| CLI 版本与帮助 | `tests/test_cli.py` | 按现有轻量 stdout 断言保留 |

需要注意：像 `tests/test_daemon_protocol.py` 这类同时覆盖 CLI stdout 和 transport 协议的文件，应在实现阶段先按职责拆分，再迁移 CLI 输出断言。

### 关键信息不丢失测试

除 golden 外，还需要专门验证默认文本或 YAML 中是否保留关键事实，例如：

1. `stale` 标记。
2. `next_page_token`。
3. 截图路径。
4. 错误码。
5. 引用相似度。
6. OCR bbox 与 score。
7. control-plane / 结果未知场景下的 `request id=<id>` 与 `recover`。

### 第一批 must-keep 矩阵

第一批代表命令在计划阶段必须冻结以下最小保留字段集合，并用测试校验“源 payload -> 默认文本/YAML”未丢失这些事实：

| 命令 | 默认模式必须保留 |
| --- | --- |
| `cw.stage.detect` | `stage`、`stale`、`shot` |
| `cw.shop.status` | `count`、每个 `item` 的 `slot/name/cost`、`shot` |
| `guide.list.cw` | `count`、`more`、`next`（仅当 `more=1`）、每个 `guide` 的 `id/carry/hard/change_equip/expert` |
| `ocr.read` | `hits`、每个 `text` 的 `rank/value/score/box`、`shot` |
| 结果未知 / 可恢复失败 | `code`、`request id=<id>`、`recover`、`why`，以及 `tainted`（若存在） |

### 列表能力测试

列表类命令重点验证：

1. 分页语义正确。
2. 筛选/搜索语义正确。
3. 默认文本忠实表达当前页完整结果。

不再把“渲染是否截断”当作主要测试目标。

## 文档变更

### README

README 的“输出约定”章节需要整体改写，说明：

1. 默认输出已改为紧凑文本协议。
2. 首行统一语法。
3. 常见正文前缀含义。
4. `--format yaml` 的用途。
5. `--verbose` 的定位是开发调试。
6. `request_id` 的默认恢复链路如何从文本协议触发到 `trail daemon request-status --request-id <id>`。

### 项目级 `AGENTS.md`

仓库当前没有项目级 `AGENTS.md`。实现本设计时需要新增，但它是贡献者约束文档，不是协议真源。协议真源仍然是本 spec、README 和测试。

项目级 `AGENTS.md` 需要约束：

1. 新命令默认输出应进入哪个命令族 renderer。
2. 哪些事实必须进入默认模式。
3. 哪些信息只能进入 verbose helper。
4. YAML 兜底的适用范围。
5. 新命令必须补哪些 README 示例和测试增量。

## 迁移顺序

1. 先引入渲染层与内部结果适配层。
2. 再把最具代表性的命令族切到新文本协议。
3. 同步重写 README 输出约定和新增项目级 `AGENTS.md`。
4. 最后逐步减少实现内部对旧 envelope 形状的直接依赖。

这样做的原因是：先把“外部输出协议”改对，再逐步收紧“内部结构协议”，风险最小。

## 结论

本设计将 `trail-cli` 的默认输出从“统一 JSON envelope”改为“统一首行 + 命令族专门 body”的紧凑文本协议。它继承了 `rtk` 的核心思路：不是把所有命令塞进一种通用结果壳里，而是让输出围绕实际决策信息组织；同时通过 `--format yaml` 和 `--verbose` 保住低信息损失与开发调试能力。

对当前项目而言，这是一种更适合 Agent 消费的协议升级，而不是单纯的序列化格式替换。
