# PROTOCOL — 网络协议

传输：**WebSocket 文本帧，JSON**。一个连接 = 一个会话；服务器主动推送，客户端只发指令。
所有时间以 **Tick** 计（`0.5s` = 1 Tick，`Turn N` / `Turn N.`）。

---

## 1. 连接

```
ws://<host>:<port>/ws
```

连接后客户端必须首先发送 `join`（§2.1），否则服务器在 10s 后断开。

服务器下发帧都是 `{ "t": "<type>", ... }`，客户端上行帧都是 `{ "t": "<type>", ... }`，
未知 `t` 一律忽略（向前兼容），未知字段一律忽略。

---

## 2. 上行（Client → Server）

### 2.1 `join`
```json
{ "t": "join", "name": "Alice", "room": "custom-1", "spectate": false }
```
| 字段 | 类型 | 必需 | 说明 |
| --- | --- | --- | --- |
| `name` | string(1..16) | ✔ | 昵称，重名自动加后缀 |
| `room` | string | ✘ | 缺省 = 加入/创建公共房 |
| `spectate` | bool | ✘ | true = 观战者占位（不占玩家座） |

回应：`{ "t": "joined", "playerId": 3, "roomId": "...", "seat": 0, "token": "..." }`

### 2.2 `spectate`
```json
{ "t": "spectate", "room": "custom-1" }
```

### 2.3 `ready`
```json
{ "t": "ready" }
```
房主开局：`{ "t": "start" }`。

### 2.4 `commands`
一次可携带多条的**指令批**，服务器按顺序对调兵链执行：
```json
{ "t": "commands", "cmds": [
  { "op": "move",   "from": 202, "dir": "up",   "ratio": 1.0 },
  { "op": "clear" },
  { "op": "pop" },
  { "op": "select", "tile": 205 }
] }
```
| `op` | 字段 | 语义 | 规则 |
| --- | --- | --- | --- |
| `move` | `from: int`, `dir: "up"\|"down"\|"left"\|"right"`, `ratio: float` | 向 4 相邻调兵入队 | R4.1 / R6.2 / R6.5 / R6.6 |
| `clear` | — | 取消完整调兵链 | R6.4 `Q` |
| `pop` | — | 取消最新调兵请求 | R6.4 `E` |
| `select` | `tile: int` | 选中（仅用于服务端校验与观战同步） | R6.1 |

> 服务端只在**出错**时回 `reject`（附 `code`，§5），不逐条回 `ack`——
> 前端不需要确认帧也能靠权威 `commands` 回显（§3.6）自我校正。
> 调兵请求只记录 `(from, dir, ratio)`，**实际兵力在结算时才计算**（R3 附录、R4.1）。
>
> **`ratio` 只取 10% 的整数倍**（R6.6）：`{0.1, 0.2, …, 1.0}`。缺省 `1.0`；其它值（含任意小数、
> 越界值、负数）一律**就近取整**到最近的 10% 档位（正好在中间时向上取整，如 `0.37 → 0.4`、
> `0.95 → 1.0`），不因此拒绝整条指令；非有限数值（`NaN` / `±inf`）回 `bad_frame`。
> 真人与 AI 走同一量化入口，因此 AI 不可能调出真人无法选择的比例。
>
> **`from` 不要求是当前控制的地块**：它可以是调兵链里任意请求的目标（**预调兵**，R4.11）。
> 服务端入队时只校验越界 / 方向 / 山地 / 链长，**起点归属与调兵量留到发起调兵阶段判定**
> （R4.12）；作废时下发 `chain_dropped` 事件并重发 `commands`。

### 2.4.1 `ack`（保留帧，默认不发）
```json
{ "t": "ack", "op": "move", "seq": 12 }
```
仅当服务端需要显式确认「指令已接受且产生了可观察副作用」时才发送。
当前实现对**所有**合法指令都不发 `ack`；客户端必须能容忍它完全缺失。

### 2.4.2 未开局时的指令
`move` 回 `reject(code="not_started")`；
`select`/`clear`/`pop` **静默接受**（无副作用），便于前端在等待房间时预热调兵链。

### 2.5 `chat`
```json
{ "t": "chat", "text": "gg" }
```

### 2.6 `surrender`
```json
{ "t": "surrender" }
```
进入 40 Tick 过渡期（R5.5）。

### 2.7 `ping`
```json
{ "t": "ping", "n": 12 }
```
回应 `{ "t": "pong", "n": 12 }`。

---

## 3. 下行（Server → Client）

### 3.1 `game_start`
```json
{ "t": "game_start", "size": 26, "players": [
  { "playerId": 3, "name": "Alice", "color": "red", "seat": 0 },
  { "playerId": 7, "name": "Bob",   "color": "blue", "seat": 1 }
] }
```

### 3.2 `turn`
```json
{ "t": "turn", "turn": 12, "half": 1 }
```
`half: 0` ⇒ `Turn N`（上半回合）；`half: 1` ⇒ `Turn N.`（下半回合）。

### 3.3 `tiles` — 视野增量
只发送**本刻发生变化且在当前视野内**的地块：
```json
{ "t": "tiles", "list": [
  [202, 12, 0, 3],      // index, army, ownerId, terrain
  [205, 0,  null, 2]    // ownerId null = 中立
] }
```
地块四元组 `[index, army, ownerId, terrain]`：
| terrain | 含义 |
| --- | --- |
| `0` | 平原 |
| `1` | 首都（仅视野内） |
| `2` | 山地 |
| `3` | 超市 |
| `4` | 首都转化型城市（R5.4，视野内表现同超市） |

> 视野外，服务端把地形折叠为 `0`（可见外观=平原）或 `2`（可见外观=山地）——
> 即 R6.9：山地与城市 → `2`；平原/首都/首都转化型城市 → `0`；`army = null`。

### 3.4 `sight` — 视野集合变化
```json
{ "t": "sight", "add": [201, 202], "remove": [140] }
```
客户端可据此本地推演未知区域；不强制使用。

### 3.5 `leaderboard`
```json
{ "t": "leaderboard", "rows": [
  { "playerId": 3, "name": "Alice", "color": "red", "army": 2499, "land": 208,
    "alive": true, "surrenderIn": null, "star": 0 }
] }
```
`army` = 全部地块兵力之和，`land` = 控制地块数（R6.8），均已对所有人公开。
`star` 是排行榜首列的「星数」（原站为历史战绩分）。本项目**没有评级系统，恒为 `0`**，
字段保留只为对齐原站界面（R6.12）。

### 3.6 `commands` — 权威调兵链回显
```json
{ "t": "commands", "chain": [
  { "from": 202, "dir": "up", "ratio": 1.0 }
] }
```

### 3.7 `event`
```json
{ "t": "event", "kind": "captured", "turn": 37, "who": "Alice", "target": "Bob" }
```
`kind ∈ { captured, left, surrendered, kicked, game_over, chain_dropped }`。

`chain_dropped`（R4.12 调兵请求作废）**只发给该请求的拥有者**，同时该玩家会立刻收到一份新的 `commands` 全量快照：
```json
{ "t": "event", "kind": "chain_dropped", "playerId": 0, "from": 202,
  "dir": "up", "ratio": 1.0, "reason": "src_lost", "cascade": 2 }
```
- `playerId`：请求的拥有者（与 `joined.playerId` 同源）。
- `reason ∈ { "src_lost", "zero_army" }`：起点已不归自己 / 算出的调兵量 `≤ 0`。
- `cascade`：级联一并作废的后续请求条数（`0` 表示没有下游依赖）。

### 3.8 `chat`
```json
{ "t": "chat", "turn": 12, "name": "Alice", "color": "red", "text": "gg", "system": false }
```

### 3.9 `game_over`
```json
{ "t": "game_over", "winner": 3, "turn": 294 }
```

### 3.10 `reject`
```json
{ "t": "reject", "op": "move", "seq": 4, "code": "not_owned" }
```

---

## 4. HTTP

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/rooms` | 房间列表 `[{id, name, players, maxPlayers, state}]` |
| `GET` | `/api/config` | 客户端启动参数 `{tickMs, maxPlayers, minSize, maxSize, colors}` |
| `GET` | `/healthz` | 存活探针 `{"ok": true}` |
| `GET` | `/waiting.html` | 房间列表页 |
| `GET` | `/` | 游戏页 |

> 地图边长不由客户端指定（R2.3）：开局时按玩家数计算，实际值从 `game_start.size` 读取。

---

## 5. 错误码

| code | 含义 |
| --- | --- |
| `bad_frame` | JSON 解析失败或结构不符合 schema |
| `not_joined` | 尚未 join |
| `not_started` | 对局未开始 |
| `not_your_turn` | 已出让（出局/投降过渡期） |
| `not_owned` | 起点地块不归你控制（**仅结算阶段判定**，见 §3.7 `chain_dropped`；入队不再返回） |
| `not_adjacent` | 方向越界或非相邻 |
| `blocked` | 目标为山地（R4.9） |
| `chain_full` | 调兵链超过上限（R6.11） |
| `room_full` | 房间已满 |
| `rate_limited` | 指令过于频繁 |
