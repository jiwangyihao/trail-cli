# CW 事件路由与 unknown 手工恢复设计规格

> 本规格固定 `cw.event.handle` 从“固定点击特殊事件”改为“先识别事件，再按类型处理”的实现边界。后续实现计划与代码必须以本规格为准；不允许继续把所有事件都当作 `银狼LV.999` 或单一特殊事件处理。

**日期：** 2026-05-05

**范围：** 货币战争局内事件页的识别、分发、unknown 手工处理 handoff、session stale 标记、`cw.event.reconcile` 恢复命令、默认文本输出、active skill 文档与契约测试。

## 背景

当前 `cw.event.handle` 的核心实现位于 `trail/scenes/cw/events.py`：`build_cw_event_handler()` 固定点击 `SPECIAL_EVENT_OPTION_POINT` 与 `SPECIAL_EVENT_CONFIRM_POINT`，并返回 `("special", "confirm")`。这会把所有“通用 / 特殊事件”压成一个动作，断绝了后续处理补给、投资、遭遇、命运卜者、`银狼LV.999` 二选一等不同事件的可能性。

`银狼LV.999` 只是事件体系中的一种具体事件。`cw.event.handle --variable-cost-choice ...` 可以继续作为 LV999 显式选择入口，但它不能定义整个事件系统。

用户确认的目标是：

1. `cw.event.handle` 必须先识别事件类型。
2. 已识别事件由对应 handler 处理。
3. 识别不到时不得乱点；必须返回 unknown，让 Agent 手工处理。
4. unknown 时先把会被事件改变、且项目能读取的 session facts 标记为 stale。
5. Agent 手工处理完成后，通过一个整合命令 `cw.event.reconcile` 恢复可确认状态；`crystals` 不可稳定读取，且通常不会被事件改变，不参与 stale / reconcile。
6. unknown 返回必须自动注入专门 skill：`trail-cw-event-unknown`。

## 术语

- **event router**：`cw.event.handle` 内部的检测 + 分发层。
- **event detection**：只读取页面文本、模板或其它非 mutation 事实，返回 `event_type`、置信度与可用选项。
- **event handler**：在检测结果足够可信时执行真实点击的处理函数。
- **unknown**：检测不足以选择 handler，或检测到的页面不是当前版本支持的事件类型。unknown 必须不点击。
- **reconcile**：Agent 手工处理 unknown 事件后，重新读取当前页面并解除可确认 stale 的命令。

## 事件类型模型

首批事件类型固定如下：

| `event_type` | 含义 | 首批动作边界 |
|---|---|---|
| `lv999_choice` | `银狼LV.999` 二星上场触发的升费 / 装备选择 | 只有显式 `variable_cost_choice` 与页面检测匹配时才点击并应用状态转移 |
| `special_confirm` | 兼容旧特殊事件确认流 | 只有检测到单一可确认特殊事件时才执行原先 `special confirm` 动作 |
| `replenish` | 补给类事件 | 首批 `cw.event.handle` 只识别并可返回 `next_action=cw.replenish.choose`；不替 Agent 固定选择 |
| `invest` | 普通投资事件，不是投资环境页 | 首批只识别并可返回 `next_action=cw.invest.choose` |
| `encounter` | 遭遇事件 | 首批只识别并可返回 `next_action=cw.encounter.choose` |
| `fortune` | 命运卜者事件 | 首批只识别并可返回 `next_action=cw.fortune.choose` |
| `unknown` | 无法可靠识别或尚未支持 | 不点击，标 stale，handoff 到 `trail-cw-event-unknown` |

首批实现允许只自动处理 `lv999_choice` 和 `special_confirm`，其它已识别类型可以返回 `handled=0` 与对应 `next_action`，让 Agent 走现有显式 choose 命令。关键是不再把它们误判成 `special_confirm`。

### 类型契约

`trail/scenes/cw/events.py` 必须定义明确类型，不能继续用裸 `dict[str, object]` 作为内部契约：

```python
from typing import Literal, TypedDict

CwEventType = Literal[
    "lv999_choice",
    "special_confirm",
    "replenish",
    "invest",
    "encounter",
    "fortune",
    "unknown",
]

class CwEventOption(TypedDict, total=False):
    id: str
    text: str
    center: tuple[int, int]

class CwEventDetection(TypedDict, total=False):
    event_type: CwEventType
    confidence: float
    options: list[CwEventOption]
    title: str
    text: str

class CwEventResult(TypedDict, total=False):
    event_type: CwEventType
    handled: bool
    handled_action: str
    next_action: str
    confidence: float
    variable_cost_choice: dict[str, object]
    stale_facts: str
    crystals_stale: bool
    reconcile_action: str
```

内部可以用 dataclass，但 daemon / renderer 边界必须输出上述字段的 JSON-able dict。`handled` 是 must-keep bool，默认文本渲染为 `0/1`。

## 检测与分发规则

### 检测输入

`trail/scenes/cw/events.py` 应新增只读检测函数：

```python
def detect_cw_event(runtime) -> CwEventDetection:
    ...
```

检测函数可使用：

- `runtime.ocr()` 或局部 OCR capture。
- 现有事件页模板 / action 按钮模板。
- 当前页面标题、正文、选项文字与按钮文字。

检测函数不得点击、拖拽、键盘输入或修改 session。

首批测试用 fake runtime 只需要支持：

- `runtime.ocr()`：返回可被现有 OCR helper 解析的文本 / box 片段。
- `runtime.locate(path)` 与 `runtime.wait_img(path, ...)`：用于 special confirm 兼容路径，可返回 `None` 或含中心点的 box。
- `runtime.click_point(x, y)`：记录点击；unknown 测试必须断言没有调用。

### 分发输入

`handle_cw_event()` 不再直接依赖 `Callable[[], tuple[str, str]]` 的“固定动作 handler”。实现应把 no-arg handler 替换为 router：

```python
CwEventRouter = Callable[[Mapping[str, object] | None], CwEventResult]

def build_cw_event_router(runtime) -> CwEventRouter:
    ...

def handle_cw_event(
    session: SessionModel,
    *,
    router: CwEventRouter,
    variable_cost_choice: Mapping[str, object] | None = None,
) -> dict[str, object]:
    ...
```

`build_cw_event_handler` 可作为临时兼容 alias，但新 daemon 路由必须使用 router 名称。`handle_cw_event()` 负责所有 session mutation：known handled 后的 stage stale、unknown stale、LV999 状态转移；router 只负责检测和 UI 点击，不直接修改 session。

建议返回数据结构：

```python
{
    "event_type": "unknown",
    "handled": False,
    "next_action": "manual",
    "confidence": 0.42,
}
```

已处理事件返回：

```python
{
    "event_type": "special_confirm",
    "handled": True,
    "handled_action": "confirm",
}
```

stale ownership 固定如下：

- `handled=1`：说明 CLI 已执行 UI mutation；`handle_cw_event()` 必须调用 `mark_cw_stage_stale()`，并在需要时标记受影响 facts stale。
- `event_type=unknown handled=0`：说明 CLI 没有点击；`handle_cw_event()` 必须调用 `mark_cw_event_unknown_stale()`。
- `event_type in {replenish, invest, encounter, fortune} handled=0`：说明 CLI 只识别页面并推荐现有 choose 命令；不得标记 stale，也不得改变 slots / shop / equipment / LV999 状态。
- `event_type=lv999_choice handled=0`：说明页面需要 Agent 明确选择；不得标记 stale，也不得应用 LV999 状态转移。

`variable_cost_choice` 只参与 `lv999_choice`：

- 页面检测不是 `lv999_choice` 时，带 `variable_cost_choice` 的请求必须失败，错误码建议 `CW_EVENT_CHOICE_MISMATCH`。
- 页面检测是 `lv999_choice` 但未提供 `variable_cost_choice` 时，不点击，返回 `handled=0 next_action=manual` 或 `next_action="cw.event.handle --variable-cost-choice"`，并由 Agent 决策。
- 页面检测是 `lv999_choice` 且提供合法 choice 时，必须同时匹配：`role_name=银狼LV.999`、当前 session 中该角色 `choice_available=True`、页面 options 中存在对应 choice id。`cost_up` 与 `equipment` 通过检测到的 option `id` 匹配：`cost_up` 文本可含 `升费` / `提升费用` / `提高费用`；`equipment` 文本可含 `装备` / `组件` / `Hacking Component` / `Hacker Mod`。匹配成功后点击该 option 的 `center`，再点击确认按钮，并调用 `apply_cw_variable_cost_choice()`。
- 如果检测到 `lv999_choice` 但目标 option 缺少 `center`，必须失败，错误码建议 `CW_EVENT_CHOICE_TARGET_MISSING`，不得应用状态转移。
- `handle_cw_event()` 必须在调用任何会点击的 router / handler 路径前完成 session 中 `role_name`、`choice_available`、choice 值与目标 option 的 LV999 前置校验；校验失败不得让 router 执行 UI mutation。

### unknown 禁止行为

当检测为 `unknown` 时：

- 不点击任何选项。
- 不点击确认按钮。
- 不调用 `apply_cw_variable_cost_choice()`。
- 不重扫 slots / shop / equipment 来假装状态 fresh。
- 必须先标 stale，再保存 session，再返回手工处理指示。
- unknown 是命令级 success：必须返回 `ok cw.event.handle event_type=unknown handled=0 next_action=manual`；不得把无法识别事件渲染成 `fail`、`request` / `recover` 或 daemon failure。只有运行时异常、参数错误或显式 choice 与检测结果冲突才属于 failure。

## unknown stale 模型

新增 helper，建议放在 `trail/scenes/cw/events.py`：

```python
def mark_cw_event_unknown_stale(session: SessionModel) -> None:
    ...
```

unknown 时必须标记 stale 的事实：

- `cw_state["stage"]`：先调用现有 `mark_cw_stage_stale(session)`，再调用 `mark_cw_stage_status_stale(session)`，确保 `stage.stale=1` 且 `stage.status.stale=1`，并清空 `session.last_stage`。
- `cw_state["slots"]`：保留已有结构，只将 `stale=True`；缺失时写入 `{"stale": True}`。
- `cw_state["shop"]`：保留已有 `opened/items/coins/reserve_full` 等字段，只将 `stale=True`；缺失时写入 `{"stale": True}`。
- `cw_state["equipment"]`：保留已有 `items/recommendations` 等字段，只将 `stale=True`；缺失时写入 `{"stale": True}`。
- `cw_state["strategy"]`：保留已有 `cards`、已加载攻略等字段，只将 `stale=True`；缺失时写入 `{"cards": [], "stale": True}`。unknown 手工点击可能让旧投资策略快照不再对应当前页面，不能继续让 `cw.strategy.select` 消费 fresh-looking 缓存。
- `cw_state["sell_plan"]`：保留已有结构，只将 `stale=True`；缺失时写入 `{"stale": True}`。
- `cw_state["variable_cost_roles_stale"]`：新增布尔标记，unknown 时设为 `True`。

`variable_cost_roles_stale=True` 的含义：缓存的 `cw_state["variable_cost_roles"]` 仍可保留供诊断和后续恢复参考，但不得被 `bind_cw_variable_cost_shop_item()`、slots normalization 或任何 LV999 cost/star 推断逻辑当作 fresh 事实使用。清除该 flag 的条件只有：

- 缺失 `variable_cost_roles_stale` 时按 `False` 处理，以兼容旧 session；新增 / 更新 `CwSceneState` 默认值时应显式包含 `variable_cost_roles_stale=False`。
- 成功处理已识别的 `lv999_choice`，且处理前该 flag 已经是 `False`；或 handler 在本次页面上有可靠 fresh 直接观察到当前 `银狼LV.999` cost/star，并把该观察值用于 `apply_cw_variable_cost_choice()`。
- 或 `cw.event.reconcile` 通过 fresh slots / shop 直接可靠观察到所有可见 `银狼LV.999` 的 cost/star，且没有 LV999 低置信、费用未知或 stale reader warning。
- 如果 flag 原本为 `True` 且当前 fresh 画面没有可见 `银狼LV.999`，不得仅凭“未看见”清除该 flag；除非 session 中本来没有 `variable_cost_roles` 记录。
- 如果 `variable_cost_roles_stale=True` 且没有 fresh 直接 cost/star 观察，任何依赖缓存 cost/star 的 `lv999_choice` 状态转移都必须失败，错误码建议 `CW_EVENT_LV999_STATE_STALE`；不得点击、不得调用 `apply_cw_variable_cost_choice()`，并提示先运行 `cw.event.reconcile`。

不得标记或刷新：

- `crystals` / `metrics`。晶矿不可稳定读取，也一般不会被事件改变。

unknown stale 不得清空 `session.last_result` 或 `session.last_screenshot`；它们是命令历史字段，由 command service 在新响应完成后自然更新。

## `cw.event.handle` 输出协议

`cw.event.handle` 归入检测 / 状态摘要 renderer family。

success 首行字段顺序固定为：

```text
ok cw.event.handle event_type=<type> handled=<0|1> handled_action=<action> next_action=<action> variable_cost_choice=<choice>
```

字段省略规则：

- `event_type` 必须保留。
- `handled` 必须保留，unknown 必须输出 `handled=0`。
- `handled_action` 只在已执行动作时输出。
- `next_action` 只在未处理且有推荐下一步时输出。
- `variable_cost_choice` 只在 payload 中提供并成功消费时输出。

unknown success 示例：

```text
ok cw.event.handle event_type=unknown handled=0 next_action=manual
shot path=.trail/shots/req-cw-event-handle.png
info read_image_first=1
info stale=1 stale_facts=stage/status|slots|shop|equipment|strategy|sell_plan|variable_cost_roles crystals_stale=0 reconcile_action=cw.event.reconcile
info handoff_skill=trail-cw-event-unknown handoff_strength=strong handoff_reason=event_unknown_manual_required
```

要求：

- 带截图 success 仍必须 `shot path=...` 后紧跟 `info read_image_first=1`。
- unknown success 必须返回本次事件页截图；Agent 手工处理依赖该截图，因此不得只返回文本 handoff。
- unknown 的 handoff line 必须是 success 输出最后一行。
- 不新增正文前缀；使用既有 `info` 行。
- 不加入 YAML allowlist。
- `cw.event.handle` 是 mutation 命令；`trail --format yaml cw event handle ...` 必须在 CLI 入口 preflight 返回 `OUTPUT_FORMAT_NOT_SUPPORTED`，不得为了渲染 YAML 不支持而先执行 daemon mutation。
- `_render_cw_event_result()` 必须手动构造 success 行：首行 -> `_append_success_capture_block()` -> unknown / next_action 相关 `info` 实体行 -> `_append_warnings()` -> `_append_references()`。不得通过 `_render_success_summary()` 提前追加 `warn` / `ref` 后再补 unknown `info`，也不得在 renderer 内直接追加 workflow handoff；handoff 只能由 `_finalize_success_lines()` 追加，保证它始终是最后一行。

## workflow handoff 注册

`skills/registry/workflow-handoffs.yaml` 应新增 `cw.event.handle` 的 conditional handoff。当前 `_select_workflow_handoff()` 只支持 `status` / `stage`，必须扩展它支持按 `data.event_type` 匹配，例如：

```yaml
commands:
  cw.event.handle:
    event_types:
      unknown:
        handoff_skill: trail-cw-event-unknown
        handoff_strength: strong
        handoff_reason: event_unknown_manual_required
```

不得只在 `_render_cw_event_result()` 内硬编码该 handoff。workflow handoff 的单一配置入口必须仍是 registry，并复用 `_finalize_success_lines()` 保证 handoff 位于 success 输出最后一行。

## `cw.event.reconcile` 命令

### 命令入口

新增 CLI：

```text
trail cw event reconcile --session <id>
```

canonical command：

```text
cw.event.reconcile
```

该命令不加入 YAML allowlist。

### daemon 行为

新增 daemon method `cw.event.reconcile`，由 `CwService` 路由。它会读取当前页面并写回 session，因此必须走会保存 session 的 mutation / session update 路径。

执行顺序：

1. 运行当前 stage detector 的安全包装。
   - 如果 detector 返回已知 stage：写入 `cw_state.stage.value`，设置 `stage.stale=False`，更新 `session.last_stage`。
   - 如果 detector 返回 unknown / `None`：调用 `mark_cw_stage_stale(session)` 与 `mark_cw_stage_status_stale(session)`，清空 `session.last_stage`，返回 `stage=unknown stale=1 next_action=manual`；不得继续收集 slots / shop / equipment。
2. stage detector 只确认阶段，不确认 `stage.status`（等级、经验、队伍人数等）。除非后续 slots/status reader 实际刷新了这些字段，否则必须保留 `stage.status.stale=True`，且 `reconciled=` 只能写 `stage`，不能写 `stage/status`。
3. 如果当前阶段是 `preparation`，调用一个无 crystals 的 preparation collect helper，建议顺序为 `("slots", "shop", "equipment")`。该 helper 不得调用 `collect_cw_crystals()`，也不得在 response data 中输出 `crystals`。
4. 如果当前阶段是 `shop`，先扫描当前已打开的 shop 页面，不得再执行 open-shop。扫描成功后可以明确执行 close-shop 回到普通备战，再收集 slots / equipment；如果 close 或后续读取失败，则只把已确认 fresh 的 shop 放入 `reconciled=`，其它 facts 保持 stale 并输出 `todo=`。
   - 如果 reconcile 扫描 shop 后关闭商店，最终持久化的 `cw_state["shop"]` 不得仍是 `opened=True`。可以把扫描快照持久化为 `opened=False, stale=False`；如果复用 close 语义导致 `stale=True`，则必须在 `stale_facts=` 中保留 `shop`，并不得在 `reconciled=` 中输出 `shop`。
5. 如果 slots / shop 读取成功且未出现 LV999 低置信或费用未知，按 `variable_cost_roles_stale` 规则决定是否清除该 flag；不能仅因为 reader “尝试过”就清除。
6. 如果当前阶段不是 `preparation` / `shop`，只解除已知 stage；保留 slots / shop / equipment / strategy / sell_plan / `variable_cost_roles_stale`，并输出适合该 stage 的 `next_action`，例如 battle flow 使用 `cw.battle.run`，仍无法识别则使用 `manual`。

`_collect_cw_preparation_facts()` 当前即使 order 不包含 `crystals`，也会把 session `metrics` 复制到 response 的 `crystals` key。实现 `cw.event.reconcile` 时必须修正这一点；可以：

- 为 helper 增加 `include_crystals=False`，在该模式下既不调用 `collect_cw_crystals()`，也不写 response `crystals` key；或
- 拆出一个不会调用 `collect_cw_crystals()`、也不会写 `crystals` key 的 helper。

测试必须断言 reconcile 前后 `cw_state["metrics"]` 不变。

不得为了 reconcile 新增随机点击、刷新商店或默认购买行为。

如果 `cw.event.reconcile` 走 `CW_MUTATING_METHODS`，必须同步更新 `_has_fresh_auto_collect_equipment()` 或等价收尾路径，确保当本次 reconcile 返回 `equipment.stale=0` 时不会被通用 mutation 收尾逻辑重新标记为 stale。

### 输出协议

`cw.event.reconcile` 归入检测 / 状态摘要 renderer family。

success 首行字段顺序固定为：

```text
ok cw.event.reconcile stage=<stage|unknown> stale=<0|1> reconciled=<facts|none> stale_facts=<facts|none> next_action=<action>
```

字段要求：

- `stage` 必须在检测到时输出。
- `stage` 必须总是输出；检测失败时使用字面值 `unknown`。
- `stale` 必须输出，表示 reconcile 后是否仍有关键事实 stale。
- `reconciled` 使用 `|` 压缩，例如 `stage|slots|shop|equipment|variable_cost_roles`；没有 fresh 事实时输出 `none`。
- `reconciled` 只包含本命令确认 fresh 的 facts，不能包含仅 attempted 的 reader。
- `stale_facts` 必须输出；没有剩余 stale facts 时输出 `none`。当 `stale=1` 时，它必须完整列出所有仍 stale 的关键事实，范围至少覆盖 unknown stale 模型中的 `stage/status|slots|shop|equipment|strategy|sell_plan|variable_cost_roles`。
- 可选 `attempted` 字段列出尝试过的 reader，例如 `attempted=slots|shop|equipment`。
- `next_action` 只在仍需 Agent 后续动作时输出。

若仍有未解除事实，正文用既有 `info` 行输出：

```text
info stale=1 stale_facts=stage/status|strategy|sell_plan todo=stage/status|strategy|sell_plan why=stale_after_reconcile
```

如果 `sell_plan` 因 unknown 被标记 stale，`cw.event.reconcile` 不直接重算出售计划，也不得把 `sell_plan` 放入 `reconciled=`。若复用 slots / shop / equipment reader 导致旧 `cw_state["sell_plan"]` 被清除，reconcile 必须恢复原 sell_plan 并保持 `stale=True`；只有 `cw.hand.sell_plan` 可用 fresh facts 替换它。只要 sell_plan 仍不可用，首行 `stale_facts=` 与正文 `todo=` 都必须包含 `sell_plan`，提示后续通过 `cw.hand.sell_plan` 重算。

如果 `strategy` 因 unknown 被标记 stale，`cw.event.reconcile` 不直接刷新投资策略卡片；除非当前阶段明确是 strategy 页并运行了对应 strategy reader，否则必须保持 `strategy.stale=True`，并在 `stale_facts=` / `todo=` 中包含 `strategy`。

`cw.event.reconcile` renderer 输出顺序固定为：首行 -> `shot` -> `info read_image_first=1` -> section / entity / `todo` 行 -> `warn` -> `ref` -> optional workflow handoff。若某字段值是 `0` 或 `False`，必须按默认协议输出为 `0`，不得因 truthiness 被省略；尤其 `handled=0`、`stale=0` 这类决策字段必须按 key presence 渲染。

如果 reconcile 回到普通备战且自动收集了 facts，正文可复用 `cw.portal.select` / `cw.battle.run(status=completed, stage=preparation)` 的分组输出：`# 综合信息`、`# 角色信息`、`# 羁绊信息`、`# 装备信息`、`# 装备优先级`、`# 角色装备需求`、`# 商店信息`。标题仍不承载 must-keep facts。

`cw.event.reconcile` 不加入 `YAML_ALLOWLIST`；`trail --format yaml cw event reconcile --session <id>` 必须返回 `OUTPUT_FORMAT_NOT_SUPPORTED`。作为新增 mutation CLI，命令入口应优先做 YAML preflight，避免为了返回“不支持 YAML”而执行 daemon mutation。

## skill 同步

已新增并注册 `skills/trail-cw-event-unknown/`。实现本规格时必须继续保持以下文档同步：

- `AGENTS.md`：说明 `cw.event.handle` unknown 自动注入 `trail-cw-event-unknown`，以及 unknown stale / reconcile 规则。
- `skills/trail-cw-event-unknown/SKILL.md`：与最终输出字段保持一致，尤其是 `handled=0`、`next_action=manual`、`cw.event.reconcile`。
- `skills/trail-cw-event-unknown/references/manual-resolution-guide.md`。
- `skills/trail-cw-event-unknown/evals/triggers.json`。
- `skills/trail-cw-prep/SKILL.md` 与 `skills/trail-cw-prep/references/*`：普通 prep 遇到 event unknown 时必须停止自治并交给专用 skill。
- `skills/registry/scene-entries.yaml`：`trail-cw-event-unknown` 保持 active internal，不得成为 public scene entry。

不得引用或恢复 archive skill，例如 `trail-cw-events`。

## 测试要求

### 单元测试

`tests/test_cw_events.py` 必须新增测试覆盖：

- detector 返回 `unknown` 时不点击 runtime。
- `handle_cw_event()` unknown 标记 stage/status、slots、shop、equipment、strategy、sell_plan、`variable_cost_roles_stale`，但不修改 crystals / metrics，也不清空 `last_result` / `last_screenshot`。
- `variable_cost_choice` 只允许在 `lv999_choice` 检测结果下消费；检测不匹配时报 `CW_EVENT_CHOICE_MISMATCH`。
- `lv999_choice` 缺少目标 option center 时报 `CW_EVENT_CHOICE_TARGET_MISSING`，不得应用状态转移。
- `lv999_choice` 在 `variable_cost_roles_stale=True` 且没有 fresh 直接 cost/star 观察时失败 `CW_EVENT_LV999_STATE_STALE`，不得点击或应用状态转移。
- `bind_cw_variable_cost_shop_item()` 与 slots normalization 在 `variable_cost_roles_stale=True` 时不得从 `cw_state["variable_cost_roles"]` 回填 LV999 cost/star。
- `special_confirm` 仍能执行旧的 confirm 行为，但输出 `handled=1`。
- `replenish` / `invest` / `encounter` / `fortune` 这类 recognized-but-unhandled 结果只返回 `handled=0 next_action=...`，不得点击，也不得标记 stale。

### daemon / CLI 测试

必须更新或新增：

- `tests/test_cw_events.py`：`cw.event.handle` command service 持久化 unknown stale。
- `tests/test_cw_rpc_contracts.py`：
  - `cw event handle` known success 首行包含 `handled=1`。
  - unknown success 输出 `handled=0 next_action=manual`、`info stale=1 stale_facts=stage/status|slots|shop|equipment|strategy|sell_plan|variable_cost_roles crystals_stale=0 reconcile_action=cw.event.reconcile`、最后一行 handoff 到 `trail-cw-event-unknown`。
  - `cw event reconcile --session <id>` 调用 `cw.event.reconcile` payload `{}`。
  - `cw.event.handle` 与 `cw.event.reconcile` 都不支持 YAML，且 YAML preflight 不调用 daemon。
- `tests/test_daemon_protocol.py` 或 `tests/test_cw_events.py`：确认 `cw.event.reconcile` 经 daemon method 保存 session，并确认它不会执行 crystals collection 或改变 `cw_state["metrics"]`。
- `tests/test_cw_events.py`：确认 `cw.event.reconcile` 在 stage detector 返回 unknown / `None` 时保持 stage stale、`last_stage=None`、`next_action=manual`。
- `tests/test_cw_events.py`：确认 `cw.event.reconcile` 的 `reconciled=` 只包含 fresh facts；reader 报错时只进入 `attempted=` / `todo=`，不进入 `reconciled=`。
- `tests/test_cw_events.py`：确认 `cw.event.reconcile` 在未显式刷新 strategy / sell_plan 时保持二者 stale；若复用 reader 清除了 sell_plan，reconcile 会恢复旧 sell_plan 且 `stale=True`。

### renderer / handoff 测试

必须更新：

- `tests/test_output_rendering.py`：workflow handoff registry 支持 `event_type=unknown`，handoff line 仍为 success 最后一行。
- `tests/test_skill_registry.py` / `tests/test_skill_structure.py`：保持 `trail-cw-event-unknown` active internal、trigger fixture 与 skill 内容约束。
- `tests/test_skill_routing_contracts.py`：legacy `trail-cw` 正则允许 active `trail-cw-event-unknown`，但继续拒绝 `trail-cw-events` 等 archive skill。

### 回归命令

实现完成后至少运行：

```powershell
uv run pytest tests/test_cw_events.py tests/test_cw_rpc_contracts.py tests/test_output_rendering.py tests/test_skill_registry.py tests/test_skill_structure.py tests/test_skill_routing_contracts.py --basetemp .pytest-tmp/cw-event-router-targeted
uv run pytest --basetemp .pytest-tmp/cw-event-router-full
```

并对修改过的 Python 文件运行 LSP error 级 diagnostics。

## 非目标

- 不在本轮实现所有事件的最优策略自动选择。
- 不让 `cw.event.handle` 在 unknown 时猜选项。
- 不新增 YAML allowlist。
- 不把 crystals 纳入 unknown stale / reconcile。
- 不新增正文前缀。
- 不恢复 archive skill 或把 `trail-cw-event-unknown` 暴露为 direct-user 公共入口。

## 验收标准

实现完成必须满足：

1. `cw.event.handle` 不再固定点击所有事件。
2. `event_type=unknown` 时没有 UI 点击副作用，session 中 stage/status、slots、shop、equipment、strategy、sell_plan、`variable_cost_roles_stale` 已 stale，crystals / metrics 未被改动。
3. unknown 输出能自动引导 Agent 切到 `trail-cw-event-unknown`，并明确第一步是读截图、手工处理、再运行 `cw.event.reconcile`。
4. `cw.event.reconcile` 能在手工处理后刷新当前 stage，并在普通备战 / 商店页整合刷新 slots、shop、equipment 与 LV999 状态；无法解除的事实用 `todo=` 明确保留。
5. `cw.event.handle --variable-cost-choice` 只处理 LV999 选择事件，不影响其它事件。
6. 全量 `uv run pytest` 通过，或只剩与本规格无关且已说明的既有失败。
