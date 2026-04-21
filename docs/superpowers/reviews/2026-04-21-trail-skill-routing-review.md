# Trail Skill Routing Review

## 方法说明

- 样本来源：固定使用 Task 6 rollout gate 规定的 8 条中文 prompt。
- 旧 skill 集合 winner：按旧 active skill 拓扑（含 `trail-cw*`）判断该 prompt 最可能命中的对外 owner。
- 新 skill 集合 winner：按重设计后的 active/public/internal 拓扑判断；planned scene 继续回退到 `trail-hsr`，`trail-hsr-advanced` 仅作为内部升级层。
- 比对口径：只比较路由 winner，不比较具体命令细节；若新 skill 集合 winner 与“预期 winner”全部一致，则结论记为 `PASS`，否则记为 `FAIL`。

| Prompt | 旧 skill 集合 winner | 新 skill 集合 winner | 预期 winner | 备注 |
| --- | --- | --- | --- | --- |
| 帮我继续玩星铁 | trail-hsr | trail-hsr | trail-hsr | 无额外差异 |
| 帮我打开星铁并接管后续流程 | trail-hsr | trail-hsr | trail-hsr | 无额外差异 |
| 帮我玩货币战争 | trail-cw | trail-hsr | trail-hsr | 旧集合会直达 `trail-cw`；新集合因 `trail-cw-entry` 仍是 planned scene，按 planned scene fallback 回到总入口 |
| 进入货币战争玩法 | trail-cw | trail-hsr | trail-hsr | 旧集合会直达 `trail-cw`；新集合因 `trail-cw-entry` 仍是 planned scene，按 planned scene fallback 回到总入口 |
| 星铁启动失败了，帮我恢复后继续玩 | trail-hsr-advanced | trail-hsr | trail-hsr | 旧集合里恢复语义更容易误触发 `trail-hsr-advanced`；新集合固定由 `trail-hsr` 先接管，再走 internal escalation |
| 游戏窗口不对，帮我接起来继续玩 | trail-hsr-advanced | trail-hsr | trail-hsr | 旧集合里窗口恢复语义更容易误触发 `trail-hsr-advanced`；新集合固定由 `trail-hsr` 先接管，再走 internal escalation |
| 帮我读一下这个 PDF | none | none | none | 无额外差异 |
| 帮我接管当前星铁任务 | trail-hsr | trail-hsr | trail-hsr | 无额外差异 |

结论：PASS
