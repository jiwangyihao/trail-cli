# Command Surface

这里讲的是攻略相关命令家族该怎么分工，不是参数手册。

## 候选筛选

- `guide list cw` 负责先列出候选攻略，用在“我想先找几套可能的路线”这个阶段。
- 它适合在已知投资环境、羁绊方向或角色方向时缩小范围。

## 内容确认

- `guide fetch cw` 负责读取完整攻略内容，用来核对标签、阵容、运营思路、投资环境和版本。
- 如果只是要决定“选哪套攻略”，先看到这里；一旦确认候选，再用 `guide.fetch.cw --select` 记录当前攻略。

## 记录当前攻略

- `guide.fetch.cw --select` 只把当前攻略写入 session，不做 UI 应用。
- 它是把“攻略预览”切成“当前已选攻略”的关键一步，应该发生在回到 `trail-cw-entry` 之前。

## 真正进入游戏后

- `cw guide current` 只看当前已选攻略摘要，不是“已经 apply 之后”的专属查询。
- `cw guide apply` 只在需要手动兜底时才有意义；不要一上来就 apply。

## 交回入口

- 攻略选定并记录后，把后续开局动作交回 `trail-cw-entry`，再走 `cw enter` / `cw start`。
- 如果这是投资环境页里的无人值守链路，攻略选定后，把后续环境选择动作交回 `trail-cw-portal`，由它继续 `portal select --card-idx ...`。
- 返回开局链路后，`cw.portal.select` 成功时会自动应用当前已选攻略；这才是默认路径。
