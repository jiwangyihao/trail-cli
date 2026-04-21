# Window Launch

- 启动主命令是 `trail window launch --channel official|bilibili|global`；只有确认目标窗口已经存在后，才继续 `trail window attach --window-title ...`。
- `window launch` 恢复依赖 `channel` 与任何已知的 `game path`；缺少这两类事实时，不要假装已经能安全启动。
- 显式提供的 `game path` 应被当作最高优先级。
- 显式 `--game-path` 仍可显式提供，且优先级最高、失败时不会回退。
- 无显式路径时固定顺序：历史成功路径 -> 默认路径 -> 直接问用户。
- 默认路径只覆盖 `official`，示例：`C:\Program Files\miHoYo Launcher\games\Star Rail Game\StarRail.exe`。
- `bilibili` / `global` 无历史成功路径时，通常仍需显式 `--game-path`。
- 不同 `channel` 可能对应不同启动路径或历史路径；不能在失败后静默切换 channel 继续试。
- Agent 不默认乱搜路径、不扫注册表、不全盘搜索；缺少可靠路径事实时就直接问用户。
- `GAME_PATH_REQUIRED` 表示现在该直接问用户提供路径；`GAME_PATH_NOT_FOUND` 表示给定路径不存在；`GAME_LAUNCH_FAILED` 表示启动动作已执行但进程或窗口未成功起来；`GAME_PATH_PERSIST_FAILED` 表示本次启动成功但历史路径写回失败。
- launch / attach 恢复结果里要保留 `session=<id>` 事实，方便后续把窗口恢复结果挂回当前 session 链路。
- 如果当前恢复链路仍缺少 `game path` 或窗口定位事实，advanced 应停止自动重试并要求上层补充信息。
