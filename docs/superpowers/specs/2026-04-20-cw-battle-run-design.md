# 货币战争 battle.run 收口设计（活参考补记）

> Note: 若本文旧示例与当前 screenshot-first guidance 冲突，以 `2026-04-20-screenshot-first-guidance-design.md` 为准。
> 当前带截图 success 路径固定为 `shot path=...` -> `info read_image_first=1` -> 实体行，README / AGENTS / skills 也必须同步更新。

- 这份活参考只覆盖当前分支需要冻结的 screenshot-first override，不承担 battle.run 的完整历史设计说明。
- `cw.battle.run` 若在 success 路径返回截图，包括 `status=in_progress` 这类 success 分支，也必须按 `shot path=...` -> `info read_image_first=1` -> 实体行理解与示例化。
- failure 路径即便带截图，也不因为这条 override 额外插入 `info read_image_first=1`；仍按现有 failure 顺序阅读。
