# Portal Command Surface

这里讲的是投资环境页上的动作分工，不是参数手册。

## 进入前提

- 只有在 `cw start` 或 `portal refresh` 已成功把流程推进到投资环境页之后，才进入这一组动作。
- 这一步是投资环境页内的跟进行为，不是总入口的启动阶段。

## 当前页动作

- `portal detect`：回读当前可见的环境卡片，适合先弄清这一屏现在有哪些候选。
- `portal refresh`：保留当前开局流程，只刷新这一屏可见的候选环境。
- `portal restart`：放弃当前这把开局，重新开始一把新的投资环境页筛选；只有在用户允许刷开局时才优先考虑。
- `portal select --card-idx ...`：确认当前选中的投资环境，继续这一把后续流程。

## 基本定位

- `detect` 负责看清现状，`refresh` 负责继续筛，`restart` 负责重开，`select` 负责定案。
- 先判断这一页是不是已经有足够好的环境，再决定要不要 refresh、restart 或直接 select。
