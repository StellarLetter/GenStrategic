# AI_API — 策略接入手册

> **只读本文件就能写完一个策略并让它上场。** 不需要读服务端源码、不需要改服务端代码、
> 不需要重启进程（改完文件调一次 `POST /api/ai/reload`，或点界面上的「刷新策略」）。

---

## 0. 60 秒上手

```powershell
# 1) 在仓库根建一个策略文件（ai/ 目录已存在，服务器启动时会自动挂载里面所有 .py）
notepad ai\my_bot.py
```

```python
# ai/my_bot.py —— 全部内容
from generals.ai import register
from generals.ai.api import Action, Observation, neighbours4


@register("my-bot")            # ← 这个字符串就是界面上要选的名字
class MyBot:
    name = "my-bot"
    label = "我的机器人"        # 界面显示名（可省，省了显示 name）
    description = "占相邻空地"   # 界面提示（可省，省了取类 docstring 首行）

    def reset(self, spec) -> None:
        self.size = spec.size   # 开局调用一次；把地图边长记下来

    def act(self, obs: Observation) -> Action | None:
        for tile in obs.tiles:                      # 遍历自己看得见的格
            if tile.owner != obs.me or not tile.army or tile.army <= 1:
                continue
            for direction, nb in neighbours4(tile.index, obs.size).items():
                target = obs.tiles[nb]
                if target.owner == obs.me or target.terrain == 2:
                    continue                        # 己方 / 山地（R4.9）跳过
                return Action(op="move", src=tile.index, dir=direction, ratio=1.0)
        return None             # 本刻不操作
```

```powershell
# 2) 起服务
.\scripts\dev.ps1 -Port 8000
```

浏览器打开 `http://127.0.0.1:8000/`，左下角「策略接管」面板里选 **我的机器人**，
然后点 `Start` 开局 —— 你的策略就会接管你自己的座位。

**三个必须记住的事实**

| 事实 | 含义 |
| --- | --- |
| AI 与真人**看到的东西完全一样** | 观测一律经 `game.view.project` 裁剪，没有透视、没有作弊通道 |
| AI 的指令走**真人同一条校验路径** | 非法动作会被服务端 `reject`，不是"AI 内部报错" |
| 每刻**最多 1 条**调兵（R4.7） | 想连招就把请求排进调兵链（`Action(op="move")` 可连续多次） |

---

## 1. 数据契约

### 1.1 `Observation` — 每刻递给你一次（`act(obs)` 的入参）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `turn` | `int` | 回合号。`Turn N` 与 `Turn N.` 共用同一个 `turn` |
| `half` | `int` | `0` = `Turn N`（上半回合）；`1` = `Turn N.`（下半回合，增长在此刻结算 R3.4） |
| `size` | `int` | 地图边长；`index = row * size + col`（R2.1） |
| `me` | `int` | 自己的 `playerId`；**观战者为 `-1`** |
| `tiles` | `list[TileView]` | 长度 `size²`，**下标 == index**；已按视野裁剪 |
| `my_army` | `int` | 自己全部地块兵力之和（R6.8） |
| `my_land` | `int` | 自己控制的地块数（R6.8） |
| `leaderboard` | `list[Row]` | 所有人的公开战绩，按 `player_id` 升序（R7.3） |
| `chain` | `list[MoveRequest]` | 自己当前调兵链的**服务端权威回显**（R6.7） |

`TileView`（`obs.tiles[i]` 就是第 `i` 格）：

| 字段 | 值域 | 语义 |
| --- | --- | --- |
| `index` | `int` | `row * size + col` |
| `army` | `int \| None` | 兵力；**`None` = 视野外**（R3.6）。不要在 `None` 上做算术 |
| `owner` | `int \| None` | 控制者 `playerId`；`None` = 中立。视野外**仍给 owner**（能看见雾里的敌方领土色） |
| `terrain` | `int` | 可见地形编码 |

`terrain` 编码（PROTOCOL §3.3）：

| 值 | 视野内含义 | 视野外（R6.9 折叠） |
| --- | --- | --- |
| `0` | 平原 | 平原外观 |
| `1` | **首都**（王城） | 折叠为 `0` |
| `2` | 山地（不可通行 R4.9） | 山地**或城市**的外观 |
| `3` | 城市 | 折叠为 `2` |
| `4` | 首都转化型城市（R5.4） | 折叠为 `0` |

> 视野 = 自己全部地块 ⊕ 与其 8 相邻（含斜角）（R3.5）。

`Row`（`obs.leaderboard[j]`）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `player_id` | `int` | 也有线协议风格别名 `playerId` |
| `name` / `color` | `str` | 昵称 / 配色名 |
| `army` / `land` | `int` | 兵力总和 / 地块数（R6.8） |
| `alive` | `bool` | 是否仍在局中（R5.3） |

`MoveRequest`（`obs.chain[k]`）：`src: int`、`dir: Dir`（**枚举**，取字面量用 `dir.value`）、`ratio: float`。

### 1.2 `Action` — 你要返回的东西

```python
Action(op="move", src=202, dir="up", ratio=1.0)
```

| `op` | 需要的字段 | 语义 | 规则 |
| --- | --- | --- | --- |
| `"move"` | `src: int`, `dir: "up"\|"down"\|"left"\|"right"`, `ratio: float` | 往 4 相邻调兵入队 | R4.1 / R4.11 / R6.6 |
| `"clear"` | — | 清空整条调兵链 | R6.4 |
| `"pop"` | — | 取消最新一条 | R6.4 |
| `"select"` | `tile: int` | 改变选中（纯 UI，一般不需要） | R6.1 |

返回 `None` = **本刻不操作**（最安全的选择，任何"想不清楚"的分支都该返回它）。

**比例只有十档**：`RATIOS = (0.1, 0.2, …, 1.0)`（R6.6），与真人界面完全一致。
写 `0.37` 会**就近量化**成 `0.4`（不是报错）；写 `0.0`/负数会被量化到 `0.1`。
`from generals.ai import RATIOS` 可拿到这个元组。

```python
from generals.ai import RATIOS
Action(op="move", src=10, dir="left", ratio=RATIOS[4])   # 50%
```

### 1.3 `GameSpec` — 开局时递给你一次（`reset(spec)` 的入参）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `size` | `int` | 地图边长（**由玩家数算出**，R2.3：客户端无法指定） |
| `player_count` | `int` | 本局玩家数 |
| `players` | `tuple[Row, ...]` | 开局时的玩家清单，按 `player_id` 升序 |
| `tick_ms` | `int` | 每个 Tick 的毫秒数（默认 500 ⇒ 1 回合 1 秒，R1.1） |

> 开局时所有人都是 1 块首都、1 兵（R3.2）；`size` 只在 `reset` 里拿得到，别指望 `act` 里现算。

---

## 2. 策略协议（`Agent`）

```python
class Agent(Protocol):
    name: str
    def reset(self, spec: GameSpec) -> None: ...
    def act(self, obs: Observation) -> Action | None: ...
```

| 方法 | 何时调用 | 硬性要求 |
| --- | --- | --- |
| `reset(spec)` | 每局**开局时一次** | 把跨回合状态初始化干净；几秒内返回，别做重活 |
| `act(obs)` | 每 Tick **最多一次** | **不要抛异常**：异常会被吞掉并当作"本刻不操作"，但会拖慢对局 |
| `close()` | 对局结束/换策略时（**可选**） | 释放文件/网络资源；不实现也行 |

可选类属性（给界面用的元信息，都可不写）：

| 属性 | 作用 | 缺省 |
| --- | --- | --- |
| `name` | 策略标识 | 必填（与 `register` 名一致最省心） |
| `label` | 下拉框显示名 | 等于注册名 |
| `description` | 下拉框悬浮提示 | 类 docstring 首行 |

**实例隔离**：每个座位一个**独立实例**（`ai.runner.make_agent` 每次新建），
所以状态放 `self` 上是安全的；**不要**把状态放在类属性或模块级全局变量上（多个座位会互串）。

---

## 3. 自动挂载（`ai/` 目录）

服务器启动时自动扫描**仓库根的 `ai/` 目录**，把里面的策略全部注册进注册表。

```
generals/
└── ai/                     ← 策略根目录（放这里就自动生效）
    ├── README.md           ← 目录约定速查
    ├── sample_greedy.py    ← 示例（可复制改名）
    └── my_bot.py           ← 你的策略
```

### 3.1 目录规则

| 路径 | 行为 |
| --- | --- |
| `ai/*.py` | 每个文件按**独立模块**加载，执行其中的 `@register(...)` |
| `ai/_*.py`、`ai/.*` | **不加载**（放私有草稿） |
| `ai/__init__.py` | 不当作策略文件 |
| `ai/<子目录>/__init__.py` | 整个子目录作为一个包加载一次 |
| `ai/<子目录>/*.py`（无 `__init__.py`） | 逐个加载（只扫一层） |
| `ai/__pycache__/` | 跳过 |

`ai/` 目录会被加入 `sys.path`（在末尾之外的**最前**），所以策略文件可以直接
`from helpers import foo` 引用同目录下的辅助模块（辅助模块用 `_` 开头避免被当策略加载）。

### 3.2 换目录 / 部署到别处

环境变量 `GENERALS_AI_DIR` 覆盖策略根目录：

```powershell
$env:GENERALS_AI_DIR = "D:\my-strategies"
.\scripts\dev.ps1 -Port 8000
```

### 3.3 热重载（改完不用重启）

| 方式 | 做法 |
| --- | --- |
| 界面 | 「策略接管」面板点 **刷新策略** |
| HTTP | `curl -X POST http://127.0.0.1:8000/api/ai/reload` |

重载语义：先摘掉上一次**由文件注册**的策略，再重新扫描。
因此新增 / 删除 / 改名 / 改内容都会立即反映；内置的 `hold`/`greedy`/`random` 不受影响。

> **重载不影响进行中的对局**：已建好的策略实例继续跑；新开局或切换策略才用新代码。

### 3.4 加载失败怎么办

一个坏文件**只影响它自己**：报错进 `errors`，服务器照常启动、其它策略照常可用。

```powershell
curl http://127.0.0.1:8000/api/ai/strategies
```

```json
{
  "directory": "D:\\COSMOS\\generals\\ai",
  "strategies": [
    {"name": "greedy", "label": "Greedy（贪心扩张）", "description": "…", "source": "builtin", "builtin": true},
    {"name": "my-bot",  "label": "我的机器人",        "description": "…", "source": "my_bot.py", "builtin": false}
  ],
  "errors": [{"file": "broken.py", "error": "SyntaxError: invalid syntax (…/broken.py:1)"}],
  "files": ["broken.py", "my_bot.py"]
}
```

常见错误对照：

| `error` | 原因 | 修法 |
| --- | --- | --- |
| `SyntaxError: … (file.py:N)` | 语法错 | 看行号改 |
| `ImportError: cannot import name 'X'` | 导入了不存在的名字 | 别 `.api` 与 `.builtin` 混着导（见 §5 导入表） |
| `ModuleNotFoundError: No module named 'generals'` | 用的解释器不对 | 用仓库 `.venv` 里的 python 跑服务 |
| `ValueError: strategy name must be a non-empty string` | `register("")` | 给个非空名字 |

---

## 4. 让策略上场：Web 界面 / 协议 / API

### 4.1 Web 界面（推荐）

对局页左下角有「**策略接管**」浮动面板（可拖动，位置会记住）：

1. 下拉框列出 `GET /api/ai/strategies` 的全部策略（内置 + `ai/` 目录）；
2. 选中即生效 —— 服务端回执后状态行显示「当前：<名字>」；
3. 选「**我自己操作**」收回操作权，立刻可以手动打；
4. **开局前也能选**：先选策略再点 `Start`，从 `Turn 0` 第一刻起就是它在打。

> 观战者没有座位，面板自动隐藏。托管状态是**全房间可见**的（观战者也能看出谁在托管）。

### 4.2 线协议：`autopilot` 帧

上行（Client → Server）：

```json
{ "t": "autopilot", "strategy": "my-bot" }    // 交给该策略
{ "t": "autopilot", "strategy": null }        // 收回操作权（也可写空串/省略字段）
```

下行回执（服务端 → **全房间**广播）：

```json
{ "t": "autopilot", "playerId": 0, "strategy": "my-bot" }
```

| 情况 | 结果 |
| --- | --- |
| 策略未注册 | `reject(code="bad_frame")` |
| 观战者发送 | `reject(code="not_your_turn")` |
| 开局前发送 | **允许**，开局即生效 |
| 开局中发送 | **允许**，下一 Tick 起生效 |
| 自己已出局 | 允许设置但不再产生指令 |

### 4.3 机器人座位（`join.bot`）

不想要真人座位、只想让策略之间互打时，在 `join` 帧里写 `bot`：

```json
{ "t": "join", "name": "Bot", "room": "demo", "bot": "my-bot" }
```

该座位**没有 WS 连接**、不占人类玩家之外的额外容量（它就是普通玩家座），
从此由策略驱动。`bot` 与 `autopilot` 的区别：

| | `bot` | `autopilot` |
| --- | --- | --- |
| 有真人连接 | 无 | **有**（可随时收回操作权） |
| 谁指定 | `join` 帧 | `autopilot` 帧 / 界面 |
| 相互覆盖 | 对 `bot` 座位再发 `autopilot` = **换策略** | 对托管座位发 `autopilot: null` = 收回 |

### 4.4 HTTP 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/ai/strategies` | 策略清单 + 加载错误（界面下拉框数据源） |
| `POST` | `/api/ai/reload` | 重新扫描策略目录，返回与上者同构的响应体 |

---

## 5. 导入表（照抄即可）

```python
# 注册与注册表
from generals.ai import register, get, all_names, registered

# 数据结构与工具（一处全拿）
from generals.ai.api import (
    Action, Observation, GameSpec, Row, RATIOS,
    neighbours4,              # (index, size) -> {"up": idx, ...}（只含未越界方向）
    TERRAIN_MOUNTAIN_VISIBLE, # = 2，视野里"山地外观"
    DIR_DELTAS,               # {"up": (-1,0), ...}
)

# 内置策略（参考实现）
from generals.ai import Hold, Greedy, Random

# 进程外（HTTP）策略客户端
from generals.ai import RemoteAgent
```

> `neighbours4` 等工具在 `generals.ai.api`（**不是** `generals.ai.builtin`）。
> `generals.ai` 顶层也导出了 `neighbours4` 与 `RATIOS`，两种写法都行；
> 但 `TERRAIN_MOUNTAIN_VISIBLE` / `DIR_DELTAS` 只在 `generals.ai.api` 里。

---

## 6. 离线开发：不连服务器也能跑

### 6.1 用真实内核跑一局（推荐）

```python
import random

from generals.ai import make_agent, to_observation
from generals.ai.loader import StrategyLoader
from generals.ai.runner import spec_from_state
from generals.game import commands, mapgen, tick

StrategyLoader().scan()            # 让 ai/ 里的策略可用

state = mapgen.generate(player_count=2, rng=random.Random(42))
agents = {pid: make_agent("my-bot") for pid in sorted(state.players)}
for agent in agents.values():
    agent.reset(spec_from_state(state))

while not state.ended and state.tick < 400:
    for pid in sorted(agents):
        action = agents[pid].act(to_observation(state, pid))
        if action is None:
            continue
        cmd = action.to_command()                  # 与线上完全同一条转换路径
        if cmd["op"] == "move":
            commands.enqueue_move(state, pid, cmd["from"], cmd["dir"], cmd["ratio"])
    tick.step(state)

print("winner:", state.winner, "tick:", state.tick)
print("land:", {pid: state.land_count(pid) for pid in state.players})
```

### 6.2 跑对局时校验动作合法性

自己写的校验器很容易和内核走偏，**直接借服务端房间**做裁判：

```python
from generals.net.room import Room
from generals.config import DEFAULT_CONFIG

room = Room("local-bench", DEFAULT_CONFIG, seed=1, size=16)
seat = room.add(None, "Bench", bot="my-bot")   # 策略名已注册即可
room.start()
for _ in range(200):
    room.step_once()
print("land:", room.state.land_count(seat.player_id))
print("errors:", room.strategy_state())        # 策略构造/运行异常都会出现在这里
```

策略抛出的异常会被吞掉并记进 `last_error`，**不会**中断对局 —— 但请把它当成 bug 修掉。

---

## 7. 进程外接入（跨语言）

不想用 Python 写策略时，起一个 HTTP 服务，让服务端当客户端去问它：

```
POST <你的地址>
{ "observation": { … Observation 的 JSON … } }
→ 200 { "action": { "op": "move", "from": 202, "dir": "up", "ratio": 1.0 } }
→ 200 { "action": null }        # 本刻不操作
```

要点：

- 字段名与 §1 一致；`tiles` 每项是 `{"index","army","owner","terrain"}`（`army` 可为 `null`）。
- 决策预算 **< 400ms**（`remote_client.DEFAULT_TIMEOUT = 0.4`）；超时 = 本刻不操作。
- 非 200 / JSON 非法 / 结构不对，一律降级为"本刻不操作"，不会崩溃。

接进注册表（再让 `ai/` 里的文件或 `autopilot` 指向它）：

```python
# ai/remote_bot.py
from generals.ai import register
from generals.ai.remote_client import RemoteAgent

register("remote")(lambda: RemoteAgent(url="http://127.0.0.1:9000/decide", name="remote"))
```

> `RemoteAgent` 内部用 `httpx`；房间侧统一走 `Room.step_async()` 的线程池，
> **不会**阻塞事件循环。

---

## 8. 内置策略

| 名字 | 说明 | 用途 |
| --- | --- | --- |
| `hold` | 什么都不做 | 对照 / 压测 |
| `greedy` | 贪心扩张：先占空地，其次打最弱邻格 | 基线对手 |
| `random` | 合法动作里等概率挑一条（比例也随机） | **仅测试** |

`random` 用 `random.Random(seed)` 显式播种：AI 的随机性属于**输入**，
不影响结算确定性（R7.3 约束的是结算，不是策略）。

---

## 9. 规则速查（写策略真正用得上的部分）

| 编号 | 条文 | 对策略的含义 |
| --- | --- | --- |
| R1.1–R1.3 | 1 回合 = 2 Tick = 1 秒 | 每刻一次决策，思考预算 ≈ 400ms |
| R3.2 | 开局每人 1 座首都、1 兵 | 前几刻几乎无事可做 |
| R3.3 | 城市初始中立驻军 40–50 | 早期别拿 1 兵去撞城 |
| R3.4 | `Turn N.` 结算增长：首都/城市 +1；平原每 25 回合 +1 | 攒兵靠城市，不是靠平原 |
| R3.5 | 视野 = 己方全部地块 ⊕ 8 相邻 | 视野外 `army is None` |
| R3.8 | 中立地块有驻军、对所有人敌对，但不调兵不增长 | 打中立 = 互减，不是白拿 |
| R4.2 | 实际调兵 = `min(ceil(army×ratio), army−1)` | **兵力 ≤ 1 调不动** |
| R4.3 | 阵地战：各方求和互减，最大者胜，并列最大全部归零且不占领 | 别用均势硬撞 |
| R4.4 | 战斗后到达兵力 > 0 即占领，兵力 = 结算剩余（不叠加） | — |
| R4.6 | 己方地块：累加；中立空地（0 兵）直接占领 | 往自家格子调兵是"运输" |
| R4.7 | 每刻只处理 **1 条**请求 | 用调兵链排队 |
| R4.9 | 不可向山地调兵 | `terrain == 2` 且在视野内则跳过 |
| R4.11 | 调兵链的起点可以是**链中任意请求的目标**（预调兵） | 支持整条路线一次排完 |
| R4.12 | 起点已失 / 调兵量 ≤ 0 的请求会被**作废**，本刻不再弹下一条 | 链越长，作废代价越大 |
| R5.2 | 攻陷他人王城 = 继承其**全部领土** | 斩首是最高收益动作 |
| R5.3 | 失去全部地块即出局 | — |
| R6.6 | 比例只取 10% 整数倍 | 精细调度只能靠多次请求 |
| R6.11 | 调兵链上限 20 条 | 超出被拒（`chain_full`） |
| R7.3 | 结算无随机、无时间依赖 | 同状态必然同结果，可复现 |

---

## 10. 常见坑（按被坑概率排序）

1. **视野外做算术**：`army is None` 上运算会 `TypeError`。先判 `None`。
2. **以为 `terrain == 2` 一定是山**：视野外的**城市**也渲染成 `2`（R6.9）。
   它可能不是山 —— 但也没法从观测区分，按"不能走"处理最安全。
3. **兵力 1 还想调兵**：`min(ceil(1×1.0), 0) = 0` ⇒ 请求被作废（R4.2/R4.12），
   还可能连带作废它的下游。判 `army > 1` 再行动。
4. **每刻返回多个动作**：`act()` 只能返回**一个** `Action`（或 `None`）。
   想排队就在一个 `act()` 里多次调用？不行 —— 返回值只有一个。
   正确做法：不同 Tick 分别返回，让它们自然进入调兵链；或用 `Action(op="move")`
   视作"追加一条"，后面几刻继续追加。
5. **比例写成 `0.37` 以为会更精细**：会被量化到 `0.4`（R6.6）。
6. **在 `reset` 里做重活**（读大文件、发网络请求）：会卡住开局。
7. **状态放类属性**：多个座位共用一个类，状态会互串（§2）。
8. **`act` 里抛异常**：被吞掉当"不操作"，策略看起来"沉默不动"却没有任何报错。
   先在离线循环（§6.2）里跑通再上场。
9. **`obs.chain[k].dir` 是枚举**：直接 `json.dumps` 会失败，取 `.value`。
10. **改完文件忘了 reload**：不开 `--reload` 时服务端不会自己重扫策略目录，
    点「刷新策略」或 `POST /api/ai/reload`。

---

## 11. 调试与排错

| 症状 | 先查哪里 |
| --- | --- |
| 下拉框里没有我的策略 | `GET /api/ai/strategies` 的 `errors`；文件名是否 `_` 开头；`@register` 名字是否为空 |
| 选了策略但一动不动 | 策略是否返回 `None`；`room.strategy_state()` 的 `error` / `lastError` |
| 动作被服务端拒绝 | 房间对局里看是否收到 `reject`（开局离线循环最能暴露问题，§6.2） |
| 改完不生效 | 有没有 reload（§3.3）；是否改了 `ai/` 之外的副本 |
| 想确认真的在跑 | 对局里看自己的地块数是否随 Turn 增长；或离线跑 §6 的脚本打印 `land` |

**开关调试**：策略里可以 `print`，会直接打到服务端控制台（`dev.ps1` 的窗口）。
生产环境请删掉或用 `logging`。

---

## 12. 与规则文档的关系

- 本文件描述**接口**（怎么接、怎么写），规则本身以 [`RULES.md`](./RULES.md) 为唯一事实源。
- 线协议字段以 [`PROTOCOL.md`](./PROTOCOL.md) 为准（`autopilot` 帧见 §2.8 / §3.11）。
- 模块签名以 [`INTERFACES.md`](./INTERFACES.md) §3 为准。
- 改动过接口（新增帧/字段）时，三处必须同步：本文件、`PROTOCOL.md`、`INTERFACES.md`。
