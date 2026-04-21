# Scene Entry Index

- `trail-hsr` 只根据 `skills/registry/scene-entries.yaml` 判断 scene entry 的当前入口资格。
- 当前入口规则固定为：只有 `status=active` 且 `exposure=public` 的条目，才可以作为从总入口直接移交出去的 scene entry。
- `status=planned` 表示该 scene 还没有成为当前入口；即使用户提到了对应玩法，也仍应先由 `trail-hsr` 承接。
- `exposure=public` 代表它可以成为对外场景入口；internal skill 不在这里作为用户直达入口出现。
- registry 是唯一索引来源；不要凭旧文档、archive skill 名或历史习惯推断当前入口。
