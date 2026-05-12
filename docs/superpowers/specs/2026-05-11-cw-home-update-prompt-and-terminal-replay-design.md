# CW 首页更新提示与 daemon terminal replay 修正设计

## 结论

本次修正拆成两个独立但相关的行为边界：

1. `cw.enter` 必须把「积分线已更新」视为货币战争首页变种，而不是局内阶段。命中后点击下方中间空白区域关闭提示，重新确认仍处于首页，再按首页 no-op 成功返回。
2. daemon 无显式 request id 的业务命令重发只允许复用当前 executor 内确认为 live running 的 job。所有 terminal job（无论成功、失败、取消、unknown）都不允许被同 payload 的普通业务命令 replay；没有 live worker 的非终态持久化 job 也不得被当作 running replay。terminal 结果只能通过显式 request id 查询。

用户已确认：`cw.enter` 允许在积分线更新界面执行一次 dismiss；所有确认命令结束的状态都不允许 replay，只有进行中状态允许 replay。

## 问题定义

### CW 首页更新提示误判

实机截图显示当前页面是货币战争首页：左上为「货币战争 / 零和博弈」，右下为 `开始「货币战争」`，中部横向提示条为「积分线已更新」。这是每周首次进入货币战争首页时出现的奖励 / 积分线更新提示。

当前 `cw.enter` 在这个页面返回：

```text
fail cw.enter code=CW_ENTER_ALREADY_PAST_HOME
why msg="cw enter only supports world or home, current page: in_game, stage: game_over"
```

这不是正确分类。该页面仍是首页，只是被 transient 提示遮挡。`cw.enter` 本来支持在货币战争首页 no-op，因此该变种也应由 `cw.enter` 自行收口。

真实误判入口不只来自 stage resource fallback。当前代码还会在 `entry.start` 可见时，如果 session 记录了 `cw.stage.value == "game_over"` 且 `stale is False`，优先返回 `in_game/game_over`。因此 update prompt 的首页证据必须能覆盖这种 recorded stale=false `game_over` 的首页误判；但不能覆盖未结束进度、结算页、`entry.new`、`entry.continue`、`invest` 这类真实已过首页或需要保护的状态。

### reconcile 后同命令仍返回旧错误

实机过程中，先执行：

```bash
uv run trail daemon reconcile-session --session <session>
```

返回：

```text
ok daemon.reconcile_session session=<session> tainted=0
```

随后重新执行相同 `cw.enter`，仍返回：

```text
fail cw.enter code=SESSION_RECONCILE_REQUIRED
```

检查 session 文件可见 `scene_state.daemon.tainted=false`，相关 call / job 记录也显示 `tainted=false`。因此不是 reconcile 没有清除 tainted，而是 daemon 异步 job replay 了旧 terminal envelope。

## 目标

- `cw.enter` 在普通首页继续 no-op 成功。
- `cw.enter` 在「积分线已更新」首页变种上点击空白区域关闭提示，重新确认首页后成功返回。
- `cw.start` 在点击开始前也能处理同一提示，保证直接 start 路径不被遮挡。
- `_detect_current_enter_page()` 不再把首页更新提示误分类为 `in_game/game_over`，包括 session recorded stale=false `game_over` 误判。
- update prompt 只覆盖首页误判 fallback，不覆盖未结束进度、结算页、`entry.new`、`entry.continue`、`invest` 等高置信页面。
- daemon 无显式 request id 的业务命令重发只复用当前 executor 内有未完成 future 或 paused job 的 live running job。
- terminal job 不再通过普通业务命令 replay；没有 live worker 的非终态持久化 job 也不 replay。
- 用户若要查看 terminal 结果，必须使用显式 request id，例如 `daemon.request_result` 或支持 request id 的查询入口。
- 保留当前 request / job / call journal 的可追溯性，不删除历史记录。
- 同步更新 active Agent skill 文档与契约测试，避免继续指导 Agent 通过普通业务命令 replay terminal 结果。

## 非目标

- 不新增 renderer 字段或默认文本协议字段。
- 不把「奖励」「更新内容」这类泛词作为独立触发条件。
- 不修改 `trail/scenes/cw/stage.py` 的局内 stage detector 语义；首页提示不属于局内 stage。
- 不改变 `daemon.request_status`、`daemon.request_result`、`daemon.request_cancel`、`daemon.reconcile_session` 作为 control-plane 同步命令的定位。
- 不支持对已结束 terminal job 的无 request id 业务命令查询。
- 不删除或改写历史 terminal job 文件；历史结果仍需可通过显式 request id 查询。
- 不把没有当前 executor live worker 的非终态持久化 job 当作可普通重发续查的 running job；这类孤儿记录只能通过 control-plane 恢复 / 排障。

## CW 首页更新提示设计

### 页面分类

在 `trail/scenes/cw/entry.py` 中把首页更新提示建模为 home page variant。

推荐新增常量：

```python
HOME_UPDATE_PROMPT_KEYWORDS = ("积分线已更新",)
HOME_REWARD_PROMPT_KEYWORDS = ("积分奖励",)
HOME_PROMPT_DISMISS_MAX_CLICKS = 2
HOME_UPDATE_PROMPT_REGION = {"from_x": 520, "from_y": 420, "to_x": 1160, "to_y": 600}
HOME_UPDATE_PROMPT_DISMISS_POINT = (1450, 580)
HOME_UPDATE_PROMPT_DISMISS_SETTLE_SECONDS = 0.8
```

检测规则：

- 主要方式：对中部提示条区域做 OCR，命中 `积分线已更新` 才认为存在首页更新提示。
- 只有 runtime 不支持 region/capture OCR 时才允许退化为全屏 OCR；退化时必须同时具备首页锚点，例如 `entry.start` 可见，或刚从 `_enter_from_world()` 入口链等待到 `entry.start`。
- 无首页锚点时，即使全屏 OCR 命中 `积分线已更新`，也不得返回 `home/update_prompt`，不得点击 dismiss。
- OCR 异常时返回未命中，不能抛出阻断 `cw.enter`。
- 若模板锚点缺失，但全屏 OCR 明确命中首页 / 积分页稳定字段（例如 `货币战争`、`零和博弈`、`创业指南`、`积分奖励`、`当前积分`），可作为首页锚点；该规则只用于覆盖已知首页/积分页对 `entry.start` 模板不稳定的实机情况。
- 不用 `奖励`、`更新内容`、`开始` 等泛词单独触发。
- dismiss 点击必须只在 update prompt 强哨兵命中后执行。

### 页面检测优先级

`_detect_current_enter_page()` 应明确区分高置信页面与首页误判 fallback。

推荐优先级：

1. 定位 `entry.start` / `entry.continue`。
2. 如果可见首页锚点，读取首页 OCR 文本。
3. 如果 OCR 显示未结束进度（`继续进度` + `结束并结算` / `当前进度`），返回 `{"page": "home", "unfinished_progress": "1"}`，不得被 update prompt 覆盖。
4. 检测 `entry.new` / `entry.continue`，这些是真实 pre-invest 页面，不得被 update prompt 覆盖。
5. 检测 `entry.invest_environment`。
6. 检测结算链页面。
7. 如果存在首页锚点且命中 `积分线已更新`，返回 `{"page": "home", "update_prompt": "1"}`。
8. 再进入 stage detector、OCR stage、stage resource fallback、session recorded `game_over` fallback。

该顺序保证 update prompt 能覆盖 session recorded stale=false `game_over` 这种首页误判，但不会绕过未结束进度保护或真实已过首页状态。

### `cw.enter` 行为

`enter_cw()` 收到 `{"page": "home", "update_prompt": "1"}` 后：

1. 调用 `_dismiss_home_update_prompt(runtime)`。
2. 第一次点击 `HOME_UPDATE_PROMPT_DISMISS_POINT` 关闭 `积分线已更新`。
3. 等待 `0.8s`。
4. 重新读取提示区域；若出现后续 `积分奖励` 提示，再点击同一空白点一次并再次等待 `0.8s`。
5. 最多点击两次；两次后仍命中 `积分线已更新` 或 `积分奖励` 时返回可恢复错误，不得把不确定状态写成已在首页。
6. 确认成功后记录 entry 为首页。

推荐记录：

```python
{"page": "home", "already_home": True, "dismissed_update_prompt": True}
```

`dismissed_update_prompt` 用于 session 诊断，不新增默认文本字段；如果实现选择不保存该字段，测试不得依赖它。规格只要求默认输出仍走现有 `cw.enter` renderer，不新增默认文本字段。

普通首页仍保持原有 no-op 行为，不点击、不等待。

### `cw.start` 行为

`cw.start` 从首页点击开始前调用同一个 dismiss helper，但必须遵守状态保护：

1. 初次 `_detect_current_enter_page()` 若返回 `unfinished_progress`、结算页、`entry.new`、`entry.continue`、`invest`，按原有逻辑处理，不被 update prompt 覆盖。
2. 若返回 `home/update_prompt`，先 dismiss。
3. dismiss 后重新运行页面检测或至少重新定位 `entry.start` 并重新检查未结束进度。
4. 只有重新确认仍是干净首页时，才点击 `entry.start`。
5. 重新确认失败时不得点击 `entry.start`，应返回可恢复错误。
6. 后续 battle mode / difficulty / invest flow 保持不变。

无提示时 helper 必须完全 no-op，不额外点击，不额外等待。

### 点击点选择

`(1450, 580)` 是 1920×1080 画面右侧信息区域附近的空白 / overlay 关闭安全点（实机验证 `(960, 900)` 不能关闭该提示）：

- 位于中部提示条外，可触发 dismiss。
- 远离右下 `开始「货币战争」` 按钮。
- 远离左侧首页菜单。

该点必须作为命名常量，而不是从按钮位置推导，避免不同按钮匹配框导致点击漂移。

## daemon terminal replay 设计

### 当前根因

`RequestExecutor.handle()` 在创建新 job 前会调用 `_singleton_job_response()`。

现有逻辑会通过 `find_latest_job_for_method()` 找到最新同 method job，并在 `job_key` 匹配时直接返回 `_job_response()`。该逻辑没有排除 terminal job，导致同 payload 的业务命令会 replay 上一次 terminal envelope。

这与已确认的新规则冲突：普通业务命令重发只应用于 running job 查询，不应用于 terminal job 查询。

### 新规则

无显式 request id 的普通业务命令：

| 最新匹配 job 状态 | 行为 |
| --- | --- |
| 当前 executor 内存在未完成 `future` | 允许复用，返回 running 状态或等待结果 |
| 当前 executor 内存在 paused job | 允许复用，返回 running 状态或等待结果 |
| `final=false` 但没有 live `future` / paused job | 不 replay；创建新 job，或在显式 control-plane 查询中报告原记录状态 |
| `final=true` / terminal success | 不 replay，创建新 job |
| `final=true` / terminal failure | 不 replay，创建新 job |
| `final=true` / terminal cancelled | 不 replay，创建新 job |
| `final=true` / terminal unknown | 不 replay，创建新 job |

显式 request id 查询路径保持不变：

- `daemon.request_status --request-id <job_id>` 查询状态。
- `daemon.request_result --request-id <job_id>` 查询结果。
- 对支持 `--request-id` 的命令，显式 request id 仍可读取对应 job，但不能把普通重发当 terminal result 查询。

“普通业务命令”包括进入 async executor 的游戏 / 非游戏业务请求，例如 `cw.*`、`input.*`、`ocr.read`、`screen.shot`、`guide.*` 等。该规则不覆盖 `state.dump` 这种同步直读入口，也不覆盖 `daemon.request_status`、`daemon.request_result`、`daemon.request_cancel`、`daemon.reconcile_session`、`daemon.ping` 等 control-plane 命令。

### 推荐实现点

修改 `trail/daemon/request_executor.py`：

- `_latest_singleton_job()` 只返回当前 executor 内确认为 live running 的 job。
- `_singleton_job_response()` 只处理 live running job。
- 内存 `_job_metadata` 分支不能仅检查 `metadata.get("final")`，还必须检查对应 future / paused job 是否仍 live。
- 如果内存 metadata 显示非 final，但 future 已完成或不存在，不得回读持久化非终态记录并当作 replayable running；应返回 `None`，允许创建新 job。
- 持久化 `find_latest_job_for_method()` lookup 不应再作为无 request id singleton replay 的依据，除非还能证明该 job 在当前 executor 内有 live worker。

推荐集中封装 helper，例如：

```python
def _is_replayable_running_job(self, job_id: str, metadata: dict | None) -> dict | None:
    if metadata is None or metadata.get("final"):
        return None
    future = self._futures.get(job_id)
    if future is not None and not future.done():
        return deepcopy(metadata)
    if job_id in self._paused_jobs:
        return deepcopy(metadata)
    return None
```

持久化 lookup 只能用于显式 request id 查询、control-plane 状态查询或恢复排障，不能用于普通业务命令无 request id terminal / orphan job replay。

### 与显式 request id 的关系

`_explicit_job_response()` 不受影响。只要用户传入明确 job id，就仍按 request id 查询旧 job。这样保留审计与恢复能力，同时避免普通业务命令被旧 terminal 结果吞掉。

第二次无 request id 重发产生新 job / call 后，旧 terminal job 仍必须可通过 `daemon.request_status` 与 `daemon.request_result` 使用显式 request id 查询。

## 文档与契约同步

该变更会影响 Agent 使用方式，必须同步更新 active skill 文档和相关测试。

必须更新：

- `skills/trail-hsr/references/async-command-model.md`
  - 删除或改写“旧 job 已终态时也回放终态业务输出，不重新执行同 payload mutation”。
  - 明确：普通业务命令重发只用于 running job；terminal 结果必须通过显式 job id 的 control-plane 查询。
- `skills/trail-hsr/SKILL.md` 若引用该规则无需改正文，但要确保引用文档语义一致。
- `tests/test_skill_structure.py` 中与旧文案绑定的断言。
- `docs/superpowers/specs/2026-05-10-daemon-async-command-model-design.md` 是历史设计记录，不作为契约来源；按项目规则不得为 specs/plans 写测试，也不要求回改旧 spec。

## 测试计划

### `tests/test_cw_entry.py`

新增或调整：

1. `test_enter_cw_home_update_prompt_dismisses_and_returns_home`
   - OCR 返回 `积分线已更新`。
   - 模拟当前页面是货币战争首页变种。
   - 断言点击 `HOME_UPDATE_PROMPT_DISMISS_POINT`。
   - 断言 dismiss 后发生二次首页确认（例如再次定位 `entry.start`）。
   - 断言 `enter_cw()` 不抛 `CW_ENTER_ALREADY_PAST_HOME`。
   - 断言 session `scene_state["cw"]["entry"]` 记录为 home。

2. `test_enter_cw_home_update_prompt_reconfirm_failure_is_recoverable_error`
   - OCR 返回 `积分线已更新`。
   - dismiss 点击后 `entry.start` 不可见，或 update prompt 仍无法确认消失。
   - 断言 `enter_cw()` 返回 / 抛可恢复错误。
   - 断言不把 `scene_state["cw"]["entry"]` 写成 home。

3. `test_detect_current_enter_page_prioritizes_home_update_prompt_over_recorded_game_over`
   - session 中保留 `scene_state["cw"]["stage"] = {"value": "game_over", "stale": False}`。
   - 页面可见 `entry.start`。
   - OCR 命中 `积分线已更新`。
   - 断言 `_detect_current_enter_page()` 返回 `{"page": "home", "update_prompt": "1"}`。
   - 断言 `enter_cw()` dismiss 后成功，而不是返回 `CW_ENTER_ALREADY_PAST_HOME stage=game_over`。

4. OCR 区域 / 首页锚点测试。
   - 断言 update prompt 检测调用 OCR 时使用 `HOME_UPDATE_PROMPT_REGION`。
   - 当没有首页锚点时，即使 OCR 返回 `积分线已更新`，也不得点击 dismiss，不得返回 `home/update_prompt`。
   - 如果实现包含全屏 OCR fallback，测试必须证明 fallback 只有在 runtime 不支持 region/capture 且具备首页锚点时才生效。

5. 保留并调整旧行为测试。
   - `test_enter_cw_rejects_game_over_state_recorded_in_session` / `test_enter_cw_prefers_recorded_game_over_over_home_on_shared_start_resource` 仍应覆盖无 update prompt 时 recorded `game_over` 的保护行为。
   - 新增 update prompt 变体覆盖例外优先级。

6. 负例优先级测试。
   - 未结束进度 OCR 命中时，即使同时存在 update prompt 文字，也必须返回 `unfinished_progress`。
   - `entry.new`、`entry.continue`、`invest`、settlement 页面不得被 update prompt 覆盖。

7. `test_start_from_home_update_prompt_dismisses_before_start_click`
   - 先 dismiss，再重新确认首页，再点 `entry.start`。
   - 复检失败时不得点击 `entry.start`。
   - 无提示路径断言不多点，保留普通首页 `cw.enter` no-op 测试（例如现有 `test_enter_cw_returns_home_noop_when_already_on_start_page`）。

如果 `dismissed_update_prompt` 被作为 session 诊断字段保存，则测试可以断言该字段；如果实现不保存该字段，测试只断言 home 和点击顺序。

### `tests/test_daemon_async_executor.py`

新增或调整：

1. 替换旧测试 `test_without_request_id_terminal_same_payload_replays_latest_singleton_job`。
   - 新行为：同 method / same payload / no explicit job id 的 terminal job 不 replay。
   - 断言 business handler 被调用 2 次。
   - 断言第二次产生新的 job id。

2. 参数化覆盖所有 terminal 类别。
   - `completed`
   - `failed_before_side_effect`
   - `cancelled`
   - `applied_but_not_persisted`
   - `persisted_but_response_unknown`
   - `cancel_unknown`

3. running attach 仍保持。
   - 保留并强化 `test_resending_business_command_attaches_to_running_singleton_without_request_id`。
   - 断言 running 状态下仍复用同一 job。

4. metadata / future 竞态。
   - 构造 `_job_metadata final=False` 但 future 已完成或持久化 job 已 terminal 的窗口。
   - 构造持久化 job `final=false` 但当前 executor 无 live future / paused job 的孤儿记录。
   - 断言无 request id 重发不会 replay 这些记录，而是创建新 job。

### `tests/test_daemon_protocol.py`

新增或调整：

1. 通过真实 daemon async executor 路径覆盖 reconcile 回归。
   - 使用 `TrailDaemonServer(async_enabled=True)` 或直接使用 `RequestExecutor.handle()`，不要只测 `CommandService` / `SessionService`。
   - 第一次 tainted session 下提交 `cw.enter`，返回 `SESSION_RECONCILE_REQUIRED`。
   - 调用 `daemon.reconcile_session`。
   - 使用不同 `call_id`、相同 method / payload、无显式 `job_id/request_id` 再次提交 `cw.enter`。
   - 断言不 replay 旧 `SESSION_RECONCILE_REQUIRED`，而是进入新的 handler 路径。

2. 显式 request id 查询仍可用。
   - 对第一次 terminal job 使用 `daemon.request_status` / `daemon.request_result` 查询，断言旧结果仍可追溯。

### `tests/test_skill_structure.py`

更新旧 async 文档契约测试：

- 正向断言：普通业务命令重发只用于 running job。
- 正向断言：terminal job 必须通过显式 request id / control-plane 查询。
- 反向断言：`skills/trail-hsr/references/async-command-model.md` 不包含旧 terminal 普通重发 replay 语义，例如“旧 job 已终态时也回放终态业务输出”。

## 风险与约束

- 首页更新提示依赖 OCR；如果 OCR 未识别出 `积分线已更新`，`cw.enter` 仍可能走后续页面检测。需要保证 stage fallback 不优先于明确首页 OCR。
- 点击 dismiss 是真实 UI mutation，因此只应在强哨兵命中且具备首页锚点时执行。
- dismiss 后必须重新确认页面状态；确认失败不能写入成功首页状态。
- 禁止用泛词触发 dismiss，避免误点其他页面。
- update prompt 优先级只能覆盖首页误判 fallback，不能绕过未结束进度保护或已过首页页面。
- terminal replay 行为变更会影响既有测试和部分旧使用习惯，但这是用户确认的新契约：普通业务命令重发只用于 running job。
- 历史 terminal job 不能删除，否则会破坏 `daemon.request_status` / `daemon.request_result` 的可追溯性。
- 没有当前 executor live worker 的非终态持久化 job 不允许普通业务命令 replay；否则 daemon 重启或清理竞态会让孤儿记录吞掉后续同 payload 命令。

## 验收标准

- 在「积分线已更新」首页变种上运行 `uv run trail cw enter --session <id>`：
  - 点击 `HOME_UPDATE_PROMPT_DISMISS_POINT` 关闭提示。
  - 重新确认首页。
  - 返回 `ok cw.enter ...`。
  - 不返回 `CW_ENTER_ALREADY_PAST_HOME`。
- 在普通货币战争首页运行 `cw.enter`：
  - 不额外点击。
  - 正常 no-op 成功返回。
- 在提示存在时运行 `cw.start`：
  - 先 dismiss。
  - 重新确认首页 / 未结束进度保护。
  - 再点击开始。
- `daemon.reconcile_session` 后，同 payload `cw.enter` 不再 replay 旧 `SESSION_RECONCILE_REQUIRED`。
- 无 request id 的同 payload terminal job 不 replay；running job 仍可通过同业务命令重发复用。
- 无 live worker 的非终态持久化 job 不 replay。
- 旧 terminal job 仍可通过显式 request id 使用 `daemon.request_status` / `daemon.request_result` 查询。
- active skill 文档不再包含 terminal 普通重发 replay 的旧规则，并包含 running-only 重发规则。
- 相关测试通过：
  - `uv run pytest tests/test_cw_entry.py`
  - `uv run pytest tests/test_daemon_async_executor.py`
  - `uv run pytest tests/test_daemon_protocol.py`
  - `uv run pytest tests/test_skill_structure.py`
