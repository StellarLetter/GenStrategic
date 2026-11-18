# AGENT.md — 上手总览

> 只读本文件 + `docs/` 即可掌握：文件结构、项目架构、各模块位置与功能、规则与纪律。

---

## 0. 一句话

用 **Python（FastAPI + 纯内核）** 写服务端、**原生 TypeScript + Canvas（Vite）** 写前端，
复刻 [generals.io](https://generals.io)：一回合 1 秒 = 两个半回合，4 相邻调兵、阵地战/遭遇战、
王城斩首继承领土、40 Tick 投降过渡期。

---

## 1. 必读顺序

| 顺序 | 文件 | 内容 |
| --- | --- | --- |
| 1 | **`docs/RULES.md`** | **规则唯一事实源**。编号 `R*`、用户裁决 `C-*`、待澄清 `P-*`。改规则必须先改这里。 |
| 2 | `docs/ARCHITECTURE.md` | 分层、依赖方向、目录树、一个 Tick 的数据流 |
| 3 | `docs/INTERFACES.md` | 每个模块的黑盒调用契约（签名 + 前后置条件） |
| 4 | `docs/PROTOCOL.md` | WebSocket / HTTP 帧格式与错误码 |
| 5 | `docs/AI_API.md` | 比赛策略 AI 的接入接口 |

---

## 2. 目录速查

```
generals/
├── AGENT.md  README.md
├── docs/                    # RULES / ARCHITECTURE / INTERFACES / PROTOCOL / AI_API
├── server/
│   ├── generals/
│   │   ├── app.py           # FastAPI 装配点（唯一组装根）
│   │   ├── config.py        # 常驻参数
│   │   ├── game/            # ★ 纯内核：mapgen / state / commands / combat / rules / tick / view
│   │   ├── net/             # 房间与连接：lobby / room / session / protocol
│   │   ├── ai/              # AI 接口：api / registry / runner / builtin
│   │   └── transport/       # FastAPI 路由：http_routes / ws_routes
│   ├── tests/               # pytest（内核规则单测 + 集成测）
│   └── pyproject.toml
├── web/                     # Vite + TS，无框架
│   ├── index.html           # 对局页
│   ├── waiting.html         # 房间列表页
│   ├── src/core/            # 纯逻辑：types / geometry / state / chains / client（不碰 DOM）
│   ├── src/render/          # Canvas：palette / camera / icons / painter
│   ├── src/ui/              # DOM：leaderboard / turn / chat
│   ├── src/input/           # keyboard / pointer
│   ├── src/main.ts          # ★ 前端唯一装配点
│   ├── styles/              # palette.css（配色变量）/ game.css / waiting.css
│   └── scripts/             # shot.mjs（截图）、e2e-page.mjs（真浏览器联调）
├── scripts/                 # dev.ps1 / test.ps1 / e2e_smoke.py
└── SaveWeb/                 # 用户提供的原站存档与截图 = 视觉基准（只读，勿改）
```

---

## 3. 架构一图

```
web (TS, 无框架)  ──WebSocket JSON──►  transport/ ──► net/ ──► game/ (纯内核)
                                          ▲                    ▲
                                          └── ai/ (策略) ──────┘ 只读视野
```

**依赖只能向下**：`transport → net → game`，`ai → game`；`game` 不 import 任何上层，不碰 I/O、时钟、随机。

**一个 Tick**：`增长 → 发起调兵 → 遭遇战 → 阵地战 → 更新占领 → 检查停止抵抗`（R7.1），
然后 `view.project()` 按座位裁剪并广播。前端不做战斗预测。

> `rules.step(state)` 的时序约定：**先把 `state.tick` 自增，再用新的 `(turn, half)` 走六个阶段**。
> 也就是「`step()` 把状态推进到第 `tick` 刻并结算该刻」，`report.tick` 是**已进入**的刻。
> 开局 `tick=0` 即 `Turn 0`；第一次 `step()` 落在 `tick=1` = `Turn 0.`，所以开局首刻就会增长（C-01）。

---

## 4. 纪律（硬性）

1. **规则不可猜测**。任何规则空白先写进 `docs/RULES.md` 附录 B 的「待澄清」，向用户确认后再实现。
   已经裁决的写在附录 A（`C-*`），实现与测试必须引用编号。
2. **对称一致**（R7.3）：无随机、无时间依赖；所有遍历按 `(owner_id, tile_index)` 排序，禁止依赖 dict 插入序。
3. **高内聚低耦合**：模块只暴露 `__init__.py` 的公开符号；跨层禁止 import 内部实现。
   函数级注释必须写清「前置 / 后置 / 抛出」，达到可直接黑盒调用的程度。
4. **纯内核**：`server/generals/game/**` 不允许出现 `import fastapi`、`time.time()`、`random.random()`。
5. **版本控制**：每个逻辑变更一个 commit，提交身份 `cosmos`，不推送远程；同步更新 README。
6. **视觉基准**：`SaveWeb/generals.io.htm` 与 `SaveWeb/屏幕截图 *.png`，任何样式改动需对照。

---

## 5. 常用命令

```bash
# ---- 首次安装 ----
python -m venv .venv
.venv/Scripts/python -m pip install -e "server[dev]" -i https://pypi.tuna.tsinghua.edu.cn/simple
cd web && pnpm install && cd ..

# ---- 一条命令跑起来（构建前端 + 起服务，页面/API/WS 同端口）----
.\scripts\dev.ps1 -Port 8000
#    http://127.0.0.1:8000/               对局页
#    http://127.0.0.1:8000/waiting.html   房间列表页
# 等价写法：.venv/Scripts/python -m generals --port 8000 [--reload]

# ---- 开发热更新（两个终端）----
.venv/Scripts/python -m generals --port 8000 --reload
cd web && pnpm dev        # http://127.0.0.1:5173 → 代理 /ws、/api 到 :8000

# ---- 测试 ----
.\scripts\test.ps1                                    # 全量：pytest + tsc + build + e2e
.venv/Scripts/python -m pytest server/tests -q        # 只跑后端
.venv/Scripts/python scripts/e2e_smoke.py --ticks 20  # 只跑协议级端到端
cd web && node scripts/e2e-page.mjs http://127.0.0.1:8000 ../.dispatch/live.png  # 真浏览器

# ---- 前端 ----
cd web && pnpm exec tsc --noEmit   # 类型检查（strict + noUnusedLocals）
cd web && pnpm build               # 产物 web/dist，由后端静态托管
```

> `?demo=1` 是不连服务端的本地演示棋盘，用来做视觉比对截图（见 `web/src/main.ts` 的 `loadDemo`）。

---

## 6. 玩法操作

| 输入 | 行为 | 规则 |
| --- | --- | --- |
| 点击地块 | 选中 | R6.1 |
| `W`/`A`/`S`/`D` | 向 4 相邻调兵并选中该地块 | R6.2 |
| 再次点击当前选中地块 | 比例切到 50% | R6.6 |
| `+` / `-` | 比例 ±10%（10%..100%） | R6.6 |
| 点击调兵（可开关） | 点击选中地块的相邻地块触发一次调兵 | R6.5 |
| `Q` / `E` | 清空 / 取消最新一条调兵请求 | R6.4 |
| `Enter` | 聊天 | — |

---

## 7. 状态

当前实现进度、已知缺口、待澄清事项统一维护在 `docs/RULES.md` 附录 B 与仓库 README 的「实现进度」一节。
