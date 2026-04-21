---
name: trail-hsr-advanced
description: Use when `trail start` fails or when an agent needs advanced Trail HSR commands for daemon, window, session, screen, image, or state troubleshooting.
---

# Skill: trail-hsr-advanced

## 何时使用

- `trail start` 失败
- simple 层不能满足定位需求
- 需要手工拆解 daemon、窗口、session、截图、找图或状态恢复链路

## 进阶命令面

- `trail daemon install`
- `trail daemon status`
- `trail daemon start`
- `trail daemon request-status --request-id <id>`
- `trail daemon reconcile-session --session <id>`
- `trail window launch --channel official|bilibili|global`
- `trail window attach --window-title "崩坏：星穹铁道"`
- `trail session create`
- `trail screen shot`
- `trail image locate`
- `trail image wait`
- `trail state dump --session <id>`
- `trail state dump --session <id> --format yaml`

## Window Launch 规则

- 入口命令：`trail window launch --channel official|bilibili|global`
- 显式 `--game-path` 仍可显式提供，且优先级最高、失败时不会回退
- 无显式路径时固定顺序：历史成功路径 -> 默认路径 -> 直接问用户
- 默认路径只覆盖 `official`，冻结值为 `C:\Program Files\miHoYo Launcher\games\Star Rail Game\StarRail.exe`
- `bilibili` / `global` 无历史成功路径时，通常仍需显式 `--game-path`
- 显式路径不存在时返回 `GAME_PATH_NOT_FOUND`
- 显式路径存在但启动失败时返回 `GAME_LAUNCH_FAILED`
- Agent 不应默认乱搜路径；收到 `GAME_PATH_REQUIRED` 表示现在该直接问用户提供路径
- 如果游戏已成功启动但历史路径写回失败，仍返回 success，并追加 `warn code=GAME_PATH_PERSIST_FAILED`
- 上述 success warning 的稳定码是 `GAME_PATH_PERSIST_FAILED`

## 执行建议

- 先用 `trail daemon status` 确认控制面是否 ready；需要预热时再运行 `trail daemon start`
- 需要手工拉起游戏时，优先使用 `trail window launch`；只在确认窗口已存在时再运行 `trail window attach`
- 需要重建会话时，运行 `trail session create`
- 如果命令结果未知，先记录默认文本里的 `request id=<id>`，再执行 `trail daemon request-status --request-id <id>`，从默认文本里的 `session=<id>` 获取需要恢复的 session
- 如果 session 已被标记为 `tainted`，先确认请求终态，再执行 `trail daemon reconcile-session --session <id>`
- 如果 `trail cw battle run --session <id> --timeout 570` 已在 daemon 内成功收口但 stdout 丢失，且你还能确认 `session=<id>`，立刻执行 `trail state dump --session <id> --format yaml` 回读当前 scene / session 状态
- scene-local 的 battle / settle 手工拆链不在这里处理；那类 fallback 切到 `trail-cw-battle-advanced`，这里只有 control-plane / request-status / state / reconcile-session 恢复链路
- 只有在 simple 层无法满足需求时，才使用这些进阶命令
