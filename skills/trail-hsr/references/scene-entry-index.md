# Scene Entry Index

- `trail-hsr` 只根据 `skills/registry/scene-entries.yaml` 判断 scene entry 的当前入口资格。
- 当前入口规则固定为：只有 `status=active` 且 `exposure=public` 的条目，才可以作为从总入口直接移交出去的 scene entry。
- 当前 `cw` scene entry 已在 registry 中登记为 `trail-cw-entry`，并且是 `status=active` + `exposure=public`。
- `exposure=public` 代表它可以成为对外场景入口；internal skill 不在这里作为用户直达入口出现。
- registry 是唯一索引来源。
- 不要凭 archive、旧文档或历史习惯推断当前入口。
- 部分命令 success 后会额外给出 workflow handoff 提示；`cw.enter -> trail-cw-entry` 是 scene entry handoff，`cw.portal.select -> trail-cw-prep` 是货币战争内部阶段 handoff。
- `cw.portal.select -> trail-cw-prep` 不让 `trail-cw-prep` 成为 direct-user scene entry；它只是普通备战阶段的 active internal 跟进 skill。
