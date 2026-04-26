# 输出板块标题与 CW 综合信息拆分设计

## 背景

当前默认文本协议强调紧凑事实行：首行给出命令核心事实，正文用冻结前缀和 `key=value` 输出实体事实。这个方向本身正确，但当一个命令同时输出多类事实时，纯粹连续的紧凑行不够易扫读。例如 `cw.slots.read` 本质上同时读取了综合状态、角色槽位和羁绊摘要；`cw.shop.scan` 输出商店商品，同时投影阶段状态；`cw.portal.select` 成功后还会混合攻略提示、槽位、羁绊、商店和综合状态事实。

本设计新增标题式文本板块：用 `# 标题` 行分隔正文中的事实组。标题行不承载事实，不替代已有紧凑 `prefix key=value` 行，只用于让 Agent 和人类更快识别“接下来这组事实属于什么”。同时，`cw.slots.read` 的综合状态读取要从角色槽位读取中拆出，并额外识别当前阶段。命令只要实际读取了某类事实，就应在自己的默认输出中输出对应事实，而不是只写入 session。

## 目标

- 默认文本协议允许 `# 标题` 行作为正文板块标题。
- 多板块命令按实际正文类型插入标题；单一正文类型命令不强行加标题。
- 标题行不含业务事实；所有业务事实继续使用现有紧凑 `prefix key=value` 行。
- renderer 提供通用标题 helper，避免各命令手写不同格式。
- `cw.slots.read` 拆出综合状态 reader/helper，角色槽位 reader 只负责角色信息。
- CW 综合状态读取额外识别当前阶段，并在命令输出的 `# 综合信息` 板块展示。
- `cw.slots.read` 的羁绊摘要独立为 `# 羁绊信息`，不混在 `# 角色信息` 中。

## 非目标

- 不新增外部 CLI/RPC 命令。
- 不把默认输出改成结构化嵌套字段。
- 不把标题行当作 prefix、warn、recover 或 machine fact。
- 不要求所有命令都加标题；只有首行后存在多类正文事实的命令需要板块化。
- 不一次性重构所有 session 内部存储；内部可继续保留现有 `cw_state.stage.status` 等状态缓存。

## 输出协议

默认文本新增一种正文行：

```text
# 标题
```

标题行规则：

- 必须以 `# ` 开头，后接简短中文标题。
- 标题行只用于分组，不包含 `key=value`，不携带 must-keep 事实。
- 标题行不是 renderer family 前缀，不进入冻结 prefix 列表。
- 标题行只能出现在首行和截图引导之后、业务事实行之前或不同事实组之间。
- 如果命令有 `shot path=...`，仍必须保持 `shot path=...` 后紧跟 `info read_image_first=1`；标题行不得插入二者之间。
- failure 路径暂不加标题，保持恢复链路顺序稳定。
- handoff 行仍是 success 最后一行；标题行不得出现在 handoff 之后。

推荐板块标题：

- `# 综合信息`：当前阶段、等级、经验、队伍人数、状态 stale 等跨动作决策事实。
- `# 角色信息`：槽位、手牌、角色、星级、traits 等角色事实。
- `# 羁绊信息`：羁绊摘要、档位、已激活档位、占比等。
- `# 商店信息`：商店商品、金币、手牌满位等商店页事实。
- `# 攻略提示`：`skill_info`、攻略动态提醒等提示性事实。

## CW 综合状态读取

现状中 `build_cw_slots_reader` 同时做了：

- 页面固化点击。
- 当前等级、经验、队伍人数 OCR。
- 角色槽位点击、详情截图、角色名 OCR、星级读取。

改造后拆成两个内部 reader/helper：

- 综合状态 reader：读取当前阶段、等级、经验、队伍人数。
- 角色槽位 reader：读取前台、后台、手牌角色名、星级和详情。

综合状态 reader 应复用现有 stage 识别能力，例如 `stage.build_cw_stage_detector(runtime)` 使用 stage 模板和 settle OCR fallback。状态 OCR 仍复用 `CW_STATUS_LEVEL_REGION`、`CW_STATUS_EXP_REGION`、`CW_STATUS_TEAM_SIZE_REGION` 与 `stage.parse_cw_stage_status`。

`read_cw_slots` 继续负责把合并后的角色快照写入 `cw_state["slots"]`，并把综合状态写入 `cw_state.stage.status`。如果综合状态 reader 读到了当前阶段，也同步刷新 `cw_state.stage.value` 和 `session.last_stage`。如果只读角色、不读综合状态，则不得伪造综合信息输出。

## Renderer 设计

新增通用标题 helper，例如：

```python
def _append_section(lines: list[str], title: str) -> None:
    lines.append(f"# {title}")
```

领域 helper 必须复用 `_append_section`：

- `_append_cw_status_section(lines, data)`：追加 `# 综合信息` 和综合 info 行。
- `_append_cw_slot_section(lines, data)`：追加 `# 角色信息` 和 `slot ...` 行。
- `_append_cw_trait_section(lines, data)`：追加 `# 羁绊信息` 和 `info 羁绊=...` 行。
- `_append_cw_shop_section(lines, data)`：追加 `# 商店信息` 和商店 item/coins 行。
- `_append_skill_info_section(lines, data)`：如有攻略提示，追加 `# 攻略提示` 和 `info skill_info=...` 行。

helper 应只在对应事实存在时输出标题，避免空标题。比如没有 `trait_summary` 时，不输出 `# 羁绊信息`。

## 命令输出变化

### `cw.slots.read`

成功输出顺序：

1. 首行：`ok cw.slots.read front=... back=... hand=... stale=...`
2. `shot path=...`（如有）
3. `info read_image_first=1`（如有截图）
4. `# 综合信息`（如果读取了综合状态）
5. `info stage=... stale=...`（如果识别了当前阶段）
6. `info stage_level=... stage_exp=... stage_team_size=... stage_status_stale=...`
7. `# 角色信息`
8. `slot ...`
9. `# 羁绊信息`（如果存在羁绊摘要）
10. `info 羁绊=...`
11. `warn`、`ref`

### `cw.shop.scan|status|buy_exp`

如果命令输出商店商品和综合状态，应分成：

1. `# 商店信息`
2. `item ...`
3. `info coins=... reserve_full=...` 或买经验动作专属商店事实
4. `# 综合信息`
5. `info stage=... stale=...`（若有）
6. `info stage_level=... stage_exp=... stage_team_size=... stage_status_stale=...`

如果只输出一种事实，则不强行加标题。

### `cw.portal.select`

成功输出仍保持首行、截图和 read-image-first 顺序。之后按实际存在的事实输出：

1. `# 攻略提示`（如有 `skill_info`）
2. `# 综合信息`（如有 stage/status facts）
3. `# 角色信息`（如有 slots）
4. `# 羁绊信息`（如有 trait summary）
5. `# 商店信息`（如有 shop facts）
6. `warn`、`ref`
7. handoff 最后一行

`shop opened/stale` 仍不进入 `cw.portal.select` 默认 body。

### 其它多板块命令

`guide.fetch.cw`、`guide.list.cw`、`cw.hand.sell_plan` 等如果正文天然包含多个事实组，也接入标题 helper。具体标题按已有中文语义命名，优先使用页面/协议里已稳定的概念，不新增不必要术语。

## 文档与测试

需要同步更新：

- `AGENTS.md`：说明标题行协议、标题行顺序限制、多板块命令必须使用 helper。
- `README.md`：更新默认输出示例，展示 `# 综合信息`、`# 角色信息`、`# 羁绊信息`、`# 商店信息`。
- active skills：如果它们消费 `cw.slots.read`、`cw.shop.scan` 或 `cw.portal.select` 输出，需要说明先按标题板块扫读，再读取事实行。
- renderer 单测：锁定标题行合法性、板块顺序和空板块不输出。
- CLI/RPC/daemon 单测：验证 `cw.slots.read` 额外识别当前阶段，并把实际读取的综合状态返回到输出数据路径。

测试重点：

- `cw.slots.read` 读到 stage/status 时输出 `# 综合信息`，并在 `# 角色信息` 和 `# 羁绊信息` 前后顺序稳定。
- `cw.slots.read` 的角色 reader 不再负责 status OCR。
- 综合状态 reader 复用 stage detector，并在读到当前阶段时刷新 session stage。
- `cw.shop.scan/status/buy_exp` 和 `cw.portal.select` 复用同一综合/商店/角色/羁绊板块 helper。
- 标题行不得插在 `shot` 与 `info read_image_first=1` 之间。
- failure 输出不新增标题。

## 风险与缓解

- 风险：标题行改变默认文本协议，可能影响依赖“每个 body 行都有 prefix”的消费者。缓解：同步 AGENTS/README/tests，并明确标题行不承载事实，消费者可跳过 `# ` 行。
- 风险：一次改造过多 renderer 容易造成示例遗漏。缓解：先列出多板块命令清单，按 renderer family 分批测试。
- 风险：当前阶段识别增加 `cw.slots.read` 的耗时。缓解：复用现有 stage detector，且只在综合状态 reader 被调用时执行；必要时在计划中评估是否允许定向 slots 读取跳过综合状态。
- 风险：session 内部仍保留旧 `stage_status` 命名，和输出板块术语不完全一致。缓解：把旧字段视为内部缓存，不在用户文档中强调；默认输出以标题板块为准。
