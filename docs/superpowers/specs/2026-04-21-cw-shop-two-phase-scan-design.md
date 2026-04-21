# CW 商店两段式扫描设计

## 背景

当前 `cw.shop.scan` 在商店弹窗打开时可以读取商品名称、价格、金币和等级，但 `max_team_size` 在该界面上不可稳定读取。用户提供的两张截图表明：

- `\.trail\shots\2033ad2b66ff4947b0b6644f94f3a6c1-f6e3c2d5f4.jpg`：商店弹窗已打开，适合读取商品与价格。
- `\.trail\shots\3e2fae9b5be74f99bbfce0953d9d1ec7-7ed1d69ed6.jpg`：备战阶段无弹窗且商店关闭，能直接看到 `3/3` 队伍容量。

结论：`max_team_size` 不是同一张商店弹窗截图里的 OCR 问题，而是当前界面根本没有稳定可读的目标。要想在一次 `cw.shop.scan` 里同时拿到商品快照和队伍容量，必须显式切换界面状态。

## 目标

只改 `cw.shop.scan`，让它在一次命令中稳定完成：

1. 退回无弹窗备战页。
2. 读取队伍容量。
3. 重新打开商店。
4. 读取商品、价格、金币、等级。
5. 合并成同一份 shop 快照写回 session。

## 非目标

- 不修改 `cw.shop.status` 的语义，仍然只读当前缓存。
- 不改 `cw.shop.open` / `cw.shop.close` / `cw.shop.refresh` / `cw.shop.buy_slot` 的行为。
- 不在这次改动里重构 renderer、协议文本或 guide 流程。

## 方案概览

采用“两段式扫描”方案，只在 `cw.shop.scan` 内执行：

1. 点击前台区域与后台区域之间的空白位置，关闭可能存在的商店弹窗或其它遮挡层。
2. 在无弹窗备战页的中心区域读取 `3/3` 这类队伍容量，提取上限值作为 `max_team_size`。
3. 点击商店按钮 `1630 992`，确保重新进入商店弹窗。
4. 使用现有“纯商店页 OCR”逻辑读取商品、价格、金币、等级。
5. 将两步结果组装成一次新的 shop snapshot。

## 代码边界

保持 `scan_cw_shop()` 的职责不变：它仍然只负责把 scan 流程产出的最终快照写入 `session.scene_state["cw"]["shop"]`。

不能把“两段式交互”直接塞进现有 `build_cw_shop_scanner(runtime)`。原因是这个 factory 还会被 `cw.shop.buy_slot` 的购买后确认复用；一旦把“点空白 -> 读队伍容量 -> 再开商店”的副作用塞进去，会无意改变 `buy_slot` 行为。

因此需要新增一个 **仅供 `cw.shop.scan` 使用** 的 scan 专用 orchestrator / snapshot reader，例如 `build_cw_shop_scan_snapshot_reader(runtime)`，内部负责：

- 点击空白位并等待 UI 收拢。
- 读取队伍容量。
- 点击商店按钮并等待商店弹窗稳定出现。
- 调用现有纯商店页扫描逻辑读取 `items/coins/level/reserve_full`。
- 组装最终 snapshot，并显式写出 `opened=True`。

原有 `build_cw_shop_scanner(runtime)` 保持“纯商店页扫描器”定位不变，这样：

- `cw.shop.scan` 会获得新的扫描流程。
- `cw.shop.status` 仍然是纯读取，不会附带点击副作用。
- 现有 `open` / `close` / `refresh` / `buy_slot` 不会被顺带改坏。

## 识别点与解析规则

### 1. 关闭弹窗点击点

- 新增一个固定点击点，落在前台区域与后台区域之间的空白地带。
- 目的不是“关闭商店命令”，而是把当前界面强制收敛到“无弹窗备战页”基线状态。

### 2. 队伍容量 OCR 区域

- 新增一个针对备战页中心 `3/3` 文本的 OCR 区域。
- 解析规则优先匹配 `a/b` 形态，读取右侧上限值 `b`。
- 必须显式覆盖噪声场景：当 OCR 结果里混入 `1-1`、`80`、`Lv.3`、`0/4`、左侧词条的 `1/2` / `1/3` 等无关数字时，不能误把这些数字当成 `max_team_size`。
- 必须覆盖 token 分裂/合并场景，例如 `3/3` 被 OCR 成单 token、被拆成 `3` `/` `3`、或被其它文本黏连时的处理规则。只有在文本内容和位置信息都足以稳定证明这些 token 共同组成队伍容量时才写入上限值；否则一律返回 `None`。
- 如果该区域 OCR 失败，则 `max_team_size=None`，但不阻断后续重新打开商店并扫描商品。

### 3. 商店页 OCR 区域

- 商品、价格、金币、等级继续沿用现有商店页扫描逻辑。
- 其中 tuple 文本读取与 level 解析边界已经在本轮修复中收紧，避免把置信度或 `0/4` 进度误读成业务字段。

## 数据流

`cw.shop.scan` -> scan-specific orchestrator / snapshot reader

1. `click(blank)`
2. `wait_for_settle(after_blank_click)`
3. `ocr(team_size_region)` -> `max_team_size`
4. `click(shop_button)`
5. `wait_for_settle(after_shop_open)`
6. `pure_shop_scanner()` -> `items/coins/level/reserve_full`
7. 组装最终 snapshot，其中 `opened=True`
8. `scan_cw_shop()` 写回 `{items, coins, level, reserve_full, max_team_size, opened=True, stale=False, ...}`

最终持久化的 shop snapshot 必须只反映“重开商店后的最终状态”，不能泄漏起始界面差异。也就是说，无论命令开始时商店本来是开着还是关着，写回后的关键字段都必须收敛到同一份最终快照，至少包括：`opened=True`、`stale=False`、`items`、`coins`、`level`、`reserve_full`、`max_team_size` 与 `guide_summary`。

## 失败与容错

- 若“关闭弹窗”点击后仍读不到队伍容量：保留 `max_team_size=None`，继续扫描商店商品。
- 若重新打开商店后商品 OCR 失败：沿用当前 `cw.shop.scan` 的失败链路，不吞错误。
- 若命令开始时商店本来就是关闭状态：第 1 步点击空白区应是幂等的，不应破坏后续流程。
- 若命令开始时商店已打开：第 1 步应把它收起，确保能读到备战页 `3/3`。
- 因为 `cw.shop.scan` 现在含有点击副作用，所以它必须转入 mutation 语义：要么进入 `CW_MUTATING_METHODS`，要么有等价的 request journal / unknown-result / tainted 保护链路。不能继续把它当纯读取命令处理。
- 只要点击已经发生、后续 OCR 或持久化失败，就必须走与副作用相匹配的失败/恢复语义，而不是静默降格为普通读取失败。

## 测试计划

至少补以下回归：

### Unit 级

1. scan 专用 orchestrator 的调用顺序测试：必须先点击空白区，再等待稳定，再读队伍容量，再点击商店按钮，再等待稳定，再调纯商店页 scanner。
2. 队伍容量解析正例：`3/3`、`4/6` 等文本能正确提取右侧上限值。
3. 队伍容量解析反例：OCR 结果里只有 `1-1`、`80`、`Lv.3`、`0/4`、`1/2`、`1/3`、无法通过位置关系确认为同一分数的拆裂 token、畸形分数或其它噪声时，必须返回 `None`。
4. 容错测试：队伍容量缺失时不阻断商品扫描，但 `max_team_size` 明确为 `None`。
5. 纯商店页 scanner 保持无副作用，确保 `cw.shop.buy_slot` 复用它时不会意外执行“关店再开店”。

### Orchestration / state 级

6. 合并结果测试：同一次扫描能同时写入 `items/coins/level/reserve_full/max_team_size`。
7. 起始状态收敛测试：从“商店已开”和“商店已关”两种输入状态开始，最终持久化的完整 shop snapshot 必须一致，而不是只比较局部字段。
8. `opened/stale` 断言：两段式扫描完成后的最终 snapshot 必须表达“商店当前已重新打开且快照新鲜”。
9. `cw.shop.scan` 的 mutation 语义测试：一旦发生点击后失败，daemon 必须进入与副作用匹配的 unknown-result / tainted / recover 路径，而不是继续按纯读取命令处理。

### Image-backed 回归

10. 不能只用 stubbed `runtime.ocr()` 返回值和点击日志做伪回归；必须把用户提供的两张截图纳入可重复的 image-backed 最小复现链路，证明：
   - 商店打开图能稳定读到商品/价格/金币/等级。
   - 商店关闭图能稳定读到 `3/3` 队伍容量。
   - 基于真实截图的区域与解析规则不会把无关数字误判成 `max_team_size`。

### 契约与文档同步

11. 若实现不改 renderer 输出，也要补守卫测试确认 `cw.shop.scan` **成功路径** 文本协议不变，包括 `opened=1 stale=0`、`shot` 行位置和正文顺序。
12. 同时必须补 `cw.shop.scan` **失败路径** 的 renderer / RPC 合约测试，确认带副作用失败时仍保留 `fail cw.shop.scan ...`、`request id=...`、`recover action=daemon.request_status ...` 以及 AGENTS.md 规定的 failure 行顺序。
13. 若 agent 使用前提发生变化，需同步更新 `skills/trail-cw-shop/SKILL.md`；README / help 若无需调整，也要在实现说明里明确“为何不需要改”。

## 风险

- 中间空白点击点如果偏得不够稳，可能无法关闭部分弹窗，需要用用户提供截图反复校准。
- `3/3` 文本区域如果被其它 UI 遮挡，`max_team_size` 仍可能为空；这次设计允许为空，但不会再错误读取商店词条。
- 该方案引入了 `cw.shop.scan` 的额外交互副作用，因此实现时必须确保只影响 `scan`，不外溢到 `status`。

## 验证方式

- 将用户提供的两张截图接入可重复的 image-backed 测试/最小复现链路，而不是只保留为手工查看素材。
- 用商店打开图验证商品/价格/金币/等级，用商店关闭图验证队伍容量，并显式覆盖无关数字噪声不会误判成 `max_team_size`。
- 运行至少以下验证集：
  - `uv run pytest --basetemp .trail/pytest-tmp/tests tests/test_cw_shop.py -q`
  - 与 `cw.shop.scan` 文本协议相关的 renderer / RPC 合约回归测试
  - 如有变更，补 README / skill 文档同步检查
