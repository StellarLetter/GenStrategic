"""房间：座位、连接、Tick 时钟、视野增量的分发。

契约见 `docs/INTERFACES.md` §2.2（`Room` / `Seat` / `Connection`）。

职责边界（ARCHITECTURE.md §3 依赖方向 `net → game`）
----------------------------------------------------
- 本模块**不操作地块数组**，一切状态变更只通过 `generals.game` 的公开 API 完成：
  `commands.enqueue_move / clear_chain / pop_chain`、`tick.step`、`view.project`。
- **视野裁剪一律调 `game.view.project`**（R3.5 / R3.6 / R6.9），本模块不重复实现裁剪逻辑。
- 本模块只做「座位 → 玩家」的映射、指令校验 + `ack`/`reject`，以及把内核报告转成每个座位各自的帧。

并发模型
--------
- 每个 `Connection` 有一个 `asyncio.Queue`；`Room.broadcast()` 按契约是**同步**的，
  它只把帧放进各座位队列；真正的 socket 写出由 `net.session` 的 sender task 完成。
- 队列长度超过 `QUEUE_MAXSIZE` 视为慢客户端：直接踢出该座位并移出房间，**绝不丢中间帧**
  （增量地块帧一旦丢失，客户端状态会永久错位）。
- `GameState` 只能被所属 `Room` 的 loop 触碰（INTERFACES §1.1 约束）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final, Protocol, runtime_checkable

from generals.config import DEFAULT_CONFIG, GameConfig
from generals.game import commands as game_commands
from generals.game import mapgen, rules as game_rules, tick as game_tick, view as game_view
from generals.game.state import GameState, turn_of

from . import protocol

__all__ = [
    "Connection",
    "QUEUE_MAXSIZE",
    "Room",
    "RoomState",
    "Seat",
]


#: 单个连接的发送队列上限。超过即视为慢客户端（见模块 docstring）。
QUEUE_MAXSIZE: Final[int] = 64

#: 聊天历史条数上限（供中途加入者补齐；超出丢最旧的，避免房间永占内存）。
CHAT_LOG_MAX: Final[int] = 200

#: 星数（排行榜 `star` 字段，PROTOCOL.md §3.5）。
#:
#: `docs/PROTOCOL.md` §3.5 的示例里写了 `star=47`，但 `RULES.md` 里**没有「星数」概念**——
#: 原站那个数字是玩家的历史战绩分，本项目没有评级系统，因此**恒发 0**。
#: 协议字段保留（前端按 boolean 语义渲染即可），只是不再承载任何含义。
#: 曾错误地沿用内核 `PlayerState.star_count()`（有王城=1）：语义是错的——玩家丢了王城
#: 但仍然活着时会显示 0 星、又没有骷髅图标，与 UI 约定冲突。
STAR_COUNT: Final[int] = 0


def _star_of(pstate: Any) -> int:
    """取某玩家的星数。前置：无。后置：**恒返回 0**（R6.12：本项目无评级系统）。抛出：无。"""
    return STAR_COUNT


class RoomState(str, Enum):
    """房间生命周期（BRIEF 成员 B §2）: `WAITING → RUNNING → ENDED`。"""

    WAITING = "waiting"
    RUNNING = "running"
    ENDED = "ended"


@runtime_checkable
class Connection(Protocol):
    """一条下行传输通道的抽象（transport 层实现，net 层只依赖此协议）。

    前置：实现方必须在创建 `Seat` 之前可接受 `send()` 调用（先入队列、后连 socket 也可）。
    后置：`send()` 立即返回，不得阻塞事件循环超过一次 `await` 调度；`close()` 幂等。
    抛出：`send()` 内部错误必须由实现方吞掉（视为慢/坏客户端），不得向 `Room` 冒泡。
    """

    async def send(self, text: str) -> None:
        """把一条已编码的 JSON 文本帧投递给对端。"""
        ...

    async def close(self) -> None:
        """关闭连接（幂等）。"""
        ...


@dataclass
class Seat:
    """一名占位者（玩家或观战者）在房间里的全部状态。

    字段说明：
    - `seq`   —— 房间内自增席位号（0 起），用于 `joined` 帧的 `seat` 字段。
    - `player_id` —— 内核里的玩家 id；**观战者为 `None`**（`view.project(state, None)`）。
    - `bot`   —— 进程内 AI 策略名（`join.bot`）；非空时该座位由 `ai.runner.AIRunner` 接管，无 WS 连接。
    - `queue` —— 该连接的下行帧队列（同步 `broadcast` 只做 `put_nowait`）。
    - `last_view` —— 上一次下发的 `TileView` 列表，用于计算增量与「新进入视野补发」。
    - `last_sight` —— 上一次可见地块集合（`GameState.visible_tiles`；观战者为全图）。
    - `chain_sig` —— 上一次下发的调兵链签名，避免每刻重复广播 `commands` 帧。
    - `closed` —— 传输层已断开，Room 不再向它写帧。
    - `kicked` —— 因慢客户端/异常被移出房间（R6.10 语义下会广播 `left` 系统消息）。
    """

    room_id: str
    seq: int
    name: str
    color: str
    player_id: int | None
    spectate: bool = False
    bot: str | None = None
    token: str = ""
    conn: Connection | None = None
    queue: asyncio.Queue[str] = field(default_factory=lambda: asyncio.Queue(QUEUE_MAXSIZE))
    last_view: list | None = None
    last_sight: set[int] | None = None
    chain_sig: tuple = field(default_factory=tuple)
    closed: bool = False
    kicked: bool = False

    @property
    def is_bot(self) -> bool:
        """是否由进程内 AI 接管（无 WS 连接）。"""
        return self.bot is not None

    @property
    def joined(self) -> bool:
        """是否已完成 join（`Room.add` 成功即算加入）。"""
        return True

    async def drain(self) -> list[dict]:
        """把该座位队列里的帧全部取出并解析（**供测试与同步驱动使用**）。

        前置：无。
        后置：队列被清空；返回按发送顺序排列的 dict 列表。
        抛出：`json.JSONDecodeError` —— 队列里被塞了非 JSON（属于编程错误）。
        """
        return self.drain_nowait()

    def drain_nowait(self) -> list[dict]:
        """同步版 `drain()`：不 await，直接清空队列并解析（测试/同步驱动用）。

        前置：无。
        后置：队列被清空；返回按发送顺序排列的 dict 列表。
        抛出：`json.JSONDecodeError` —— 队列里被塞了非 JSON（属于编程错误）。
        """
        out: list[dict] = []
        while True:
            try:
                text = self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            out.append(json.loads(text))
        return out

    def enqueue(self, text: str) -> bool:
        """同步把一条已序列化帧放入该座位的队列。

        前置：无（`closed` 座位会被直接拒绝）。
        后置：返回 `True` = 已入队；返回 `False` = 队列已满（慢客户端）或已关闭，调用方应踢人。
              **机器人座位直接丢弃**（`bot is not None`：进程内 AI 不消费 WS 帧，
              若仍入队会累积到 64 条上限被误判为慢客户端而踢掉）。
        抛出：无。
        """
        if self.closed or self.kicked:
            return False
        if self.bot is not None:
            return True
        try:
            self.queue.put_nowait(text)
        except asyncio.QueueFull:
            return False
        return True


def _uuid_token() -> str:
    """生成席位凭证（`joined.token`）。

    注意：`uuid4` 自带随机源，但它**只用于连接凭证**，不参与任何游戏结算，
    因此不违反 R7.3（「不产生任何随机结果」约束的是结算）。
    """
    return uuid.uuid4().hex


class Room:
    """单个对局房间：等待阶段 → 开局 → 每 Tick 推进 → 结束。

    契约见 `docs/INTERFACES.md` §2.2。

    前置（构造）：
    - `config` 提供 `tick_ms`/`max_players`/`min_size`/`max_size` 等常量；
      缺省用 `generals.config.DEFAULT_CONFIG`。
    - `room_id` 必须唯一（由 `net.lobby.Lobby` 保证）。
    - `size` **仅测试用**：显式传入时覆盖地图边长（让测试地图小、跑得快）。
      生产路径不传，边长按 R2.3 由玩家数与地图种子在 `start()` 时算出。
    后置：
    - `state is None` 表示等待阶段；`start()` 之后 `state` 为内核 `GameState`。
    - `seats` 按加入顺序保存；遍历时用 `_seats_sorted()` 保证 `(seat 序号)` 确定性（R7.3）。
    抛出：构造本身不抛。
    """

    def __init__(
        self,
        room_id: str,
        config: GameConfig | None = None,
        *,
        seed: int | None = None,
        size: int | None = None,
    ) -> None:
        self.id: str = room_id
        self.config: GameConfig = config or DEFAULT_CONFIG
        self.phase: RoomState = RoomState.WAITING
        self.state: GameState | None = None
        self.seats: list[Seat] = []
        self._seed_override: int | None = seed
        self._size_override: int | None = size
        self._next_seq: int = 0
        self._next_player_id: int = 0
        # R1.4：开局只由房主的 `start` 帧触发，不设自动倒计时。
        self._loop_task: asyncio.Task | None = None
        self._closed: bool = False
        self._sight_all: set[int] | None = None  # 观战者/全图缓存
        #: 聊天/系统消息历史（供中途加入者补齐，`Room.add` 会补发）。
        #: 元素就是完整的 `chat` 帧（`system=False` 为真人聊天，`True` 为系统消息）。
        self.chat_log: list[dict] = []
        #: 已播报过 `"<name> left."` 的对象键（R6.10：同一个人只播一次）。
        #: 键为 `("p", player_id)`（玩家）或 `("s", seat_seq)`（无 player_id 的座位）。
        self._left_announced: set[tuple[str, int]] = set()

    # ------------------------------------------------------------------
    # 构造期查询
    # ------------------------------------------------------------------
    @property
    def max_players(self) -> int:
        """最大玩家座数（观战者不占座；PROTOCOL.md §5 `room_full`）。"""
        return int(getattr(self.config, "max_players", 8))

    @property
    def tick_ms(self) -> int:
        """一个 Tick 的毫秒数（默认 500ms = 1 秒 2 刻，R1.1 / R1.3）。"""
        return int(getattr(self.config, "tick_ms", 500))

    @property
    def size(self) -> int | None:
        """本房间地图边长；**未开局时为 `None`**（R2.3）。

        前置：无。
        后置：
        - 已开局 → `state.size`（权威值；`game_start.size` 也取自它）；
        - 未开局 → 只有构造时显式传了 `size=`（**仅测试用**）才返回该值，否则 `None`。

        为什么不是 `config.default_size`：R2.3 规定边长由**开局玩家数与地图种子**算出，
        房间与客户端都不指定；开局前根本无从得知，返回一个假的默认值只会诱使调用方
        （如 `game_start`）误用它。协议里的边长一律读 `game_start.size`。
        抛出：无。
        """
        if self.state is not None:
            return int(self.state.size)
        if self._size_override is None:
            return None
        return self._clamp_size(int(self._size_override))

    @property
    def player_seats(self) -> list[Seat]:
        """所有占玩家座的座位（不含观战者与 AI 以外的特殊位）。"""
        return [s for s in self.seats if not s.spectate]

    @property
    def running(self) -> bool:
        """Tick 时钟是否在跑。"""
        return self.phase is RoomState.RUNNING

    def _clamp_size(self, size: int) -> int:
        lo = int(getattr(self.config, "min_size", 10))
        hi = int(getattr(self.config, "max_size", 40))
        return max(lo, min(hi, size))

    def _seats_sorted(self) -> list[Seat]:
        """按席位号升序返回座位快照（R7.3：禁止依赖 dict 插入序做遍历决策）。"""
        return sorted(self.seats, key=lambda s: s.seq)

    # ------------------------------------------------------------------
    # 加入 / 离开
    # ------------------------------------------------------------------
    def add(
        self,
        conn: Connection | None,
        name: str,
        spectate: bool = False,
        *,
        bot: str | None = None,
        token: str | None = None,
    ) -> Seat:
        """把一条连接（或一个 AI 座位）加入房间，并立即下发 `joined` 帧。

        前置：
        - 房间未结束（`phase is not RoomState.ENDED`）；
        - 对局已开始（`phase is RoomState.RUNNING`）时只允许观战者加入。
        后置：
        - 新 `Seat` 追加到 `seats`，`seq` 连续；玩家座分配一个新的 `player_id`
          （对局已开始则不分配，观战者 `player_id=None`）。
        - 向该座位队列投递 `joined` 帧作为**第一帧**。
        - 若对局已在进行，还补发 `game_start` + 全量 `tiles` + `leaderboard`，让中途加入者立刻对齐。
        抛出：
        - `protocol.ProtocolError('room_full')` —— 玩家座已满或对局中试图占玩家座。
        """
        if self.phase is RoomState.ENDED:
            raise protocol.ProtocolError("room_full", "room ended")
        if not spectate and self.phase is RoomState.RUNNING:
            # 对局进行中不可再占玩家座（玩家集合在开局时锁定，GAME_START 已广播给所有人）。
            raise protocol.ProtocolError("room_full", "game already started")
        if not spectate and len(self.player_seats) >= self.max_players:
            raise protocol.ProtocolError("room_full", "no free player seat")

        color = self._pick_color(self._next_seq)
        seat_idx = self._next_seq
        self._next_seq += 1

        player_id: int | None
        if spectate:
            player_id = None
        else:
            player_id = self._next_player_id
            self._next_player_id += 1

        seat = Seat(
            room_id=self.id,
            seq=seat_idx,
            name=name,
            color=color,
            player_id=player_id,
            spectate=spectate,
            bot=bot,
            token=token or _uuid_token(),
            conn=conn,
        )
        self.seats.append(seat)

        # 座位队列与连接队列合并：`Room` 只往 `Seat.queue` 里塞，
        # 而 `Session` 的 sender task 需要消费**同一个**队列。
        # 约定：若 `conn` 提供 `attach_queue(queue)`（`Session` 提供），就把座位队列交给它消费。
        attach = getattr(conn, "attach_queue", None)
        if callable(attach):
            # 只吞 `AttributeError`（连接对象形状不对）；其它异常属于传输层真实故障，必须冒泡，
            # 否则会把「sender 永远收不到帧」这类 P0 缺陷彻底掩盖（2026-11-18 lead 指出）。
            try:
                attach(seat.queue)
            except AttributeError:
                pass

        # `joined.playerId`：观战者没有内核 id，取 `-1`（R6.13），前端把负数视为观战。
        self._send(
            seat,
            protocol.encode_joined(
                player_id if player_id is not None else -1,
                self.id,
                seat.seq,
                seat.token,
            ),
        )

        if self.phase is RoomState.RUNNING and self.state is not None:
            # 中途加入的观战者：立刻补齐视图，避免客户端空白（BRIEF「新进入视野的地块必须补发」同源需求）。
            self._send(seat, self._game_start_frame(self.state))
            self._send_view(seat, self.state, force_full=True)
            self._send(seat, self._leaderboard_frame(self.state))
            self._send_chain(seat, self.state)
        # 补齐聊天/系统消息历史，让后加入者看到完整战报（不占 `chat_log` 之外的额外状态）。
        for frame in self.chat_log:
            self._send(seat, frame)
        return seat

    def _pick_color(self, seq: int) -> str:
        """按席位号轮转玩家配色（PROTOCOL.md §3.1 / ARCHITECTURE.md §5 的 8 色顺序）。"""
        return protocol.COLORS[seq % len(protocol.COLORS)]

    def remove(self, seat: Seat, *, kicked: bool = False) -> None:
        """把座位移出房间，并广播 `left` 事件（R6.10 三种系统消息之一）。

        前置：`seat` 可能仍在 `seats` 中，也可能已被移除。
        后置：
        - 从 `seats` 摘除；置 `kicked`；
        - 若该玩家还没被播报过 `left`：广播 `event(kind="left")` + 系统消息 `"<name> left."`；
          已播报过（例如先被结算判定出局、再关页面）则**不重复播**（R6.10 只播三种，不刷屏）；
        - 若对局中该玩家仍有领地，先按 R5.5 进入投降过渡期（`_surrender_for_disconnect`）。
        抛出：无。
        """
        if seat in self.seats:
            self.seats.remove(seat)
        seat.kicked = kicked
        seat.closed = True
        if self.state is not None and seat.player_id is not None:
            self._surrender_for_disconnect(seat)
        turn, _half = turn_of(self.state.tick) if self.state is not None else (0, 0)
        self._announce_left(_left_key(seat), seat.name, seat.color, turn)

    def _surrender_for_disconnect(self, seat: Seat) -> None:
        """断线/踢出时让该玩家的地块进入投降过渡期（R5.5）。

        前置：`self.state` 非空，`seat.player_id` 非空。
        后置：该玩家 `surrender_ticks_left` 被置为 `config.surrender_ticks`（40），调兵链被清空。
        抛出：无（内核未提供入口时静默跳过，并在日志中可见）。
        """
        state = self.state
        assert state is not None and seat.player_id is not None
        pstate = state.players.get(seat.player_id)
        if pstate is None:
            return
        if getattr(pstate, "surrender_ticks_left", None) is not None:
            return
        try:
            game_rules.begin_surrender(state, seat.player_id)
            return
        except Exception:  # noqa: BLE001 - 内核未提供入口或已出局
            pass
        try:
            game_commands.clear_chain(state, seat.player_id)
        except Exception:  # noqa: BLE001
            pass
        pstate.surrender_ticks_left = int(getattr(self.config, "surrender_ticks", 40))

    # ------------------------------------------------------------------
    # 开局
    # ------------------------------------------------------------------
    def start(self) -> None:
        """生成内核状态并进入 `RUNNING`（由房主 `start` 帧或测试直接调用）。

        前置：`phase is RoomState.WAITING` 且至少 1 名玩家座。
        后置：
        - `self.state = mapgen.generate(player_count, rng)`：**边长由内核按玩家数算**（R2.3），
          只在构造时显式传了 `size=`（仅测试用）才把边长交给内核；
        - 每个玩家座位的 `Seat` 信息同步进内核 `PlayerState`（name/color）；
        - 向所有座位广播 `game_start` + 全量 `tiles` + `leaderboard`；
        - `phase = RUNNING`；`tick` 从 0 开始（R1.2 `Turn 0`）。
        抛出：
        - `protocol.ProtocolError('not_started')` —— 没有玩家座。
        """
        if self.phase is not RoomState.WAITING:
            return
        players = [s for s in self.player_seats if s.player_id is not None]
        if not players:
            raise protocol.ProtocolError("not_started", "no players in room")

        seed = self._seed_override if self._seed_override is not None else _seed_from_room_id(self.id)
        rng = random.Random(seed)
        # 内核 `mapgen.generate` 支持 1..8 任意玩家数（含奇数）：奇数多出的一座放在中心 3×3。
        # 座位数的上限由 `max_players` 与内核 `MAX_PLAYERS` 保证，这里直接传真实人数。
        player_count = len(players)
        if self._size_override is None:
            # R2.3：`size = clamp(6 + 3N + rand(0..6), 10, 40)`，由内核按人数与种子算。
            state = mapgen.generate(player_count=player_count, rng=rng)
        else:
            # 显式覆盖**仅测试用**（`make_room(size=12)` 让地图小、跑得快）。
            state = mapgen.generate(
                player_count=player_count,
                rng=rng,
                size=self._clamp_size(int(self._size_override)),
            )

        # 把座位身份同步给内核（INTERFACES §1.1 的 PlayerState 公开字段 name/color）。
        for seat in sorted(players, key=lambda s: s.player_id or 0):
            pstate = state.players.get(seat.player_id)
            if pstate is None:
                continue
            pstate.name = seat.name
            pstate.color = seat.color

        self.state = state
        self.phase = RoomState.RUNNING

        frame = self._game_start_frame(state)
        self.broadcast(frame)
        for seat in self._seats_sorted():
            self._send_view(seat, state, force_full=True)
        self.broadcast(self._leaderboard_frame(state))
        self.broadcast(self._turn_frame(state))

        # 开局即启动 Tick 时钟（否则没人调 `run()`，房间永远停在 Turn 0 —— e2e 发现）。
        # 只有在事件循环里、且尚未启动时才创建任务；同步测试（无 loop）走 `step_once()`。
        self._ensure_clock()

    def _ensure_clock(self) -> None:
        """确保 Tick 时钟在跑（内部，幂等）。

        前置：`phase is RUNNING` 且 `state` 非空。
        后置：`self._loop_task` 是一个正在运行的 `run()` 任务；
              若当前没有事件循环（同步测试/单元测试），静默跳过，由 `step_once()` 手动推进。
        抛出：无 —— `asyncio.get_running_loop()` 无 loop 时的 `RuntimeError` 被吞掉。
        """
        if self._closed or self.state is None or self.state.ended:
            return
        if self._loop_task is not None and not self._loop_task.done():
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # 没有事件循环（同步测试直接调 start()/step_once()）：由调用方驱动。
            return
        self._loop_task = asyncio.create_task(self.run())

    def _game_start_frame(self, state: GameState) -> dict:
        """构造 `game_start` 帧（PROTOCOL.md §3.1）。玩家列表按 `player_id` 升序（R7.3）。"""
        rows: list[dict] = []
        for pid in sorted(state.players):
            pstate = state.players[pid]
            seat = self._seat_of(pid)
            rows.append(
                {
                    "playerId": pid,
                    "name": pstate.name or (seat.name if seat else f"Player {pid}"),
                    "color": pstate.color or (seat.color if seat else protocol.COLORS[pid % 8]),
                    "seat": seat.seq if seat is not None else pid,
                }
            )
        return protocol.encode_game_start(state.size, rows)

    def _seat_of(self, player_id: int) -> Seat | None:
        for seat in self.seats:
            if seat.player_id == player_id:
                return seat
        return None

    # ------------------------------------------------------------------
    # 指令处理
    # ------------------------------------------------------------------
    def handle(self, seat: Seat, frame: dict) -> None:
        """处理一条**已通过 `protocol.parse_client_frame` 的上行帧**。

        前置：
        - `frame["t"]` 是 PROTOCOL.md §2 定义的字符串；未知 `t` 会被静默忽略（向前兼容）；
        - 房间相关操作由 `net.session` 在调用前完成（`join` 由 `Lobby` 处理）。
        后置：
        - `commands` 帧：按顺序对调兵链逐条执行；**被接受时不发帧**（靠随后的权威 `commands`
          回显体现），只有失败才逐条 `reject`（`reject.code` 取自内核 `CommandError.code`，PROTOCOL.md §5）；
        - `chat` 帧：广播 `chat{system:false}` 并记入 `chat_log`（供后加入者补齐）；
        - `surrender` 帧：见 `surrender()`——调用内核 `begin_surrender`，广播
          `event{kind:"surrendered"}` + 系统消息，投降后该玩家 `move` 一律 `not_your_turn`；
        - `ready` / `start` 帧：`start` 由房主触发开局（R1.4）；
        - `ping` 帧：回 `pong`。
        抛出：
        - `protocol.ProtocolError` —— 帧结构非法（由 `protocol.validate_*` 抛出，`session` 转成 `reject`）。
          其它所有业务失败都只会产生 `reject` 帧，不会向上抛。
        """
        kind = frame.get("t")
        if kind == "commands":
            self._handle_commands(seat, frame)
        elif kind == "chat":
            text = protocol.validate_chat(frame)
            if self.state is None:
                turn = 0
            else:
                turn, _half = turn_of(self.state.tick)
            self._log_chat(
                protocol.encode_chat(turn, seat.name, seat.color, text, system=False)
            )
        elif kind == "surrender":
            self._handle_surrender(seat)
        elif kind == "start":
            self.start()
        elif kind == "ready":
            # R1.4：开局只由房主的 `start` 触发；`ready` 仅记录，不改变房间行为。
            return
        elif kind == "ping":
            n = protocol.validate_ping(frame)["n"]
            self._send(seat, protocol.encode_pong(n))
        else:
            # 未知 t：忽略（PROTOCOL.md §1 向前兼容）。
            return

    def _handle_commands(self, seat: Seat, frame: dict) -> None:
        """执行 `commands` 批（PROTOCOL.md §2.4）。

        前置：`seat.player_id` 已分配（观战者调用会被逐条 `reject`，`code='not_your_turn'`）。
        后置：调兵链状态由内核维护（R6.3 / R6.4）；本方法只做 ack/reject 与链回显。
        抛出：`protocol.ProtocolError` —— `cmds` 结构非法（整批拒绝）。
        """
        cmds = protocol.validate_commands(frame)
        state = self.state
        pid = seat.player_id

        for idx, cmd in enumerate(cmds):
            op = cmd["op"]
            if state is None:
                # 等待阶段：调兵链还不存在（内核状态尚未生成）。
                # `clear`/`pop`/`select` 是无副作用的本地意图，**静默接受**（不回 ack，见下）；
                # `move` 无处可入队 → `not_started`。
                if op not in ("clear", "pop", "select"):
                    self._send(seat, protocol.encode_reject(op, idx, "not_started"))
                continue
            if pid is None:
                # 观战者不能操作（R3.10：观战者只有视野，没有座位）。
                self._send(seat, protocol.encode_reject(op, idx, "not_your_turn"))
                continue
            try:
                self._apply_command(state, pid, cmd)
            except game_commands.CommandError as exc:
                self._send(seat, protocol.encode_reject(op, idx, getattr(exc, "code", "bad_frame")))
            except KeyError:  # pragma: no cover - 防御：内核异常结构
                self._send(seat, protocol.encode_reject(op, idx, "bad_frame"))
            # 指令被接受时**不发 ack**（lead 裁决 2026-11-18）：前端没有等 ack 的逻辑，
            # 每 Tick 逐条回 ack 只会污染帧流。接受与否由随后的权威 `commands` 回显体现。
            # 已定(PROTOCOL §2.4.1): 保留帧但默认不发，只在 reject 时回帧。

        if state is not None and pid is not None:
            # 权威调兵链回显（PROTOCOL.md §3.6）：整批处理完后同步一次，客户端以服务端为准乐观回滚。
            self._send_chain(seat, state)

    @staticmethod
    def _apply_command(state: GameState, player_id: int, cmd: dict) -> None:
        """把一条规范化指令交给内核。**指令到达即入队**（R6.3 / R4.7：链由内核维护）。

        前置：`cmd` 来自 `protocol.validate_commands`（`op` ∈ `OPS`）。
        后置：内核对 `state.players[player_id].chain` 做了相应改动（`move` 入队 / `clear` 清空 / `pop` 弹一条）。
              `select` 只影响客户端 UI，服务端无状态 → 仅记录 ack（R6.1）。
        抛出：`generals.game.commands.CommandError` —— 校验失败，`code` ∈ PROTOCOL.md §5。
        """
        op = cmd["op"]
        if op == "move":
            game_commands.enqueue_move(
                state, player_id, cmd["from"], cmd["dir"], cmd["ratio"]
            )
        elif op == "clear":
            game_commands.clear_chain(state, player_id)
        elif op == "pop":
            game_commands.pop_chain(state, player_id)
        elif op == "select":
            # `select` 是纯客户端 UI 概念（R6.1）；服务端无状态，直接返回。
            return

    def surrender(self, seat: Seat) -> None:
        """让某座位投降（`surrender` 帧的公开入口，R5.5）。

        前置：对局进行中且 `seat.player_id` 已分配（观战者/未开局会被 `reject`）。
        后置：
        - 调用内核 `rules.begin_surrender(state, player_id)`：该玩家进入 40 Tick 过渡期，
          调兵链**立即清空**（R5.5），期间仍会增长与作战但**不能操作**；
        - 广播 `event{kind:"surrendered"}` 与系统消息 `"<name> surrendered."`（并记入聊天历史）；
        - 拒绝情况回 `reject`（`not_started` / `not_your_turn`），不抛异常。
        抛出：无。
        """
        self._handle_surrender(seat)

    def _handle_surrender(self, seat: Seat) -> None:
        """`surrender` 帧：进入 40 Tick 投降过渡期（R5.5）。

        前置：对局进行中且 `seat.player_id` 已分配。
        后置：该玩家 `surrender_ticks_left = config.surrender_ticks`，调兵链清空（R5.5），
              广播 `event(kind='surrendered')` 与系统聊天（R6.10）。
        抛出：无。
        """
        if self.state is None or seat.player_id is None:
            self._send(seat, protocol.encode_reject("surrender", 0, "not_started"))
            return
        pstate = self.state.players.get(seat.player_id)
        if pstate is None:
            self._send(seat, protocol.encode_reject("surrender", 0, "not_your_turn"))
            return
        if getattr(pstate, "surrender_ticks_left", None) is not None:
            # 已在投降过渡期内：重复请求既不改状态也不该重播事件（否则聊天被刷屏）。
            self._send(seat, protocol.encode_reject("surrender", 0, "not_your_turn"))
            return
        # R5.5：投降瞬间清空调兵链 + 进入 40 Tick 过渡期。统一交给内核。
        try:
            game_rules.begin_surrender(self.state, seat.player_id)
        except Exception:  # noqa: BLE001 - 已出局等边界情况
            self._send(seat, protocol.encode_reject("surrender", 0, "not_your_turn"))
            return
        turn, _half = turn_of(self.state.tick)
        self.broadcast(protocol.encode_event("surrendered", turn, who=pstate.name, target=None))
        self._system_chat(
            turn,
            f"{pstate.name} surrendered.",
            kind="surrendered",
            name=pstate.name,
            color=pstate.color,
        )
        self._send_chain(seat, self.state)

    # ------------------------------------------------------------------
    # Tick 时钟
    # ------------------------------------------------------------------
    async def run(self) -> None:
        """驱动 Tick 时钟，直到对局结束（INTERFACES §2.2）。

        前置：`start()` 已把 `phase` 置为 `RUNNING`（若未开局，本方法先尝试 `start()`）。
        后置：
        - 每 `tick_ms` 毫秒推进 1 Tick（默认 500ms ⇒ 1 秒 2 刻，R1.1 / R1.3）；
        - 每刻依次广播：`turn` → `tiles`（per-seat 增量）→ `leaderboard` → `commands` → 事件/聊天；
        - 对局结束时广播 `game_over` 并把 `phase` 置 `ENDED`；方法正常返回。
        抛出：`asyncio.CancelledError` —— 调用方 `cancel()` 时按约定向上传播。
        """
        if self.phase is RoomState.WAITING:
            self.start()
        state = self.state
        if state is None:  # pragma: no cover - start() 保证非空
            return
        if self.phase is RoomState.ENDED:
            return

        self.phase = RoomState.RUNNING
        interval = self.tick_ms / 1000.0
        # 记录自身任务，让 `close()` 能从外部取消本循环（`_loop_task` 由调用方设时保留原值）。
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.current_task()
        try:
            while not state.ended and not self._closed:
                await asyncio.sleep(interval)
                if self._closed:
                    break
                self.step_once()
        except asyncio.CancelledError:
            raise
        finally:
            if self.phase is not RoomState.ENDED and state.ended:
                self.phase = RoomState.ENDED

    def step_once(self) -> None:
        """同步推进 1 Tick 并广播本刻所有帧（供 `run()` 与测试直接调用）。

        前置：`self.state` 非空且 `not state.ended`。
        后置：
        - `state.tick` +1；帧顺序：`turn` → `tiles` → `leaderboard` → `commands` → 事件/聊天；
        - 结束时额外广播 `game_over`，`phase = ENDED`。
        抛出：无（内核 `step` 内部异常会向上抛，属于内核缺陷）。
        """
        state = self.state
        if state is None or state.ended:
            return

        report = game_tick.step(state)
        turn, half = turn_of(state.tick)

        self.broadcast(protocol.encode_turn(turn, half))

        for seat in self._seats_sorted():
            self._send_view(seat, state)

        self.broadcast(self._leaderboard_frame(state))

        # R4.12：作废的调兵请求只通知本人（event + 紧随的 commands 快照），不广播。
        # 放在 `_send_chain` 循环之前：作废处理已把 `chain_sig` 更新到最新，
        # 后面的循环因此不会重复发同一份快照。
        self._send_chain_dropped(report, turn)

        for seat in self._seats_sorted():
            self._send_chain(seat, state)

        # 终局帧必须**先于**本刻的事件/聊天批处理下发：终局那一 Tick 可能同时产生几十条
        # captures（event + 系统消息各一条），足以把 `QUEUE_MAXSIZE` 灌满并触发「慢客户端
        # 踢人」——那样 `game_over` 就永远到不了客户端（实测：第 1016 Tick 自然终局时本刻
        # 产出 64 帧恰好占满队列，观战者被踢，game_over 丢失）。
        # 因此先发 `game_over`，再发事件；语义上也更正确（终局状态先于终局战报）。
        if state.ended:
            self.broadcast(protocol.encode_game_over(state.winner, turn))

        self._broadcast_report_events(report, turn)

        if state.ended:
            if state.winner is not None:
                winner_name = state.players[state.winner].name if state.winner in state.players else ""
                self.broadcast(
                    protocol.encode_event("game_over", turn, who=winner_name, target=None)
                )
            self.phase = RoomState.ENDED

    def _broadcast_report_events(self, report: Any, turn: int) -> None:
        """把 `TickReport` 里的 capture / elimination 转成 `event` + 系统聊天（PROTOCOL.md §3.7）。

        前置：`report` 是内核 `TickReport`。
        后置：广播顺序固定为「captures（按 tile index 升序）→ eliminations（按 player id 升序）」，
              保证确定性（R7.3）。聊天栏严格只产 R6.10 允许的两种系统消息：
              `"<A> captured <B>."` 与 `"<B> left."`。
        抛出：无 —— 对报告字段缺失采取宽容处理。

        `captured` 的三条硬约束（R6.10 / R5.5）：
        1. **必须有明确攻占者**：`new_owner is None` 不播（例如投降过渡期满、地块转中立 R5.5，
           `reason="neutral"`，没有「谁攻占了谁」这回事）；
        2. **同一次攻占只播一条**：攻陷王城会一次性继承全部领土（R5.2），内核按地块
           逐块发 `reason="inherit"` 事件，若逐块播报会刷几十条重复文案；
           这里按 `(攻占者, 被攻占者)` 在本刻内去重；
        3. **文案不含回合号**：`"<A> captured <B>."`；回合号只填 `chat` 帧的 `turn` 字段，
           由前端 `chat.ts::renderLine` 渲染成独立的 `.turn` 元素（显示为 `A captured B. 37.`）。
        """
        state = self.state
        if state is None:
            return

        def ident_of(pid: Any) -> tuple[str, str]:
            """`player_id` → `(name, color)`；查不到时降级为 `(str(pid), "")`。"""
            if pid is None:
                return ("", "")
            try:
                pstate = state.players.get(int(pid))
            except (TypeError, ValueError):
                return ("", "")
            if pstate is None:
                return (str(pid), "")
            return (pstate.name, pstate.color)

        captures = list(getattr(report, "captures", []) or [])
        announced: set[tuple[str, str]] = set()
        for cap in sorted(captures, key=lambda c: int(getattr(c, "tile", 0))):
            target_pid = getattr(cap, "previous_owner", None)
            if target_pid is None:
                # 占空地：不是「攻占某人的地」，不播。
                continue
            if getattr(cap, "reason", "arrive") not in ("arrive", "inherit"):
                # `"neutral"`（R5.5 投降过渡期满转中立）没有攻占者，不播。
                continue
            victor = getattr(cap, "new_owner", None)
            if victor is None:
                # 没有获胜方就不存在「谁攻占了谁」。
                continue
            pair = (ident_of(victor), ident_of(target_pid))
            if pair in announced:
                continue
            announced.add(pair)
            (v_name, v_color), (t_name, t_color) = pair
            self.broadcast(
                protocol.encode_event("captured", turn, who=v_name, target=t_name)
            )
            # R6.10：`text` **只**写 `"<A> captured <B>."`，回合号由 `chat` 帧的 `turn`
            # 字段单独承载 —— 前端 `chat.ts::renderLine` 会把 `turn` 渲染成独立的 `.turn`
            # 元素（视觉结果是 `A captured B. 37.`）。若把回合号也塞进 `text`，
            # 界面会重复成 `... 37. 37.`。
            # `kind`/`target`/`targetColor` 供前端渲染两个色块（攻占方 + 被攻占方）。
            self._system_chat(
                turn,
                f"{v_name} captured {t_name}.",
                kind="captured",
                name=v_name,
                color=v_color,
                target=t_name,
                target_color=t_color,
            )

        eliminations = list(getattr(report, "eliminations", []) or [])
        for elim in sorted(
            eliminations,
            key=lambda e: int(getattr(e, "player", getattr(e, "player_id", 0))),
        ):
            pid = getattr(elim, "player", getattr(elim, "player_id", None))
            if pid is None:
                continue
            name, color = ident_of(pid)
            self._announce_left(("p", int(pid)), name, color, turn)

    # ------------------------------------------------------------------
    # 帧构造 / 发送
    # ------------------------------------------------------------------
    def _turn_frame(self, state: GameState) -> dict:
        turn, half = turn_of(state.tick)
        return protocol.encode_turn(turn, half)

    def _leaderboard_frame(self, state: GameState) -> dict:
        """把内核的图鉴数据转成 `leaderboard` 帧（PROTOCOL.md §3.5）。

        前置：`state` 非空。
        后置：每行字段 `playerId/name/color/army/land/alive/surrenderIn/star`；
              `army = state.army_total(pid)`（R6.8）、`land = state.land_count(pid)`（R6.8）。
        抛出：无。
        """
        rows: list[dict] = []
        for pid in sorted(state.players):
            pstate = state.players[pid]
            army = int(state.army_total(pid))
            land = int(state.land_count(pid))
            surrender_left = getattr(pstate, "surrender_ticks_left", None)
            rows.append(
                {
                    "playerId": pid,
                    "name": pstate.name,
                    "color": pstate.color,
                    "army": army,
                    "land": land,
                    "alive": bool(pstate.alive),
                    "surrenderIn": None if surrender_left is None else int(surrender_left),
                    "star": _star_of(pstate),
                }
            )
        return protocol.encode_leaderboard(rows)

    def _sight_of(self, state: GameState, seat: Seat) -> set[int]:
        """该座位当前可见的地块集合（观战者 = 全图，R3.10）。"""
        if seat.player_id is None:
            if self._sight_all is None or len(self._sight_all) != state.size * state.size:
                self._sight_all = set(range(state.size * state.size))
            return self._sight_all
        return set(state.visible_tiles(seat.player_id))

    def _send_view(self, seat: Seat, state: GameState, *, force_full: bool = False) -> None:
        """向该座位下发**增量** `tiles` 帧（PROTOCOL.md §3.3）。

        前置：`state` 非空。
        后置：
        - 裁剪一律调用 `game.view.project(state, seat.player_id)`（R3.5 / R3.6 / R6.9）；
        - 增量 = 与 `seat.last_view` 对比后的变化项 + **新进入视野的补发**（否则客户端缺格）；
        - 同时按 `visible_diff(seat.last_sight, 新视野)` 下发 `sight` 帧（PROTOCOL.md §3.4）；
        - 无变化则不发 `tiles` 帧。
        抛出：无。
        """
        projection = game_view.project(state, seat.player_id)
        new_sight = set(self._sight_of(state, seat))

        updates = protocol.diff_tiles(
            [] if (force_full or seat.last_view is None) else seat.last_view,
            projection,
        )
        if seat.last_sight is not None:
            entered = new_sight - seat.last_sight
            if entered:
                have = {item[0] for item in updates}  # 四元组首项是 index
                for idx in sorted(entered):
                    if idx in have:
                        continue
                    tv = projection[idx]
                    updates.append(protocol.tile_update(tv.index, tv.army, tv.owner, tv.terrain))
                updates.sort(key=lambda item: item[0])  # 按 index 升序（R7.3）
        if updates:
            self._send(seat, protocol.encode_tiles(updates))

        if seat.last_sight is None:
            add, remove = sorted(new_sight), []
        else:
            add, remove = game_view.visible_diff(seat.last_sight, new_sight)
        if add or remove:
            self._send(seat, protocol.encode_sight(add, remove))

        seat.last_view = projection
        seat.last_sight = new_sight

    def _send_chain(self, seat: Seat, state: GameState, *, force: bool = False) -> None:
        """回显该座位的权威调兵链（PROTOCOL.md §3.6），默认仅在签名变化时发送。

        前置：`state` 非空；观战者（`player_id is None`）直接跳过。
        后置：`seat.chain_sig` 更新为本次签名；无变化且 `force=False` 则不写帧。
              `force=True` 时无条件重发一份全量快照（R4.12 作废后必须紧随补给，§3.7）。
        抛出：无。
        """
        pid = seat.player_id
        if pid is None:
            return
        # 链内容一律以内核的线协议快照为准（单一事实源：`commands.chain_snapshot`）。
        chain = game_commands.chain_snapshot(state, pid)
        sig = tuple((int(c["from"]), str(c["dir"]), float(c["ratio"])) for c in chain)
        if not force and sig == seat.chain_sig:
            return
        seat.chain_sig = sig
        self._send(seat, protocol.encode_commands(chain))

    def _send_chain_dropped(self, report: Any, turn: int) -> None:
        """把本刻作废的调兵请求**只发给各自的拥有者**（R4.12 / PROTOCOL.md §3.7）。

        前置：`report` 是内核 `TickReport`；`report.dropped` 每项含
              `(player_id, src, dir, ratio, reason, cascade)`（`dir` 已是协议字面量）。
        后置：对每条作废请求，向该玩家单独发两条帧，顺序固定：
              ① `event{kind:"chain_dropped"}`；
              ② 紧随其后的 `commands` 全量快照（链已变短）。
              其它座位**收不到**这两帧（不广播）。
        抛出：无（座位已断开/已出局则跳过，不影响其它玩家）。
        """
        state = self.state
        if state is None:
            return
        dropped = list(getattr(report, "dropped", []) or [])
        if not dropped:
            return
        # R7.3：按 (player_id, src) 升序下发，不依赖内核列表顺序。
        ordered = sorted(
            dropped,
            key=lambda d: (int(getattr(d, "player_id", 0)), int(getattr(d, "src", 0))),
        )
        for item in ordered:
            pid = int(getattr(item, "player_id", -1))
            seat = self._seat_of(pid)
            if seat is None or seat.closed:
                continue
            self._send(
                seat,
                protocol.encode_chain_dropped(
                    player_id=pid,
                    src=int(getattr(item, "src", 0)),
                    direction=_dir_name(getattr(item, "dir", "")),
                    ratio=float(getattr(item, "ratio", 1.0)),
                    reason=str(getattr(item, "reason", "src_lost")),
                    cascade=int(getattr(item, "cascade", 0)),
                ),
            )
            # 链已变短：立刻补一份全量快照（§3.7 要求紧随 chain_dropped）。
            self._send_chain(seat, state, force=True)

    # ------------------------------------------------------------------
    # 聊天历史
    # ------------------------------------------------------------------
    def _log_chat(self, frame: dict) -> None:
        """把一条 `chat` 帧记入历史并广播（内部）。

        前置：`frame` 由 `protocol.encode_chat` 生成。
        后置：追加到 `self.chat_log`（超出 `CHAT_LOG_MAX` 时丢最旧的），并广播给所有座位。
        抛出：`TypeError` / `ValueError` —— 同 `broadcast`（帧不可 JSON 化）。
        """
        self.chat_log.append(frame)
        if len(self.chat_log) > CHAT_LOG_MAX:
            del self.chat_log[: len(self.chat_log) - CHAT_LOG_MAX]
        self.broadcast(frame)

    def _system_chat(
        self,
        turn: int,
        text: str,
        *,
        kind: str,
        name: str = "",
        color: str = "",
        target: str | None = None,
        target_color: str | None = None,
    ) -> None:
        """广播一条系统播报（`system=True`，R6.10）。

        前置：`kind` ∈ `{surrendered, captured, left}`（`protocol.SYSTEM_CHAT_KINDS`）；
              `captured` 需给 `target`/`target_color`（被攻占者及其颜色），其余 `None`。
        后置：入历史并广播；`text` **不含回合号**，回合号只在 `turn` 字段。
        抛出：`ValueError` —— `kind` 非法（编程错误，测试会立刻发现）。
        """
        self._log_chat(
            protocol.encode_chat(
                turn,
                name,
                color,
                text,
                system=True,
                kind=kind,
                target=target,
                target_color=target_color,
            )
        )

    def _announce_left(self, key: tuple[str, int], name: str, color: str, turn: int) -> None:
        """播报某座位/玩家「离开」（`event{kind:"left"}` + `chat{kind:"left"}`，R6.10）。

        前置：`key` 是 `_left_key(seat)` 或 `("p", player_id)` 形式的稳定标识；
              `name`/`color` 是该玩家的显示名与配色（离开者色块用）。
        后置：**同一对象只播一次**（结算出局与关页面可能先后发生，重复播会刷屏）；
              首次播报时广播事件帧并记入聊天历史。
        抛出：同 `broadcast`。
        """
        if key in self._left_announced:
            return
        self._left_announced.add(key)
        self.broadcast(protocol.encode_event("left", turn, who=name, target=None))
        self._system_chat(turn, f"{name} left.", kind="left", name=name, color=color)

    def broadcast(self, frame: dict) -> None:
        """把一帧**同步**投递给房间内所有座位（INTERFACES §2.2）。

        前置：`frame` 是 `protocol.encode_*` 产生的纯 dict。
        后置：
        - 每座位各自序列化后入队（**同步**，不 await）；
        - 队列满的座位视为慢客户端 → `remove(seat, kicked=True)`（不丢中间帧，直接断开）；
        - 序列化失败（帧不可 JSON 化）会抛 `TypeError`，属于编程错误。
        抛出：`TypeError` / `ValueError` —— `json.dumps` 失败（内部错误）。
        """
        text = json.dumps(frame, separators=(",", ":"), ensure_ascii=False)
        slow: list[Seat] = []
        for seat in self._seats_sorted():
            if seat.closed:
                continue
            if not seat.enqueue(text):
                slow.append(seat)
        for seat in slow:
            self.remove(seat, kicked=True)

    def _send(self, seat: Seat, frame: dict) -> None:
        """向**单个**座位投递一帧（同步）。队列满则按慢客户端踢人。"""
        if seat.closed:
            return
        text = json.dumps(frame, separators=(",", ":"), ensure_ascii=False)
        if not seat.enqueue(text):
            self.remove(seat, kicked=True)

    # ------------------------------------------------------------------
    # 关闭 / 概要
    # ------------------------------------------------------------------
    def close(self) -> None:
        """停止 Tick 时钟并关闭所有座位（幂等）。

        前置：无。
        后置：`_closed = True`；已运行的任务被 `cancel()`（调用方负责 await）；所有座位置 `closed`。
        抛出：无。
        """
        self._closed = True
        task = self._loop_task
        if task is not None and not task.done():
            task.cancel()
        for seat in self.seats:
            seat.closed = True
        if self.phase is not RoomState.ENDED:
            self.phase = RoomState.ENDED

    def summary(self) -> dict:
        """房间概要（PROTOCOL.md §4 `/api/rooms` 行格式）。

        前置：无。
        后置：返回 `{"id", "name", "players", "maxPlayers", "state"}`，`state` ∈
              `{"waiting", "running", "ended"}`。`name` 当前与 `id` 相同（无自定义房名概念）。
        抛出：无。
        """
        return {
            "id": self.id,
            "name": self.id,
            "players": len(self.player_seats),
            "maxPlayers": self.max_players,
            "state": self.phase.value,
        }


def _left_key(seat: Seat) -> tuple[str, int]:
    """`_announce_left` 的去重键：优先用 `player_id`（跨座位稳定），否则用席位号。"""
    if seat.player_id is not None:
        return ("p", int(seat.player_id))
    return ("s", int(seat.seq))


def _dir_name(direction: Any) -> str:
    """把内核 `Dir`（或字符串）规范化为协议字面量 `"up"|"down"|"left"|"right"`。

    前置：`direction` 是 `commands.Dir` 成员、其 `.value`、`.name`，或已是协议字符串。
    后置：返回小写字符串；无法识别时返回 `str(direction)`（不抛，保证回显不因怪值崩掉）。
    抛出：无。
    """
    value = getattr(direction, "value", direction)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in protocol.DIRECTIONS:
            return lowered
    name = getattr(direction, "name", None)
    if isinstance(name, str) and name.lower() in protocol.DIRECTIONS:
        return name.lower()
    return str(value).lower()


def _seed_from_room_id(room_id: str) -> int:
    """由房间号派生地图随机种子（mapgen 必须可复现，R7.3 / INTERFACES §1.2）。

    前置：`room_id` 为字符串。
    后置：返回稳定的整数种子（同一房间号恒等）；用 `hashlib` 而非内建 `hash`，
          避免 PYTHONHASHSEED 造成跨进程不一致。
    抛出：无。
    """
    digest = hashlib.sha256(room_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")
