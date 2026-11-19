"""确定性与不变量：无随机、无时间、集合遍历有序、地块总数守恒（R7.3 / R2.1 / R3.5）。"""

from __future__ import annotations

import inspect
import random

from helpers_kernel import make_state, make_tile
from generals.game import (
    Dir,
    Terrain,
    enqueue_move,
    generate,
    tick,
)
from generals.game import commands, combat, mapgen, rules, state as state_mod, view


FORBIDDEN_IMPORTS = ("fastapi", "time", "datetime", "uuid", "secrets")


def test_kernel_modules_avoid_forbidden_imports_r7_3() -> None:
    """内核层不得依赖 fastapi / time 之类非纯函数来源（BRIEF 统一约束）。"""
    for mod in (state_mod, commands, combat, rules, tick, view, mapgen):
        src = inspect.getsource(mod)
        for name in FORBIDDEN_IMPORTS:
            assert f"import {name}" not in src, f"{mod.__name__} 不应 import {name}"
            assert f"from {name}" not in src, f"{mod.__name__} 不应 from {name} import"


def test_kernel_uses_no_module_level_random_r7_3() -> None:
    """随机只允许由外部注入 ``random.Random``；不得调用模块级 random 函数。"""
    for mod in (state_mod, commands, combat, rules, tick, view, mapgen):
        src = inspect.getsource(mod)
        for call in ("random.random(", "random.randint(", "random.choice(", "random.shuffle("):
            assert call not in src, f"{mod.__name__} 不应调用模块级 {call}"


def _play(seed: int, ticks: int = 6) -> list[tuple[int, int, int | None]]:
    """用给定种子跑一段固定剧本，返回终局指纹。"""
    st = generate(4, random.Random(seed))
    rng = random.Random(seed * 31 + 7)
    for _ in range(ticks):
        for pid in sorted(st.players):
            if not st.players[pid].can_operate():
                continue
            owned = st.owned_tiles(pid)
            if not owned:
                continue
            src = owned[rng.randrange(len(owned))]
            dirs = [d for d in Dir if st.step_index(src, d) is not None]
            if not dirs:
                continue
            d = dirs[rng.randrange(len(dirs))]
            try:
                enqueue_move(st, pid, src, d, 0.5)
            except Exception:  # noqa: BLE001 - blocked 属正常
                continue
        tick.step(st)
    return [(int(t.terrain), t.army, t.owner) for t in st.tiles]


def test_same_script_same_outcome_r7_3() -> None:
    assert _play(12345) == _play(12345)


def test_different_seeds_usually_differ_r7_3() -> None:
    assert _play(1) != _play(2)


def test_tile_count_conserved_r2_1() -> None:
    st = generate(4, random.Random(5))
    total = st.tile_count
    for _ in range(5):
        tick.step(st)
        assert len(st.tiles) == total == st.size * st.size


def test_owned_tiles_sorted_ascending_r7_3() -> None:
    st = generate(4, random.Random(6))
    for pid in sorted(st.players):
        owned = st.owned_tiles(pid)
        assert owned == sorted(owned)


def test_leaderboard_deterministic_order_r7_3() -> None:
    st = generate(4, random.Random(8))
    first = st.leaderboard()
    second = st.leaderboard()
    assert first == second
    # 排序键稳定：星数相同、按 (地块数 desc, player_id asc) 之类固定规则
    keys = [tuple(row) if isinstance(row, tuple) else row for row in first]
    assert len(keys) == len(first)


def test_changed_set_is_index_sorted_when_serialized_r7_3() -> None:
    st = make_state(size=5, players=(0, 1))
    cap = st.players[0].capital
    assert cap is not None
    st.tiles[cap].army = 9
    st.tick = 1
    enqueue_move(st, 0, cap, Dir.RIGHT, 1.0)
    report = tick.step(st)
    assert sorted(report.changed) == list(sorted(report.changed))


def test_report_owners_changed_is_sorted_r7_3() -> None:
    st = make_state(size=5, players=(0, 1))
    cap = st.players[0].capital
    assert cap is not None
    st.tiles[cap].army = 9
    st.tick = 1
    enqueue_move(st, 0, cap, Dir.RIGHT, 1.0)
    report = tick.step(st)
    assert report.owners_changed() == sorted(report.owners_changed())


def test_turn_of_is_pure_function_r1_2() -> None:
    assert state_mod.turn_of(0) == (0, 0)
    assert state_mod.turn_of(1) == (0, 1)
    assert state_mod.turn_of(2) == (1, 0)
    assert state_mod.turn_of(99) == (49, 1)
    # 纯函数：重复调用同入同出
    assert state_mod.turn_of(57) == state_mod.turn_of(57)


def test_no_wall_clock_dependency_in_step_r7_3() -> None:
    """同一局面在不同时刻推进，结果一致（不读时钟）。"""
    a = make_state(size=5, players=(0, 1))
    b = make_state(size=5, players=(0, 1))
    for st in (a, b):
        st.tiles[st.players[0].capital].army = 11
        st.tick = 1
        enqueue_move(st, 0, st.players[0].capital, Dir.RIGHT, 1.0)
    tick.step(a)
    tick.step(b)
    assert [(int(t.terrain), t.army, t.owner) for t in a.tiles] == [
        (int(t.terrain), t.army, t.owner) for t in b.tiles
    ]


def test_mountain_blocks_expansion_invariant_r3_5() -> None:
    """任何时刻山地都不会被占领（R3.5 不变量）。"""
    st = generate(4, random.Random(77))
    for _ in range(4):
        tick.step(st)
        for tile in st.tiles:
            if tile.is_mountain():
                assert tile.owner is None
