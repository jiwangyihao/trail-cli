# 货币战争首页与投资环境流程 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven development. Steps use checkbox syntax for tracking.

**Goal:** 把 `cw enter` 收口成“只到首页”，新增 `cw start` 与 `cw portal.select|refresh|restart`，并让 `guide list cw` 支持投资环境过滤。

**Architecture:** 继续沿用现有 daemon/CLI 分层。daemon 侧负责首页/投资环境/刷开局的真实状态推进与 OCR/匹配，CLI 只做 RPC 薄壳与文本协议输出。投资环境识别抽成独立 helper，先做三卡 lane 合并与 `portal_list` 匹配，再把结果缓存到 session，供 `cw.portal.select` 与 `cw.portal.restart` 复用。

**Tech Stack:** Python, Typer, daemon RPC, 现有 `trail.scenes.cw.*` 场景层, RapidOCR, 现有文本 renderer/testing harness

---

## File Structure

### Create
- `trail/scenes/cw/portal.py`
  - 投资环境页 OCR 合并、portal 匹配、结果缓存 helper。
- `trail/scenes/cw/assets/collection.png`
- `trail/scenes/cw/assets/invest_env_refresh.png`
  - 复用“未收集标志”和“刷新按钮”的模板资源，减少继续依赖硬编码点位的风险。
- `tests/test_cw_portal.py`
  - daemon-side `cw start` / `cw portal.select|refresh|restart` 与 portal matching 测试。
- `tests/test_guide_portal_filter.py`
  - `guide list cw --portal...` 的 canonical 校验、本地过滤、错误候选测试。

### Modify
- `trail/scenes/cw/entry.py`
  - 把 `enter_cw()` 收口成“只到首页”。
- `trail/scenes/cw/guide.py`
  - `guide config cw` 新增 `portal_list`；`guide list` 支持 portal 过滤所需的 detail fan-out。
- `trail/scenes/cw/models.py`
  - 增加 `scene_state["cw"]["portal"]` 作为三卡摘要缓存真相源。
- `trail/daemon/command_service.py`
  - `guide.list.cw` / `guide.config.cw` 增加 portal 相关输入与转发。
- `trail/daemon/cw_service.py`
  - 新增 `cw.start`、`cw.portal.select`、`cw.portal.refresh`、`cw.portal.restart`。
- `trail/daemon/command_service.py`
  - 注册新 daemon 方法；对 `cw enter` 改为首页语义；新 portal mutating 命令继续接 request journal。
- `trail/commands/cw.py`
  - CLI 新增 `cw start`、`cw portal.select|refresh|restart`，并把 `cw enter` 薄壳语义改成首页 only。
- `trail/commands/guide.py`
  - `guide list cw` 增加 `--portal` / `--portal-id`。
- `trail/output/rendering.py`
  - 冻结 `guide list cw --portal...` 非法 portal 的 failure 候选输出。
- `trail/output/rendering.py`
  - 新增 `cw.start` / `cw.portal.*` renderer；收紧 `cw.enter` 到 `page=home`。
- `tests/test_cw_entry.py`
  - 改写为首页语义与中间态边界测试。
- `tests/test_cw_guide.py`
  - 保留 daemon-side guide 行为测试，但移除 `guide list` 新 portal 过滤职责。
- `tests/test_guide_rpc_contracts.py`
  - `guide list cw --portal...` 的 CLI wrapper / payload / 错误契约测试。
- `tests/test_output_rendering.py`
  - `guide list cw --portal...` 非法 portal 的文本协议 golden tests。
- `tests/test_daemon_protocol.py`
  - 增加 `cw.start` / `cw.portal.*` / `guide.list.cw --portal...` 的 daemon-side协议测试。
- `tests/test_cw_rpc_contracts.py`
  - 新增新 wrapper 的 RPC 契约测试，并更新 `cw.enter` 旧语义断言。
- `tests/test_output_rendering.py`
  - 新命令文本协议 golden tests。
- `README.md`
  - 更新 Agent 流程：`cw enter` 到首页，首页询问偏好，`cw start` 到投资环境，`cw portal.*` 刷开局。
- `skills/trail-cw/SKILL.md`
- `skills/trail-cw-guide/SKILL.md`
- `skills/trail-cw-shop/SKILL.md`
- `skills/trail-cw-events/SKILL.md`
- `skills/trail-cw-replenish/SKILL.md`
- `skills/trail-cw-slots/SKILL.md`
  - 同步新心智与示例流程。

## Task 1: `guide config/list` 的 portal canonical 与过滤

**Files:**
- Modify: `trail/scenes/cw/guide.py`
- Modify: `trail/commands/guide.py`
- Modify: `trail/daemon/command_service.py`
- Modify: `trail/output/rendering.py`
- Modify: `tests/test_cw_guide.py`
- Modify: `tests/test_guide_rpc_contracts.py`
- Modify: `tests/test_output_rendering.py`
- Create: `tests/test_guide_portal_filter.py`

- [ ] 写失败测试，冻结 `guide config cw` 新增 `portal_list` 的最小 shape：`portal_id/title/description`。
- [ ] 跑红灯，确认当前 `guide config cw` 还不返回 `portal_list`。
- [ ] 最小实现 `guide config cw` 的 portal schema 扩展。
- [ ] 写失败测试，冻结 `guide list cw --portal ...` / `--portal-id ...` 的行为：
  - 双传时报错
  - `page > 1` 或 `next_page_token` 同传时报错
  - 非法 portal 返回最接近 3 个候选
  - `--portal` / `--portal-id` 可重复传入多个值
  - portal 过滤按真实上游分页不断往后抓，而不是假设 `limit=60` 就等于拿到 60 条候选
  - 上游单页实际最多返回 10 条时，也必须继续翻页直到为每个 portal 凑够 `limit`、上游 exhausted、或 hit `30 页` 保护上限
  - portal 过滤基于逐页候选的 detail fan-out，本地对每个 portal 返回最多 `limit` 条
  - guide 单条摘要要新增核心互动数据：`like`、`favour`
  - portal 模式下的 `more / next` 要反映“是否还有未扫描上游页”，不能再固定 `more=0`
  - 多 portal 请求结果按环境分组返回，不做扁平合并
- [ ] 写失败测试，冻结 daemon / CLI 转发链：
  - `trail/daemon/command_service.py` 必须把 `portal / portal_id` 透传到 guide list 过滤逻辑
  - `tests/test_guide_rpc_contracts.py` 必须覆盖 `--portal` / `--portal-id` payload 映射与互斥错误
- [ ] 写失败测试，冻结非法 portal 的默认文本失败输出：
  - `why msg=...`
  - 最多 3 条 `warn portal=<title> score=<score>`
- [ ] 写失败测试，冻结多 portal 成功输出：
  - 结果按环境分组
  - 每组都带 `portal_title / list / more / next?`
  - 单条 guide 摘要新增 `like / favour`
- [ ] 跑红灯，确认当前 `guide list` 不支持这些语义。
- [ ] 最小实现 `guide list cw` portal 过滤，并把停止条件锁成：`limit` / exhausted / 30 页上限；把 `<5s` 记成开发期性能目标而不是用户面硬错误。
- [ ] 运行：
   - `uv run pytest tests/test_guide_portal_filter.py tests/test_cw_guide.py tests/test_guide_rpc_contracts.py tests/test_output_rendering.py -q --basetemp .trail/pytest-temp-cw-portal-guide -p no:cacheprovider`
- [ ] 提交：`feat(cw): 增加攻略投资环境过滤`

## Task 2: 投资环境 OCR 合并与匹配 helper

**Files:**
- Create: `trail/scenes/cw/portal.py`
- Create: `tests/test_cw_portal.py`

- [ ] 写失败测试，冻结三卡 lane 分配、y 中心差 `<= 32` 的行合并、文本归一化、portal top1 选择和 tie-break。
- [ ] 在测试里把 helper 输入形状写死为：`ocr pieces + portal_list + collection matches -> [{card_idx, portal_title, portal_description, score, new?}]`，其中 `new` 仅在命中未收集标志时出现，避免执行者临场设计输入/输出格式。
- [ ] 跑红灯，确认 helper 尚不存在。
- [ ] 最小实现 `portal.py`：
  - OCR piece -> lane
  - lane 内行合并
  - 基于 `SequenceMatcher` 的 `title` / `title+description` 相似度计算
  - 基于 `collection.png` 模板匹配，把“未收集”标志归到对应卡片 lane
  - 产出三卡摘要：`card_idx / portal_title / portal_description / score`，命中未收集标志时额外带 `new=1`
- [ ] 跑绿灯：
  - `uv run pytest tests/test_cw_portal.py -q --basetemp .trail/pytest-temp-cw-portal-match -p no:cacheprovider`
- [ ] 提交：`feat(cw): 增加投资环境识别与匹配`

## Task 3: 收紧 `cw enter` 到首页语义

**Files:**
- Modify: `trail/scenes/cw/entry.py`
- Modify: `trail/daemon/cw_service.py`
- Modify: `trail/daemon/command_service.py`
- Modify: `tests/test_cw_entry.py`
- Modify: `tests/test_daemon_protocol.py`

- [ ] 写失败测试，冻结新状态矩阵：
  - 大世界 -> 首页成功
  - 已在首页 -> no-op 成功
  - `entry.new / entry.continue / stage.boss_preview / invest / in-game` -> 稳定报错并带页面信息
- [ ] 注意：Task 3 先只收紧 `cw enter` 的**行为语义**，daemon-side 对旧 `mode / difficulty / battle_mode` payload 可暂时容忍为 ignored input；真正移除旧参数的对外契约，放到 Task 6 与 CLI cutover 同批完成，避免 Task 3 到 Task 6 之间出现真实断档。
- [ ] 跑红灯，确认当前 `cw enter` 仍会推进到投资环境页。
- [ ] 最小实现 scene/daemon 侧的新 `cw enter` 语义。
- [ ] 运行：
  - `uv run pytest tests/test_cw_entry.py tests/test_daemon_protocol.py -q --basetemp .trail/pytest-temp-cw-enter-home -p no:cacheprovider`
- [ ] 提交：`feat(cw): 收紧 enter 到首页语义`

## Task 4: 新增 `cw start` 与 session 入口真相源

**Files:**
- Modify: `trail/scenes/cw/models.py`
- Modify: `trail/daemon/cw_service.py`
- Modify: `trail/daemon/command_service.py`
- Modify: `trail/scenes/cw/entry.py`
- Create: `tests/test_cw_portal.py`
- Modify: `tests/test_daemon_protocol.py`

- [ ] 写失败测试，冻结 `cw start`：
  - 首页 -> 推进到投资环境页并返回三卡摘要
  - 首页若仍有未收尾的当前进度（`继续进度` / `结束并结算`）-> 稳定报错，并要求 Agent 先问用户
  - 首页若在点击 `开始「货币战争」` 后才确定性暴露未收尾进度 -> 仍稳定报同一错误，但 request-status 记为 `completed` 且 `tainted=0`
  - `entry.new / entry.continue / stage.boss_preview` -> 继续推进
  - invest -> no-op 返回三卡摘要
  - in-game -> 稳定报错
  - 在 no-op 分支上，若 session 尚无 `mode/difficulty/battle_mode` 则补写；若已存在且冲突则报错
  - 成功后把三卡摘要写入 `scene_state["cw"]["portal"]`，shape 至少包含 `cards/mode/difficulty/battle_mode/stale`
- [ ] 跑红灯，确认当前不存在 `cw.start`。
- [ ] 在 `trail/scenes/cw/entry.py` 中抽出“从首页之后继续推进到投资环境页”的 scene helper，避免执行者在 `cw_service.py` 里复制旧入口链。
- [ ] 最小实现 `cw start`，并把 entry 参数持久化到 session 真相源。
  - `mode=continue` 明确表示“上一局结束后再来一局”，不是继续当前未收尾对局；如果首页仍显示 `继续进度` / `结束并结算`，必须稳定报错。
  - 若是在点击 `开始「货币战争」` 之后才暴露出未收尾进度，按稳定业务错误处理，不进入 unknown-result / tainted。
- [ ] 运行：
  - `uv run pytest tests/test_cw_portal.py tests/test_daemon_protocol.py -q --basetemp .trail/pytest-temp-cw-start -p no:cacheprovider`
- [ ] 提交：`feat(cw): 增加 start 到投资环境语义`

## Task 5: 新增 `cw portal.select|refresh|restart`

**Files:**
- Modify: `trail/scenes/cw/portal.py`
- Modify: `trail/daemon/cw_service.py`
- Modify: `trail/daemon/command_service.py`
- Modify: `tests/test_cw_portal.py`
- Modify: `tests/test_daemon_protocol.py`

- [ ] 写失败测试，冻结：
  - `cw portal.select` 只在投资环境页可用
  - `cw portal.select` 只接受 `card_idx=1|2|3`
  - session 没有最近一次三卡摘要缓存时稳定报错
  - `cw portal.refresh` 点击刷新并返回新三卡摘要
  - `cw portal.refresh` 优先使用 `invest_env_refresh.png` 模板定位刷新按钮，而不是继续依赖固定点位
  - `cw portal.restart` 依赖 session 中 entry 参数，能经由内部 helper 退局回首页后再回到投资环境页
- [ ] 明确保持现有 `cw.invest.read|choose` 不变；它们继续代表局内 invest 事件，不迁移成首页投资环境页命令。
- [ ] 先在 `trail/scenes/cw/portal.py` 明确实现并测试内部 `restart-to-homepage` helper，再在 `cw_service.py` 组合它，避免 Task 5 同时发明 helper 和 RPC 语义。
- [ ] 跑红灯，确认当前命令不存在。
- [ ] 最小实现 daemon-side 三个 portal 命令。
- [ ] 运行：
  - `uv run pytest tests/test_cw_portal.py tests/test_daemon_protocol.py -q --basetemp .trail/pytest-temp-cw-portal-ops -p no:cacheprovider`
- [ ] 提交：`feat(cw): 增加 portal 选择刷新与重开`

## Task 6: CLI cutover 与 renderer

**Files:**
- Modify: `trail/commands/cw.py`
- Modify: `trail/daemon/cw_service.py`
- Modify: `trail/output/rendering.py`
- Modify: `tests/test_cw_rpc_contracts.py`
- Modify: `tests/test_daemon_protocol.py`
- Modify: `tests/test_output_rendering.py`

- [ ] 写失败测试，冻结新的 CLI 薄壳：
  - `cw enter`
  - `cw start`
  - `cw portal.select|refresh|restart`
  - 以及 `cw enter` 的新输出首行 `page=home`
  - `cw.start / cw.portal.refresh / cw.portal.restart` 统一使用同一家族的三卡摘要输出
  - `cw.portal.select` 的成功首行 `idx=<card_idx> title=<portal_title>`
- [ ] 在 `tests/test_cw_rpc_contracts.py` 里显式新增：
  - `cw enter` 的负向契约：旧的 `--mode / --difficulty / --battle-mode` 不再属于 `cw enter`
  - `cw start` 的正向契约：必须发送 `mode / difficulty / battle_mode`
- [ ] 在 `tests/test_daemon_protocol.py` 里显式新增：
  - `cw enter` 直接收到旧字段时稳定报错
  - `cw enter` 的新 payload 只剩 `session_id`
  - `cw start` 的 daemon payload 必须接收 `mode / difficulty / battle_mode`
- [ ] 跑红灯，确认当前 wrapper 与 renderer 仍是旧语义。
- [ ] 在 `tests/test_output_rendering.py` 里显式冻结：
  - `cw.enter -> ok cw.enter page=home`
  - `cw.start` / `cw.portal.refresh` / `cw.portal.restart` -> `opt` 三卡摘要家族
  - 命中 collection 图标的卡，在第一条 `opt idx=... title=... score=...` 上追加 `new=1`
  - `cw.portal.select` -> `ok cw.portal.select idx=... title=...`
- [ ] 最小实现 CLI cutover 和文本 renderer；必要时同步收紧 daemon-side `cw.enter` 参数校验。
- [ ] 运行：
  - `uv run pytest tests/test_cw_rpc_contracts.py tests/test_daemon_protocol.py tests/test_output_rendering.py -q --basetemp .trail/pytest-temp-cw-cli-cutover -p no:cacheprovider`
- [ ] 提交：`feat(cw): 完成首页与投资环境命令 cutover`

## Task 7: README 与 skills 同步

**Files:**
- Modify: `README.md`
- Modify: `skills/trail-cw/SKILL.md`
- Modify: `skills/trail-cw-guide/SKILL.md`
- Modify: `skills/trail-cw-shop/SKILL.md`
- Modify: `skills/trail-cw-events/SKILL.md`
- Modify: `skills/trail-cw-replenish/SKILL.md`
- Modify: `skills/trail-cw-slots/SKILL.md`

- [ ] 更新 README：
  - `cw enter` 到首页
  - 首页询问偏好
  - `cw start`
  - 若首页仍有未收尾进度，先问用户是 `继续进度`、`结束并结算`，还是稍后再开新局
  - `cw portal.refresh|restart`
  - `cw portal.select`
  - `guide list cw --portal|--portal-id ...`
- [ ] 更新 skills，移除旧的“`cw enter` 直接到投资环境页”心智。
- [ ] 运行最小文档相关回归（若存在）。
- [ ] 提交：`docs(cw): 同步首页与投资环境新流程`

## Task 8: 最终回归与 smoke

**Files:**
- 无新增代码文件；只做验证

- [ ] 跑 focused regression：
  - `uv run pytest tests/test_cw_entry.py tests/test_cw_portal.py tests/test_guide_portal_filter.py tests/test_cw_rpc_contracts.py tests/test_output_rendering.py -q --basetemp .trail/pytest-temp-cw-portal-regression -p no:cacheprovider`
- [ ] 跑 fresh 全量：
  - `uv run pytest -q --basetemp .trail/pytest-temp-cw-portal-full -p no:cacheprovider`
- [ ] 手工 / 实机 smoke：
  - `cw enter` 到首页
  - 首页不再继续开局
  - `cw start` 到投资环境页并返回三卡摘要
  - 若当前页存在 collection 标志，返回摘要里对应卡应带 `new=1`
  - `cw portal.select` 可完成选卡+确认
  - `cw portal.refresh` 可刷新并返回新三卡
  - `guide list cw --portal ...` 可过滤
- [ ] 若 smoke 期间发现真实问题，先修代码再结束任务链。
