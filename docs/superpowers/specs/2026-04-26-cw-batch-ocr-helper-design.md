# CW 合并 OCR Helper 与商店扫描加速设计

## 背景

当前 `cw.slots.read` 已经有一条“多次截图、一次 OCR”的角色扫描管线：命令逐个点击槽位，抓取 `SLOT_NAME_REGION` 和 `SLOT_STAR_REGION`，再把名字区域合成一张图后调用一次 `runtime.ocr_image()`。这条链路证明了降低 OCR 调用次数是有效方向，但实现目前散落在 `trail/scenes/cw/slots.py`，布局和回映射逻辑与槽位业务耦合。

当前 `cw.shop.scan` 仍然是两段式 UI 采集后多次 OCR：先收起商店读取队伍容量，再重新打开商店读取商品、金币、等级和经验。进一步梳理后，等级、经验、人口这类全局状态不应绑定到商店扫描；它们应作为独立的 stage status 扫描单元持久化到 session，并由低成本命令（第一批是 `cw.slots.read`）顺带刷新。

本设计把合并 OCR 抽成一个可组合 helper，让 `cw.slots.read` 迁移到 helper，并把全局状态扫描作为独立单元接入 `cw.slots.read`。`cw.shop.scan` 不再重新识别全局状态，只扫描商店页本身的商品/金币信息，并在输出时附带 session 中已有的 stage status。

## 已确认决策

- 抽一个通用 helper，而不是只在 `shop.py` 复制 slots 的拼图逻辑。
- `cw.slots.read` 同步迁移到 helper，避免长期存在两套相似实现。
- helper 使用 `rectangle-packer` 作为布局依赖，但不把 `rpack` 直接暴露给 scene 层；依赖更新必须同时覆盖 `pyproject.toml` 和 `uv.lock`。
- helper 只处理图像切片布局、一次 OCR、piece 回映射和 drop trace，不理解 CW 业务语义。
- 新增独立的全局状态扫描单元，结果持久化到 `cw_state.stage.status`，不混入 `cw_state.shop` 主存储。
- `cw.slots.read` 在第一次点击屏幕中央并等待稳定后、点击第一个角色框前，采集全局状态切片，并与角色名切片合并到同一次 OCR。
- `cw.shop.scan` 不再识别 `team_size/level/exp`；它只扫描商店商品、金币和 `reserve_full`，输出可附带 session 中已有的 stage status，但不重新 OCR 全局状态。
- 不改变 renderer 家族、正文前缀或 YAML allowlist；`cw.shop.scan` 附带 stage status 属于既有 `info` 前缀下的事实扩展，必须同步更新 README 和测试。
- 不创建 git commit，除非用户后续明确要求。

## 目标

1. 新增一个方便组合的 batch OCR helper，可被不同扫描命令复用。
2. 把 `cw.slots.read` 的现有合并 OCR 行为迁移到 helper，保持输出和状态语义不变。
3. 新增全局状态扫描单元，读取等级、经验、人口等状态并持久化到 `cw_state.stage.status`。
4. 让 `cw.slots.read` 在几乎不增加交互成本的情况下刷新角色与全局状态，并把二者合并到同一次最终 OCR。
5. 让 `build_cw_shop_scanner()` 的纯开店页扫描也复用 helper，使 `cw.shop.scan` / `cw.shop.buy_slot` 的商店页扫描只做一次商店 OCR，但不引入全局状态 OCR。
6. 通过测试锁定布局、回映射、状态持久化、容错和输出协议。

## 非目标

- 不改变 `cw.shop.open`、`cw.shop.close`、`cw.shop.refresh` 的行为；`cw.shop.status` 允许和 `cw.shop.scan` 一样投影已有 stage status，但不触发 OCR。
- 不新增 `cw.shop.scan`、`cw.slots.read` 的正文前缀，不改变失败/截图/`read_image_first` 顺序；允许 `cw.shop.scan` 在既有 `info` 行中附带 session stage status。
- 不改 OCR 后端或 `RuntimeOperator.ocr_image()` 的底层实现。
- 不让 helper 发起点击、等待、网络请求或 session 状态写入。
- 不引入图像模板检测空槽位、商店是否打开等新的识别能力。
- 不把 helper 扩展成 workflow engine、UI 状态机、区域常量注册中心、renderer 适配层或业务解析框架。
- 不新增独立用户命令来扫描全局状态；本轮只提供可复用单元，并由 `cw.slots.read` 调用。
- 不恢复 archive skill，也不把 legacy skill 重新挂回 active 拓扑。

## 方案比较

### 方案 A：只在商店复制 slots 拼图逻辑

优点是改动最小，能快速让 shop 少做 OCR。缺点是会形成第二套布局和回映射代码，后续组合其它扫描命令时继续复制，违背“方便扩展”的目标。

### 方案 B：自实现轻量 shelf layout helper

优点是不新增依赖，可按 OCR 目标自定义比例和空白率。缺点是布局质量和边界都要自己维护，用户明确希望避免实现复杂布局。

### 方案 C：用 `rectangle-packer` 做 helper 内部布局

优点是核心矩形装箱交给现成库，项目代码只负责 OCR 相关的 padding、候选约束、回映射和 trace。缺点是 `rpack` 的目标是固定方向矩形 packing，不天然知道 OCR 友好比例，因此 helper 仍需要在它外层做少量布局评分和保护。

采用方案 C。

参考来源：`rectangle-packer` PyPI 显示 2.1.0 于 2026-03-12 上传并提供 Python 3.12 Windows wheel；官方文档说明 `rpack.pack()` 接收矩形尺寸并支持 `max_width`、`max_height` 约束。`rectpack` 提供 MaxRects/Skyline/Guillotine 等算法，但 PyPI 版本较旧且状态为 Alpha，因此本设计不选择它作为生产依赖。

## Helper 设计

新增文件：`trail/runtime/batch_ocr.py`。

新增依赖：`pyproject.toml` 增加 `rectangle-packer>=2.1.0`，并同步更新 `uv.lock`。实现计划必须包含一次最小验证，例如 `uv run python -c "import rpack; print(rpack.pack([(1, 1)]))"` 或等价单测，锁定包名 `rectangle-packer` 与导入名 `rpack` 的差异。

### 数据结构

`BatchOcrTarget` 表示一个待 OCR 切片：

```python
@dataclass(frozen=True)
class BatchOcrTarget:
    key: Hashable
    image: Image.Image
    padding: int = 4
    metadata: Mapping[str, Any] | None = None
```

约束：

- `key` 必须唯一，允许使用字符串、整数或 tuple 等可哈希值。重复 key 直接抛 `TrailError("BATCH_OCR_DUPLICATE_KEY", ...)`，不能静默覆盖 `by_key`。
- `image` 必须是非空 PIL image，宽高都必须大于 0；无效图片抛 `TrailError("BATCH_OCR_INVALID_TARGET_IMAGE", ...)`。
- `padding` 必须为非负整数。`metadata` 只做调用方调试透传，可进入 `PackedOcrTarget` / `BatchOcrEntry` 供测试或 verbose trace 使用，但 helper 不按其中的业务字段分支。

`PackedOcrTarget` 表示切片在合成图里的位置：

```python
@dataclass(frozen=True)
class PackedOcrTarget:
    key: Hashable
    rect: dict[str, int]
    content_rect: dict[str, int]
    metadata: Mapping[str, Any]
```

坐标约束：`rect` 是 atlas 中包含 padding 的外框，`content_rect` 是原始切片内容框。二者都使用 atlas top-left 坐标系，字段固定为 `left/top/right/bottom/width/height` 这类标量，不使用嵌套 box 对象。

`BatchOcrEntry` 表示单个 key 的 OCR 结果：

```python
@dataclass(frozen=True)
class BatchOcrEntry:
    key: Hashable
    pieces: list[Any]
    text: str | None
    rect: dict[str, int]
    dropped: list[dict[str, Any]]
```

返回约束：

- `pieces` 是已经归属到该 target 后的 OCR pieces，几何坐标必须平移回 target-local 坐标系，而不是 atlas 坐标系。现有 shop parser 依赖本地坐标计算商店槽位，不能收到 atlas offset。
- piece 的原始 shape 尽量保持不变；dict box、RapidOCR polygon tuple、对象式 box 只做 `dx=-content_rect.left`、`dy=-content_rect.top` 平移。
- `text=None` 表示该 target 无可用 OCR 文本；空字符串不用于表达缺失。`pieces=[]` 时 entry 仍必须存在。
- `rect` 使用 atlas 坐标，供调试和测试定位，不传给业务 parser。

`BatchOcrResult` 表示整批结果：

```python
@dataclass(frozen=True)
class BatchOcrResult:
    image: Image.Image
    packed: list[PackedOcrTarget]
    by_key: dict[Hashable, BatchOcrEntry]
    dropped: list[dict[str, Any]]
```

`by_key` 必须覆盖所有输入 target，即使 OCR 没有识别出任何文字。`BatchOcrResult.image`、`packed`、`dropped` 和 layout metadata 只能供 helper 测试、scene 内部解析和 verbose debug 使用，不能原样写入命令 `data`、envelope、YAML body 或默认文本输出。

### Public API

```python
def pack_batch_ocr_targets(
    targets: Sequence[BatchOcrTarget],
    *,
    gap: int = 24,
    target_aspect: float = 16 / 9,
    max_width: int | None = None,
    max_height: int | None = None,
) -> tuple[Image.Image, list[PackedOcrTarget]]:
    ...


def run_batch_ocr(
    runtime,
    targets: Sequence[BatchOcrTarget],
    *,
    ocr: OcrRequestConfig | None = None,
    gap: int = 24,
    target_aspect: float = 16 / 9,
    trace_prefix: str = "batch_ocr",
) -> BatchOcrResult:
    ...
```

调用方只传图片切片。helper 不调用 `runtime.capture_image()`，因为不同命令的 UI 状态、点击、等待、可降级字段都不同，必须留在 scene 层。

`run_batch_ocr()` 默认 OCR 配置固定为 `OcrRequestConfig(ocr_mode="high", retry_high="never")`。当前 `cw.slots.read` 迁移时仍要显式传入同一配置，避免从旧 slots 专用路径迁移后意外改成 runtime 默认 OCR 模式。

错误模型：

- 空 `targets` 抛 `TrailError("BATCH_OCR_EMPTY_TARGETS", ...)`。scene 层不能把关键字段全空当成成功扫描。
- packing 失败、所有候选 layout 都失败，或显式 `max_width/max_height` 约束无法满足时，抛 `TrailError("BATCH_OCR_PACK_FAILED", ...)`。
- `runtime.ocr_image()` 的 `TrailError` 和未预期异常必须原样重新抛出；helper 只做 best-effort finalized debug 记录，不能把 OCR 后端失败降级为空 pieces。
- helper 不回退到逐 target OCR。是否降级字段由 scene 层根据具体字段决定。

### 布局策略

helper 使用 `rpack.pack()` 生成不重叠 packing，并在外层做 OCR 友好的约束选择：

1. 所有切片保持原始像素尺寸，不缩放、不旋转。
2. 每个 target 先扩展为 padded size：`image.width + 2 * padding`、`image.height + 2 * padding`。packing size 再额外加 `gap`，用于在相邻 padded rect 之间留白。`rect` 对应 padded rect，`content_rect` 对应原图内容位置；atlas 最终可裁掉最外侧多余 gap，但不能裁掉 padding。
3. `sizes` 必须与输入 `targets` 同序，`positions = rpack.pack(sizes, ...)` 的返回坐标按同序 `zip(..., strict=True)` 回映射到 target。不得依赖 `rpack` 内部排序，也不得在回映射前重排 target。
4. `rpack` 文档把坐标描述为 lower-left corner；helper 在 atlas 绘制、`content_rect` 和回映射中统一把返回的数值坐标当作同一二维画布坐标使用，不做额外 y 轴翻转。`rpack.bbox_size(sizes, positions)` 是 atlas 尺寸来源。
5. 候选 layout 固定为有限集合：先跑 unconstrained；再用 `base_width = sqrt(total_packing_area * target_aspect)` 生成 `0.75x/1.0x/1.25x/1.5x` 候选宽度；再加入 `max(target_packing_width)` 与调用方显式 `max_width`。候选宽度向上取整并去重，低于最大 packing 宽度的候选丢弃；显式 `max_height` 作为硬约束传入每次 `rpack.pack()`。
6. 单个候选触发 `rpack.PackingImpossibleError` 或等价失败时丢弃并计入 finalized trace 的失败计数；全部候选失败时抛 `BATCH_OCR_PACK_FAILED`。
7. 候选评分公式固定为：`score = blank_ratio * 2.0 + aspect_penalty * 1.0 + long_side_penalty * 0.5`。其中 `blank_ratio = max(0, (atlas_area - total_content_area) / atlas_area)`，`aspect_penalty = abs(log((atlas_width / atlas_height) / target_aspect))`，`long_side_penalty = max(0, max(atlas_width, atlas_height) - preferred_long_side) / preferred_long_side`，`preferred_long_side = max(sqrt(total_content_area * target_aspect), sqrt(total_content_area / target_aspect), max_target_side)`。tie-break 顺序为 atlas area 更小、长边更短、宽更小、高更小。
8. 合成图背景使用白色，切片贴到 `content_rect` 内。
9. 返回每个 target 的 `rect` 和 `content_rect`，OCR piece 使用中心点落入 `content_rect` 做归属。

这里不追求全局最优装箱，只要求足够密集、比例正常、运算开销远低于 OCR。

### Piece 回映射

helper 统一支持 dict box、RapidOCR polygon tuple、对象式 `left/top/width/height` 三类几何输入。

回映射规则：

- piece 有几何信息时，取中心点。
- `content_rect` 命中规则使用半开区间：`left <= center_x < right` 且 `top <= center_y < bottom`。
- 中心点落入某个 target 的 `content_rect`，且 piece box 未跨入其它 target 的 `content_rect`，归属到该 key。
- 中心点落入 padding、gap、所有 target 外，或 piece box 同时跨多个 target 的 `content_rect`，drop 并记录 reason。
- piece 无几何信息且本批只有一个 target，归给唯一 target。
- piece 无几何信息且本批多个 target，drop 并记录 reason。
- piece 无文本或文本规范化后为空时 drop 并记录 `empty_text`，不参与 `text` 拼接。
- 同一个 target 内，piece 按 `top`、`left`、原始顺序稳定排序后拼接文本。
- 返回给调用方前，归属 piece 的几何坐标必须平移回 target-local 坐标系，保证 `shop.py` 的 `_shop_scan_slot_for_item()`、`_parse_shop_team_size()` 等现有 parser 可以继续按裁剪图坐标工作。

debug 记录使用 runtime 的 finalized action 语义：如果存在 `_begin_debug_action()` / `_finish_debug_action()`，`run_batch_ocr()` 以 `trace_prefix` 作为 step 开始动作，成功和失败都通过 action finish 产生带 `ts`、`ok`、`dur_ms` 的 trace。layout、候选数量、packing failure count、drop count、drop reasons 等只作为该 finalized trace 的受控字段或 `BatchOcrResult.dropped` 暴露给测试；不直接拼接 stdout，不新增非 finalized `<trace_prefix>_drop` trace。若 fake runtime 没有 finalized debug API，helper 可以不记录 debug，但不能回退到默认输出。

## 全局状态扫描单元

全局状态扫描从商店扫描中拆出，落到 `trail/scenes/cw/stage.py`。`stage.py` 已负责 `cw_state.stage.value/stale` 的阶段检测；新增 status 扫描时必须避免破坏这两个既有字段。

新增或迁移的职责：

- 把 `SHOP_TEAM_SIZE_REGION`、`SHOP_LEVEL_REGION`、`SHOP_EXP_REGION` 这类实际表示全局状态的区域常量迁移或别名到 `stage.py`，命名为 `CW_STATUS_TEAM_SIZE_REGION`、`CW_STATUS_LEVEL_REGION`、`CW_STATUS_EXP_REGION`。
- 把 `_parse_shop_level()`、`_parse_shop_exp()`、`_parse_shop_team_size()` 中与全局状态解析相关的逻辑迁移或复用为 stage status parser；商店页 parser 不再拥有这些全局字段的主职责。
- 提供纯函数 `parse_cw_stage_status(result.by_key, *, role_count)` 或等价函数，返回 `{"stale": False, "level": ..., "exp": ..., "team_size": ..., "role_count": ...}`。
- `role_count` 由 `cw.slots.read` 的角色槽位结果派生，至少保留 `front/back/hand/field/total` 这类稳定数字；这些 `0` 值不能因为看起来为空而省略。
- 写入 session 的位置固定为 `cw_state["stage"]["status"]`，而不是直接覆盖 `cw_state["stage"]`。这样 `cw.stage.detect` 仍可继续维护 `cw_state.stage.value/stale`。
- 所有已有会写 `cw_state["stage"] = {...}` 的路径都必须改为 merge-preserve：更新 `value/stale/error` 时保留既有 `stage.status`。至少覆盖 `stage.py` 的 `mark_cw_stage_stale()`、`_invalidate_cw_stage()`、`detect_cw_stage()`，以及当前会整体覆盖 stage 的 battle/portal/strategy/entry/guide 流程。
- 新增 `mark_cw_stage_status_stale(session)` 或等价 helper，只设置 `cw_state.stage.status.stale=True`，并保留 `level/exp/team_size/role_count` 等已有值用于排障。
- 当前所有会调用 `_mark_slots_stale()` 或使角色/人口计数不可信的命令，也必须标记 stage status stale。第一批至少包括 `cw.shop.buy_slot`、`cw.hand.sell`、`cw.slots.place`、`cw.slots.swap`、`cw.slots.place_one` 以及任何会清空/移动/出售角色槽位的路径。

`cw_state.stage.status` 示例：

```python
{
    "stale": False,
    "level": 7,
    "exp": "4/52",
    "team_size": "3/3",
    "role_count": {"front": 1, "back": 1, "hand": 1, "field": 2, "total": 3},
}
```

`mark_cw_stage_stale()` 仍只表示阶段页面识别过期；如果后续命令会改变等级、经验、人口或角色数量，应新增/复用明确的 status stale helper，例如 `mark_cw_stage_status_stale(session)`，只把 `cw_state.stage.status.stale` 设为 `True`，不要清掉 `stage.value`。

## slots reader 返回契约

现有 `SlotsSnapshotReader` 只返回 `(front, back, hand)`，无法把同一次 OCR 得到的 stage status 明确传给 `read_cw_slots()`。本设计需要扩展为显式结果对象，避免 reader 通过 side-channel 写 session。

建议新增：

```python
@dataclass(frozen=True)
class CwSlotsReadResult:
    front: list[Any]
    back: list[Any]
    hand: list[Any]
    stage_status: dict[str, Any] | None = None
```

`build_cw_slots_reader()` 返回 `CwSlotsReadResult`。`read_cw_slots()` 仍负责 session 写入：先按现有逻辑 merge/normalize slots，再基于最终 merged slots 计算 `role_count`，最后把 reader 提供的 `level/exp/team_size` 与 `role_count` 合成 `cw_state.stage.status`。为了减少破坏面，测试 helper 或旧调用方若仍返回 tuple，可在 `read_cw_slots()` 内部做短期适配；但生产 reader 应迁移到 `CwSlotsReadResult`。

## `cw.slots.read` 迁移设计

保留现有交互与状态语义：

1. `_collapse_expanded_hand_card(runtime)` 保持不变。
2. 根据 `targets` 计算目标槽位集合。
3. 继续先点击 `INFO_DISMISS_POINT` 并等待稳定。
4. 在点击第一个角色框前，采集全局状态切片：等级、经验、人口。这个时机没有额外 UI 交互，只增加截图切片。
5. 每个目标槽位继续点击、等待、抓 `SLOT_NAME_REGION` 与 `SLOT_STAR_REGION`、关闭面板。
6. 名字切片包装成 `BatchOcrTarget(key=("slot", area, index), image=name_image)`；全局状态切片包装成 `BatchOcrTarget(key=("stage_status", field), image=...)`。
7. 所有名字与状态 target 一起调用 `run_batch_ocr(..., ocr=OcrRequestConfig(ocr_mode="high", retry_high="never"), trace_prefix="cw_slots_batch_ocr")`，保持一次最终 OCR。
8. 从 `result.by_key[("slot", area, index)].text` 取名字；从 `("stage_status", field)` entries 解析等级、经验、人口。
9. 星级仍用 `_count_slot_stars_in_image(star_image)`，不进 OCR helper。
10. `result.by_key` 对每个输入 target 都必须有 entry；无文本时按现有空槽/缺失状态逻辑写回 `None`，不能因为 entry 缺失跳过目标槽位。
11. 后续 `_merge_area_snapshot()`、`_slots_read_empty_snapshot()`、名字归一化、trait summary 都保持现状。
12. `build_cw_slots_reader()` 返回 `CwSlotsReadResult(front, back, hand, stage_status=...)`，不直接写 session。
13. `read_cw_slots()` 写入 `cw_state["slots"]` 后，基于最终 merged slots 计算 `role_count`，并把 `stage_status` 写入 `cw_state["stage"]["status"]`。全局状态写入不应影响 `cw_state["stage"]["value"]`。

迁移后不再保留 slots 内部的 `_compose_slot_name_strip_image()`、`_map_ocr_pieces_to_slot_names()` 等重复逻辑；如果测试需要低层行为，迁移到 `tests/test_batch_ocr.py`。slots 层可以保留小型 orchestration helper，例如 `_capture_cw_stage_status_targets()`，但具体 status parser 应放在 `stage.py`，避免 `slots.py` 拥有全局状态业务规则。

## `cw.shop.scan` 设计

`cw.shop.scan` 不再扫描全局状态。它只处理商店页本身，并在输出时附带 session 里已有的 `cw_state.stage.status` 投影。

流程：

1. 点击 `SHOP_OPEN_POINT` 打开商店（若调用方已经在商店页，纯 scanner 不点击）。
2. 等待 `SHOP_SCAN_OPEN_SETTLE_SECONDS`。
3. 抓取 `SHOP_SCAN_REGION` 与 `SHOP_COINS_REGION` 切片，分别命名为 `items`、`coins`。
4. 把成功采集到的商店 target 交给 `run_batch_ocr(..., trace_prefix="cw_shop_batch_ocr")`。
5. 对 `result.by_key` 调用商店 parser：`_parse_shop_items()`、`_parse_first_int()`。
6. 持久化 shop snapshot：`{"opened": True, "stale": False, "items": ..., "coins": ..., "reserve_full": ...}`。
7. command response / status projection 通过显式投影函数附带 `cw_state.stage.status` 中已有的 `level/exp/team_size/role_count`，但不能在 `cw.shop.scan` 中重新 OCR 这些字段。

逐字段采集责任仍留在 `shop.py`：

- `items` 和 `coins` 使用开店页局部切片，parser 接收 target-local pieces。
- `reserve_full` 仍由 `_parse_shop_items()` 从商品 OCR 文本中解析。
- `level/exp/team_size/role_count` 只能来自 `cw_state.stage.status`，作为输出附带信息或 `shop.status` 投影，不写回 `cw_state.shop` 主数据。
- 新增 `project_cw_shop_snapshot(session)` 或复用/改造 `shop_cw_status(session)` 作为唯一投影入口：它返回 `{**cw_state.shop, stage_status=...}` 或等价扁平字段给 renderer/RPC；`scan_cw_shop()` 持久化后也应返回这个投影，而不是直接返回裸 `cw_state.shop`。`cw_state.shop` 主存储仍只保存商店事实。

如果开店页关键切片采集失败，packing 失败，或最终 batch OCR 失败，命令按现有 mutation 失败链路处理。由于 `cw.shop.scan` 已是 mutating command，点击后失败仍会进入 request_id、tainted、recover 语义；helper 不能吞掉异常。

### 纯开店页 scanner

`build_cw_shop_scanner(runtime)` 继续保持纯商店页扫描器定位：

- 不点击空白。
- 不点击开店按钮。
- 只抓取开店页的 `items/coins` 两个 target。
- 调用一次 batch OCR。
- 继续供 `cw.shop.buy_slot` 的购买后确认复用。

这样 `buy_slot` 也减少 OCR 次数，但不会触发全局状态 OCR。后续若某个购买/升级命令确定会改变全局状态，应显式调用全局状态扫描单元或标记 `cw_state.stage.status.stale=1`，不能让普通 `shop.scan` 隐式重扫。

## 输出协议

本设计不改变输出 renderer。

`cw.shop.scan` 成功路径保持：

```text
ok cw.shop.scan opened=1 stale=0 count=<n>
shot path=...
info read_image_first=1
item ...
info coins=... reserve_full=...
info stage_level=... stage_exp=... stage_team_size=... stage_status_stale=0
```

若 `cw_state.stage.status` 缺失或 stale，则 `cw.shop.scan` 不重新识别全局状态；renderer 只输出已有且语义有效的 shop facts，并必须稳定输出 `info stage_status_stale=1`。当 status fresh 时必须稳定输出 `stage_status_stale=0`，并按可用字段追加 `stage_level/stage_exp/stage_team_size`。`stage_status_stale` 是影响下一步决策的 must-keep 事实，不能因缺失或 false/0 而省略。如果需要新增这些 `stage_*` 输出字段，必须更新 renderer 测试、README 和 active skills；不能把 `BatchOcrResult` 或 stage status 原始嵌套结构直接泄漏到默认文本。

`cw.slots.read` 成功路径保持现有首行、截图行、slot 行、trait summary 与 warning/reference 顺序。

失败路径继续遵守 AGENTS.md：`request` -> `shot` -> `why` -> `warn` -> `ref` -> `recover`。helper 不新增默认正文前缀。

`--verbose` 可以出现新的 finalized trace，例如 `step=cw_shop_batch_ocr`、`step=cw_slots_batch_ocr` 或 helper 内部 `step=batch_ocr`，但默认模式不得泄漏 trace。trace 必须通过 `trail.output.debug.collect_debug_events` 和 `render_debug_lines` 渲染，不能由命令或 helper 直接拼接 `debug ...` 行。

scene 层只能把 helper result 消化成现有业务 data：shop snapshot、slots snapshot 或 `cw_state.stage.status`。`BatchOcrResult.image`、atlas path、`packed`、`rect`、layout metadata、drop 详情和 `image_guidance` 都不得写入命令 `data`、envelope 或 YAML body。YAML allowlist 不因本设计变化；`cw.shop.scan` 和 `cw.slots.read` 仍不支持 YAML。

`image_guidance.read_image_first=1` 仍只由 envelope 顶层截图生成；默认文本中的 `info read_image_first=1` 仍只在带截图的 success renderer 中紧跟 `shot path=...`。

## 扩展配方

后续第三个扫描命令复用 helper 时，只需要在自己的 scene 层完成三件事：

1. 按该命令的 UI 状态采集一组 `BatchOcrTarget(key=..., image=..., metadata=...)`。
2. 调用 `run_batch_ocr()` 获取 `result.by_key`。
3. 用该命令自己的 parser 消费 target-local pieces，并把结果写回既有业务 data。

helper 不持有截图区域常量，不注册命令，不编排点击/等待，不读取或写入 session，不解析商品、角色、经验等业务字段，也不参与 renderer。这样可以组合不同扫描命令，但不会把工具层扩张成场景框架。

## 测试计划

### `tests/test_batch_ocr.py`

- `test_rectangle_packer_import_and_minimal_pack_smoke`：`import rpack` 成功，两个小矩形调用 `rpack.pack()` 返回坐标数量等于输入数量。
- `pack_batch_ocr_targets` 对宽条加小块生成不重叠 rect，packing 结果宽高比不会退化成极端单列。
- packing invariant：`packed` 长度、key 集合、`by_key` 覆盖所有输入 target、rect 非重叠、atlas size 覆盖所有 padded rect、`content_rect` 尺寸等于原图尺寸、重复输入输出稳定；明确 `packed` 顺序跟随输入顺序，`by_key` 按 key 稳定访问。
- 依赖坐标回映射：不同尺寸 target 的 `sizes` / `positions` 同序 zip，使用 `rpack.bbox_size()` 计算 atlas 尺寸，不做 y 轴翻转。
- padding/gap/out-of-atlas 区域不会被误归属，并记录对应 drop reason。
- dict box、polygon box、对象 box 都能映射，并且返回 pieces 的坐标被平移回 target-local。
- 二维 `content_rect` 命中、半开边界规则、横向/纵向相邻 target 归属、跨 target box drop。
- 多 target 缺几何 piece 被 drop；单 target 缺几何 piece 被归属并保持原始顺序。
- piece 无文本或空文本被 drop 为 `empty_text`。
- 同 target 内 piece 按 `top/left/order` 稳定排序，不同 key 文本不串接。
- candidate `max_width` 评分能选择更接近目标比例的布局；单候选 packing impossible 被丢弃，全部失败抛 `BATCH_OCR_PACK_FAILED`。
- 重复 key、空 targets、0 尺寸图片分别抛稳定 `TrailError` code。
- `run_batch_ocr()` 遇到 `runtime.ocr_image()` 的 `TrailError` / `RuntimeError` 会重新抛出，不降级为空文本。

### `tests/test_cw_slots.py`

- `build_cw_slots_reader()` 仍然只调用一次 `ocr_image()`。
- 迁移后仍显式使用 `OcrRequestConfig(ocr_mode="high", retry_high="never")`。
- 默认全量和 `--slot` 定向读取都只为目标槽位采集图片。
- `build_cw_slots_reader()` 在点击屏幕中央并等待后、点击第一个角色框前采集 `CW_STATUS_LEVEL_REGION`、`CW_STATUS_EXP_REGION`、`CW_STATUS_TEAM_SIZE_REGION`，并把这些 target 与 slot name target 合并到同一次 `ocr_image()`。
- `build_cw_slots_reader()` 返回 `CwSlotsReadResult`；`read_cw_slots()` 不依赖 reader side-channel。
- `read_cw_slots()` 在写入 `cw_state.slots` 的同时写入 `cw_state.stage.status`，保留 `cw_state.stage.value/stale`；断言 `level/exp/team_size/role_count` 持久化，`role_count` 中的 `0` 值不省略。
- `stage.py` 和其它整体覆盖 `cw_state.stage` 的路径必须保留已有 `stage.status`；测试覆盖 `cw.stage.detect`、`mark_cw_stage_stale()` 以及至少一个 battle/portal/entry 类路径不会清掉 status。
- 当前会让 slots stale 的命令也必须让 `cw_state.stage.status.stale=1`；测试覆盖 `cw.shop.buy_slot`、`cw.hand.sell`、`cw.slots.swap/place` 中至少各一类路径。
- `_count_slot_stars_in_image()` 仍对每个目标 `star_image` 调用；非目标槽位不计星；batch OCR 只消费 name image。
- helper finalized trace 保持可观察，step 使用 `cw_slots_batch_ocr` 或等价稳定名称，并带 `ok`、`dur_ms`、drop count/reasons；默认输出不泄漏。
- 局部读取 fresh/stale merge 矩阵不变。
- `SLOTS_READ_EMPTY` 判定不变。
- 名字归一化与 trait summary 不变。
- OCR `TrailError` 与 unexpected exception 语义不被 helper 吞掉，captured read failure envelope 继续带截图。

### `tests/test_cw_shop.py`

- `build_cw_shop_scan_snapshot_reader()` 的新测试中 `runtime.ocr()` 必须 raise，事件顺序为：开店点击 -> 等待 -> `items/coins` 两个开店页 `capture_image(..., normalize=False)` -> 一次 `ocr_image(atlas)`；不得采集 `team_size/level/exp`。
- `cw.shop.scan` 解析和持久化结果包含 `items/coins/reserve_full/opened/stale`，不把 `level/exp/team_size` 写入 `cw_state.shop`。
- `cw.shop.scan` / `cw.shop.status` 输出 projection 可以附带已有 `cw_state.stage.status`，但 fake runtime 中 `capture_image` 若收到全局状态区域必须 fail，防止重新 OCR。
- `cw.shop.scan` 持久化后通过 `project_cw_shop_snapshot(session)` 或等价函数返回投影结果；测试断言 `cw_state.shop` 主存储不含 `level/exp/team_size/role_count`，但响应 data 含 stage projection。
- 开店页 capture、packing 或 batch OCR 失败时不吞错误；通过 CommandService 覆盖 request id、tainted、recover 语义。
- `build_cw_shop_scanner()` 不点击，只采 `items/coins` 两个开店页 target，并只调用一次 `ocr_image()`；`click_point` 和 `ocr` spy 应 raise，防止误回旧路径。
- `buy_slot` 购买后确认继续复用纯 scanner，不触发收店/开店。

### 契约与回归

- `tests/test_output_rendering.py` 保持 `cw.shop.scan` success/failure 文本协议。
- `tests/test_cw_rpc_contracts.py` 保持 RPC 输出契约。
- 增加 `cw.shop.scan` 附带 stage status 的 renderer/RPC 测试：stage 信息来自 session projection，不来自本次 shop OCR；stage stale/缺失时不伪造全局状态。
- renderer/RPC 必须稳定输出 `stage_status_stale=0|1`；缺失或 stale 时也不能省略该 must-keep 决策事实。
- 增加 `cw.slots.read` 持久化 `cw_state.stage.status` 的 session/RPC 测试，确认截图 success 仍先输出 slots 信息，stage status 不破坏 slots renderer。
- 增加 batch OCR trace no-leak 测试：`cw.shop.scan` 与 `cw.slots.read` payload 带 helper debug/drop 信息时，默认输出不出现 `debug`、layout、rect 或 atlas；`--verbose` 才追加 finalized debug 行。
- success 带截图时继续断言 `shot path=...` 后紧跟 `info read_image_first=1`；failure 继续断言 `request id` 与 `recover` 顺序。
- `cw.shop.scan` 和 `cw.slots.read` 请求 `--format yaml` 仍返回 `OUTPUT_FORMAT_NOT_SUPPORTED`，`YAML_ALLOWLIST` 不新增。
- `tests/test_runtime_backends.py` 只在必要时补 `capture_image(normalize=False)` 与 `ocr_image()` 回归；现有基础能力已经存在，不重复测试底层。
- 运行至少：`uv run pytest tests/test_batch_ocr.py tests/test_cw_slots.py tests/test_cw_shop.py tests/test_output_rendering.py tests/test_output_debug.py tests/test_cw_rpc_contracts.py -q`。

## 文档与 skill 同步

- `README.md` 增加说明：`cw.shop.scan` 只扫描商店页商品/金币切片，调用者不需要手动先开店。
- `README.md` 还要说明：`cw.slots.read` 会顺带刷新 `cw_state.stage.status`；`cw.shop.scan` 不重新识别全局状态，只附带 session 中已有的 stage status。
- active skills 只更新当前存在的 `skills/trail-hsr`、`skills/trail-cw-entry`、`skills/trail-cw-portal`、`skills/trail-cw-guide` 中与 shop/slots 工作流有关的部分。
- 不修改 archive skill，不把旧的商店或槽位场景 skill 恢复成 active 入口。

## 风险与缓解

### 布局质量风险

`rpack` 优化的是矩形 packing，不是 OCR 识别率。缓解方式是 helper 外层保留候选约束和评分，并用宽条+小块、slots 19 切片两类测试锁定布局不退化。

### OCR box 回映射风险

合成图布局从单列 y 区间改为二维 rect，box 中心点落在 padding/gap 时可能被 drop。缓解方式是给每个切片加 padding/gap，并对 drop 事件写 verbose trace，默认不影响输出。

### 商店字段降级风险

team size 不再由 shop scan 采集。shop scan 附带的 stage status 可能缺失或 stale，不能为了补齐输出而触发额外 OCR；调用方应通过 `cw.slots.read` 或未来接入 status scanner 的命令刷新。

### stage status 与 stage value 混淆风险

`cw_state.stage` 已被 `cw.stage.detect` 用于阶段值。新增全局状态必须放在 `cw_state.stage.status`，并且 stale helper 只改 `status.stale`，避免覆盖 `stage.value/stale` 导致阶段恢复链路回归。

### 依赖风险

新增 `rectangle-packer` 是二进制 wheel 依赖。PyPI 已提供 Python 3.12 Windows wheel，但实现时仍要通过 `uv lock` / `uv sync`、`uv.lock` diff 和最小 `import rpack` smoke 测试验证当前开发环境可安装。

## 自检

- 没有占位符或待补章节。
- 设计覆盖用户确认的通用 helper、`rectangle-packer`、slots 迁移、shop 单次 OCR、stage status 持久化和 renderer 家族不变。
- helper 边界不包含点击、等待、session 写入或业务解析。
- 全局状态扫描已从 shop scan 拆出为 stage status 单元，并由 `cw.slots.read` 在低成本时机合并进同一次 OCR。
- `cw.shop.scan` 和 `build_cw_shop_scanner()` 的副作用边界分开，避免影响 `buy_slot`；shop scan 不重新识别全局状态。
- 设计已明确 target-local piece 坐标、finalized debug trace、OCR 异常传播、`uv.lock` 更新、rpack 候选评分、全局状态持久化和 YAML/debug 不泄漏约束。
- 文档同步遵守当前 active skill 拓扑，不恢复 archive skill。

## 参考链接

- `rectangle-packer` PyPI: https://pypi.org/project/rectangle-packer/
- `rectangle-packer` 文档: https://rectangle-packer.readthedocs.io/
- `rectpack` PyPI: https://pypi.org/project/rectpack/
- `rectpack` GitHub: https://github.com/secnot/rectpack
