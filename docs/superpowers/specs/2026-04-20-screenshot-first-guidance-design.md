# 截图优先提示与漏截图命令补齐设计

## 背景

`trail-cli` 当前已经在多个层面表达了“截图是第一手事实”：

1. `README.md` 已写明多模态 Agent 应把截图视为优先事实来源。
2. `skills/trail-hsr`、`skills/trail-cw`、`skills/trail-cw-events`、`skills/trail-cw-guide` 等 skill 也已经反复强调“先看 screenshot，再参考 detect/read/status 文本”。
3. 默认文本协议里，只要当前命令带截图，就会输出 `shot path=...`。

但目前仍有两个明显缺口：

1. **协议本身缺少硬提示。** Agent 能看到 `shot path=...`，但默认文本没有一条稳定、显式、可机读的提示告诉它“必须先读原始图，再消费压缩文本”。
2. **仍有一批读命令缺少当前画面截图。** 这些命令要么直接压缩当前画面判断，要么返回粗粒度读结果但仍需要当前窗口参考图辅助决策；没有同步给出截图路径时，Agent 很容易只看文本。

用户已经明确要求：

1. 既要改文档 / skill，也要增加默认协议提示。
2. 需要顺手检查当前哪些命令不会自动截图，并把本轮真正该补截图的命令纳入实现范围。
3. 本轮补截图范围收紧为 **当前游戏画面读命令**，不扩到 `start.run`、`session.create`、`state.dump`。

## 目标

1. 让所有带截图的默认文本结果都显式告诉 Agent：**先读原始截图，再信任压缩文本**。
2. 在公共 envelope 层保留同语义元数据，避免把该规则散落到各命令业务 `data` 中。
3. 为当前缺失的 `cw` 读命令补上自动截图，和现有 mutation / `cw.slots.read` 的截图契约保持一致。
4. 同步更新 README、相关 skill 与测试，让这条规则成为稳定约束，而不是隐含习惯。
5. 产出一份清晰的截图策略审计结果，区分“已有自动截图”“本轮补截图”“保持无截图”。

## 非目标

1. 本轮不改失败路径顺序。
2. 本轮不新增正文前缀，继续复用 `info`。
3. 本轮不新增 YAML allowlist。
4. 本轮不把 `guide.fetch.cw`、`guide.config.cw`、`guide.list.cw`、`daemon.*`、`cw.guide.current` 等纯控制面 / 纯 artifact 数据命令强行改成自动截图。
5. 本轮不尝试让“所有命令都输出截图提示 0/1”；没有截图就不伪造提示。

## 现状审计

### 已有自动截图的主要命令家族

1. `ocr.read`
2. `screen.shot`
3. `image.locate`、`image.wait`
4. `window.attach`
5. `input.click`、`input.drag`、`input.key`
6. `cw.slots.read`
7. `cw` mutation 家族：`cw.enter`、`cw.start`、`cw.portal.*`、`cw.guide.apply`、`cw.slots.swap`、`cw.slots.place_one`、`cw.shop.open`、`cw.shop.buy_slot`、`cw.shop.refresh`、`cw.shop.close`、`cw.crystals.collect`、`cw.hand.sell_one`、`cw.hand.sell_plan`、`cw.replenish.choose`、`cw.invest.choose`、`cw.encounter.choose`、`cw.fortune.choose`、`cw.boss_preview.confirm`、`cw.battle.start`、`cw.battle.continue`、`cw.settle.next`、`cw.event.handle`

### 当前应补自动截图的命令

这些命令当前还没有稳定带图，但都应在返回结果时伴随当前窗口截图：

1. `cw.stage.detect`
2. `cw.stage.wait`
3. `cw.shop.scan`
4. `cw.replenish.read`
5. `cw.invest.read`
6. `cw.encounter.read`
7. `cw.fortune.read`

### 当前保持无截图的命令

1. `daemon.*` 控制面命令
2. `guide.fetch.cw`、`guide.config.cw`、`guide.list.cw`
3. `cw.guide.current`
4. `cw.shop.status`（当前是 session / artifact 汇总命令，不是当前屏幕读命令）
5. 其他本质上不依赖当前屏幕判读的纯控制面 / 纯 artifact / 纯远端数据命令

## 设计原则

1. **截图优先规则必须下沉到协议。** 不能只靠 README 或 skill 提醒。
2. **公共层统一生成。** 不在每个命令里手写一条提示，也不把该语义塞进各业务 `data`。
3. **只在有截图时提示。** 没截图就不输出“先读图”提示，避免制造伪信号。
4. **先看画面，再看摘要。** 新提示只表达一件事，不混入别的解释文案。
5. **截图补漏按命令家族策略管理。** 不做零散特判，避免后续再漏。

## 协议设计

### 公共 envelope 元数据

在公共返回 envelope 中新增顶层元数据：

```python
"image_guidance": {
    "read_image_first": True,
}
```

约束如下：

1. 只要当前 envelope 存在 `screenshot`，无论 success 还是 failure，都生成该字段。
2. 不进入各命令自己的 `data`，避免污染业务语义。
3. `read_image_first=True` 明确要求消费本次命令返回的截图，而不是只看压缩文本。
4. 当 `screenshot is None` 时省略 `image_guidance`，而不是输出 `false` 或空对象。
5. failure 文本路径即便 envelope 带有 `image_guidance`，也不新增新的 `info` 行；failure 顺序仍保持现状。
6. `image_guidance` 只存在于 envelope / RPC 顶层；`--format yaml` 继续只输出首行摘要和 `data` YAML body，不把该字段并入 YAML；`--verbose` 也不为它新增独立输出层。

### 默认文本新增稳定提示行

所有带截图的成功结果，在 `shot path=...` 之后固定追加：

```text
info read_image_first=1
```

语义冻结：

1. `read_image_first=1`：看到这条结果时，Agent 应先消费本次命令返回的截图，再消费后续压缩文本。
2. 该行只在 success 文本路径出现；failure 路径即便带截图，也不新增该行。

### 行顺序约束

成功路径顺序更新为：

1. 首行 `ok ...`
2. `shot path=...`
3. `info read_image_first=1`
4. `item` / `guide` / `text` / `slot` / `opt` / 其他 `info`
5. `warn`
6. `ref`

这满足当前项目对 success 行顺序的约束：`shot` 仍然先于实体行出现，新提示复用已有 `info` 前缀，不引入新的 renderer 家族。

### renderer 落点约束

实现时不能把新提示挂在 `_append_shot(...)` 这种 success / failure 共用 helper 上，否则 failure 路径会被误插入 `info` 行。renderer 侧必须显式区分三类落点：

1. 走 `_append_common_success_lines(...)` 的 success renderer。
2. 手工 `_append_shot(...)` 后再拼实体行的 success renderer。
3. `_render_failure_lines(...)` 等 failure renderer。

本轮只能改前两类 success 路径，不能改第三类 failure 路径。

另外，`cw.slots.read` 当前不是简单顺序拼接，而是先渲染再插入 `slot` 行。新增 guidance 后，`slot` 行的插入位置也必须同步调整到 guidance 之后，不能继续假设“首行后最多只有一条 `shot`”。

## 自动截图补漏设计

### 新的命令策略表

在 daemon 命令分发层新增一份“伴随当前画面截图的读命令”集合，例如：

```python
CW_CAPTURED_READ_METHODS = {
    "cw.stage.detect",
    "cw.stage.wait",
    "cw.shop.scan",
    "cw.replenish.read",
    "cw.invest.read",
    "cw.encounter.read",
    "cw.fortune.read",
}
```

路由规则：

1. `cw.slots.read` 与这 7 条命令统一走“读命令自动截图”路径。
2. 现有 `CW_MUTATING_METHODS` 继续走 mutation auto-capture，不改语义。
3. 其余 `cw` 只读命令继续走无截图路径。
4. `CW_MUTATING_METHODS` 当前只是既有路由桶，本轮不借机清洗其成员，也不顺手改 `cw.hand.sell_plan` 等已有分类。

### 复用现有 capture 机制

本轮不新造一套截图实现，而是复用当前已有的：

1. request-scoped screenshot 路径归一化
2. `_run_cw_with_capture(...)`
3. `with_selective_capture(...)`

需要额外冻结两点：

1. 这 7 条命令的 route-level capture 落点在 `CommandService.handle(...)` 的 `cw` 分发层，不改 CLI 层，也不把所有 `CwService.handle(...)` 默认改成 capture。
2. `cw.replenish.read`、`cw.invest.read`、`cw.encounter.read`、`cw.fortune.read` 当前虽是粗粒度读命令，但本轮明确接受它们新增 runtime 附着前提，语义升级为“读结果 + 当前窗口参考图”；对应 spec、plan 与测试都要把这点写清楚。

这样做的好处：

1. 可以直接继承现有 `.trail/shots/...` 归一化规则。
2. 不需要为读命令另写一套 screenshot / warning / ref / debug 采集逻辑。
3. CLI / RPC / renderer 的成功包络形状保持一致。
4. 新增读命令继续共享 `cw.slots.read` 的 selective-capture 语义：仅在 `with_selective_capture(...)` 作用域内产生的业务 `TrailError` failure 允许 best-effort capture；runtime 附着 / 初始化失败保持现有无图错误语义；unexpected exception 仍保持现有 unknown failure 语义，不改 failure 顺序。

## 文档与 Skill 调整

### README

需要更新 `README.md` 的两部分：

1. “输出约定”中补充新 `info` 行语义。
2. 涉及 `shot path=...` 的示例增加新提示行，明确说明“`shot` + `info read_image_first=1`”代表必须先读图。

### AGENTS

除了 README 和 skill，还必须同步更新项目根 `AGENTS.md`，把以下内容冻结进去：

1. 带截图 success 路径新增 `info read_image_first=1`。
2. 新 guidance 行位于 `shot` 之后、实体行之前。
3. envelope 顶层新增 `image_guidance.read_image_first` 的生成条件。
4. `image_guidance` 不进入 YAML body，也不新增独立 verbose 输出。
5. 本轮不改 failure 顺序，不新增 YAML allowlist，不新增 verbose 事件类型。

### Skill

至少更新以下 skill：

1. `skills/trail-hsr/SKILL.md`
2. `skills/trail-hsr-advanced/SKILL.md`
3. `skills/trail-cw/SKILL.md`
4. `skills/trail-cw-events/SKILL.md`
5. `skills/trail-cw-slots/SKILL.md`
6. `skills/trail-cw-shop/SKILL.md`
7. `skills/trail-cw-replenish/SKILL.md`
8. `skills/trail-cw-guide/SKILL.md`

更新原则：

1. 把“截图是第一手事实”升级为更硬的执行规则。
2. 明确写出：当命令返回 `shot path=...` 且紧随 `info read_image_first=1` 时，Agent 不应跳过原始图，必须先读图再根据文本决策。
3. 保持既有 simple-first / cw 编排边界不变，不借这次文档改动扩展命令职责。

## 测试策略

### renderer 回归

需要在 `tests/test_output_rendering.py` 中新增或更新断言，锁住：

1. 带截图的成功结果会输出新 `info` 行。
2. 该行固定排在 `shot` 之后、实体行之前。
3. 没截图的成功结果不会凭空输出该行。
4. failure payload 即便带 screenshot 和 `image_guidance`，默认文本也不新增该行。
5. `cw.slots.read` 的 `slot` 行仍位于 guidance 之后，而不是插到 `shot` 后面。
6. `cw.shop.status` 继续不输出 `shot`，也不输出 `info read_image_first=1`。

### envelope / payload 回归

需要在 `tests/test_output_envelope.py` 中新增断言，锁住：

1. `command_success(...)` 在有 screenshot 时生成 `image_guidance.read_image_first`，无 screenshot 时省略。
2. `command_failure(...)` 在有 screenshot 时也生成该字段，且无 screenshot 时省略。
3. daemon `success(...)` / capture success 路径的 payload 词面保持一致。

### daemon / CLI 契约回归

需要为以下命令补测试，锁住 `screenshot` 非空与 `shot path=...`：

1. `cw.stage.detect`
2. `cw.stage.wait`
3. `cw.shop.scan`
4. `cw.replenish.read`
5. `cw.invest.read`
6. `cw.encounter.read`
7. `cw.fortune.read`

测试层建议覆盖：

1. route-level `CommandService -> CwService.handle_with_capture` 测试：这 7 条命令真正走到 read-capture 路由，而不只是 renderer fake payload。
2. daemon RPC contract：响应里 `screenshot` 非空且为 workspace-relative `.trail/shots/...`。
3. 代表性 request-scoped 测试：至少 1 条命令锁住 `request_id -> .trail/shots/<request_id>.png` 的文件名语义与路径归一化。
4. 代表性非 mutation 测试：至少 1 条命令锁住它仍是 read-capture 路由，而不是 mutation journal 路由。
5. CLI stdout：`shot path=...` 后紧跟新 `info` 行。
6. renderer 单测：顺序与词面冻结。
7. negative regression：`cw.shop.status` 继续不输出 `shot` / `info read_image_first=1`。

### 文档断言

需要同步更新 README / skill 相关断言，避免协议已经变了但文档仍停留在旧语义。当前仓库里这类断言主入口在 `tests/test_output_rendering.py`，本轮不新增新的 snapshot 机制。

同时需要同步删除或更新仓库里把 `cw.shop.status` 当成带截图命令的旧示例与旧断言，避免测试和 README 把实现重新拉回错误边界；至少核查：

1. `C:\Users\34404\source\repos\trail-cli\README.md`
2. `C:\Users\34404\source\repos\trail-cli\tests\test_output_rendering.py`
3. `C:\Users\34404\source\repos\trail-cli\tests\test_cw_rpc_contracts.py`

另外，`docs/superpowers/**` 中仍会被当作活参考、且包含旧默认输出顺序/示例的文档，需要同步更新示例或显式标注被本 spec 覆盖；至少核查：

1. `C:\Users\34404\source\repos\trail-cli\docs\superpowers\specs\2026-04-17-trail-output-format-design.md`
2. `C:\Users\34404\source\repos\trail-cli\docs\superpowers\specs\2026-04-20-cw-battle-run-design.md`
3. `C:\Users\34404\source\repos\trail-cli\docs\superpowers\specs\2026-04-20-cw-battle-action-buttons-design.md`

### 最小验证命令

implementation plan 至少要收敛到一组明确的最小验证命令，避免只写“跑相关测试”。本轮最小验证集应覆盖：

1. `uv run pytest tests/test_output_rendering.py`
2. `uv run pytest tests/test_output_envelope.py`
3. `uv run pytest tests/test_daemon_protocol.py`
4. `uv run pytest tests/test_cw_rpc_contracts.py`
5. `uv run pytest tests/test_runtime_backends.py`
6. `uv run pytest tests/test_atomic_commands.py`

## 风险与取舍

1. **风险：提示词面选得不够硬。**
   取舍：使用 `read_image_first=1`，直接把“先读图”编码进 key，而不是使用模糊词如 `image_hint=1`。

2. **风险：把纯数据命令也误纳入截图。**
   取舍：本轮范围明确收紧到 7 条 `cw` 读命令，不碰 `cw.shop.status`、`start.run`、`session.create`、`state.dump`、`cw.guide.current`。

3. **风险：renderer 顺序被破坏。**
   取舍：通过公共 helper 统一追加，配合 renderer 单测锁住顺序。

4. **风险：未来新增读命令再漏掉。**
   取舍：引入显式策略表，并在本轮审计清单中把“截图策略按家族维护”写清楚。

5. **风险：测试只覆盖 fake payload，没有覆盖真实 capture 路由。**
   取舍：把 route-level `CommandService -> CwService.handle_with_capture` 测试列为必做项，而不只补 renderer / CLI 文本测试。

## 预期结果

完成后，Agent 会在所有带截图的成功结果中同时看到：

```text
ok cw.shop.scan count=2
shot path=.trail/shots/req-shop-scan.png
info read_image_first=1
item idx=1 slot=1 name=希儿 cost=2
item idx=2 slot=2 name=停云 cost=1
```

而对当前缺失截图的 7 条 `cw` 读命令，CLI / RPC 契约会补齐到同一套模式，减少 Agent 只看文本、不读图的机会。
