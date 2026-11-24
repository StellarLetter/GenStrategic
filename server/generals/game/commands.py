"""玩家指令 → 调兵链操作（Dir / MoveRequest / CommandError / enqueue_move / clear_chain / pop_chain）。

职责：把上层（net 帧解析后）的"往某方向调兵"意图校验并入队；不做任何结算。
契约见 docs/INTERFACES.md §1.3；错误码见 docs/PROTOCOL.md §5。
本模块无 I/O / 时间 / 随机依赖（R7.3）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from .. import config
from .state import GameState, PlayerState, Terrain

# 与 config 同源，避免两处硬编码漂移（R4.7 / R6.6 / INTERFACES §1.3）。
CHAIN_LIMIT: int = config.CHAIN_LIMIT
RATIO_MIN: float = config.RATIO_MIN
RATIO_MAX: float = config.RATIO_MAX
RATIO_STEP: float = config.RATIO_STEP

#: 真人与 AI 共享的**唯一**比例集合（R6.6）：10%、20%、…、100%。
#: 任何来源（前端输入、线协议、AI `Action`）的比例都会被 :func:`quantize_ratio`
#: 量化到本集合，因此 AI 不可能调出真人无法选择的比例。
RATIOS: tuple[float, ...] = tuple(
    round(RATIO_MIN + i * RATIO_STEP, 1)
    for i in range(round((RATIO_MAX - RATIO_MIN) / RATIO_STEP) + 1)
)


class Dir(Enum):
    """调兵方向，仅 4 相邻（R4.1）。

    ``value`` 就是线协议里的方向字符串（PROTOCOL §3.6：``"up"/"down"/"left"/"right"``），
    因此 ``Dir("up")`` 可直接由帧字段构造。
    """

    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"

    @property
    def delta(self) -> tuple[int, int]:
        """``(drow, dcol)`` 位移。"""
        return _DELTAS[self]

    @property
    def opposite(self) -> "Dir":
        """反方向（遭遇战判定 R4.5 用）。"""
        return _OPPOSITE[self]


_DELTAS: dict[Dir, tuple[int, int]] = {
    Dir.UP: (-1, 0),
    Dir.DOWN: (1, 0),
    Dir.LEFT: (0, -1),
    Dir.RIGHT: (0, 1),
}

_OPPOSITE: dict[Dir, Dir] = {
    Dir.UP: Dir.DOWN,
    Dir.DOWN: Dir.UP,
    Dir.LEFT: Dir.RIGHT,
    Dir.RIGHT: Dir.LEFT,
}


def dir_of(sr: int, sc: int, dr: int, dc: int, size: int) -> Dir | None:
    """返回从 ``(sr, sc)`` 到 ``(dr, dc)`` 的方向；非 4 相邻或越界返回 ``None``。

    仅供 mapgen / 测试等需要"反查方向"的场合使用（内核结算用 :class:`Flight`，不靠方向）。
    """
    if not (0 <= dr < size and 0 <= dc < size):
        return None
    delta = (dr - sr, dc - sc)
    for d, dv in _DELTAS.items():
        if dv == delta:
            return d
    return None


@dataclass(frozen=True)
class MoveRequest:
    """一条调兵请求（R4.1 / R6.7）。

    - ``src``：起点地块 index。
    - ``dir``：方向（4 相邻）。
    - ``ratio``：**入队时**的比例，为 ``RATIOS`` 之一（10%..100%，步长 10%）（R6.6 / R6.7）。
    """

    src: int
    dir: Dir
    ratio: float = 1.0


class CommandError(Exception):
    """指令校验失败。``code`` 与 PROTOCOL §5 的错误码一一对应。"""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.message = message or _CODE_MESSAGES.get(code, code)
        super().__init__(f"{code}: {self.message}")


#: 错误码 → 中文说明（PROTOCOL §5）。
_CODE_MESSAGES: dict[str, str] = {
    "bad_frame": "帧结构或字段非法",
    "not_joined": "尚未加入房间",
    "not_started": "对局未开始",
    "not_your_turn": "已出让：玩家已出局或处于投降过渡期",
    # 说明：``not_owned`` 保留在错误码表中以兼容 PROTOCOL §5，但**入队阶段不再产生**
    # 它 —— 起点归属改为在发起调兵阶段判定并按 R4.12 作废（R4.11）。
    "not_owned": "起点地块不归你控制",
    "not_adjacent": "方向越界或非 4 相邻",
    "blocked": "目标为山地，不可通行",
    "chain_full": f"调兵链已满（上限 {CHAIN_LIMIT} 条）",
    "room_full": "房间已满",
    "rate_limited": "指令过于频繁",
}


def _coerce_dir(direction: Dir | str) -> Dir:
    """把 ``Dir`` 或协议字符串转成 :class:`Dir`；非法抛 ``CommandError('bad_frame')``。"""
    if isinstance(direction, Dir):
        return direction
    try:
        return Dir(str(direction).lower())
    except (ValueError, TypeError) as exc:  # pragma: no cover - 防御分支
        raise CommandError("bad_frame", f"非法方向：{direction!r}") from exc


def quantize_ratio(ratio: float) -> float:
    """把任意比例量化到**真人可选集合** ``RATIOS`` = ``{10%, 20%, …, 100%}``（R6.6 / R6.7）。

    规则：先 clamp 到 ``[RATIO_MIN, RATIO_MAX]``，再**就近取整**到 10% 的整数倍
    （正好 5% 的中点向上取整，例如 15% → 20%、95% → 100%）。
    这是所有来源（前端、线协议、AI `Action`）共用的唯一入口：AI 因此不可能
    调出真人无法选择的比例（AI_API.md §1.2）。

    前置：``ratio`` 是有限数值。
    后置：返回值 ∈ ``RATIOS``（10 个离散值之一），不做"是否作废"的判定。
    抛出：``CommandError('bad_frame')`` —— ``ratio`` 不是有限数值（NaN / ±inf / 非数值）。
    """
    try:
        value = float(ratio)
    except (TypeError, ValueError) as exc:
        raise CommandError("bad_frame", f"非法比例：{ratio!r}") from exc
    if value != value or value in (float("inf"), float("-inf")):  # NaN / ±inf
        raise CommandError("bad_frame", f"非法比例：{ratio!r}")
    # 用整数百分比做量化，避免 0.3 / 0.7 等十进制小数的浮点误差。
    percent = min(100, max(1, int(math.floor(value * 100 + 0.5))))
    percent = min(100, max(10, int(math.floor(percent / 10 + 0.5)) * 10))
    return RATIOS[percent // 10 - 1]


def _require_player(state: GameState, player_id: int) -> PlayerState:
    """取玩家状态；不存在抛 ``CommandError('not_joined')``。"""
    player = state.players.get(player_id)
    if player is None:
        raise CommandError("not_joined", f"不存在的玩家：{player_id}")
    return player


def enqueue_move(
    state: GameState,
    player_id: int,
    src: int,
    direction: Dir | str,
    ratio: float,
) -> None:
    """入队一条调兵请求（INTERFACES §1.3，R4.11 / R7.2）。

    **预调兵**（R4.11）：入队时**不校验起点归属** —— 起点既可以是当前归该玩家控制的
    地块，也可以是该玩家调兵链中任意请求的目标地块；合法性一律推迟到
    **发起调兵阶段**判定（R7.2，见 :func:`generals.game.tick.launch_moves`），
    届时起点已失守的请求按 R4.12 作废。

    前置（依次校验，任一不满足抛对应错误码）：
      * ``not state.ended`` → ``not_started``；
      * 玩家存在（``not_joined``）、``alive`` 且不在投降过渡期（R5.5）→ ``not_your_turn``；
      * ``src`` 是界内整数（R2.1）→ ``bad_frame``；
      * ``direction`` 为 4 方向之一且不越界（R4.1）→ ``not_adjacent``；
      * 目标地块**真实**地形非山地（R4.9）→ ``blocked``；
      * 调兵链长度 < ``CHAIN_LIMIT`` → ``chain_full``（R4.7 / R6.11）。
    后置：``state.players[player_id].chain`` 末尾追加一条 :class:`MoveRequest`；
          ``ratio`` 已按 :func:`quantize_ratio` 量化到 10% 的整数倍（10%..100%，R6.6）。
    抛出：:class:`CommandError`，``code`` ∈
          ``{not_started, not_joined, not_your_turn, bad_frame, not_adjacent, blocked, chain_full}``。
          **不产生 ``not_owned``**（R4.11：入队不校验起点归属）。
    说明：兵力 1 的地块也可以入队 —— 结算时按 R4.2 算出 ``<= 0`` 的量，
          再由 R4.12 作废并下发原因。
    """
    if state.ended:
        raise CommandError("not_started", "对局已结束")
    player = _require_player(state, player_id)
    if not player.can_operate():
        raise CommandError("not_your_turn", f"玩家 {player_id} 当前不可操作")

    if not isinstance(src, int) or isinstance(src, bool):
        raise CommandError("bad_frame", f"非法起点：{src!r}")
    if not (0 <= src < state.tile_count):
        raise CommandError("bad_frame", f"起点越界：{src}")

    d = _coerce_dir(direction)
    dst = state.step_index(src, d)
    if dst is None:
        raise CommandError("not_adjacent", f"地块 {src} 向 {d.value} 越界")
    if state.tiles[dst].is_mountain():
        raise CommandError("blocked", f"目标地块 {dst} 是山地（R4.9）")

    if len(player.chain) >= CHAIN_LIMIT:
        raise CommandError("chain_full")

    player.chain.append(MoveRequest(src=src, dir=d, ratio=quantize_ratio(ratio)))


def clear_chain(state: GameState, player_id: int) -> int:
    """清空该玩家的调兵链（R6.4 ``Q``）。

    前置：玩家存在（不存在时静默返回 0，便于 net 层兜底）。
    后置：链长度归 0；返回被清除的请求条数。
    """
    player = state.players.get(player_id)
    if player is None:
        return 0
    removed = len(player.chain)
    player.chain.clear()
    return removed


def pop_chain(state: GameState, player_id: int) -> MoveRequest | None:
    """取消该玩家**最新**一条调兵请求（R6.4 ``E``，后进先出）。

    前置：玩家存在（不存在时返回 ``None``）。
    后置：链尾被弹出并返回；链为空返回 ``None``。
    """
    player = state.players.get(player_id)
    if player is None or not player.chain:
        return None
    return player.chain.pop()


def chain_snapshot(state: GameState, player_id: int) -> list[dict]:
    """该玩家调兵链的线协议快照（PROTOCOL §3.6 ``commands`` 帧内容）。

    后置：按 FIFO 顺序返回 ``[{"from": src, "dir": "up", "ratio": 1.0}, ...]``。
    """
    player = state.players.get(player_id)
    if player is None:
        return []
    return [{"from": req.src, "dir": req.dir.value, "ratio": req.ratio} for req in player.chain]


def launched_amount(army: int, ratio: float) -> int:
    """实际移动兵力（R4.2 / R7.2）。

    公式：``min(ceil(army * ratio), army - 1)``，再夹到 ``>= 0``。

    前置：``army >= 0``（地块兵力恒非负，R3.9）；``ratio`` 任意，非 ``(0, 1]`` 时内部量化。
    后置：返回**原始计算值**，可能为 ``0``（``army <= 1`` 时必然为 ``0``）；
          **不做"是否作废"的判定** —— ``<= 0`` 的语义由调用方按 R4.12 处理
          （见 :func:`generals.game.tick.launch_moves`：``<= 0`` ⇒ ``reason="zero_army"`` 作废）。
    说明：R4.2 明确"兵力为 1 的地块无法调兵"，因此 ``army == 1`` 时返回 ``0``。
    """
    if army <= 0:
        return 0
    r = ratio if 0.0 < ratio <= 1.0 else quantize_ratio(ratio)
    # ceil(army * r)：用整数运算避免浮点误差导致的向上取整漂移。
    scaled = army * r
    n = int(scaled)
    if n < scaled:
        n += 1
    cap = army - 1
    return max(0, min(n, cap))


__all__ = [
    "Dir",
    "MoveRequest",
    "CommandError",
    "CHAIN_LIMIT",
    "RATIO_MIN",
    "RATIO_MAX",
    "RATIO_STEP",
    "RATIOS",
    "quantize_ratio",
    "dir_of",
    "enqueue_move",
    "clear_chain",
    "pop_chain",
    "chain_snapshot",
    "launched_amount",
]

# ``Terrain`` 仅在类型检查/文档层面被提及，保留导入以便 `from .commands import Terrain` 的历史用法。
_ = Terrain
