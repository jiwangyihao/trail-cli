---
name: trail-hsr
description: 当用户希望 Agent 接管并自动游玩《崩坏：星穹铁道》，或从当前局面继续推进游戏任务时使用。
---

# Skill: trail-hsr

## Installation Note

- Trail CLI and the complete Trail skill bundle should be installed through the release installer or `AGENT_INSTALL.md`.
- OpenClaw is a supported target environment for the installer.
- Do not install only this single skill for normal play; handoff depends on the bundle's active public/internal skills, `registry`, and `shared` references.

## Role

- `trail-hsr` 是《崩坏：星穹铁道》的对外总入口 skill，先承接“接管并自动游玩”或“从当前局面继续推进”的用户意图。
- 当用户只表达“继续玩星铁”“接管当前任务”“从现在这个画面继续”这类总入口诉求时，由 `trail-hsr` 先接手，不要求用户先说出具体系统或玩法名。
- `trail-hsr` 负责判断当前请求应该继续由总入口处理，还是把 owner 交给已经对外开放的 scene entry。

## Default Workflow

1. 先判断用户是否真的在请求 Agent 接管并继续推进星铁；如果请求其实是 PDF、表格、邮件、代码或别的非游戏任务，不进入本 skill。
2. 以“先接住当前局面”为默认动作；`trail start` 不再只是拿 `session`，还会返回 `status` 并自动带回截图。只要结果里有 `shot path=...` 与 `info read_image_first=1`，就先读这张原始图，再决定是否继续后续命令。
3. 当 `trail start` 返回 `status=attached`、`status=launched_needs_check` 或 `status=launched_clicked_enter` 时，都先做画面判断，不能盲目继续 scene 命令。具体判断逻辑下沉到 `references/start-run-status-handling.md`，并配合 `references/simple-command-surface.md` 与 `references/ocr-and-screenshot.md` 使用。
4. 如果 `skills/registry/scene-entries.yaml` 里存在 `status=active` 且 `exposure=public` 的 scene entry，并且用户意图已经明确命中该场景入口，则把 owner 交给对应的 `trail-<scene>-entry`。
5. 如果某个 scene 仍是 `planned`，即使用户提到了该玩法，也继续由 `trail-hsr` 接住当前请求；planned scene 只能回退到 `trail-hsr`，不得回退 archive skill。
6. 只在总入口已经无法继续推进、需要专门恢复链路时，按共享升级契约转交到 advanced 层，恢复完成后再拿回控制权继续推进。

## Scene Entry Index

- scene entry 的单一事实源是 `skills/registry/scene-entries.yaml`。
- 当前入口规则只认 `status=active` 且 `exposure=public` 的 scene entry；只有同时满足这两个条件，才是可以从总入口移交出去的当前入口。
- `status=planned` 的 scene 即使将来会有独立入口，现在也仍由 `trail-hsr` 承接，不能把请求转给 archive skill，也不能把 future scene 当成已经上线的当前 owner。
- internal skill 不属于用户直达入口；它们只服务于总入口或 scene entry 的内部升级路径。

## Escalate to Advanced When

- 启动失败、窗口异常、上下文断裂、当前局面无法继续推进，且问题已经超出总入口可直接恢复的范围时，才升级到 advanced。
- 升级必须遵守 `skills/shared/escalation-contract.md`，并显式携带失败症状、最近动作、可复用上下文，以及 `expected_return_owner`。
- 如果当前 owner 仍是总入口，则 `expected_return_owner` 应回到 `trail-hsr`；如果已经移交给 scene entry，则应回到对应 scene entry。
- advanced 完成恢复后，控制权必须回到 `expected_return_owner`，不能停留在 advanced，也不能跳到 archive。

## Reference Map

- `references/simple-command-surface.md`：总入口可依赖的基础命令表面，以及它们与 owner 路由之间的边界。
- `references/ocr-and-screenshot.md`：截图优先、`shot path=...` 与 `info read_image_first=1` 的读取顺序，以及 OCR 在观察链路中的角色。
- `references/start-run-status-handling.md`：`trail start` / `start.run` 的三态解释、何时继续观察、何时才允许用 OCR 坐标点击 `点击进入`。
- `references/scene-entry-index.md`：如何只根据 registry 判断当前有哪些 scene entry 可以从总入口移交。
- `skills/shared/escalation-contract.md`：总入口与 future scene entry 共用的 advanced 升级契约。
