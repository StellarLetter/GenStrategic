"""指令与调兵链：实际兵力公式、FIFO、上限、校验错误码（R4.1 / R4.2 / R4.7 / R4.9 / C-02 / C-05 / C-06 / C-07 / C-08）。"""

from __future__ import annotations

import pytest

from helpers_kernel import make_state, make_tile
from generals.game import (
    CHAIN_LIMIT,
    CommandError,
    Dir,
    MoveRequest,
    Terrain,
    chain_snapshot,
    clear_chain,
    enqueue_move,
    launched_amount,
    pop_chain,
    tick,
)


# ---------------------------------------------------------------------------
# R4.2 / C-02 / C-07 —— 实际移动兵力公式
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("army", "ratio", "expected"),
    [
        (0, 1.0, 0),  # C-02：0 兵力起步 → 0
        (1, 1.0, 0),  # army - 1 = 0
        (2, 1.0, 1),
        (10, 1.0, 9),  # 100% 也留 1 兵
        (10, 0.5, 5),
        (10, 0.1, 1),  # ceil(1.0) = 1
        (11, 0.1, 2),  # ceil(1.1) = 2
        (9, 0.5, 5),  # ceil(4.5) = 5
        (3, 0.34, 2),  # ceil(1.02) = 2
        (100, 0.99, 99),
        (2, 0.01, 1),  # 极小比例仍至少 1，且不超过 army-1
    ],
)
def _launched_amount_case(army: int, ratio: float, expected: int) -> None:
    """参数化用例的实体（由下面的具体测试函数调用）。"""
    assert launched_amount(army, ratio) == expected


def test_launched_amount_ceil_and_cap_r4_2_c07() -> None:
    """R4.2 / C-07：ceil 取整 + 不超过 army-1 + 允许 0。"""
    cases = [
        (0, 1.0, 0),
        (1, 1.0, 0),
        (2, 1.0, 1),
        (10, 1.0, 9),
        (10, 0.5, 5),
        (10, 0.1, 1),
        (11, 0.1, 2),
        (9, 0.5, 5),
        (3, 0.34, 2),
        (100, 0.99, 99),
        (2, 0.01, 1),
    ]
    for army, ratio, expected in cases:
        _launched_amount_case(army, ratio, expected)


def test_launched_amount_never_exceeds_army_minus_one_r4_2() -> None:
    for army in range(0, 40):
        got = launched_amount(army, 1.0)
        assert 0 <= got <= max(0, army - 1)


# ---------------------------------------------------------------------------
# R4.7 / C-05 —— 每刻每玩家只弹 1 条，FIFO
# ---------------------------------------------------------------------------


def test_fifo_pops_one_request_per_tick_r4_7() -> None:
    state = make_state(size=5, players=(0, 1))
    cap = state.players[0].capital
    assert cap is not None
    # 首都 0 加大兵力，连排 3 条向右（同一 src 同一方向，链式验证不需要）
    state.tiles[cap].army = 9
    right = state.step_index(cap, Dir.RIGHT)
    assert right is not None
    state.tiles[right] = make_tile(Terrain.PLAIN, army=0, owner=None)

    enqueue_move(state, 0, cap, Dir.RIGHT, 1.0)
    enqueue_move(state, 0, cap, Dir.RIGHT, 0.5)
    enqueue_move(state, 0, cap, Dir.RIGHT, 0.1)
    assert chain_snapshot(state, 0) == [
        {"from": cap, "dir": "right", "ratio": 1.0},
        {"from": cap, "dir": "right", "ratio": 0.5},
        {"from": cap, "dir": "right", "ratio": 0.1},
    ]

    # 第一刻只消费 1 条（FIFO 头）。同刻先增长：9 → 10（R3.4/C-01），
    # 于是实际兵力 = min(ceil(10*1.0), 10-1) = 9。
    r1 = tick.step(state)
    assert len(r1.launched) == 1
    assert r1.launched[0].amount == 9
    assert r1.launched[0].src == cap
    assert len(state.players[0].chain) == 2

    # 第二刻消费第 2 条：起点被上一刻打空（10-9=1），且本刻是 Turn 1（上半刻）**不增长**，
    # 于是 ceil(1*0.5)=1 被 army-1=0 截断 → 0 兵力，不产生 flight（C-02），但请求仍被消费。
    r2 = tick.step(state)
    assert r2.half == 0
    assert r2.launched == []
    assert len(state.players[0].chain) == 1

    # 第三刻（Turn 1.）增长 +1 → 起点 2，ceil(2*0.1)=1 → 1 兵力。
    r3 = tick.step(state)
    assert r3.half == 1
    assert len(r3.launched) == 1
    assert r3.launched[0].amount == 1
    assert state.players[0].chain == []


def test_chain_limit_rejects_21st_request_r4_7() -> None:
    state = make_state(size=5, players=(0, 1))
    cap = state.players[0].capital
    assert cap is not None
    for _ in range(CHAIN_LIMIT):
        enqueue_move(state, 0, cap, Dir.RIGHT, 1.0)
    assert len(state.players[0].chain) == CHAIN_LIMIT
    with pytest.raises(CommandError) as exc:
        enqueue_move(state, 0, cap, Dir.RIGHT, 1.0)
    assert exc.value.code == "chain_full"


# ---------------------------------------------------------------------------
# C-06 / R6.7 —— 每个请求各自记录入队时的比例
# ---------------------------------------------------------------------------


def test_each_request_keeps_its_own_ratio_c06() -> None:
    state = make_state(size=5, players=(0, 1))
    cap = state.players[0].capital
    assert cap is not None
    enqueue_move(state, 0, cap, Dir.RIGHT, 0.3)
    enqueue_move(state, 0, cap, Dir.RIGHT, 0.7)
    ratios = [req.ratio for req in state.players[0].chain]
    assert ratios == [0.3, 0.7]
    assert all(isinstance(req, MoveRequest) for req in state.players[0].chain)


def test_ratio_is_clamped_into_open_unit_interval_c02() -> None:
    state = make_state(size=5, players=(0, 1))
    cap = state.players[0].capital
    assert cap is not None
    enqueue_move(state, 0, cap, Dir.RIGHT, 5.0)
    enqueue_move(state, 0, cap, Dir.RIGHT, -2.0)
    enqueue_move(state, 0, cap, Dir.RIGHT, 0.0)
    ratios = [req.ratio for req in state.players[0].chain]
    assert ratios[0] == 1.0
    assert all(0.0 < r <= 1.0 for r in ratios)


# ---------------------------------------------------------------------------
# R4.1 / R4.9 与 PROTOCOL §5 —— 校验与错误码
# ---------------------------------------------------------------------------


def test_enqueue_rejects_not_owned_r4_1() -> None:
    state = make_state(size=5, players=(0, 1))
    neutral = state.index(2, 2)
    with pytest.raises(CommandError) as exc:
        enqueue_move(state, 0, neutral, Dir.RIGHT, 1.0)
    assert exc.value.code == "not_owned"


def test_enqueue_rejects_out_of_board_r4_1() -> None:
    state = make_state(size=5, players=(0, 1))
    with pytest.raises(CommandError) as exc:
        enqueue_move(state, 0, 0, Dir.UP, 1.0)
    assert exc.value.code == "not_adjacent"
    with pytest.raises(CommandError) as exc2:
        enqueue_move(state, 0, 0, Dir.LEFT, 1.0)
    assert exc2.value.code == "not_adjacent"


def test_enqueue_rejects_mountain_target_r4_9() -> None:
    state = make_state(size=5, players=(0, 1))
    cap = state.players[0].capital
    assert cap is not None
    target = state.step_index(cap, Dir.RIGHT)
    assert target is not None
    state.tiles[target] = make_tile(Terrain.MOUNTAIN, army=0, owner=None)
    with pytest.raises(CommandError) as exc:
        enqueue_move(state, 0, cap, Dir.RIGHT, 1.0)
    assert exc.value.code == "blocked"


def test_enqueue_rejects_dead_or_surrendering_player_c08() -> None:
    state = make_state(size=5, players=(0, 1))
    cap = state.players[0].capital
    assert cap is not None

    state.players[0].alive = False
    with pytest.raises(CommandError) as exc:
        enqueue_move(state, 0, cap, Dir.RIGHT, 1.0)
    assert exc.value.code == "not_your_turn"

    state.players[0].alive = True
    state.players[0].surrender_ticks_left = 40
    with pytest.raises(CommandError) as exc2:
        enqueue_move(state, 0, cap, Dir.RIGHT, 1.0)
    assert exc2.value.code == "not_your_turn"


def test_enqueue_rejects_after_game_end_r7_4() -> None:
    state = make_state(size=5, players=(0, 1))
    cap = state.players[0].capital
    assert cap is not None
    state.ended = True
    with pytest.raises(CommandError) as exc:
        enqueue_move(state, 0, cap, Dir.RIGHT, 1.0)
    assert exc.value.code == "not_started"


def test_enqueue_accepts_protocol_dir_strings() -> None:
    state = make_state(size=5, players=(0, 1))
    # 用地图中间的一块自有地块，四个方向都不越界
    src = state.index(2, 2)
    state.tiles[src] = make_tile(Terrain.PLAIN, army=5, owner=0)
    enqueue_move(state, 0, src, "up", 1.0)
    enqueue_move(state, 0, src, "left", 0.5)
    assert state.players[0].chain[0].dir is Dir.UP
    assert state.players[0].chain[1].dir is Dir.LEFT
    assert Dir("down") is Dir.DOWN
    assert Dir("right").value == "right"


# ---------------------------------------------------------------------------
# R6.4 —— clear / pop
# ---------------------------------------------------------------------------


def test_clear_chain_removes_all_r6_4() -> None:
    state = make_state(size=5, players=(0, 1))
    cap = state.players[0].capital
    assert cap is not None
    enqueue_move(state, 0, cap, Dir.RIGHT, 1.0)
    enqueue_move(state, 0, cap, Dir.RIGHT, 1.0)
    assert clear_chain(state, 0) == 2
    assert state.players[0].chain == []
    assert clear_chain(state, 99) == 0


def test_pop_chain_removes_latest_r6_4() -> None:
    state = make_state(size=5, players=(0, 1))
    cap = state.players[0].capital
    assert cap is not None
    enqueue_move(state, 0, cap, Dir.RIGHT, 0.25)
    enqueue_move(state, 0, cap, Dir.DOWN, 0.75)
    popped = pop_chain(state, 0)
    assert popped is not None
    assert popped.dir is Dir.DOWN and popped.ratio == 0.75
    assert len(state.players[0].chain) == 1
    _ = pop_chain(state, 0)
    assert pop_chain(state, 0) is None
    assert pop_chain(state, 99) is None


# ---------------------------------------------------------------------------
# R4.2 —— 0 兵力请求合法且仍消耗请求
# ---------------------------------------------------------------------------


def test_zero_army_request_is_legal_and_consumed_r4_2_c02() -> None:
    """C-02：允许发起 0 兵力请求，且该请求仍被 FIFO 消费掉（不产生 flight）。

    构造真正的"0 兵力起点"：本源刻不增长（tick=2 是 Turn 1 的上半刻），
    且起点兵力恰为 1 → army-1 = 0。
    """
    state = make_state(size=5, players=(0, 1))
    cap = state.players[0].capital
    assert cap is not None
    state.tiles[cap].army = 1  # army-1 = 0
    state.tick = 1  # 下一次 step 落在 tick=2 = (turn 1, half 0) → 不增长
    enqueue_move(state, 0, cap, Dir.RIGHT, 1.0)
    report = tick.step(state)
    assert report.half == 0
    assert report.growth == []
    assert report.launched == []  # 0 兵力不产生 flight
    assert state.players[0].chain == []  # 但请求被消费
    assert state.tiles[cap].army == 1  # 未被扣减
