# ARCHITECTURE — 项目架构

复刻 [generals.io](https://generals.io) 的回合制领土战争小游戏：**Python 后端 + 原生 TypeScript 前端**。
本文件说明**文件结构、模块划分、依赖方向与数据流**；规则见 [RULES.md](./RULES.md)，线协议见
[PROTOCOL.md](./PROTOCOL.md)，模块调用契约见 [INTERFACES.md](./INTERFACES.md)。

---

## 1. 总览

```
┌──────────────────────────── 浏览器 ────────────────────────────┐
│  web/ (Vite + TypeScript, 无框架)                              │
│   正投影 Canvas 渲染 11×11 视口 · 键盘/鼠标输入 · 调兵链 UI      │
│   网络层只做「帧解码 → 状态树 diff → 重绘」                     │
└───────────────────────────┬────────────────────────────────────┘
              WebSocket (JSON 文本帧)     HTTP (GET /api/rooms, /waiting.html)
┌───────────────────────────┴────────────────────────────────────┐
│  server/ (FastAPI + uvicorn)                                   │
│   transport/  HTTP 与 WS 适配层（薄）                            │
│   net/        房间与座位、连接生命周期、广播                        │
│   game/       纯 Python 游戏内核（零 I/O、零时间、确定性）          │
│   ai/         AI 接口与内置机器人                                 │
└────────────────────────────────────────────────────────────────┘
```

**核心分层原则**

1. `server/game/**` 是**纯内核**：不 import `fastapi`、不读时钟、不用随机数，只吃 `GameState` 和指令、吐 `TickReport`。
   可被无服务端地单测、被 AI 离线模拟、被回放器复用。
2. `server/net/**` 负责把多路连接映射成房间与座位，把内核报告转成每名玩家**各自裁剪**的帧并下发。
3. `server/transport/**` 只做 FastAPI 路由与鉴权，不含业务。
4. `web/src/core/**` 镜像内核语义（回合、调兵链、地图内存模型），但不做战斗结算——战斗只在服务端。
5. 每个模块对外只暴露 `__init__.py` 中的**公开 API**（见 INTERFACES.md），内部实现以 `_` 前缀或非导出方式隐藏。

---

## 2. 目录结构

```
generals/
├── AGENT.md                     # 给 Agent/新人的「只看这一份就能上手」总览
├── README.md                    # 项目简介、玩法、运行方式
├── docs/
│   ├── RULES.md                 # 规则事实源 + 用户裁决 + 待澄清
│   ├── PROTOCOL.md              # WebSocket / HTTP 协议，帧格式，错误码
│   ├── ARCHITECTURE.md          # 本文件
│   ├── INTERFACES.md            # 各模块黑盒调用契约（含注释级签名）
│   └── AI_API.md                # 接入比赛策略 AI 的接口说明
├── server/
│   ├── generals/
│   │   ├── __init__.py
│   │   ├── app.py               # FastAPI 装配：静态托管 + 路由挂载（唯一组装点）
│   │   ├── config.py            # 常量（刻长、房间上限、地图尺寸…）
│   │   ├── game/                # ★ 纯游戏内核
│   │   │   ├── __init__.py      # 公开 API 汇总
│   │   │   ├── mapgen.py        # 地图生成（对称、可复现，播种由房间号派生）
│   │   │   ├── state.py         # GameState / Tile / Player 数据结构 + 查询（视野、面板）
│   │   │   ├── commands.py      # 玩家指令 → 调兵链操作
│   │   │   ├── combat.py        # 遭遇战 + 阵地战结算（纯函数）
│   │   │   ├── rules.py         # 增长、攻占、继承、停止抵抗、投降过渡期
│   │   │   ├── tick.py          # 单刻结算编排（R7.1 顺序）
│   │   │   └── view.py          # 视野裁剪：GameState → 每玩家可见视图
│   │   ├── net/
│   │   │   ├── __init__.py
│   │   │   ├── protocol.py      # 帧 schema + 校验 + 序列化（与 PROTOCOL.md 一一对应）
│   │   │   ├── lobby.py         # 房间表、加入/离开、座位分配
│   │   │   ├── room.py          # 单房间生命周期：等待 → 对局 → 结束；刻时钟驱动
│   │   │   └── session.py       # 单条 WS 连接的会话状态机
│   │   ├── ai/
│   │   │   ├── __init__.py
│   │   │   ├── api.py           # ★ AI 接口：Action / Observation / Agent 协议
│   │   │   ├── registry.py      # 策略注册表（名字 → 工厂）
│   │   │   ├── runner.py        # AI 座位：把 Observation 喂给策略、把 Action 回灌房间
│   │   │   └── builtin.py       # 内置示例策略（贪心扩张）
│   │   └── transport/
│   │       ├── __init__.py
│   │       ├── http_routes.py   # GET /api/rooms 等
│   │       └── ws_routes.py     # WS /ws
│   ├── tests/                   # pytest：内核单测 + 集成测
│   └── pyproject.toml
├── web/
│   ├── index.html               # 游戏页（结构对齐保存页 generals.io.htm）
│   ├── waiting.html             # 房间列表页
│   ├── src/
│   │   ├── main.ts              # 入口装配
│   │   ├── core/                # 纯逻辑（无 DOM）
│   │   │   ├── types.ts         # 与 PROTOCOL.md 对齐的类型
│   │   │   ├── client.ts        # WS 客户端（重连、发送）
│   │   │   ├── state.ts         # 客户端状态树 + 帧应用（diff）
│   │   │   ├── chains.ts        # 调兵链本地镜像 + 比例状态机
│   │   │   └── geometry.ts      # 坐标换算、4/8 相邻
│   │   ├── render/              # Canvas 渲染（只读核心状态）
│   │   │   ├── camera.ts        # 正投影视口与缩放
│   │   │   ├── painter.ts       # 地块/兵量/选中/路径绘制
│   │   │   └── icons.ts         # 王冠/城市/山地 图标与调色板
│   │   ├── ui/                  # DOM 面板
│   │   │   ├── leaderboard.ts   # 排行榜（星/骷髅/兵量/地盘）
│   │   │   ├── turn.ts          # 回合计数器
│   │   │   └── chat.ts          # 系统消息与聊天
│   │   └── input/
│   │       ├── keyboard.ts      # WASD/Q/E/+/-/Esc
│   │       └── pointer.ts       # 点击选中、点击调兵
│   ├── styles/                  # CSS（对齐截图配色与字体）
│   └── vite.config.ts
├── scripts/                     # 开发脚本（一键起服务、构建前端）
├── SaveWeb/                     # 用户提供的原站存档与截图（视觉基准，只读）
└── .gitignore
```

---

## 3. 依赖方向（禁止反向）

```
transport ──► net ──► game
                 └──► ai ──► game
web (浏览器) ──(协议)──► transport
```

- `game` **不得** import `net` / `ai` / `transport` / `fastapi` / `time` / `random`（地图生成用注入的 `Random(seed)`）。
- `ai` 只依赖 `game` 的**只读视图**，不得直接改写状态。
- `net` 不直接操作地块数组，只调用 `game.tick.step(state)` 等公开 API。
- `web/src/render` 与 `web/src/ui` 不得互相 import；都由 `main.ts` 装配。

---

## 4. 数据流：一个 Tick 的生命周期

```
Room 内部时钟 (asyncio, 500ms/Tick)
  │  ① 收集：各座位在上一 Tick 提交的 commands（入队调兵链）
  │  ② tick.step(state) → TickReport
  │       ├ 阶段 1 增长      rules.apply_growth
  │       ├ 阶段 2 发起调兵  tick.launch_moves   (每玩家 FIFO 弹 1 条, R4.7)
  │       ├ 阶段 3 遭遇战    combat.resolve_encounters
  │       ├ 阶段 4 阵地战    combat.resolve_battles
  │       ├ 阶段 5 更新占领  rules.apply_captures (含王城继承 R5.2)
  │       └ 阶段 6 停止抵抗  rules.check_eliminations (含投降过渡期 C-08)
  │  ③ view.project(state, seat) → 每座位各自的帧
  │  ④ Room 广播：turn / visible-map diff / leaderboard / chat / events
  ▼
WebSocket 客户端 → client.ts 解帧 → state.ts 应用 → render + ui 重绘
```

**关键不变量**

- 结算只读「刻开始时的状态 + 本刻指令」，所有遍历按 `(owner_id, tile_index)` 升序（R7.3）。
- 每个座位的帧是对**同一份权威状态**的裁剪，裁剪规则集中在 `game/view.py`，保证不同玩家不会看到不一致的结算。
- 前端不做任何战斗预测：点击调兵只改本地调兵链与选中态，真实结算等下一帧。

---

## 5. 前端渲染（对齐截图）

- **正投影**：地图为正方形，视口为 `11 × 11` 地块，居中于摄像机；`--cell-size` 用 CSS 变量驱动，
  等价于原站 `#gameMap` 的 `--cell-size / --bg-size / --number-size` 三档尺寸。
- **配色**：`styles/palette.css` 定义 8 个玩家色（`red/blue/green/teal/purple/pink/maroon/orange`）+ 中立灰/山地深灰，
  与截图排行榜的色块一一对应。
- **视野外**：地形被"隐藏"，只画山地或平原外观（C-17），不显示兵量。
- **选中**：选中地块描边 + 调兵链箭头（数字表示该请求的兵力）。
- **排行榜**：`★` 星数、玩家色块、骷髅图标（已出局）、Army / Land 三列。

视觉基准：`SaveWeb/屏幕截图 *.png` 与 `SaveWeb/generals.io.htm`。

---

## 6. 工程约定

- Python：类型注解 + `dataclass`，无全局可变状态；`ruff` 风格；`pytest` 覆盖内核规则。
- TypeScript：`strict` 模式；`core` 层不碰 DOM，可被 Node 单测。
- 每个逻辑变更一个 git commit，提交身份 `cosmos`，不推送远程。
- 新增规则必须先在 `docs/RULES.md` 加编号，再改代码与测试。
