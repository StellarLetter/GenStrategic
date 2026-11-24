# REPLAY — 对局回放与策略复盘

> **每一局对局都自动录制、结束时自动保存**，无需任何开关。回放的格式、字段语义与
> 「什么时候能读」以本文件为唯一事实源（规则条文见 [`RULES.md`](RULES.md) §8）。

---

## 1. 一句话

对局开始 → 服务器在内存里录下**这一局生成的每一帧**与**各策略的调试条目** → 对局结束
（或房间被关闭）→ 整份写成 `replays/<id>.json`。**对局进行中磁盘上没有这一局的文件，
接口也拒绝读取**（R8.2），所以策略无法读回放作弊。

| 能力 | 位置 |
| --- | --- |
| 自动录制 / 落盘 / 保留 | `server/generals/replay/`（`ReplayRecorder` / `ReplayStore`） |
| 策略写调试日志 | `generals.ai.log()`（`docs/AI_API.md` §11） |
| 列表 / 详情 / 删除 | `GET /api/replays`、`GET /api/replays/{id}`、`DELETE /api/replays/{id}` |
| 网页复盘 | 对局页右侧「回放」入口 → `/replay.html?id=<id>` |
| 终端复盘 | `.venv/Scripts/python.exe tools/replay.py show <id> --ai` |
| 离线批量对战 | `tools/ai_arena.py` 的 `--replay-dir <dir>`（每局都留档） |

---

## 2. 文件格式（`format = "generals.replay"`，`version = 1`）

一份回放是一个**纯 JSON 对象**（UTF-8，无缩进）。读取方无需任何服务端代码即可复现整局画面。

```jsonc
{
  "format": "generals.replay",
  "version": 1,

  "id": "20261124T120530Z-public-1-050548",  // == 文件名主干
  "room": "public-1",                        // 房间号
  "seed": 1234567890123456789,               // 地图种子（与玩家数一起可复现地图，R2.3/R2.6）
  "size": 26,                                // 地图边长（R2.3 算出的实际值）
  "tickMs": 500,                             // 每 Tick 毫秒（R1.1）
  "startedAt": "2026-11-24T12:05:30.532+00:00",   // UTC，毫秒精度
  "endedAt":   "2026-11-24T12:18:02.115+00:00",
  "reason": "ended",                         // "ended" = 自然终局；"aborted" = 房间中途关闭；
                                             // "timeout" = 离线基准（tools/ai_arena.py）打满 Tick 上限

  "players": [                               // 开局玩家清单，按 playerId 升序（R7.3）
    { "playerId": 0, "seat": 0, "name": "Alice", "color": "red",
      "bot": null, "autopilot": "aegis" }     // bot = 机器人座位；autopilot = 真人座位托管
  ],

  "result": { "ended": true, "winner": 0, "tick": 588, "turn": 294, "half": 0 },

  "truncated": false,     // 帧流撞到字节上限（§6），后面的帧没录
  "aiTruncated": false,   // AI 条目撞到条数上限（§6）

  "frames": [ /* §2.1 */ ],
  "ai":     [ /* §2.2 */ ]
}
```

### 2.1 `frames` — 与线协议**同构**的帧流

每条帧就是 `docs/PROTOCOL.md` §3 的一条**下行帧**（`game_start` / `turn` / `tiles` /
`leaderboard` / `event` / `chat` / `game_over` / `autopilot`），外加一个字段：

| 附加字段 | 类型 | 说明 |
| --- | --- | --- |
| `tick` | `int` | 该帧**产生时**的 Tick 序号（`0` = 开局帧；`tick.step()` 之后为已进入的刻） |

因此**播放回放 = 按顺序把 `frames` 喂给客户端状态机**：前端 `web/src/core/state.ts::applyFrame`
原样可用，不需要任何回放专用的解码逻辑。

与实况的三点差别（都是为了让回放自洽、且不泄露信息）：

| 差别 | 实况 | 回放 |
| --- | --- | --- |
| `tiles` 帧 | 每人各自视角的增量（迷雾裁剪） | **观战者视角**的增量（全图真实，R3.10）——各座位视角的帧互不相同，无法在一份文件里共存 |
| `commands` 帧 | 每人各自的调兵链回显 | **不录**（属座位私有）；改为录进 `ai` 流的 `action` / `drop` 条目 |
| `reject` 帧 | 只发给当事人 | **不录帧**；改为录进 `ai` 流的 `reject` 条目 |

开局全量 `tiles` 一定存在（`frames[1]` 左右），客户端从空状态直接开始播放即可。

### 2.2 `ai` — 策略调试流

按发生顺序排列，每条都带 `tick` / `playerId` / `seat` / `strategy`：

```jsonc
{ "tick": 42, "playerId": 0, "seat": 0, "strategy": "aegis",
  "kind": "log", "level": "debug", "text": "turn 21: army=137 land=48" }
{ "tick": 42, "playerId": 0, "seat": 0, "strategy": "aegis",
  "kind": "action", "action": {"op":"move","from":312,"dir":"up","ratio":1.0} }
{ "tick": 42, "playerId": 0, "seat": 0, "strategy": "aegis",
  "kind": "error", "text": "TypeError: '<' not supported between instances of 'NoneType' and 'int'" }
{ "tick": 42, "playerId": 0, "seat": null, "strategy": null,
  "kind": "reject", "op": "move", "seq": 0, "code": "not_adjacent" }
{ "tick": 43, "playerId": 1, "seat": 1, "strategy": null,
  "kind": "drop", "from": 208, "dir": "up", "ratio": 1.0, "reason": "src_lost", "cascade": 2 }
```

| `kind` | 来源 | 字段 | 用途 |
| --- | --- | --- | --- |
| `log` | 策略自己调 `generals.ai.log(text, level=…)` | `level` ∈ `{debug,info,warn,error}`、`text` | 策略作者自定义的复盘线索（**本条功能的主要目的**） |
| `action` | `AIRunner` 每次成功产出指令 | `action` = 线协议指令 dict（比例已量化，R6.6） | 看「这一刻它决定干什么」 |
| `error` | 策略 `act`/`reset` 抛异常、或 `Action` 非法 | `text` = `"<异常类型>: <消息>"` | 异常被吞掉后的唯一痕迹（否则对局继续跑、策略默默不动） |
| `reject` | 服务端拒绝了一条指令（`PROTOCOL` §5） | `op`、`seq`（批内序号）、`code` | 区分「策略想干」与「服务端不许」 |
| `drop` | 调兵请求在发起阶段被作废（R4.12） | `from`、`dir`、`ratio`、`reason`、`cascade` | 解释「为什么我的调兵没发生」 |

> `log` 的文本按 `config.REPLAY_AI_LOG_MAX_LEN`（默认 500 字符）截断，条目总量按
> `config.REPLAY_AI_LOG_MAX`（默认 20000 条）封顶（§6）。

---

## 3. 录制时序（服务端内部）

```
Room.start()
  ├─ mapgen.generate(seed)                      → GameState
  ├─ ReplayStore.begin(...)                     → ReplayRecorder（**内存**，绑定 state）
  ├─ 建策略 runner（reset 里的 log 也能录进来）
  ├─ 广播 game_start / 全量 tiles（逐座位）        → 录 frame；另录一份观战者全量 tiles
  └─ 每 Tick（Room._advance）
        ├─ 广播 turn / tiles（逐座位）/ leaderboard / commands（逐座位）
        ├─ 录制：turn、排行榜、事件、聊天…（`Room.broadcast` 里顺手录）
        ├─ 录制：观战者视角 tiles 增量（`Room._record_tiles`）
        ├─ 录制：chain_dropped（`_record_drop`）与 reject（`_record_reject`）
        └─ 终局：广播 game_over 与战报 → **之后**才落盘（帧序才完整）
Room.close()（对局未结束时）
  └─ ReplayStore.save(reason="aborted")
```

**目录结构**

```
<repo>/replays/                     # 可用 GENERALS_REPLAY_DIR 覆盖
  index.json                        # 列表缓存（免去逐份读大文件）
  20261124T120530Z-public-1-050548.json
  …
```

落盘是**原子写**（先写 `*.json.tmp` 再 `os.replace`），因此不会出现半截文件；
`index.json` 只是列表用的缓存，删掉会自动按目录重建。

---

## 4. 「对局中不可读取回放」（R8.2）

三重保证：

1. **物理**：录制期间数据只在内存缓冲里，磁盘上没有这一局的文件（有测试锁定这一点）；
2. **接口**：`ReplayStore.load()` 若发现该 id 仍在录制中，抛 `ReplayRunning` →
   `GET /api/replays/{id}` 返回 **409 `replay_running`**；`DELETE` 同样 409；
3. **策略 API**：策略侧唯一的日志入口 `generals.ai.log()` 只能**写**，没有任何读回放的函数；
   `ai/` 目录里的策略也 import 不到 `generals.replay`（依赖方向见 ARCHITECTURE.md §3）。

> 对局被中断（房间关闭、进程退出前 `close()`）时，已录到的部分仍会以 `reason="aborted"` 落盘，
> 所以「打到一半崩了」也留得下现场。

---

## 5. 怎么用

### 5.1 网页复盘

对局页（`/`）顶部的「回放」按钮打开最近的对局列表；也可以直接开 `/replay.html?id=<id>`。

`/replay.html` 的界面：地图（全图真实信息）+ 播放/暂停、`0.5×`–`16×` 倍速、
可拖动的时间轴（按 Tick）+ 回合显示 + 排行榜 + 聊天栏 + **AI 调试流面板**
（按玩家筛、按 `kind` 筛、点条目跳到对应 Tick）。

快捷键：`空格` 播放/暂停、`←`/`→` 退/进 1 Tick、`Shift+←/→` 退/进 10 Tick。
画面可拖拽平移、滚轮缩放（与对局页同一套摄像机）。

### 5.2 终端复盘

```powershell
# 列出最近的回放
.venv\Scripts\python.exe tools\replay.py list

# 看一局的概要（双方策略、胜负、回合数、各类 AI 条目计数）
.venv\Scripts\python.exe tools\replay.py show 20261124T120530Z-public-1-050548

# 把某策略的调试日志全部打出来（带 tick）
.venv\Scripts\python.exe tools\replay.py show <id> --ai --player 0 --kinds log,error
```

### 5.3 策略里写日志

```python
from generals.ai import log

def act(self, obs):
    log(f"turn {obs.turn}: army={obs.my_army} land={obs.my_land}")
    if 卡住:
        log("被三面夹击，改守王城", level="warn")
    return None
```

日志**只在有对局录制时进回放**；离线脚本里 `print` 与 `log` 都不报错（`log` 没有 sink 时静默丢弃）。
开发时想边打边看：设置 `GENERALS_AI_LOG_ECHO=1`，同样的日志会打到 stderr。

### 5.4 离线批量对战留档

```powershell
.venv\Scripts\python.exe tools\ai_arena.py --a aegis --b greedy --games 20 --swap --replay-dir .dispatch\replays
```

### 5.5 验收（e2e）

```powershell
.venv\Scripts\python.exe tools\replay.py demo --dir .dispatch\replays   # 造一份带日志/拒绝/作废的样本
.venv\Scripts\python.exe scripts\e2e_replay.py                          # 自造样本 → 真服务 → 真 Edge 断言
```

`scripts/e2e_replay.py` 会自动建临时目录、跑一局离线对局产出样本、起 uvicorn 并驱动
`web/scripts/e2e-replay.mjs`；`.\scripts\test.ps1` 已把它纳入全量验收。

---

## 6. 上限与保留（磁盘保险丝）

| 常量（`server/generals/config.py`） | 默认 | 语义 |
| --- | --- | --- |
| `REPLAY_MAX_BYTES` | 32 MiB | 单份回放的**帧流**估算字节上限；撞到即停止录帧并置 `truncated` |
| `REPLAY_AI_LOG_MAX` | 20000 | 单局 AI 条目上限；撞到即停止追加并置 `aiTruncated` |
| `REPLAY_AI_LOG_MAX_LEN` | 500 | 单条 AI 文本最大字符数（超出截断） |
| `REPLAY_KEEP` | 200 | 目录内最多保留几份回放（按 `endedAt` 最旧先删） |

> 回放量级参考：2 人局 400 Tick 约 140 KB；8 人 40×40 的长局约 1–3 MB。

---

## 7. 变更纪律

- 字段名/语义变更 → `version` +1，并同步本文件第 2 节与 `web/src/core/replay.ts` 的解析。
- 新增 `ai` 条目的 `kind` → 同步 §2.2 表格、`web/src/replay/*` 的筛选器与 `tools/replay.py`。
- 新增回放相关规则 → 先写 `docs/RULES.md` §8，再改代码与测试。