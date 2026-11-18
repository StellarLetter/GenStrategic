"""回合/Tick 语义与增长时机（R1.1 / R1.2 / R1.3 / R3.4 / R3.7 / C-01）。"""

from __future__ import annotations

import pytest

from helpers_kernel import make_state, make_tile
from generals.game import GameState, Terrain, Tile, tick, turn_of


def test_turn_of_maps_ticks_r1_2() -> None:
    """tick -> (turn, half)；开局 tick=0 即 Turn 0（R1.2）。"""
    assert turn_of(0) == (0, 0)
    assert turn_of(1) == (0, 1)
    assert turn_of(2) == (1, 0)
    assert turn_of(3) == (1, 1)
    assert turn_of(588) == (294, 0)


def test_turn_of_rejects_negative_tick_r1_2() -> None:
    with pytest.raises(ValueError):
        turn_of(-1)


def test_step_increments_tick_and_reports_half_r1_3() -> None:
    state = make_state(size=5)
    assert (state.tick, turn_of(state.tick)) == (0, (0, 0))

    report = tick.step(state)
    assert state.tick == 1
    assert (report.tick, report.turn, report.half) == (1, 0, 1)

    report = tick.step(state)
    assert (report.tick, report.turn, report.half) == (2, 1, 0)


def test_step_on_ended_state_is_noop_r7_4() -> None:
    """已结束的对局再 step 不应推进 tick（防御性契约）。"""
    state = make_state(size=5)
    state.ended = True
    state.winner = 0
    report = tick.step(state)
    assert state.tick == 0
    assert report.ended is True and report.winner == 0


def test_city_growth_only_on_half_one_c01() -> None:
    """首都/城市只在 Turn N.（half=1）增长 +1（R3.4 / R3.7 / C-01）。"""
    state = make_state(size=5, players=(0, 1))
    city = state.index(2, 2)
    state.tiles[city] = make_tile(Terrain.CITY, army=10, owner=0)

    # tick 0 -> 1（Turn 0.）：增长
    r = tick.step(state)
    assert state.tiles[0].army == 2
    assert state.tiles[city].army == 11
    assert {e.tile for e in r.growth} == {0, city, state.tile_count - 1}

    # tick 1 -> 2（Turn 1）：不增长
    r = tick.step(state)
    assert r.growth == []
    assert state.tiles[0].army == 2
    assert state.tiles[city].army == 11


def test_neutral_tiles_do_not_grow_r3_4() -> None:
    state = make_state(size=5, players=(0,))
    neutral_city = state.index(2, 2)
    state.tiles[neutral_city] = make_tile(Terrain.CITY, army=44, owner=None)

    for _ in range(4):
        tick.step(state)
    assert state.tiles[neutral_city].army == 44


def test_plain_growth_first_refresh_at_turn_25_dot_r3_4_c01() -> None:
    """平原第一次刷新在 Turn 25.（turn=24, half=1）（BRIEF 增长时机）。"""
    state = make_state(size=5, players=(0,))
    plain = state.index(2, 2)
    state.tiles[plain] = make_tile(Terrain.PLAIN, army=7, owner=0)

    # 推进到 turn=23 的 half=1（tick=47）：平原仍不增长
    while state.tick < 47:
        tick.step(state)
    assert turn_of(state.tick) == (23, 1)
    assert state.tiles[plain].army == 7

    # tick 47 -> 48 = turn=24, half=0，仍不增长
    tick.step(state)
    assert turn_of(state.tick) == (24, 0)
    assert state.tiles[plain].army == 7

    # tick 48 -> 49 = turn=24, half=1，即 Turn 25. 结算 → 平原 +1
    tick.step(state)
    assert turn_of(state.tick) == (24, 1)
    assert state.tiles[plain].army == 8


def test_plain_second_refresh_at_turn_50_dot_r3_4() -> None:
    state = make_state(size=3, players=(0,))
    plain = state.index(1, 1)
    state.tiles[plain] = make_tile(Terrain.PLAIN, army=0, owner=0)

    # 推进到 turn=24, half=1（tick=49）→ 第一次 +1
    while state.tick < 49:
        tick.step(state)
    assert state.tiles[plain].army == 1
    # 推进到 turn=49, half=1（tick=99）→ 第二次 +1
    while state.tick < 99:
        tick.step(state)
    assert turn_of(state.tick) == (49, 1)
    assert state.tiles[plain].army == 2


def test_plain_growth_not_on_other_halves_r3_7() -> None:
    """turn ∈ {24,49,...} 的 half=0 不增长（R3.7）。"""
    state = make_state(size=3, players=(0,))
    plain = state.index(1, 1)
    state.tiles[plain] = make_tile(Terrain.PLAIN, army=0, owner=0)
    while state.tick < 48:
        tick.step(state)
    assert turn_of(state.tick) == (24, 0)
    assert state.tiles[plain].army == 0


def test_mountain_never_grows_r3_4() -> None:
    state = make_state(size=3, players=(0,))
    mount = state.index(1, 1)
    state.tiles[mount] = make_tile(Terrain.MOUNTAIN, army=0, owner=0)
    for _ in range(4):
        tick.step(state)
    assert state.tiles[mount].army == 0


def test_city_from_capital_grows_like_city_r5_4() -> None:
    """首都转化型城市逻辑同普通城市，也享受 +1 增长（R5.4 / R3.4）。"""
    state = make_state(size=3, players=(0,))
    tile = state.tiles[state.index(1, 1)]
    tile.terrain = Terrain.CITY
    tile.city_from_capital = True
    tile.owner = 0
    tile.army = 5
    tick.step(state)
    assert tile.army == 6
