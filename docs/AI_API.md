# AI_API — 接入比赛策略 AI

本项目把「对局内核」与「策略」彻底解耦：AI 只看到**和人类玩家完全相同的视野裁剪**（`game.view.project`），
只通过 `Action` 提交意图，永远不能直接改写 `GameState`。
这保证了规则 R7.3（一致原则）对 AI 同样成立——AI 没有任何作弊通道。

---

## 1. 接口总览

```
                 ┌──────────────┐
  Room ──obs──►  │  Agent.act   │ ──action──► Room
                 └──────────────┘
                     ▲
        ┌────────────┴────────────┐
   进程内 (python)            进程外 (HTTP)
   ai.builtin / 你的模块      remote_client → POST /api/ai/decide
```

### 1.1 `Observation`（AI 每次决策输入）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `turn` / `half` | int | `Turn N` (half=0) 或 `Turn N.` (half=1) |
| `size` | int | 地图边长 |
| `me` | int | 自己的 playerId |
| `tiles` | `list[TileView]` | 长度 `size²`，**已按视野裁剪**：视野外 `army=None`，地形折叠 |
| `my_army` / `my_land` | int | 自己的兵力总和与控制地块数 |
| `leaderboard` | `list[Row]` | 所有人的 army/land（R6.8） |
| `chain` | `list[MoveRequest]` | 自己当前的调兵链（服务端权威回显） |

`TileView = {index, army: int|None, owner: int|None, terrain: int}`
（`terrain: 0 平原 / 2 山地 / 3 城市 / 4 首都转化型城市`；`1 首都` 仅在自己或己方视野内出现）

### 1.2 `Action`（AI 输出）

| `op` | 字段 | 说明 |
| --- | --- | --- |
| `move` | `src`, `dir ∈ {up,down,left,right}`, `ratio ∈ (0,1]` | 入队一条调兵请求（每 Tick 只会被执行 1 条） |
| `clear` | — | 清空调兵链 |
| `pop` | — | 取消最新一条 |
| `select` | `tile` | 改变选中（一般不需要） |

> **重要**：每名玩家**每个 Tick 最多执行 1 条**请求（R4.7）。
> 因此较优策略通常是「每刻提交 1 条请求 + 用调兵链预排队」。

---

## 2. 进程内接入（推荐）

```python
# my_agent.py
from generals.ai import register
from generals.ai.api import Action, Observation

class Greedy:
    name = "greedy"

    def reset(self, spec) -> None:
        self.size = spec.size

    def act(self, obs: Observation) -> Action | None:
        # 找到自己兵力最厚、且旁边有空地的地块，发起一次攻击
        for t in obs.tiles:
            if t.owner == obs.me and t.army and t.army > 1:
                for d, nb in neighbours4(t.index, obs.size).items():
                    if obs.tiles[nb].owner != obs.me and obs.tiles[nb].terrain != 2:
                        return Action(op="move", src=t.index, dir=d, ratio=1.0)
        return None

register("greedy")(Greedy)
```

注册后即可在 `join` 帧中指定：
```json
{ "t": "join", "name": "Greedy", "bot": "greedy" }
```

### 2.1 与内核做离线对局（无需服务器）

```python
from generals.game import mapgen, tick, GameState
import random

state = mapgen.generate(size=16, player_count=2, rng=random.Random(42))
while not state.ended:
    tick.step(state)
```

AI 可以自己驱动这套循环做**自对弈训练**：每 Tick 调用 `view.project(state, me)` 得到与线上完全一致的观测。

---

## 3. 进程外接入（跨语言）

1. 实现一个 HTTP 服务：
   ```
   POST /decide
   { "observation": {...} }
   → 200 { "action": {"op": "move", "src": 202, "dir": "up", "ratio": 1.0} }
   ```
   超时（默认 500ms）或非 200 视为该 Tick 不操作。
2. 用 `generals.ai.remote_client.RemoteAgent(url=..., name="mybot")` 包装后 `register` 即可。

---

## 4. 内置示例

| 名字 | 说明 |
| --- | --- |
| `random` | 合法随机（**仅用于测试**：使用 `random.Random(seed)`，不进入主对局） |
| `greedy` | 贪心扩张：优先占空地，其次打弱城 |
| `hold` | 什么都不做（用于对照/压测） |

> 注意：规则要求「对局不产生任何随机结果」。内置 AI 若带随机必须自行播种，
> 且不得影响**结算**本身的确定性——AI 的随机性属于输入，不属于结算。

---

## 5. 编写策略的坑

- **视野外没有 `army`**：不要在 `army is None` 的地块上做算术。
- **`terrain == 2` 是渲染折叠后的山地**：既可能是真山地，也可能是视野外的城市（R6.9）。
  不能断言"那里一定是山"。
- **调兵链是异步的**：你提交的 `move` 会在后续 Tick 才结算，且当时地块兵力可能已变；
  实际兵力 = `min(ceil(兵力×ratio), 兵力-1)`（R4.2）。
- **不允许向山地调兵**（R4.9）：服务端会以 `blocked` 拒绝。
- **每刻 1 条**：多提交的会排队（上限 20 条），可能拖慢你的反应速度。
