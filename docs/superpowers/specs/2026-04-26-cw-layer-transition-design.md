# 货币战争 `layer_transition` 状态拆分设计

## 目标

把当前被误归到 `boss_preview` 的“整层（位面）结束后，点击空白继续进入下一层”的过场页，正式拆成新的对外 stage token：`layer_transition`，并让 `battle.run` 把它继续视为 battle flow 的一部分完成收口。

这次设计要解决两个现实问题：

1. 现有 `boss_preview` token 语义过宽，混装了真正的首领预览页与“点击空白处继续”的层切换过场页，已经对 Agent 和后续实现形成误导。
2. 在 live 15 秒续跑里，`battle.run` 已经能更早从 settle 推进到“点击空白处继续”的过场页，但当前仍会把这类页判成 `boss_preview`，甚至在某些分支直接 `CW_BATTLE_STATE_UNKNOWN`，导致 battle flow 没能完整覆盖到下一稳定阶段。

## 当前问题

当前 `cw.stage.detect` / `build_cw_stage_detector()` 使用 `stage.boss_preview` 这一路模板来识别 `click_blank.png` 对应页面，因此：

- 只要页面长得像“点击空白处继续”过场，就会被归为 `boss_preview`
- 但从真实语义看，这类页并不是“本场对局首领预览”，而是“本层结束后，继续进入下一层”的过场页

这会带来两个后果：

1. 对外 stage token 本身误导上层 skill / Agent。
2. `battle.run` 对这类页的处理语义不清晰，既不像普通稳定阶段，也不是真正的首领预览页。

## 命名与语义

### 新增 token

- 新 stage token：`layer_transition`

它专门表示：

- 当前这一整层（位面）结束后
- 页面出现“点击空白处继续”之类的过场提示
- 用户点击后会继续进入下一层 / 下一阶段

### `boss_preview` 收紧

`boss_preview` 以后只用于真正的首领预览页，不再继续承载 blank-continue 过场页。

这里不能只把它当成 `layer_transition` 的排除项，还要给出正向判据：

- 首版 `boss_preview` 的正向 OCR 判据固定为 `本场对局首领`
- 只要当前页满足真正首领预览特征，就应返回 `boss_preview`
- 不能因为 blank-continue 模板命中就先把它吞进 `layer_transition`

换句话说：

- **真正的首领预览页** -> `boss_preview`
- **层切换空白继续过场页** -> `layer_transition`

## 首版识别规则

这次不做泛化的“所有空白继续页族”抽象，只先识别当前这一类 `layer_transition`。首版规则走保守特征集，而且条件要写成可执行的布尔组合，而不是“命中其一即可”：

1. 命中当前 blank-continue 模板（现有 `click_blank.png` 这一路）
2. 同页 OCR 归一化后**同时**包含：
   - `点击空白处继续`
   - `位面`
3. 同页 OCR **不**包含真正 `boss_preview` 的正向判据：
   - `本场对局首领`

也就是说，首版 `layer_transition` 的正向条件固定为：

- `click_blank.png` 命中
- `点击空白处继续` 命中
- `位面` 命中
- `本场对局首领` 不命中

这次**不**允许把条件放宽成“`点击空白处继续` 或 `位面` 任一命中即可”。那样会把其它 blank-continue 页族也过早吞进 `layer_transition`，违背本次只拆当前这一类页的边界。

本次**不**把“编号圆环”“层级进度结构”做成首版硬规则。原因是：

- 它们目前更适合作为人工解释特征
- 但没有现成、低成本、稳定的图形结构识别器
- 如果首版硬加进去，会把这次 scope 从状态拆分扩成新的视觉结构检测问题

## battle.run 语义

`battle.run` 默认继续把 `layer_transition` 视为 battle flow 的一部分，而不是在这里停住。

也就是：

1. battle / settle 已经收口到这类页时
2. `battle.run` 仍继续点击空白推进
3. 直到回到下一稳定阶段，或进入后续明确状态

这意味着 `layer_transition` 的默认处理语义更接近：

- `settle_followup` 之后的后续过场推进态

而不是：

- 普通稳定阶段
- 或需要上层 Agent 再手工决定的中断点

这次还要额外冻结 battle.run 内部状态边界：

- `classify_cw_battle_page()` 在首版实现中不得把 `layer_transition` 归成 `stable_stage`
- 也不得把它归成普通 `unknown` 然后走“只 sleep 不推进”的兜底分支
- `run_cw_battle()` 遇到 `layer_transition` 时必须执行“点击空白继续”的推进动作
- 若本次调用在 `layer_transition` 上仍因 budget 耗尽而未收口，也应继续走 battle flow 的 `in_progress` 语义，而不是直接失败为 `CW_BATTLE_STATE_UNKNOWN`

## 修改边界

### 生产代码

首版最小改动面建议锁在：

- `trail/scenes/cw/stage.py`
  - 让当前 `stage.boss_preview -> boss_preview` 这一路拆成两类：
    - 真正 `boss_preview`
    - 新的 `layer_transition`
- `trail/scenes/cw/resources.py`
  - 审计并必要时拆分当前 `stage.boss_preview -> click_blank.png` 的资源绑定，避免资源名与语义继续混装
- `trail/scenes/cw/entry.py`
  - 审计所有 `stage.boss_preview` / `page=stage.boss_preview` / `_consume_click_blank_prompt()` 的使用方
  - 明确哪些路径仍然代表真正 `boss_preview`，哪些需要改成 `layer_transition`
- `trail/scenes/cw/events.py`
  - 审计 `build_cw_boss_preview_confirmer()` / `confirm_cw_boss_preview()` 与 click-blank 路径的调用边界
  - 避免只改 detector，不改现有使用方造成 token 已拆但动作仍按旧语义流转
- `trail/scenes/cw/battle.py`
  - 把 `layer_transition` 纳入 battle flow 的可续跑状态
  - 在这类页上默认继续点空白收口，而不是 `CW_BATTLE_STATE_UNKNOWN`

### 测试

- `tests/test_cw_stage.py`
  - 新增 `layer_transition` 与 `boss_preview` 的分流测试
- `tests/test_cw_battle_run.py`
  - 新增 `settle -> layer_transition -> 下一稳定阶段` 回归
  - 新增多次 `--timeout 15` 续跑撞到 `layer_transition` 时不再 `CW_BATTLE_STATE_UNKNOWN` 的回归
- `tests/test_output_rendering.py`
  - 新增或更新 `cw.stage.detect` / `cw.stage.wait` 的对外文本断言，至少覆盖：
    - `ok cw.stage.detect stage=layer_transition stale=0`
    - `ok cw.stage.wait stage=layer_transition stale=0`
  - 不新增 YAML allowlist，也不新增正文前缀
- `tests/test_skill_structure.py`
  - 更新 active skill/reference 对 stage token 集合的断言，确保 `layer_transition` 成为独立边界，而不是仍旧只枚举 `boss_preview`
- `tests/test_output_rendering.py`
  - 同时锁 README 与 active skill/reference 中 `layer_transition` 的对外 battle-flow 文案，不允许只改 token 不改说明

### 文档/场景说明

因为 `layer_transition` 是新的**对外** stage token，这次还需要同步更新，而且同步对象要点名锁死，不能只写成泛称：

- `README.md`
  - 若其中直接把这类页或 battle flow 仍解释成 `boss_preview`，必须改成 `layer_transition`
  - battle flow 说明里要明确：`layer_transition` 仍属于 `battle.run` 的继续收口范围
- `docs/cw-stage-reference/README.md`
  - 把当前这类页从旧 `boss_preview` 语义里拆出去
- `skills/trail-hsr/references/simple-command-surface.md`
  - 若其中直接描述 `battle.run` 的对外续跑语义，也要同步引入 `layer_transition`
- `skills/trail-cw-entry/SKILL.md`
  - 若其中直接描述 battle flow 或 stage 语义，也要同步 `layer_transition`
- `skills/trail-cw-prep/references/stage-boundaries.md`
  - 把当前 stage boundary 中把 `boss_preview` 视作单一“BOSS 前流程”的说法拆分，新增 `layer_transition` 作为 battle flow 过场推进态
- 其它 active skill / active surface reference 中，凡是直接把这类页当成 `boss_preview` 的地方，都要同步修正

## 测试策略

### 1. stage 层

`tests/test_cw_stage.py` 至少要覆盖：

1. `click_blank.png` 命中 + OCR 同时包含 `点击空白处继续` 与 `位面` + 不含 `本场对局首领` -> `layer_transition`
2. 真正首领预览页在 OCR 命中 `本场对局首领` 时仍保持 `boss_preview`
3. 负向保护：只有 blank-continue 模板，但没有 `点击空白处继续` / `位面` 这组首版 OCR 特征时，不应误判成 `layer_transition`
4. 负向保护：不能把 `本场对局首领` 仅当作 `layer_transition` 的排除项；它必须是 `boss_preview` 的正向判据

### 2. battle 层

`tests/test_cw_battle_run.py` 至少要覆盖：

1. `settle -> layer_transition -> 下一稳定阶段` 能继续收口
2. 多次 `--timeout 15` 续跑撞到这类页时，不再 `CW_BATTLE_STATE_UNKNOWN`
3. 不把这类页误收口成普通稳定阶段
4. 这类页必须产生实际推进动作，不能只是被当成 `unknown` 后 sleep 等待

### 3. 文档/场景说明防回归

至少锁住：

- `docs/cw-stage-reference/README.md` 已把当前这类页从旧 `boss_preview` 语义中拆出
- `skills/trail-cw-prep/references/stage-boundaries.md` 已新增 `layer_transition`，并把它与真正 `boss_preview` 分开
- `README.md`、`skills/trail-hsr/references/simple-command-surface.md`、`skills/trail-cw-entry/SKILL.md` 若直接提到该页或 battle flow，也同步使用 `layer_transition`
- 这些对外文档还要明确：`layer_transition` 仍属于 battle flow 的过场推进态，默认继续运行 `trail cw battle run --session <id>`，而不是把它当作普通 `boss_preview`、稳定阶段或手工中断点
- `tests/test_output_rendering.py` 需要锁住以上 README / stage-reference / active skill/reference 的文案同步，而不是只测 stage token 本身
- `tests/test_skill_structure.py` 需要锁住 active prep skill 的 stage boundary 集合已经包含 `layer_transition`

同时，这次不允许简单做“把所有 `boss_preview` 文案整体替换成 `layer_transition`”。真正首领预览页仍然必须保留 `boss_preview` 语义。

## 非目标

这次明确不做：

1. 不顺手抽象其它 blank-continue 页族
2. 不顺手改 battle timeout 契约
3. 不新建 battle in-progress skill 本体
4. 不把“编号圆环/层级进度结构”做成首版硬识别规则

## 推荐落地顺序

1. 先在 `stage.py` 收紧 `boss_preview` / `layer_transition` 分流
2. 再在 `battle.py` 把 `layer_transition` 纳入 battle flow 收口路径
3. 最后同步测试与文档

这样可以先把状态语义拆干净，再收 battle.run 的实际动作链，避免“只是继续点空白，但 stage token 仍然误导”或“只改 token，不改实际动作语义”的半完成状态。
