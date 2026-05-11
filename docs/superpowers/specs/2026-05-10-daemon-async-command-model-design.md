# Trail daemon 全命令异步模型设计

## 结论

废弃上一版 `trail start status=in_progress` 通过重新识别页面来“续跑”的实现，但保留它对 Agent 友好的使用方式：Agent 仍然通过重发同一条业务命令来获取运行结果。区别在于背后不再重新执行业务动作，而是 daemon 识别到同一业务命令已有运行中的 job，并返回该 job 的当前状态或最终业务结果。

所有 daemon-backed 业务命令默认进入 daemon 端异步任务模型；CLI 默认等待一段时间（建议 100 秒）以尽量直接返回最终业务结果。只有超过等待预算时，CLI 返回该业务命令的 `state=running` 输出。Agent 后续重发同一条业务命令查询结果；daemon 不会重复执行动作。

用户确认的关键决策：

- 范围：第一版按“所有 daemon 命令默认 async”设计。
- CLI 行为：默认仍等待任务完成，建议默认 `wait_timeout=100s`，避免 Agent 为普通快速命令额外轮询。
- 查询方式：Agent 使用重发业务命令的方法查询运行结果；使用上类似原续跑模型，但 daemon 内部复用同一个异步 job。
- 并发：游戏 UI 不能并行操作；如果已有运行中的游戏操作，新命令忙碌即拒绝，并明确提示当前正在运行的 request / command。
- 终止：必须提供 Agent 主动介入终止运行中命令的入口。第一阶段必须实现可生效的 soft cancel 安全点，不交付“只标记但无法停止”的假取消。

## 问题定义

当前架构中，CLI 通过 socket 向 daemon 发送请求，daemon 请求线程同步调用 `CommandService.handle()` 并等待最终 envelope。`trail start`、`cw.battle.run` 等长耗时命令可能超过客户端响应 timeout；CLI 看到的是 `DAEMON_UNAVAILABLE` / unknown result，再用 `daemon.request_status` 追查。这把“正常长运行”伪装成“传输失败”。

上一版 `trail start` 续跑方案的问题不是“Agent 重发业务命令”这个交互，而是“重发会重新执行业务动作”。新设计修正为：重发同一业务命令时，daemon 先做 job lookup；如果发现匹配 job 仍在运行，直接返回 job 状态，不重新识别、不重新点击、不重新启动。

审查后收紧一个关键边界：无显式 request id 的重发只允许命中 running job，不允许命中 terminal job。terminal result replay 必须带显式 `--request-id`，否则重复执行相同 payload 的合法动作（例如重复点击、重复商店刷新、重复买经验）会被旧结果静默吞掉。

## 目标

- daemon 端拥有一套统一 request job 模型。
- CLI 默认提交异步任务并等待最多 100 秒。
- 快速命令行为看起来仍像同步命令：如果 100 秒内完成，直接输出原业务结果。
- 长命令超过等待预算时，输出原业务命令的 running 状态和 job id。
- Agent 后续通过重发同一条业务命令查询 running job；terminal job 必须用显式 request id 查询。
- 仍保留 `daemon.request_status` / `daemon.request_result` 作为高级排障、跨命令恢复入口。
- 游戏 UI 操作默认互斥，busy 时拒绝新 UI 操作并指向当前 request。
- 所有 session / request journal 写入有明确串行边界，避免后台 worker 并发覆盖 session 文件。
- 提供可生效的 soft cancel 与明确受限的 force cancel 入口。

## 非目标

- 不继续使用“重发业务命令 = 重新执行业务逻辑”的续跑方式。
- 不让无 request id 的 terminal job replay 成为默认行为。
- 不强迫 Agent 在常规 running 查询路径改用 `daemon.request_status`；它是 fallback / advanced。
- 不让 `daemon.request_status`、`daemon.request_result`、`daemon.request_cancel`、`daemon.reconcile_session` 自身进入 async 排队。
- 不承诺所有运行中 Python 线程都能被安全硬杀；force cancel 的本质是让 daemon 明确标记 unknown/tainted 并重启或隔离执行上下文。
- 不改变最终业务命令 renderer 的字段语义。

## 标识符模型

必须分离两类 id：

- `call_id`：每次 CLI socket 调用唯一，由 client 自动生成。用于本次传输、debug 和本次失败 envelope 的 `request id`。
- `job_id`：daemon 后台业务 job 的稳定 id。首次创建 job 时生成；running 输出、busy active_request、request-status/result/cancel 都引用它。

现有代码里 `request_id` 同时承担 journal、截图和恢复查询用途。迁移时必须显式引入 `job_id` 字段，或把现有 request journal 重命名/抽象为 job journal，避免“重发查询的 call_id 覆盖 worker job_id”。默认文本中对 Agent 暴露的 `request=<id>` 指 job id；failure 行里的 `request id=<id>` 仍指本次 call id。busy 输出必须同时区分两者。

## 记录模型

### JobRecord

后台业务 job 记录：

- `job_id`
- `job_key`
- `method`
- `payload_digest`
- `workspace_root`
- `session_id`
- `state`: `accepted | running | completed | failed | cancel_requested | cancelled | cancel_unknown`
- `final`: `0|1`
- `final_state`: 保留现有恢复语义，例如 `completed | failed_before_side_effect | applied_but_not_persisted | persisted_but_response_unknown`
- `last_visible_stage`: 保留现有恢复语义，例如 `accepted | executing | side_effect_applied | state_persisted | responded`
- `side_effect_stage`: `none | applied | persisted | unknown`
- `active_command`
- `started_at`
- `updated_at`
- `tainted`: `0|1`
- `last_envelope`（终态 envelope）
- `cancel_requested_at`
- `cancel_reason`

### CallRecord

每次 CLI 提交记录，包括 rejected/busy 这种未创建新 job 的调用：

- `call_id`
- `job_id`: 若本次 call attached 到已有 job 或创建了新 job，则记录 job id；busy rejected 时为 null。
- `method`
- `workspace_root`
- `session_id`
- `state`: `submitted | attached | rejected | completed`
- `executed`: `0|1`，busy rejected 必须是 `0`。
- `rejection_code`: 例如 `DAEMON_BUSY`。
- `active_job_id`: busy rejected 时指向当前运行 job。
- `active_command`: busy rejected 时指向当前运行 command。
- `active_session`: busy rejected 时指向当前运行 job 的 session；非 session-bound job 可为 null。
- `started_at`
- `updated_at`
- `last_envelope`

`daemon.request_status --request-id <id>` 必须先查 CallRecord，再查 JobRecord：

- 查到 rejected CallRecord：返回 `executed=0 active_request=<active_job_id> active_command=<active_command> active_session=<session_id>`。
- 查到 attached/submitted CallRecord：返回对应 JobRecord 状态。
- 查到 JobRecord：返回 job 状态。

## 核心交互

### 首次运行超时未完成

```text
$ trail start
ok start.run state=running request=<job_id> waited=100
info next_action=start.run request=<job_id>
```

这里 `next_action=start.run` 表示“重发同一个业务命令查询/等待同一个 job”，不是重新执行启动。

### Agent 重发同一业务命令查询 running job

```text
$ trail start --request-id <job_id>
ok start.run state=running request=<job_id> waited=100
info next_action=start.run request=<job_id>
```

若此时后台 job 已完成：

```text
$ trail start --request-id <job_id>
ok start.run status=attached session=... reused=... title=... hwnd=...
shot path=...
info read_image_first=1
```

### Agent 不带 request id 重发

无显式 request id 时，daemon 只能命中 active/running job；如果 job 已 terminal，daemon 必须把这次调用视为新业务命令，而不是 replay 旧结果。这样同 payload 的合法重复动作不会被吞掉。

```text
$ trail cw shop refresh --session <id>
# 第一次：创建 job / 或 100s 内返回最终 refresh 结果

$ trail cw shop refresh --session <id>
# 如果第一次仍 running：返回第一次 job 的 running
# 如果第一次已 terminal：创建新的 refresh job，真实再次刷新
```

### 显式 request id

为了避免同一业务命令参数变化造成歧义，CLI 可接受并自动回填 job id：

```text
trail start --request-id <job_id>
trail cw battle run --session <session_id> --request-id <job_id>
```

默认文本里的 `info next_action=start.run request=<job_id>` 给 Agent 足够信息。Agent 常规 running 查询应复制同命令并带 `--request-id`。对于 session-bound command，`--session` 仍然必须保留；busy / request-status 输出会提供 `active_session=<session_id>`，供 Agent 组装完整命令。若没有 `active_session`，Agent 应使用 `daemon.request_result` 或 `daemon.request_status` fallback，不猜 session。

## 命令分类与锁

### Control / recovery-plane 命令

这些命令始终同步执行，不进入业务 async 队列，不占 game-operation lease：

- `daemon.status`
- `daemon.request_status`
- `daemon.request_result`
- `daemon.request_cancel`
- `daemon.logs`
- `daemon.stop/restart`（需要单独处理 active job；第一版默认在 active job 存在时拒绝 restart，提示先 cancel 或等待；stop 可作为进程级终止但必须把 active job 标记 `cancel_unknown tainted=1`）
- `daemon.reconcile_session`：同步执行，不占 game-operation lease，但必须占目标 session 的 session-mutation lease。
- 内部 `daemon.ping` / health check：同步执行，不进入 async 队列。

### Game-operation 命令

会读取或影响游戏窗口、截图、OCR、输入、CW 流程的命令。第一版全部受同一个 game-operation lease 保护：

- `start.run`
- `ocr.read`
- `screen.shot`
- `input.*`
- `window.*`
- `cw.*`
- `image.*`

第一版策略：只要一个 game-operation 正在运行：

- 如果新请求显式 `--request-id` 指向 active job，或无 id 但与 active job 是同一业务命令和同一 active job key：返回该 job 状态。
- 如果新请求是不同业务命令或参数不匹配：忙碌即拒绝，不排队。

### Session-mutation 命令

所有会读写 `.trail/sessions`、request/job/call journal、session last_result/last_screenshot 的命令必须受 session-mutation lease 保护。这个锁独立于 game-operation lease，但同一 workspace/session 的写入必须串行。

原因：现有 `SessionService` 只有部分 journal 操作加 `_mutex`，`load_session()` / `save_session()` 和 session JSON 写入不是原子写；全命令 async 后并发 worker 可能覆盖 session state 或读到半写文件。

第一阶段必须：

- 为 `SessionServiceRegistry` / workspace service 增加 per-workspace 或 per-session 写锁。
- `SessionStore.save()` 改为临时文件 + atomic replace。
- 所有 session 写入经同一锁执行。
- 纯 guide/config 等不写 session 的命令可以不占 game-operation lease；`guide.fetch.cw --select` 必须占 session-mutation lease。

## CLI / daemon control 协议

所有 daemon-backed CLI 请求增加 control 字段，不能混入业务 payload：

```json
{
  "call_id": "...",
  "job_id": "optional-new-or-resume",
  "method": "cw.battle.run",
  "payload": {...},
  "control": {
    "mode": "async_wait",
    "wait_timeout": 100
  }
}
```

现有实现迁移点：

- `trail/daemon/models.py::DaemonRequest` 增加 `call_id` / `job_id` 或新增 `RequestControl` dataclass。
- `TrailDaemonClient.call()` 支持传入 `request_id/job_id/wait_timeout/no_wait`，不再把所有 id 都私下固定成 `uuid4()`。
- `send_daemon_request()` socket body 增加 `control`，且 socket timeout 使用 `wait_timeout + transport_buffer`，与业务 execution timeout 解耦。
- `TrailDaemonServer.handle_payload()` 解析并校验 control，不把 control 写入业务 payload。
- `tests/support/fake_daemon.py` 和 CLI/RPC 契约测试同步更新，证明 control 不污染业务 payload。

CLI 默认：

- `mode=async_wait`
- `wait_timeout=100`

用户可覆盖：

- 全局：`trail --wait-timeout 15 start`
- 不等待：`trail --no-wait start`
- 续查指定 job：`trail start --request-id <job_id>` 或全局 `trail --request-id <job_id> start`
- 环境变量：`TRAIL_WAIT_TIMEOUT=15`

`--no-wait` 等价于 `wait_timeout=0`，用于测试或明确后台执行。

## job key 与重发识别

每个业务 job 建立 `job_key`：

```text
workspace_root + session_id + method + canonical_payload_digest
```

规则：

- payload digest 必须使用规范化 payload，不包含 control 字段、call id、job id、verbose。
- 对于 `start.run`，key 包含 window_title/channel/game_path。
- 对于 `cw.battle.run`，现有业务 `--timeout` 仍是 handler 执行预算，必须进入 canonical payload digest；新增 `wait_timeout` 是 control 字段，不进入 digest。
- 如果 Agent 带 `--request-id`，优先按 job id 查 job，并校验 method/session/workspace 匹配。
- 如果 Agent 不带 request id，则只按 job_key 查 active/running job；不得命中 terminal job。

重发命令时：

- 显式 job id 找到 running job：等待最多本次 `wait_timeout`；仍未完成则返回业务 command running 输出。
- 显式 job id 找到 terminal job：返回最终业务 envelope。
- 无 job id 找到 active/running job：返回 running 或等待后的最终结果。
- 无 job id 找不到 running job 且 lease 空闲：创建新 job。
- 无 job id 找不到 running job 且 lease 忙碌：busy 拒绝。

显式 job id 必须校验 method/session/workspace：

- method mismatch：返回明确冲突错误，不执行 handler。
- session mismatch：返回明确冲突错误，不执行 handler。
- workspace mismatch：返回明确冲突错误，不执行 handler。
- call_id/job_id 混淆：返回明确冲突错误，不执行 handler。

## daemon 状态机

Job 状态转移：

```text
accepted -> running -> completed
accepted -> running -> failed
running -> cancel_requested -> cancelled
running -> cancel_requested -> cancel_unknown
running -> completed   （cancel 请求到达太晚时允许）
```

新 `state` 不能替代现有 `final_state` / `last_visible_stage`。`daemon.request_status` 必须继续输出这两个 must-keep 恢复字段，或提供兼容映射。

## cancel 状态映射

| 场景 | state | final | final_state | last_visible_stage | side_effect_stage | tainted | last_envelope / next_action |
| --- | --- | --- | --- | --- | --- | --- | --- |
| cancel before worker starts | `cancelled` | `1` | `failed_before_side_effect` | `accepted` | `none` | `0` | `fail <command> code=REQUEST_CANCELLED` |
| cancel during safe wait before UI side effect | `cancelled` | `1` | `failed_before_side_effect` | `executing` | `none` | `0` | `fail <command> code=REQUEST_CANCELLED` |
| cancel after UI side effect before persisted verification | `cancel_unknown` | `1` | `applied_but_not_persisted` | `side_effect_applied` | `applied` | `1` | `fail <command> code=REQUEST_CANCEL_UNKNOWN` + reconcile 指引 |
| cancel after state persisted but before response observed | `cancel_unknown` | `1` | `persisted_but_response_unknown` | `state_persisted` | `persisted` | `1` | `fail <command> code=REQUEST_CANCEL_UNKNOWN` + request-result/reconcile 指引 |
| cancel arrives after completed | `completed` | `1` | 原 final_state | 原 last_visible_stage | 原 side_effect_stage | 原 tainted | request-cancel 输出 completed_already，不改变 last_envelope |

`REQUEST_CANCELLED` 默认不 taint；`REQUEST_CANCEL_UNKNOWN` 必须 taint，并要求 `daemon.reconcile_session` 或 `trail start` 恢复。`daemon.request_status` 必须能展示这些映射字段。显式 `--request-id` 重发业务命令或 `daemon.request_result` 查询 cancelled/cancel_unknown 终态 job 时，必须返回该 job 的 `last_envelope`，按原业务 command renderer 渲染 failure；不得重新执行 handler。

## daemon 执行模型

### RequestExecutor

新增 `RequestExecutor`，由 `TrailDaemonServer` 持有：

1. 认证与协议校验后构造 `DaemonRequest` 和 `RequestControl`。
2. 如果是 control/recovery-plane 命令，直接同步执行；`daemon.reconcile_session` 额外占 session-mutation lease。
3. 查找显式 `job_id` 或 active `job_key`：
   - terminal + 显式 job id：返回最终业务 envelope。
   - terminal + 无显式 job id：不 replay；继续按新请求处理。
   - running：等待最多 `wait_timeout`，否则返回业务 command running 输出。
4. 如果是新业务命令：
   - 检查 game-operation lease / session-mutation lease。
   - lease 空闲：创建 job，提交后台 worker。
   - lease 忙碌：创建 rejected CallRecord，拒绝并返回 busy，指向 active request / command。
5. CLI socket 线程最多等待 `wait_timeout`，不等待真实命令无限完成。

### Worker

后台 worker 调用现有业务处理逻辑，完成后写入 job store / journal。

需要把当前 `CommandService.handle()` 拆成两层：

- `handle_control_plane()`：request-status/result/cancel/reconcile 等同步命令。
- `execute_business_request()`：执行原有业务命令并产出最终 envelope。

现有 `_run_mutation()` 的 request journal 逻辑应上移或改造成由 `RequestExecutor` 管理生命周期；避免 executor 创建 job 后，内部 `_run_mutation()` 又把同一 request 判定为 duplicate in progress。

## 输出协议

### 1. 等待时间内完成

保持原业务输出，不增加额外噪声：

```text
ok start.run status=attached session=... reused=... title=... hwnd=...
shot path=...
info read_image_first=1
```

### 2. 超过等待预算仍在运行

返回业务 command 的 running 状态，保留 Agent 重发业务命令的使用方式：

```text
ok start.run state=running request=<job_id> waited=100
info next_action=start.run request=<job_id>
```

`cw.battle.run` 同理：

```text
ok cw.battle.run state=running request=<job_id> waited=100
info next_action=cw.battle.run request=<job_id>
```

注意：这里的 `state=running` 是 daemon job state，不是业务 `status=in_progress`。

Renderer 要求：

- 增加统一 async-running renderer 分支，优先截获 `data.state=running`。
- 字段顺序固定：`ok <command> state=running request=<job_id> waited=<n>`。
- `info next_action=<command> request=<job_id>` 使用既有 `info` 前缀，不新增正文前缀。
- async-running 分支必须在 workflow handoff finalize 之前全局短路；running 输出不得追加任何 `info handoff_skill=...`。必须测试 `cw.enter state=running` 和 `cw.battle.run state=running` 不触发 registry handoff。
- 对带截图最终结果仍保持 `shot path=...` 后紧跟 `info read_image_first=1`。
- `--format yaml` allowlist 不因 running 状态扩张；running 控制态默认文本足够。
- `--verbose` debug 仍只追加，不改变默认 running/final 事实集合。

### 3. 忙碌拒绝

```text
fail daemon.request_submit code=DAEMON_BUSY active_request=<active_job_id> active_command=cw.battle.run active_session=<session_id>
request id=<call_id>
why msg="another game operation is running"
recover action=cw.battle.run request=<active_job_id> session=<session_id>
```

如果 active command 是 `start.run` 且无 session：

```text
recover action=start.run request=<active_job_id>
```

要求：

- busy failure 不代表新命令已执行；它只是拒绝提交。
- busy envelope shape 固定：`data.active_request`、`data.active_command`、`data.active_session`、`data.executed=0`、`data.recover_action=<active_command>`；`error.code=DAEMON_BUSY`。
- busy rejected call 必须写入 CallRecord；`request-status <call_id>` 返回 `state=rejected executed=0 active_request=<active_job_id> active_command=<active_command> active_session=<session_id>`。
- 这是当前 `recover action=daemon.request_status` 规则的显式例外，必须同步更新 `AGENTS.md`、renderer、skills 和测试。
- `active_request` / `active_command` / `active_session` 是 failure 首行 must-keep 字段，便于 Agent 不读 debug 也能定位当前运行任务。
- Renderer 必须对 `DAEMON_BUSY` 特判：无论原始业务命令是什么，首行使用 `daemon.request_submit`，并按上述字段顺序渲染；recover 使用 `data.recover_action`、`data.active_request` 和 `data.active_session`。

### 4. request-status 作为 fallback

仍支持：

```text
trail daemon request-status --request-id <job_id_or_call_id>
```

运行中 job：

```text
ok daemon.request_status request=<job_id> command=cw.battle.run session=<session_id> state=running final=0 final_state=null last_visible_stage=executing tainted=0
info next_action=cw.battle.run request=<job_id> session=<session_id>
```

终态 job：

```text
ok daemon.request_status request=<job_id> command=cw.battle.run session=<session_id> state=completed final=1 final_state=completed last_visible_stage=responded tainted=0
info next_action=cw.battle.run request=<job_id> session=<session_id>
```

Rejected call：

```text
ok daemon.request_status request=<call_id> command=daemon.request_submit state=rejected final=1 final_state=failed_before_side_effect last_visible_stage=rejected tainted=0 executed=0 active_request=<job_id> active_command=cw.battle.run active_session=<session_id>
info next_action=cw.battle.run request=<job_id> session=<session_id>
```

Cancel unknown job：

```text
ok daemon.request_status request=<job_id> command=input.click session=<session_id> state=cancel_unknown final=1 final_state=applied_but_not_persisted last_visible_stage=side_effect_applied tainted=1
recover action=daemon.reconcile_session session=<session_id>
```

`session`、`final_state` 和 `last_visible_stage` 必须保留现有恢复契约；`state/final` 是 async job/call 层补充字段。

### 5. request-result 作为 advanced/fallback

保留新增：

```text
trail daemon request-result --request-id <job_id>
```

用于无法方便重发业务命令、或 advanced 恢复场景。它必须输出原业务 renderer。

实现方案固定为：daemon 返回 control envelope：

```json
{
  "ok": true,
  "data": {
    "render_command": "cw.battle.run",
    "envelope": {"ok": true, "data": {...}, "screenshot": "...", "image_guidance": {...}}
  }
}
```

CLI 的 `daemon request-result` 命令不得调用 `print_output("daemon.request_result", ...)` 直接渲染 control envelope；必须提取 `data.render_command` 和 `data.envelope`，然后将内层业务 envelope 原样传给 `print_output(render_command, envelope)`。不得重建 envelope，不得丢弃内层 `request_id`、`screenshot`、`image_guidance`、`debug`、`warnings`、`references`、`timing` 或 `error`。这样 `request-result` 与重发业务命令在终态输出上逐行等价，并保留截图 guidance / verbose / warning / handoff 语义。

测试必须覆盖：`request-result` 对 `start.run`、`cw.battle.run`、带截图 handoff 成功、业务 failure 的输出逐行等于 `render_output(<business_command>, last_envelope)`，并断言内层 `image_guidance/debug/warnings/references` 未丢失。

## 取消 / 终止设计

### 第一阶段必须实现的 soft cancel

命令：

```text
trail daemon request-cancel --request-id <job_id>
```

第一阶段必须提供真正可生效的 cancel token。全命令 async 默认启用不得早于 cancel token 和 session atomicity 落地。

必须实现：

- `RequestControl` / JobRecord 持有 cancellation token。
- worker 启动前检查 token。
- RuntimeService attach/launch wait loop 检查 token。
- OCR/image wait/capture retry loop 检查 token。
- CW battle loop 检查 token。
- input/click/drag/key helper 在执行 UI 动作前检查 token。
- 任何 UI action helper 在执行点击/拖拽/按键前标记 `side_effect_stage=applied`。
- cancel 后 release lease 的规则必须明确：
  - 未运行或未开始副作用：`state=cancelled tainted=0`，释放 lease。
  - 已副作用但未验证/未持久化：`state=cancel_unknown tainted=1`，释放 lease前要求状态持久化，后续必须 reconcile。
  - 已完成：cancel 返回 completed_already，不改变最终结果。

输出：

```text
ok daemon.request_cancel request=<job_id> state=cancel_requested command=start.run
info next_action=start.run request=<job_id>
```

如果 cancel 已经观察到终态：

```text
ok daemon.request_cancel request=<job_id> state=completed_already command=start.run
info next_action=start.run request=<job_id>
```

如果某个阶段无法贯穿 cancel token，则不得宣称实现了主动终止入口；只能把 `request-cancel` 作为 unsupported，并且不得默认启用全命令 async running。

### force cancel

Python 无法安全杀死某个线程。真正强制终止必须隔离到进程级，或者重启 daemon。因此 force cancel 设计为显式风险操作：

```text
trail daemon request-cancel --request-id <job_id> --force --confirm-taint
```

第一版实现 soft cancel；force cancel 返回显式 unsupported 错误：

```text
fail daemon.request_cancel code=FORCE_CANCEL_NOT_SUPPORTED
why msg="force cancel requires process-isolated workers"
```

force cancel 真正实现放到 worker 子进程隔离阶段。

## battle.run 分层修正

需要明确区分两类状态：

1. daemon job 仍在运行：
   - `ok cw.battle.run state=running request=<job_id>`
   - Agent 重发 `cw.battle.run --session <session_id> --request-id <job_id>` 查询同一个 job；daemon 不重新执行 battle.run。
2. 业务 battle flow 本轮命令已经结束，但游戏战斗流尚未完成：
   - 最终业务结果 `ok cw.battle.run status=in_progress ...`
   - Agent 可按业务协议发起下一次新的 `cw.battle.run` 请求。

文档必须禁止把 daemon `state=running` 误判为业务 `status=in_progress`。

## 迁移计划

### 第一阶段：协议、锁、cancel 与 executor 基础

全命令 async 默认启用必须等本阶段全部完成。

- 新增 `RequestControl` / `JobRecord` / `CallRecord` / `RequestExecutor`。
- 扩展 client/server/fake daemon 支持 control 字段、call id、job id、wait timeout。
- socket timeout 与 `wait_timeout + transport_buffer` 绑定，并与业务 execution timeout 解耦。
- control/recovery-plane 命令保持同步；`daemon.reconcile_session` 占 session-mutation lease。
- 增加 session-mutation lease、game-operation lease。
- `SessionStore.save()` 改为 temporary file + atomic replace。
- 所有 session/job/call journal 写入纳入串行锁边界。
- 实现 cancellation token 和第一批安全点：RuntimeService、OCR/image wait、capture retry、CW battle loop、input helper。
- 实现 `daemon.request_cancel` soft cancel；force cancel 返回 unsupported。
- 快速命令仍在 wait_timeout 内直接返回原业务结果。
- 增加 async-running renderer 分支。

### 第二阶段：job lookup 与业务命令重发查询

- 实现 job_key active/running lookup。
- 明确无 request id 不 replay terminal job。
- 实现显式 `--request-id` 查询 running/terminal job。
- 实现 busy rejected CallRecord 和 `DAEMON_BUSY` renderer 特判。
- 实现 request-status 对 CallRecord/JobRecord 的分发。
- 实现 request-result 的 `render_command/envelope` CLI 分发。

### 第三阶段：文档、技能与默认 async 原子切换

本阶段必须按原子切换执行；默认 async enable 是最后一步。

- 更新 README / AGENTS / active skills / routing evals。
- 先让所有文档、skills、routing/eval 测试通过，证明 Agent 已理解 `state=running`、busy、cancel、request-result。
- 所有 daemon-backed 业务命令默认 `async_wait`。
- 默认 `wait_timeout=100`。
- 保留 `daemon.request_result` 作为 advanced/fallback。

### 第四阶段：force cancel / worker 隔离

- 若需要真正强杀，迁移长耗时 worker 到子进程。
- `--force --confirm-taint` 杀子进程并标记 tainted。

## 测试计划

- executor accepted/running/completed 状态机单测。
- `wait_timeout=0` 立即返回 `ok <command> state=running`。
- `wait_timeout=100` 且快速 handler 完成时返回原业务 envelope。
- 重发同业务命令命中 running job，不重复调用 handler。
- 显式 `--request-id` 在 job terminal 后返回最终业务 renderer。
- 无 request id 不 replay terminal job；重复 `input.click` / `cw.shop.refresh` 创建新 job。
- `cw.battle.run --timeout` 作为业务 payload 进入 digest；`wait_timeout` 不进入 digest。
- 显式 job id 的 method mismatch、session mismatch、workspace mismatch、call_id/job_id 混淆均返回明确冲突错误，且 handler 未被调用。
- 不同业务命令 busy 拒绝携带 active request / command / session。
- busy rejected call 可 request-status 查询，且 `executed=0`、`active_request`、`active_command`、`active_session` 正确。
- request-status running/completed/rejected/cancelled/cancel_unknown 保留 `session`、`final_state`、`last_visible_stage`、`side_effect_stage`、`tainted`。
- request-result 对 start.run、cw.battle.run、带截图 handoff 成功、业务 failure 的输出逐行等于原业务 renderer，并保留内层 image_guidance/debug/warnings/references。
- request-cancel soft cancel 状态流。
- cancel before side-effect 不 taint；cancel after side-effect 标记 tainted。
- late cancel / completed_already 不改变 last_envelope。
- cancel_unknown 在 release lease 前完成状态持久化并要求 reconcile。
- `daemon.reconcile_session` 不进入 async 队列、不创建 JobRecord、不返回 running。
- active game-operation 存在时 `daemon.reconcile_session` 仍可同步处理，且不会被 game-operation busy 拦截。
- 同 session 写锁被占用时，`daemon.reconcile_session` 与业务 session mutation 串行互斥。
- session store atomic save 与并发写锁测试。
- start.run async 不再需要业务 `status=in_progress` 重新识别续跑。
- cw.battle.run 区分 daemon `state=running` 与业务 `status=in_progress`。
- async running 不触发 workflow handoff；覆盖 `cw.enter state=running` 和 `cw.battle.run state=running`。
- 文档/skill 契约更新，要求 Agent 在 daemon `state=running` 时重发同业务命令查询，不重新执行。
- `tests/support/fake_daemon.py`、`tests/test_atomic_commands.py`、`tests/test_cw_rpc_contracts.py` 覆盖 control 字段不污染业务 payload。
- CLI 参数矩阵覆盖全局 `--wait-timeout`、`--no-wait`、全局/命令级 `--request-id`、`TRAIL_WAIT_TIMEOUT`。
- `tests/test_output_rendering.py` 覆盖 async running、busy recover 例外、request-status 新旧字段兼容、request-result/cancel 输出。
- `tests/test_skill_structure.py` / routing tests 覆盖 request-result/cancel 不误触 scene skills。

## 需要同步更新的文档

- `AGENTS.md`：新增 async 输出协议、busy、cancel、request-result、业务重发查询语义、busy recover 例外。
- `README.md`：新增默认 wait、业务命令重发查询、request-status/result/cancel fallback。
- `skills/trail-hsr/SKILL.md`
- `skills/trail-hsr/references/simple-command-surface.md`
- `skills/trail-hsr/references/start-run-status-handling.md`
- `skills/trail-hsr/references/ocr-and-screenshot.md`
- `skills/trail-hsr-advanced/SKILL.md`
- `skills/trail-hsr-advanced/references/advanced-command-surface.md`
- `skills/trail-hsr-advanced/references/request-status-and-taint.md`
- `skills/trail-hsr-advanced/references/recovery-ladder.md`
- `skills/trail-cw-entry/SKILL.md`
- `skills/trail-cw-prep/SKILL.md`
- `skills/trail-cw-prep/references/command-surface.md`
- `skills/trail-cw-guide/SKILL.md`
- routing/eval tests：确保 `daemon.request_result` / `daemon.request_cancel` 不错误触发 scene skills。

## 需要明确的实现决策

- `wait_timeout` CLI 位置：推荐全局 `trail --wait-timeout 15 <command>`，并支持 env `TRAIL_WAIT_TIMEOUT`。
- running 首行：使用原业务 command：`ok <command> state=running request=<job_id> waited=<n>`。
- 业务重发查询：支持全局或命令级 `--request-id`；无 id 时只命中 active/running job，不命中 terminal job。
- busy：failure 首行带 `active_request`、`active_command` 和 `active_session`，写入 rejected CallRecord，并用业务命令 recover action 指向 active request；这是恢复语义的显式例外，必须更新 AGENTS 和测试。
- request-result：返回 `{render_command, envelope}`，CLI 用 `print_output(render_command, envelope)` 渲染原业务结果，内层业务 envelope 必须原样透传。
- force cancel：第一版实现 soft cancel；force cancel 先作为显式 unsupported，真正 force 进入 worker 子进程隔离阶段。
