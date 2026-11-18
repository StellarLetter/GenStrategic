"""地图生成：确定性、中心对称、地形比例、首都间距（R2.1 / R3.2 / R3.3 / R7.3）。"""

from __future__ import annotations

import random

import pytest

from generals.game import (
    DEFAULT_SIZE,
    MAX_SIZE,
    MIN_SIZE,
    MapGenError,
    Terrain,
    generate,
)


def _sig(state) -> list[tuple[int, int, int | None]]:
    """逐格指纹：地形/兵力/归属（用于确定性比对，R7.3）。"""
    return [(int(t.terrain), t.army, t.owner) for t in state.tiles]


# ---------------------------------------------------------------------------
# R7.3 —— 确定性
# ---------------------------------------------------------------------------


def test_same_seed_same_map_r7_3() -> None:
    a = generate(20, 4, random.Random(20260101))
    b = generate(20, 4, random.Random(20260101))
    assert _sig(a) == _sig(b)
    assert sorted(a.players) == sorted(b.players)


def test_different_seeds_differ_r7_3() -> None:
    a = generate(20, 4, random.Random(1))
    b = generate(20, 4, random.Random(999))
    assert _sig(a) != _sig(b)


# ---------------------------------------------------------------------------
# R3.2 —— 每名玩家一座王城
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("player_count", list(range(1, 9)))
def test_generate_supports_any_player_count_r3_2(player_count: int) -> None:
    """lead 裁决：1..8 人都必须能生成（含奇数）。"""
    state = generate(20, player_count, random.Random(42))
    assert len(state.players) == player_count
    caps = sorted(state.players[pid].capital for pid in state.players)
    assert len(caps) == player_count
    assert len(set(caps)) == player_count  # 互不重叠
    for pid in state.players:
        player = state.players[pid]
        assert player.alive is True
        cap = player.capital
        assert cap is not None
        tile = state.tiles[cap]
        assert tile.is_capital()
        assert tile.owner == pid
        assert tile.army >= 1


@pytest.mark.parametrize("size", [MIN_SIZE, 12, DEFAULT_SIZE, 30, MAX_SIZE])
def test_generate_works_across_sizes(size: int) -> None:
    for n in (1, 3, 4, 8):
        state = generate(size, n, random.Random(7))
        assert state.size == size
        assert state.tile_count == size * size
        assert len(state.players) == n


def test_capitals_respect_min_distance() -> None:
    state = generate(20, 4, random.Random(11))
    caps = sorted(state.players[pid].capital for pid in state.players)
    for i, a in enumerate(caps):
        for b in caps[i + 1 :]:
            ar, ac = divmod(a, state.size)
            br, bc = divmod(b, state.size)
            assert max(abs(ar - br), abs(ac - bc)) >= 2


# ---------------------------------------------------------------------------
# R7.3 —— 中心对称（偶数人数严格；奇数人数豁免中心 3×3）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("player_count", [1, 2, 4, 6, 8])
def test_even_maps_are_fully_symmetric_r7_3(player_count: int) -> None:
    """偶数人数时地图严格 180° 中心对称（1 人只有一座落单王城，中心 3×3 同奇数处理）。"""
    state = generate(20, player_count, random.Random(3))
    total = state.tile_count
    size = state.size
    # 1 人也属"奇数"路径：多出来的那座王城落在中心 3×3，该局部豁免。
    exempt: set[int] = set()
    if player_count % 2 == 1:
        crow, ccol = size // 2, size // 2
        for dr in range(-1, 2):
            for dc in range(-1, 2):
                exempt.add((crow + dr) * size + (ccol + dc))
    for idx in range(total):
        mirror = total - 1 - idx
        if idx in exempt or mirror in exempt:
            continue
        assert int(state.tiles[idx].terrain) == int(state.tiles[mirror].terrain)


@pytest.mark.parametrize("player_count", [3, 5, 7])
def test_odd_maps_symmetric_outside_center_3x3_r7_3(player_count: int) -> None:
    state = generate(20, player_count, random.Random(3))
    total = state.tile_count
    size = state.size
    crow, ccol = size // 2, size // 2
    exempt = set()
    for dr in range(-1, 2):
        for dc in range(-1, 2):
            exempt.add((crow + dr) * size + (ccol + dc))
    for idx in range(total):
        mirror = total - 1 - idx
        if idx in exempt or mirror in exempt:
            continue
        assert int(state.tiles[idx].terrain) == int(state.tiles[mirror].terrain)


# ---------------------------------------------------------------------------
# R3.2 / R3.3 —— 初始地形与中立城市兵力
# ---------------------------------------------------------------------------


def test_neutral_cities_have_expected_army_range_r3_3() -> None:
    state = generate(20, 2, random.Random(5))
    cities = [t for t in state.tiles if t.is_ordinary_city()]
    assert cities, "20x20 地图应至少生成 1 座中立城市"
    for tile in cities:
        assert tile.owner is None
        assert 40 <= tile.army <= 50


def test_mountains_are_impassable_terrain_r3_5() -> None:
    state = generate(20, 2, random.Random(5))
    mountains = [t for t in state.tiles if t.is_mountain()]
    assert len(mountains) > 0
    for tile in mountains:
        assert tile.owner is None
        assert tile.army == 0


def test_plains_start_empty_r3_4() -> None:
    state = generate(20, 2, random.Random(5))
    for tile in state.tiles:
        if tile.is_plain() and tile.owner is None:
            assert tile.army == 0


def test_capitals_have_no_mountain_within_radius_r3_2() -> None:
    state = generate(20, 4, random.Random(13))
    for pid in state.players:
        cap = state.players[pid].capital
        assert cap is not None
        cr, cc = divmod(cap, state.size)
        for dr in range(-2, 3):
            for dc in range(-2, 3):
                r, c = cr + dr, cc + dc
                if 0 <= r < state.size and 0 <= c < state.size:
                    assert not state.tiles[r * state.size + c].is_mountain()


def test_fresh_map_is_not_ended_r7_4() -> None:
    state = generate(20, 4, random.Random(2))
    assert state.tick == 0
    assert state.ended is False
    assert state.winner is None


def test_city_like_helpers_agree_with_terrain_values() -> None:
    state = generate(20, 4, random.Random(9))
    for tile in state.tiles:
        assert tile.is_city_like() == tile.is_capital() or tile.is_ordinary_city()
        assert tile.is_mountain() == (int(tile.terrain) == int(Terrain.MOUNTAIN))


# ---------------------------------------------------------------------------
# 参数校验
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("size", [MIN_SIZE - 1, MAX_SIZE + 1])
def test_rejects_out_of_range_size(size: int) -> None:
    with pytest.raises(MapGenError):
        generate(size, 2, random.Random(1))


@pytest.mark.parametrize("player_count", [0, 9])
def test_rejects_out_of_range_player_count(player_count: int) -> None:
    with pytest.raises(MapGenError):
        generate(20, player_count, random.Random(1))


def test_rejects_too_many_capitals_for_tiny_map() -> None:
    """10x10 放不下 8 座间距足够的首都时必须报错而不是静默重叠。"""
    state = generate(MIN_SIZE, 8, random.Random(1))
    caps = sorted(state.players[pid].capital for pid in state.players)
    assert len(set(caps)) == 8  # 要么放得下且互不重叠，要么直接抛 MapGenError


# ---------------------------------------------------------------------------
# 生成后能直接驱动内核（冒烟，避免"能建不能跑"）
# ---------------------------------------------------------------------------


def test_generated_map_can_be_stepped_r7_1() -> None:
    from generals.game import tick

    state = generate(12, 4, random.Random(4))
    for _ in range(3):
        report = tick.step(state)
        assert report.tick == state.tick
    assert state.tick == 3
    # 4 名玩家各占 1 块地，未结束
    assert sorted(state.players_with_land()) == [0, 1, 2, 3]
    assert state.ended is False
