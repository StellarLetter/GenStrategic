# INTERFACES — 模块黑盒调用契约

只读本文件即可调用任意模块，**无需阅读其内部实现**。
每个条目给出：职责、公开符号、签名、前置/后置条件、抛出、线程/异步约束。

约定：
- 所有公开符号都从模块的 `__init__.py` 导出；`_` 前缀的一律视为私有。
- `TileIndex` = `int`（`row * size + col`）。`PlayerId` = `int`（`0..7`）。
- 所有函数**纯同步**；异步只存在于 `net` / `transport`。

---

## 1. `server/generals/game` — 纯游戏内核

### 1.1 `game.state`

```python
class Terrain(IntEnum):
    PLAIN = 0; CAPITAL = 1; MOUNTAIN = 2; CITY = 3

class Tile:
    """地块。可变结构，仅内核内部与 view 层读取。"""
    terrain: Terrain        # 真实地形
    army: int               # 兵力，>=0
    owner: int | None       # None = 中立

class PlayerState:
    player_id: int
    name: str
    color: str
    alive: bool
    capital: int | None          # 王城 index；被攻占后为 None
    chain: list[MoveRequest]     # 调兵链（FIFO，至多 1 条/Tick 会被消费）
    surrender_ticks_left: int | None  # 投降过渡期剩余 Tick（R5.5）；None = 未投降

class GameState:
    size: int
    tiles: list[Tile]
    players: dict[int, PlayerState]
    tick: int                   # 全局 Tick 序号（见 turn_of）
    ended: bool
    winner: int | None

    # 纯查询，O(1) / O(k)
    def neighbours4(self, idx: int) -> list[int]
    def neighbours8(self, idx: int) -> list[int]
    def visible_tiles(self, player_id: int) -> set[int]     # R3.5
    def army_total(self, player_id: int) -> int             # R6.8
    def land_count(self, player_id: int) -> int             # R6.8

def turn_of(tick: int) -> tuple[int, int]:
    """tick -> (turn, half)。half=0 表示 Turn N，half=1 表示 Turn N.（R1.2）。"""
```

**约束**：`GameState` 之外**没有**全局状态；同一 `GameState` 只能被其所属 `Room` 的 loop 触碰。

### 1.2 `game.mapgen`

```python
class MapGenError(ValueError):
    """玩家数/边长越界，或换序列重试后仍摆不下满足 R2.5 的首都。"""

CAPITAL_MIN_MANHATTAN = 13     # R2.5
MAX_ATTEMPTS = 64              # R2.6 换序列重试上限

def generate(player_count: int, rng: random.Random, *, size: int | None = None) -> GameState:
    """生成**无对称**随机地图（R2.1–R2.7 / R7.3）。
    前置：1 <= player_count <= MAX_PLAYERS；rng 已由调用方播种；
          size=None 时按 R2.3 公式 `clamp(6 + 3N + rand(0..6), 10, 40)` 自行计算
          （jitter 取自同一个 rng），显式传入仅用于测试/回放。
    后置：**无任何对称保证**（R2.4）；恰好 player_count 名 alive 玩家各拥一座
          army=1 的首都（R3.2）；任意两首都曼哈顿距离 >= 13（R2.5）；
          中立城市 army ∈ [40, 50]（R3.3）；同种子逐格可复现。
    抛出：MapGenError。调用方不得自己假设边长，一律读 `state.size`。
    """
```

**边长只由人数决定**，客户端/房间不指定（R2.3）。

### 1.3 `game.commands`

```python
class Dir(Enum):
    UP, DOWN, LEFT, RIGHT

@dataclass(frozen=True)
class MoveRequest:
    src: int
    dir: Dir
    ratio: float        # (0, 1]，入队时的比例（R6.7）

CHAIN_LIMIT = 20

class CommandError(Exception):
    code: str           # 见 PROTOCOL §5

def enqueue_move(state, player_id, src, direction, ratio) -> None:
    """入队一条调兵请求（R4.11）。
    前置：对局进行中、玩家可操作、src 是界内整数、方向未越界、目标非山地（R4.9）。
          **不校验 src 归属** —— 预调兵允许 src 是链中任意请求的目标；合法性
          推迟到发起调兵阶段判定（见 §1.6）。
    后置：chain 末尾追加；ratio clamp 到 (0, 1]；超过 CHAIN_LIMIT 抛 CommandError('chain_full')。
    抛出：CommandError，code ∈ {not_started, not_joined, not_your_turn, bad_frame,
          not_adjacent, blocked, chain_full}；**不产生 not_owned**。"""

def clear_chain(state, player_id) -> int: ...    # 返回被清条数
def pop_chain(state, player_id) -> MoveRequest | None: ...
```

### 1.4 `game.combat` — 纯函数

```python
@dataclass(frozen=True)
class Flight:
    owner: int          # 势力 player id
    src: int
    dst: int
    amount: int

def resolve_encounters(flights: list[Flight]) -> tuple[list[Flight], list[EncounterResult]]:
    """R4.5 / R7.2：找出互为出发↔目标的成对兵力，抵消。
    后置：返回 (剩余继续飞行的 flights, 交锋记录)；不修改输入的 dst 顺序以外的任何东西。"""

def resolve_battle(arrivals: list[Flight], tile: Tile) -> BattleResult:
    """R4.3 / R4.4：把到达兵力与守军按阵营求和后互减。
    后置：BattleResult 含 winner(owner|None) 与 remaining，不直接改写 tile。"""
```

### 1.5 `game.rules` — 规则原语

```python
def apply_growth(state) -> list[GrowthEvent]:
    """R3.4 / R3.7：只在 half==1 调用。首都与城市 +1；平原 (turn+1) % 25 == 0 时 +1。"""

def apply_capture(state, dst: int, owner: int, amount: int) -> CaptureEvent:
    """R4.4 / R4.6 / R5.2：把 amount 兵力的所有权转给 owner。
    若 dst 是 owner 原有地块 → 累加；若是他人王城 → 触发 inherit_all（R5.2）。"""

def inherit_all(state, conqueror: int, victim: int) -> None:
    """R5.2：victim 全部地块转给 conqueror，每块继承 max(army//2, 1)，地形不变。"""

def check_eliminations(state, turn_half: int) -> list[EliminationEvent]:
    """R5.3 / R5.5：无地玩家出局、王城转首都转化型城市；
    投降过渡期递减，期满全部地块转中立。"""

def tick_surrender(state) -> None: ...
```

### 1.6 `game.tick`

```python
@dataclass(frozen=True)
class DroppedRequest:
    player_id: int
    src: int
    dir: str            # 协议方向字符串，便于直接下发
    ratio: float
    reason: str         # "src_lost" | "zero_army"（R4.12）
    cascade: int = 0    # 级联一并作废的下游条数（不含自己）

@dataclass
class TickReport:
    tick: int
    turn: int
    half: int
    growth: list[GrowthEvent]
    launched: list[Flight]
    dropped: list[DroppedRequest]   # R4.12 作废的请求，按 (player_id, src) 排序
    encounters: list[EncounterResult]
    battles: list[BattleResult]
    captures: list[CaptureEvent]
    eliminations: list[EliminationEvent]
    changed: set[int]       # 本刻发生变化的 tile index
    ended: bool
    winner: int | None

    def dropped_for(self, player_id: int) -> list[DroppedRequest]  # 只取本人，按 src 排序

def step(state: GameState) -> TickReport:
    """推进 1 个 Tick，严格按 R7.1 的六阶段顺序。
    前置：not state.ended。
    后置：state.tick += 1；Report 完整描述本刻所有变化，供 view/回放使用。
          第 2 步内按 R4.11/R4.12 判起点归属与调兵量，作废的请求进 report.dropped
          且**不再为该玩家弹下一条**，级联作废下游依赖项。
    确定性：无随机、无时间依赖；相同输入必然相同输出（R7.3）。
    """
```

### 1.7 `game.view`

```python
@dataclass
class TileView:
    index: int; army: int | None; owner: int | None; terrain: int   # PROTOCOL §3.3

def visible_diff(before: set[int], after: set[int]) -> tuple[list[int], list[int]]: ...

def project(state: GameState, seat_player: int | None) -> list[TileView]:
    """把权威状态裁剪成某玩家视野内的地块列表（R3.5 / R3.6 / R6.9）。
    seat_player=None 表示观战者：全图真实信息。
    后置：长度为 size*size；视野外 army=None，terrain 折叠为 0 或 2。"""
```

---

## 2. `server/generals/net`

### 2.1 `net.lobby`

```python
class Lobby:
    def __init__(self, config: GameConfig)
    def list_rooms(self) -> list[RoomSummary]
    def join(self, name: str, room_id: str | None, spectate: bool) -> tuple[Room, Seat]
    def leave(self, seat: Seat) -> None
```

### 2.2 `net.room`

```python
class Room:
    id: str
    state: GameState | None            # None = 等待中
    @property
    def size(self) -> int | None       # 开局后 == state.size；未开局为 None（R2.3）
    def add(self, conn: Connection, name: str, spectate: bool) -> Seat
    def handle(self, seat: Seat, frame: dict) -> None
    async def run(self) -> None        # 驱动 Tick 时钟，直到对局结束
    def broadcast(self, frame: dict) -> None
```

地图边长由开局玩家数决定（R2.3），构造参数 `size=` 仅供测试显式覆盖。

### 2.3 `net.session`

```python
class Session:
    """单条 WS 连接的状态机：连接 → 已加入 → 对局中 → 关闭。"""
    async def on_message(self, raw: str) -> None
    async def on_close(self) -> None
```

---

## 3. `server/generals/ai` — 比赛策略 AI 接入

完整说明见 **[AI_API.md](./AI_API.md)**。摘要：

```python
class Agent(Protocol):
    name: str
    def reset(self, spec: GameSpec) -> None: ...
    def act(self, obs: Observation) -> Action | None: ...

@dataclass
class Observation:
    turn: int; half: int
    size: int
    me: int
    tiles: list[TileView]        # 与 view.project 相同的视野裁剪
    my_army: int; my_land: int
    leaderboard: list[Row]

@dataclass
class Action:
    op: Literal["move", "clear", "pop", "select"]
    src: int = 0; dir: str = ""; ratio: float = 1.0; tile: int = 0

def register(name: str) -> Callable[[type], type]: ...   # registry.register
def get(name: str) -> type[Agent]: ...
```

接入方式二选一：

1. **进程内**：`ai.register("mybot")(MyAgent)`，然后用 `{"t":"join","bot":"mybot"}` 加入座位。
2. **进程外**：实现 `POST /api/ai/decide` 的 HTTP 策略服务；用 `generals.ai.remote_client` 连进来。

---

## 4. `web/src/core` — 客户端纯逻辑

```ts
// state.ts
export interface ClientState {
  started: boolean; size: number; turn: number; half: 0 | 1; me: number | null;
  players: PlayerInfo[]; tiles: Tile[]; sight: Set<number>;
  leaderboard: LeaderRow[]; chain: MoveRequest[]; chat: ChatLine[];
  over: boolean; winner: number | null; lastError: string | null;
  lastDropped: DroppedNotice | null;   // R4.12 chain_dropped 提示
}
export function applyFrame(state: ClientState, frame: ServerFrame): ClientState  // 不可变返回

// chains.ts
export function toggleSelection(ch: ChainState, idx: number): ChainState  // R6.6 比例状态机
export function pushMove(ch, dir, size, isBlocked): PushResult            // R6.2 / R4.11 预调兵

// geometry.ts
export function neighbours4(idx: number, size: number): number[]
export function neighbours8(idx: number, size: number): number[]
export function step(idx: number, dir: Dir, size: number): number | null
```

`web/src/core` 不 import `render` / `ui` / `input` / DOM API。

---

## 5. 装配点

唯一装配文件：`server/generals/app.py` 与 `web/src/main.ts`。
其它模块之间**只通过上述公开符号**互相调用，禁止跨层直接 import 内部实现。
