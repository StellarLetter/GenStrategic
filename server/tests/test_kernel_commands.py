"""指令与调兵链：实际兵力公式、FIFO、上限、校验错误码（R4.1 / R4.2 / R4.7 / R4.9 / R4.11 / R4.12 / R6.7 / R5.5）。"""

from __future__ import annotations

import pytest

from helpers_kernel import make_state, make_tile
from generals.game import (
    CHAIN_LIMIT,
    RATIOS,
    CommandError,
    Dir,
    MoveRequest,
    Terrain,
    chain_snapshot,
    clear_chain,
    enqueue_move,
    launched_amount,
    pop_chain,
    quantize_ratio,
    tick,
)


# ---------------------------------------------------------------------------
# R4.2 —— 实际移动兵力公式
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("army", "ratio", "expected"),
    [
        (0, 1.0, 0),  # 0 兵力起步 → 0（该请求随后按 R4.12 作废）
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


def test_launched_amount_ceil_and_cap_r4_2() -> None:
    """R4.2：ceil 取整 + 不超过 army-1（``<= 0`` 由调用方按 R4.12 作废）。"""
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
# R4.7 —— 每刻每玩家只弹 1 条，FIFO
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

    # 第一刻只消费 1 条（FIFO 头）。同刻先增长：9 → 10（R3.4），
    # 于是实际兵力 = min(ceil(10*1.0), 10-1) = 9。
    r1 = tick.step(state)
    assert len(r1.launched) == 1
    assert r1.launched[0].amount == 9
    assert r1.launched[0].src == cap
    assert len(state.players[0].chain) == 2

    # 第二刻消费第 2 条：起点被上一刻打空（10-9=1），且本刻是 Turn 1（上半刻）**不增长**，
    # 于是 ceil(1*0.5)=1 被 army-1=0 截断 → 0 兵力：该请求按 R4.12 作废（zero_army）。
    r2 = tick.step(state)
    assert r2.half == 0
    assert r2.launched == []
    assert len(state.players[0].chain) == 1
    assert [d.reason for d in r2.dropped_for(0)] == ["zero_army"]

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
# R6.7 —— 每个请求各自记录入队时的比例
# ---------------------------------------------------------------------------


def test_each_request_keeps_its_own_ratio_r6_7() -> None:
    state = make_state(size=5, players=(0, 1))
    cap = state.players[0].capital
    assert cap is not None
    enqueue_move(state, 0, cap, Dir.RIGHT, 0.3)
    enqueue_move(state, 0, cap, Dir.RIGHT, 0.7)
    ratios = [req.ratio for req in state.players[0].chain]
    assert ratios == [0.3, 0.7]
    assert all(isinstance(req, MoveRequest) for req in state.players[0].chain)


def test_ratio_is_clamped_into_open_unit_interval_r6_6() -> None:
    state = make_state(size=5, players=(0, 1))
    cap = state.players[0].capital
    assert cap is not None
    enqueue_move(state, 0, cap, Dir.RIGHT, 5.0)
    enqueue_move(state, 0, cap, Dir.RIGHT, -2.0)
    enqueue_move(state, 0, cap, Dir.RIGHT, 0.0)
    ratios = [req.ratio for req in state.players[0].chain]
    assert ratios[0] == 1.0
    assert all(0.0 < r <= 1.0 for r in ratios)


def test_ratio_quantized_to_ten_percent_steps_r6_6() -> None:
    """R6.6：比例只取 10% 的整数倍，真人与 AI 共用同一集合 `RATIOS`。"""
    assert RATIOS == (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
    cases = [(0.37, 0.4), (0.44, 0.4), (0.45, 0.5), (0.95, 1.0), (0.04, 0.1), (7.0, 1.0)]
    for raw, expected in cases:
        assert quantize_ratio(raw) == expected
    state = make_state(size=5, players=(0, 1))
    cap = state.players[0].capital
    assert cap is not None
    enqueue_move(state, 0, cap, Dir.RIGHT, 0.37)
    enqueue_move(state, 0, cap, Dir.RIGHT, 0.44)
    assert [req.ratio for req in state.players[0].chain] == [0.4, 0.4]
    assert all(req.ratio in RATIOS for req in state.players[0].chain)


# ---------------------------------------------------------------------------
# R4.1 / R4.9 与 PROTOCOL §5 —— 校验与错误码
# ---------------------------------------------------------------------------


def test_enqueue_accepts_any_src_ownership_r4_11() -> None:
    """R4.11 预调兵：入队**不校验**起点归属，任意地块都能入队。"""
    state = make_state(size=5, players=(0, 1))
    neutral = state.index(2, 2)
    other = state.players[1].capital  # 别人的首都（5x5 的右下角）
    enemy_owned = state.step_index(other, Dir.UP)
    assert enemy_owned is not None
    state.tiles[enemy_owned].owner = 1

    # 4 个不同起点的请求都该入队成功（中性 / 敌方王城 / 敌方地块 / 己方王城）
    enqueue_move(state, 0, neutral, Dir.RIGHT, 1.0)
    enqueue_move(state, 0, other, Dir.UP, 1.0)
    enqueue_move(state, 0, enemy_owned, Dir.LEFT, 1.0)
    enqueue_move(state, 0, state.players[0].capital, Dir.RIGHT, 1.0)
    assert len(state.players[0].chain) == 4
    # 任何时刻都不产生 not_owned
    assert all(req.src in range(state.tile_count) for req in state.players[0].chain)


def test_enqueue_accepts_chain_target_as_src_r4_11() -> None:
    """R4.11：起点可以是链中任意请求的**目标**地块（预调兵）。"""
    state = make_state(size=5, players=(0, 1))
    cap = state.players[0].capital
    d1 = state.step_index(cap, Dir.RIGHT)
    d2 = state.step_index(d1, Dir.RIGHT)
    assert d1 is not None and d2 is not None
    # 第一条：cap → d1（d1 目前不归自己，仍可入队）
    enqueue_move(state, 0, cap, Dir.RIGHT, 1.0)
    # 第二条：以 d1 为起点（d1 还没被占，但它是上一条的目标 → 合法预调兵）
    enqueue_move(state, 0, d1, Dir.RIGHT, 1.0)
    assert [r.src for r in state.players[0].chain] == [cap, d1]


def test_enqueue_rejects_out_of_board_src_bad_frame_r4_1() -> None:
    """起点越界是帧错误（bad_frame），不是归属错误。"""
    state = make_state(size=5, players=(0, 1))
    for bad in (-1, state.tile_count, 999):
        with pytest.raises(CommandError) as exc:
            enqueue_move(state, 0, bad, Dir.RIGHT, 1.0)
        assert exc.value.code == "bad_frame"


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


def test_enqueue_rejects_dead_or_surrendering_player_r5_5() -> None:
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
    assert popped.dir is Dir.DOWN and popped.ratio == 0.8  # 0.75 就近量化到 10% 整数倍
    assert len(state.players[0].chain) == 1
    _ = pop_chain(state, 0)
    assert pop_chain(state, 0) is None
    assert pop_chain(state, 99) is None


# ---------------------------------------------------------------------------
# R4.2 / R4.12 —— 调兵量 <= 0 的请求作废
# ---------------------------------------------------------------------------


def test_zero_army_request_is_dropped_r4_2_r4_12() -> None:
    """R4.2 + R4.12：兵力 1 的地块调兵算出 0，请求**作废**并下发 ``zero_army``。

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
    assert state.players[0].chain == []  # 请求被弹出并作废
    assert state.tiles[cap].army == 1  # 未被扣减
    # R4.12：作废必须记录原因
    dropped = report.dropped_for(0)
    assert len(dropped) == 1
    assert dropped[0].src == cap
    assert dropped[0].reason == "zero_army"
    assert dropped[0].cascade == 0


# ---------------------------------------------------------------------------
# R4.11 / R7.2 —— 预调兵：起点可以是链中上一条请求的目标
# ---------------------------------------------------------------------------


def test_premove_chain_captures_then_continues_r4_11() -> None:
    """R4.11：``cap→b`` 与 ``b→c`` 可同时入队；b 尚不归己方也能预调兵。"""
    state = make_state(size=5, players=(0, 1))
    cap = state.players[0].capital
    assert cap is not None
    b = state.step_index(cap, Dir.RIGHT)
    c = state.step_index(b, Dir.RIGHT)
    assert b is not None and c is not None
    assert state.tiles[b].owner is None  # b 开局中立
    state.tiles[cap].army = 10

    enqueue_move(state, 0, cap, Dir.RIGHT, 1.0)  # cap → b
    enqueue_move(state, 0, b, Dir.RIGHT, 1.0)  # b → c（预调兵，b 还不归自己）
    assert [r.src for r in state.players[0].chain] == [cap, b]

    # 第 1 刻（Turn 0.，增长 +1）：cap 11 → 发出 10 攻占 b
    r1 = tick.step(state)
    assert r1.launched and r1.launched[0].dst == b
    assert state.tiles[b].owner == 0
    assert state.tiles[b].army == 10

    # 第 2 刻（Turn 1，不增长）：b 已归自己 → 预调兵请求合法发出
    r2 = tick.step(state)
    assert r2.dropped_for(0) == []
    assert r2.launched and r2.launched[0].src == b and r2.launched[0].dst == c
    assert state.tiles[c].owner == 0


def test_premove_src_lost_is_dropped_r4_12() -> None:
    """R4.12①：起点已不归该玩家控制 ⇒ 作废，``reason="src_lost"``。"""
    state = make_state(size=5, players=(0, 1))
    enemy_cap = state.players[1].capital
    assert enemy_cap is not None
    state.tiles[enemy_cap].army = 10
    enqueue_move(state, 0, enemy_cap, Dir.UP, 1.0)  # 入队合法（R4.11）

    report = tick.step(state)
    assert report.launched == []
    dropped = report.dropped_for(0)
    assert len(dropped) == 1
    assert dropped[0].src == enemy_cap
    assert dropped[0].reason == "src_lost"
    assert state.players[0].chain == []  # 已被弹出
    # 未扣减：tick=1 是 Turn 0.，王城按 R3.4 增长 1 → 10 + 1 = 11（说明没被调走）
    assert state.tiles[enemy_cap].army == 11


def test_drop_stops_popping_more_requests_this_tick_r4_12() -> None:
    """R4.12②：本 Tick 作废后**不再**为该玩家弹下一条（链里剩下的原样保留）。"""
    state = make_state(size=5, players=(0, 1))
    cap = state.players[0].capital
    assert cap is not None
    enemy_cap = state.players[1].capital
    assert enemy_cap is not None
    state.tiles[cap].army = 10
    enqueue_move(state, 0, enemy_cap, Dir.UP, 1.0)  # 会被作废（src_lost）
    enqueue_move(state, 0, cap, Dir.RIGHT, 1.0)  # 本刻不该被处理

    report = tick.step(state)
    assert [d.reason for d in report.dropped_for(0)] == ["src_lost"]
    assert len(state.players[0].chain) == 1
    assert state.players[0].chain[0].src == cap  # 未被消费


# ---------------------------------------------------------------------------
# R4.12③ —— 级联作废（含环形链不死循环）
# ---------------------------------------------------------------------------


def _neighbour_chain(state, start: int, dirs) -> list[int]:
    """沿 ``dirs`` 依次走出的地块序列（首元素为 ``start``）。"""
    seq = [start]
    cur = start
    for d in dirs:
        cur = state.step_index(cur, d)
        assert cur is not None
        seq.append(cur)
    return seq


def test_cascade_drops_downstream_requests_r4_12() -> None:
    """R4.12③：起点等于作废请求目标的请求，连带其下游一并作废。"""
    state = make_state(size=5, players=(0, 1))
    enemy_cap = state.players[1].capital
    assert enemy_cap is not None
    state.tiles[enemy_cap].army = 10
    e, x, y, z = _neighbour_chain(state, enemy_cap, [Dir.UP, Dir.UP, Dir.UP])
    enqueue_move(state, 0, e, Dir.UP, 1.0)  # e → x（src 是敌方：会被作废）
    enqueue_move(state, 0, x, Dir.UP, 1.0)  # x → y（下游，级联作废）
    enqueue_move(state, 0, y, Dir.UP, 1.0)  # y → z（再下游，级联作废）
    assert len(state.players[0].chain) == 3

    report = tick.step(state)
    dropped = report.dropped_for(0)
    assert len(dropped) == 1  # 只记录"源头"那一条
    assert dropped[0].src == e
    assert dropped[0].reason == "src_lost"
    assert dropped[0].cascade == 2  # 级联掉了 x→y 与 y→z
    assert state.players[0].chain == []


def test_cascade_survives_ring_chain_r4_12() -> None:
    """R4.11 允许环形链；级联必须用 visited 收敛，不能死循环。"""
    state = make_state(size=5, players=(0, 1))
    enemy_cap = state.players[1].capital
    assert enemy_cap is not None
    state.tiles[enemy_cap].army = 10
    e, x, y, _z = _neighbour_chain(state, enemy_cap, [Dir.UP, Dir.UP, Dir.UP])
    enqueue_move(state, 0, e, Dir.UP, 1.0)  # e → x（作废源）
    enqueue_move(state, 0, x, Dir.UP, 1.0)  # x → y
    enqueue_move(state, 0, y, Dir.DOWN, 1.0)  # y → x（成环！x→y→x）
    assert len(state.players[0].chain) == 3

    report = tick.step(state)  # 有界：若 visited 写错这里会挂死
    dropped = report.dropped_for(0)
    assert len(dropped) == 1
    assert dropped[0].reason == "src_lost"
    assert dropped[0].cascade == 2
    assert state.players[0].chain == []


def test_no_cascade_when_nothing_downstream_r4_12() -> None:
    """无下游时 ``cascade == 0``，且链里不相关的请求保留。"""
    state = make_state(size=5, players=(0, 1))
    cap = state.players[0].capital
    assert cap is not None
    state.tiles[cap].army = 1  # 1 兵 → 调不动（R4.2）
    state.tick = 1  # 下一次落在 turn 1 half 0，不增长
    other = state.step_index(cap, Dir.DOWN)
    assert other is not None
    enqueue_move(state, 0, cap, Dir.RIGHT, 1.0)  # 会被 zero_army 作废
    enqueue_move(state, 0, other, Dir.DOWN, 1.0)  # 与作废请求无上下游关系

    report = tick.step(state)
    dropped = report.dropped_for(0)
    assert [d.reason for d in dropped] == ["zero_army"]
    assert dropped[0].cascade == 0
    assert len(state.players[0].chain) == 1  # 无关请求保留



def test_dropped_sorted_by_player_and_src_r7_3() -> None:
    """R7.3：``report.dropped`` 按 ``(player_id, src)`` 升序，与插入顺序无关。"""
    state = make_state(size=5, players=(0, 1, 2, 3))
    # 玩家 0 与 2 各排一条必然作废的请求（起点为别人的首都）
    for pid in (2, 0):
        victim = state.players[(pid + 1) % 4].capital
        assert victim is not None
        state.tiles[victim].army = 5
        enqueue_move(state, pid, victim, Dir.UP, 1.0)

    report = tick.step(state)
    keys = [(d.player_id, d.src) for d in report.dropped]
    assert keys == sorted(keys)
    assert [d.player_id for d in report.dropped] == [0, 2]
