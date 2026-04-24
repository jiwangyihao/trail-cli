# Confirmation Checklist

## 选攻略前确认清单

- [ ] 这一把的目标是什么。
- [ ] 有没有必须顺带完成的羁绊或成就。
- [ ] 当前要跟哪一个版本对齐。
- [ ] 是否已经锁定投资环境，还是希望攻略反过来帮助判断投资环境。
- [ ] 更偏好的主C与阵容倾向是什么。

## 确认后的一般下一步

- 先用 `guide list cw` 缩小候选范围，再用 `guide fetch cw` 读取完整攻略内容。
- 如果这是 `direct-user` / 开局前链路，一旦确认候选，就执行 `guide.fetch.cw --select --session <id>` 记录当前攻略，再把流程交回 `trail-cw-entry`。
- 如果这是从 `trail-cw-entry` handoff 过来的，且上游已经确认了目标、羁绊/成就或环境偏好，就默认继承这些结论，只追问缺失项。
- 如果这是从投资环境页内部切入的无人值守模式，就不再继续追问，而是按当前投资环境、`待收集=1`、热门度、版本自动选。
- 返回开局链路后，`cw.portal.select` 成功时会自动应用当前已选攻略。
- `cw guide current` 用来回看当前已选攻略摘要；只有自动应用链路失效时，才退回 `cw guide apply` 手动兜底。
- 如果这是从投资环境页内部切入的无人值守模式，攻略选定后把流程交回 `trail-cw-portal`，由它继续 `portal select --card-idx ...`。
