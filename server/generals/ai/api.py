"""AI 接口：`Observation` / `Action` / `GameSpec` / `Agent` 协议，以及观测构造工具。

契约见 `docs/AI_API.md` §1 与 `docs/INTERFACES.md` §3。

**不变式**：AI 只看到与人类玩家**完全相同的视野裁剪**（一律经 `game.view.project`），
永远不能直接改写 `GameState`（ARCHITECTURE.md §3「ai 只依赖 game 的只读视图」）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from generals.game.state import GameState, turn_of
from generals.game.view import TileView, project

__all__ = [
    "Action",
    "Agent",
    "GameSpec",
    "Observation",
    "Row",
    "to_observation",
    "to_observation_for",
]

#: `Action.op` 允许的取值（AI_API.md §1.2）。
Op = Literal["move", "clear", "pop", "select"]

#: 调兵方向字面量（与 `docs/PROTOCOL.md` §2.4 一致）。
Dirs = Literal["up", "down", "left", "right"]


@dataclass(frozen=True)
class Row:
    """排行榜一行（AI_API.md §1.1 的 `leaderboard`）。

    - `army` = 全部地块兵力之和（R6.8）
    - `land` = 控制地块数（C-16）
    - `alive` = 是否仍在局中（R5.3）
    """

    player_id: int
    name: str
    color: str
    army: int
    land: int
    alive: bool

    @property
    def playerId(self) -> int:  # noqa: N802 - 与线协议字段名保持一致，便于跨语言对照
        """线协议风格的别名（PROTOCOL.md §3.5）。"""
        return self.player_id


@dataclass(frozen=True)
class GameSpec:
    """一局游戏的静态规格（`Agent.reset(spec)` 的入参）。

    前置：由 `Room` 在开局后构造。
    后置：只读；`players` 按 `player_id` 升序。
    抛出：无。
    """

    size: int
    player_count: int
    players: tuple[Row, ...] = ()
    tick_ms: int = 500


@dataclass(frozen=True)
class Observation:
    """AI 每次决策的输入（AI_API.md §1.1）。

    字段与文档逐字一致：`turn, half, size, me, tiles, my_army, my_land, leaderboard, chain`。
    `tiles` 已按视野裁剪：视野外 `army is None`、地形折叠为 0/2（C-17）。
    """

    turn: int
    half: int
    size: int
    me: int
    tiles: list[TileView]
    my_army: int
    my_land: int
    leaderboard: list[Row] = field(default_factory=list)
    chain: list[Any] = field(default_factory=list)

    def at(self, index: int) -> TileView:
        """按 index 取地块视图（`tiles[index]` 的下标 == index，INTERFACES §1.7）。

        前置：`0 <= index < size * size`。
        后置：返回该地块的 `TileView`。
        抛出：`IndexError` —— 越界。
        """
        return self.tiles[index]


@dataclass(frozen=True)
class Action:
    """AI 的输出（AI_API.md §1.2）。

    - `move`：`src`、`dir ∈ {up,down,left,right}`、`ratio ∈ (0,1]`
    - `clear` / `pop`：无字段
    - `select`：`tile`

    后置：字段默认值让「只填需要的字段」成立（`Action(op="clear")`）。
    """

    op: Op
    src: int = 0
    dir: str = ""
    ratio: float = 1.0
    tile: int = 0

    def to_command(self) -> dict:
        """转成 `protocol.validate_commands` 接受的指令 dict（PROTOCOL.md §2.4）。

        前置：无。
        后置：返回 `{"op": ...}` 形式的纯 dict，字段与线协议一致。
        抛出：`ValueError` —— `op` 不在 `{move, clear, pop, select}`（属于策略实现的 bug）。
        """
        if self.op == "move":
            return {"op": "move", "from": int(self.src), "dir": str(self.dir), "ratio": float(self.ratio)}
        if self.op == "select":
            return {"op": "select", "tile": int(self.tile)}
        if self.op in ("clear", "pop"):
            return {"op": self.op}
        raise ValueError(f"unknown action op: {self.op!r}")


@runtime_checkable
class Agent(Protocol):
    """策略协议（INTERFACES §3）。

    前置：`reset()` 在本局开始前调用一次；`act()` 每 Tick 至多调用一次。
    后置：`act()` 返回 `Action` 或 `None`（`None` = 本 Tick 不操作）。
    抛出：实现方**不应**让 `act()` 抛异常——`ai.runner` 会捕获并视为「本 Tick 不操作」，
          但频繁异常会拖慢对局。
    """

    name: str

    def reset(self, spec: GameSpec) -> None:  # pragma: no cover - 协议声明
        """开局初始化。"""
        ...

    def act(self, obs: Observation) -> Action | None:  # pragma: no cover - 协议声明
        """根据观测返回一个动作，或 `None`。"""
        ...


# ----------------------------------------------------------------------
# 观测构造
# ----------------------------------------------------------------------


def to_observation(state: GameState, player_id: int) -> Observation:
    """把权威状态裁剪成某玩家的 `Observation`（AI_API.md §1.1）。

    前置：`player_id` 是 `state.players` 的键。
    后置：
    - `tiles` **直接来自 `game.view.project(state, player_id)`**（长度 `size²`，下标 == index）；
    - `my_army = state.army_total(player_id)`（R6.8）、`my_land = state.land_count(player_id)`（C-16）；
    - `leaderboard` 按 `player_id` 升序（R7.3）；
    - `chain` 是该玩家当前的调兵链（服务端权威回显，C-06）。
    抛出：`KeyError` —— `player_id` 不在局中。
    """
    pstate = state.players[player_id]
    turn, half = turn_of(state.tick)
    rows = _leaderboard_rows(state)
    return Observation(
        turn=turn,
        half=half,
        size=state.size,
        me=player_id,
        tiles=project(state, player_id),
        my_army=int(state.army_total(player_id)),
        my_land=int(state.land_count(player_id)),
        leaderboard=rows,
        chain=list(getattr(pstate, "chain", []) or []),
    )


def to_observation_for(state: GameState, seat_player: int | None) -> Observation:
    """同 `to_observation`，但允许观战者（`seat_player=None`）。

    前置：`seat_player is None` 或为局中玩家。
    后置：`me = -1` 表示观战；`tiles = project(state, None)`（全图真实，P-02）；
          `my_army/my_land` 为 0；`chain` 为空。
    抛出：无。
    """
    if seat_player is not None:
        return to_observation(state, seat_player)
    turn, half = turn_of(state.tick)
    return Observation(
        turn=turn,
        half=half,
        size=state.size,
        me=-1,
        tiles=project(state, None),
        my_army=0,
        my_land=0,
        leaderboard=_leaderboard_rows(state),
        chain=[],
    )


def _leaderboard_rows(state: GameState) -> list[Row]:
    """按 `player_id` 升序构造排行榜（R7.3 / R6.8 / C-16）。"""
    rows: list[Row] = []
    for pid in sorted(state.players):
        pstate = state.players[pid]
        rows.append(
            Row(
                player_id=pid,
                name=pstate.name,
                color=pstate.color,
                army=int(state.army_total(pid)),
                land=int(state.land_count(pid)),
                alive=bool(pstate.alive),
            )
        )
    return rows
