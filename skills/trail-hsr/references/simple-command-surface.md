# Simple Command Surface

- `trail start` 用来建立或恢复一个可继续推进的游戏会话入口，但它不是 scene owner 选择器。
- `trail ocr read` 用来观察当前画面并补充结构化文本，不负责决定该把 owner 交给哪个 scene entry。
- `trail input` 用来执行点击、按键、拖拽等动作，前提是总入口或当前 scene entry 已经决定了下一步动作。
- 通用场景判断继续走 `trail start` / `trail ocr read` / `trail input ...`，不要把 `trail cw stage` 当成登录页、大世界等非 CW 场景检测器。
- 这三个命令属于通用命令表面：它们服务于 `trail-hsr` 或 future `trail-<scene>-entry`，而不是把某个旧 skill 重新抬回 active owner。
- 当总入口已经无法继续推进且问题落在启动、窗口、request-status、session 或 state 恢复面时，再升级到 `trail-hsr-advanced`，不要把 daemon / window / state 命令直接塞回总入口。
- owner 路由看 registry 与当前用户意图；基础命令只提供启动、观察、执行动作的能力边界。
