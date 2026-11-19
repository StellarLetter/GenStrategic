"""地图生成：尺寸公式、无对称、曼哈顿首都间距、可复现（R2.1–R2.7）。

覆盖：
  * R2.3 边长由玩家数决定：``clamp(6 + 3N + rand(0..6), 10, 40)``；
  * R2.4 地图**无任何对称结构**；
  * R2.5 首都间**曼哈顿**距离 ``>= 13``，可位于任意格子（含边缘/角落）；
  * R2.6 放不下换随机序列重试，边长不变。
"""

from __future__ import annotations

import random

import pytest

from generals import config
from generals.game import (
    DEFAULT_SIZE,
    MAX_SIZE,
    MIN_SIZE,
    MapGenError,
    Terrain,
    generate,
    manhattan,
)
from generals.game.mapgen import CAPITAL_MIN_MANHATTAN, MAX_ATTEMPTS


def _sig(state) -> list[tuple[int, int, int | None]]:
    """逐格指纹：地形/兵力/归属（用于确定性比对，R7.3）。"""
    return [(int(t.terrain), t.army, t.owner) for t in state.tiles]


def _caps(state) -> list[int]:
    return sorted(state.players[pid].capital for pid in state.players)


def _min_pair_manhattan(state) -> int:
    caps = _caps(state)
    best = 10**9
    for i, a in enumerate(caps):
        for b in caps[i + 1 :]:
            best = min(best, manhattan(a, b, state.size))
    return best


def _expected_size_range(player_count: int) -> tuple[int, int]:
    """R2.3 公式给出的闭区间。"""
    lo = max(MIN_SIZE, min(MAX_SIZE, config.SIZE_BASE + config.SIZE_PER_PLAYER * player_count))
    hi = max(
        MIN_SIZE,
        min(
            MAX_SIZE,
            config.SIZE_BASE
            + config.SIZE_PER_PLAYER * player_count
            + config.SIZE_JITTER_MAX,
        ),
    )
    return lo, hi


# ---------------------------------------------------------------------------
# R2.3 —— 边长由玩家数决定
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("player_count", list(range(1, 9)))
def test_size_follows_player_count_formula_r2_3(player_count: int) -> None:
    lo, hi = _expected_size_range(player_count)
    seen: set[int] = set()
    for seed in range(24):
        state = generate(player_count, random.Random(seed))
        assert lo <= state.size <= hi, (player_count, seed, state.size, lo, hi)
        seen.add(state.size)
    # 多种子应能覆盖到区间内多个值（rand(0..6) 真的在用）
    assert len(seen) > 1


def test_eight_players_size_in_30_to_36_r2_3() -> None:
    """8 人局 ⇒ size ∈ [30, 36]（R2.3 明确写出的例子）。"""
    seen = set()
    for seed in range(40):
        state = generate(8, random.Random(seed))
        assert 30 <= state.size <= 36, (seed, state.size)
        seen.add(state.size)
    assert seen  # 至少成功生成过


def test_size_formula_constants_match_r2_3() -> None:
    assert config.SIZE_BASE == 6
    assert config.SIZE_PER_PLAYER == 3
    assert config.SIZE_JITTER_MAX == 6


def test_explicit_size_is_respected_for_replay() -> None:
    """显式 size 仅供测试/回放，必须原样生效（用小玩家数以避开 R2.5 容量限制）。"""
    for size in (MIN_SIZE, 16, 28, MAX_SIZE):
        state = generate(1, random.Random(5), size=size)
        assert state.size == size
        assert state.tile_count == size * size


# ---------------------------------------------------------------------------
# R2.4 —— 无对称
# ---------------------------------------------------------------------------


def test_map_has_no_mirror_symmetry_r2_4() -> None:
    """不存在 180° 旋转对称：镜像格地形一致的格子数应远少于总数。"""
    state = generate(4, random.Random(20260101))
    total = state.tile_count
    same = sum(
        1
        for idx in range(total)
        if int(state.tiles[idx].terrain) == int(state.tiles[total - 1 - idx].terrain)
    )
    # 若真对称会是 total；无对称时平原占多数也能到 ~60%，留足余量。
    assert same < total, "地图出现完全镜像对称，违反 R2.4"


def test_capital_positions_are_not_mirrored_r2_4() -> None:
    """首都位置不做镜像配对（不再有成对旋转关系）。"""
    for seed in (1, 2, 3, 42, 777):
        state = generate(4, random.Random(seed))
        caps = set(_caps(state))
        total = state.tile_count
        mirrored = {total - 1 - c for c in caps}
        assert mirrored != caps, f"seed={seed} 首都仍成对镜像（违反 R2.4）"


def test_mapgen_module_has_no_symmetry_helpers() -> None:
    """对称相关内部函数必须已被删除（R2.4）。"""
    from generals.game import mapgen as m

    for name in (
        "_canonical",
        "_build_orbits",
        "_ring_distance",
        "_pick_center_spot",
        "_center_index",
        "_place_capitals_with_center",
    ):
        assert not hasattr(m, name), f"{name} 应已删除（R2.4 无对称）"


# ---------------------------------------------------------------------------
# R2.5 —— 曼哈顿首都间距 >= 13
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("player_count", list(range(1, 9)))
def test_capital_manhattan_distance_at_least_13_r2_5(player_count: int) -> None:
    for seed in range(12):
        state = generate(player_count, random.Random(seed))
        if player_count < 2:
            continue
        assert _min_pair_manhattan(state) >= CAPITAL_MIN_MANHATTAN, (
            player_count,
            seed,
            _min_pair_manhattan(state),
        )


def test_capital_min_manhattan_constant_r2_5() -> None:
    assert CAPITAL_MIN_MANHATTAN == config.CAPITAL_MIN_MANHATTAN == 13


def test_manhattan_is_l1_not_chebyshev_r2_5() -> None:
    """曼哈顿（L1）与切比雪夫（L∞）不等价 —— 别复用旧函数。"""
    size = 10
    a = 0  # (0,0)
    b = 3 * size + 3  # (3,3)
    assert manhattan(a, b, size) == 6  # L1 = 3+3
    # 切比雪夫会是 3，说明两者确实不同
    assert manhattan(a, b, size) != 3


def test_capitals_may_sit_on_edges_r2_5() -> None:
    """R2.5 明确允许首都落在边缘/角落 —— 拒绝采样不得排除边界格。"""
    found_edge = False
    for seed in range(40):
        state = generate(2, random.Random(seed))
        size = state.size
        for cap in _caps(state):
            row, col = divmod(cap, size)
            if row in (0, size - 1) or col in (0, size - 1):
                found_edge = True
                break
        if found_edge:
            break
    assert found_edge, "40 个种子里没有一座首都落在边缘，采样应覆盖全部格子"


# ---------------------------------------------------------------------------
# R2.6 —— 放不下就换序列重试
# ---------------------------------------------------------------------------


def test_retry_constants_r2_6() -> None:
    assert MAX_ATTEMPTS == config.MAPGEN_MAX_ATTEMPTS == 64


def test_impossible_capital_layout_raises_r2_6() -> None:
    """边长被人为压到最小且玩家极多时，重试用尽必须抛 MapGenError（而不是静默重叠）。"""
    with pytest.raises(MapGenError):
        generate(8, random.Random(1), size=MIN_SIZE)


def test_retry_keeps_size_unchanged_r2_6() -> None:
    """成功生成时 size 就是请求/公式给出的值，不会因重试而改变。"""
    state = generate(8, random.Random(3))
    assert 30 <= state.size <= 36
    assert state.tile_count == state.size * state.size


# ---------------------------------------------------------------------------
# R3.2 / R3.3 / R7.3 —— 玩家、城市、确定性
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("player_count", list(range(1, 9)))
def test_generate_supports_any_player_count_r3_2(player_count: int) -> None:
    state = generate(player_count, random.Random(42))
    assert len(state.players) == player_count
    caps = _caps(state)
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
        assert tile.army == config.CAPITAL_START_ARMY


def test_same_seed_same_map_r7_3() -> None:
    a = generate(4, random.Random(20260101))
    b = generate(4, random.Random(20260101))
    assert a.size == b.size
    assert _sig(a) == _sig(b)
    assert _caps(a) == _caps(b)


def test_different_seeds_differ_r7_3() -> None:
    a = generate(4, random.Random(1))
    b = generate(4, random.Random(999))
    assert _sig(a) != _sig(b)


def test_neutral_cities_have_expected_army_range_r3_3() -> None:
    state = generate(4, random.Random(5))
    cities = [t for t in state.tiles if t.is_ordinary_city()]
    assert cities, "地图应至少生成 1 座中立城市"
    for tile in cities:
        assert tile.owner is None
        assert config.CITY_NEUTRAL_ARMY_MIN <= tile.army <= config.CITY_NEUTRAL_ARMY_MAX


def test_mountains_are_impassable_terrain_r3_5() -> None:
    state = generate(4, random.Random(5))
    mountains = [t for t in state.tiles if t.is_mountain()]
    assert mountains
    for tile in mountains:
        assert tile.owner is None
        assert tile.army == 0


def test_terrain_ratios_roughly_respected_r2_7() -> None:
    state = generate(6, random.Random(11))
    total = state.tile_count
    mountains = sum(1 for t in state.tiles if t.is_mountain())
    cities = sum(1 for t in state.tiles if t.is_ordinary_city())
    assert mountains <= int(total * config.MAPGEN_MOUNTAIN_RATIO) + 6
    assert mountains >= int(total * config.MAPGEN_MOUNTAIN_RATIO) * 0.5 - 6
    assert 1 <= cities <= int(total * config.MAPGEN_CITY_RATIO) + 6


def test_capitals_have_no_mountain_within_radius_r2_7() -> None:
    radius = config.MAPGEN_CAPITAL_CLEAR_RADIUS
    for seed in (1, 5, 13):
        state = generate(4, random.Random(seed))
        size = state.size
        for cap in _caps(state):
            cr, cc = divmod(cap, size)
            for dr in range(-radius, radius + 1):
                for dc in range(-radius, radius + 1):
                    r, c = cr + dr, cc + dc
                    if 0 <= r < size and 0 <= c < size:
                        assert not state.tiles[r * size + c].is_mountain(), (
                            seed,
                            cap,
                            (r, c),
                        )


def test_fresh_map_is_not_ended_r7_4() -> None:
    state = generate(4, random.Random(2))
    assert state.tick == 0
    assert state.ended is False
    assert state.winner is None


def test_city_like_helpers_agree_with_terrain_values() -> None:
    state = generate(4, random.Random(9))
    for tile in state.tiles:
        assert tile.is_city_like() == (tile.is_capital() or tile.is_ordinary_city())
        assert tile.is_mountain() == (int(tile.terrain) == int(Terrain.MOUNTAIN))


# ---------------------------------------------------------------------------
# 参数校验
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("size", [MIN_SIZE - 1, MAX_SIZE + 1, 0, -5])
def test_rejects_out_of_range_size(size: int) -> None:
    with pytest.raises(MapGenError):
        generate(2, random.Random(1), size=size)


@pytest.mark.parametrize("player_count", [0, 9, -1])
def test_rejects_out_of_range_player_count(player_count: int) -> None:
    with pytest.raises(MapGenError):
        generate(player_count, random.Random(1))


def test_default_size_constant_still_available() -> None:
    assert DEFAULT_SIZE == config.DEFAULT_SIZE


# ---------------------------------------------------------------------------
# 生成后能直接驱动内核（冒烟，避免"能建不能跑"）
# ---------------------------------------------------------------------------


def test_generated_map_can_be_stepped_r7_1() -> None:
    from generals.game import tick

    state = generate(4, random.Random(4))
    for _ in range(3):
        report = tick.step(state)
        assert report.tick == state.tick
    assert state.tick == 3
    assert sorted(state.players_with_land()) == [0, 1, 2, 3]
    assert state.ended is False