# Simple Command Surface

- `trail start` 用来建立或恢复一个可继续推进的游戏会话入口，但它不是 scene owner 选择器。
- `trail start` 现在不再只是返回 `session`；默认文本首行会带 `status=...`，并在成功时附带截图，因此要把它理解成“启动 + 首帧观察”的组合入口，而不是只有 session 分配器。
- 只要 `trail start` 返回了 `shot path=...`，就说明这一轮已经产出了原始截图；如果同时带 `info read_image_first=1`，应先读图，再决定是否继续 scene 命令。
- `trail start` 的 `status=attached`、`status=launched_needs_check`、`status=launched_clicked_enter` 都只是启动链路状态，不等于已经稳定进入大世界；三态细节见 `start-run-status-handling.md`。
- `trail ocr read` 用来观察当前画面并补充结构化文本，不负责决定该把 owner 交给哪个 scene entry。
- `trail input` 用来执行点击、按键、拖拽等动作，前提是总入口或当前 scene entry 已经决定了下一步动作。
- 通用场景判断继续走 `trail start` / `trail ocr read` / `trail input ...`，不要把 `trail cw stage` 当成登录页、大世界等非 CW 场景检测器。
- battle in-progress 当前只是场景/命令说明，不实现新的 skill 本体：当 `trail cw battle run --session <id>` 返回 `status=in_progress` 和 `info next_action=cw.battle.run why=battle_flow_not_finished` 时，先读截图；若仍在 battle flow 中，就继续运行 `trail cw battle run --session <id>`。
- battle flow 包含战斗中、结算页、结算翻页但未回到下一稳定阶段；结算页也属于 battle flow，不要因为看到结算页就默认改用旧 `trail cw settle next`。
- `layer_transition` 表示整层结束后的“点击空白处继续 / 位面”过场；`layer_transition` 仍属于 battle flow，默认继续 `trail cw battle run --session <id>`，不要把它当成真正 `boss_preview`、普通稳定阶段或手工中断点。
- `trail cw battle run` 默认 timeout 现在是 `90s`；`trail cw battle clear-in-progress --session <id>` 只清 battle.run 的内部续跑提示位，不清 battle 摘要。
- 这三个命令属于通用命令表面：它们服务于 `trail-hsr` 或 future `trail-<scene>-entry`，而不是把某个旧 skill 重新抬回 active owner。
- 当总入口已经无法继续推进且问题落在启动、窗口、request-status、session 或 state 恢复面时，再升级到 `trail-hsr-advanced`，不要把 daemon / window / state 命令直接塞回总入口。
- owner 路由看 registry 与当前用户意图；基础命令只提供启动、观察、执行动作的能力边界。
