"""内核测试夹具：手工构造小块 GameState（不依赖 mapgen 的随机性）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SERVER = Path(__file__).resolve().parents[1]
if str(_SERVER) not in sys.path:
    sys.path.insert(0, str(_SERVER))

from generals.game import GameState, PlayerState, Terrain, Tile, tick, turn_of  # noqa: E402


def make_state(
    size: int = 5,
    players: tuple[int, ...] = (0, 1),
    capital_tiles: dict[int, int] | None = None,
) -> GameState:
    """构造全平原状态，每名玩家在自己的首都格上。

    缺省首都：按四角→中线的固定顺序分配（确定性，不涉及随机），最多支持 8 名玩家。
    """
    state = GameState(size=size, tiles=[Tile() for _ in range(size * size)])
    last = size * size - 1
    mid = (size // 2) * size + size // 2
    default_spots = (0, last, size - 1, last - (size - 1), mid, size * (size // 2), size // 2, size * size // 2)
    caps = capital_tiles or {
        pid: default_spots[i % len(default_spots)] for i, pid in enumerate(players)
    }
    for pid in players:
        cap = caps[pid]
        state.tiles[cap] = Tile(terrain=Terrain.CAPITAL, army=1, owner=pid)
        state.players[pid] = PlayerState(
            player_id=pid,
            name=f"P{pid}",
            color="red" if pid == 0 else "blue",
            alive=True,
            capital=cap,
            chain=[],
        )
    return state


def make_tile(
    terrain: Terrain = Terrain.PLAIN,
    army: int = 0,
    owner: int | None = None,
    city_from_capital: bool = False,
) -> Tile:
    return Tile(
        terrain=terrain, army=army, owner=owner, city_from_capital=city_from_capital
    )


def advance_to(state: GameState, tick_index: int) -> None:
    """推进到指定 tick（不超过目标）。"""
    while state.tick < tick_index and not state.ended:
        tick.step(state)


@pytest.fixture
def small_state() -> GameState:
    return make_state(size=5)


__all__ = [
    "make_state",
    "make_tile",
    "advance_to",
    "turn_of",
    "GameState",
    "PlayerState",
    "Terrain",
    "Tile",
    "tick",
]
