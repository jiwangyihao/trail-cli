# Escalation Contract

- `caller_roles`: 只有 `root_entry` 与 `scene_entry` 可以升级调用 `trail-hsr-advanced`。
- 升级目标固定为 `trail-hsr-advanced`。
- 调用方必须携带：失败症状、最近动作、可复用上下文、`expected_return_owner`。
- advanced 完成恢复后，控制权必须回到 `expected_return_owner`。
- 任何 active skill 都不得直接或间接调用 archive skill。
