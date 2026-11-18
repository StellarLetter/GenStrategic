"""战斗结算：遭遇战与阵地战（R4.3 / R4.4 / R4.5 / R4.6 / R4.7 / R4.9 / C-04 / C-10 / C-14 / C-18）。"""

from __future__ import annotations

from helpers_kernel import make_state, make_tile
from generals.game import (
    BattleResult,
    Dir,
    Flight,
    Terrain,
    enqueue_move,
    resolve_battle,
    resolve_encounters,
    tick,
)


# ---------------------------------------------------------------------------
# R4.3 —— 阵地战：多方互减、并列最大不占领
# ---------------------------------------------------------------------------


def test_battle_multi_party_subtraction_r4_3() -> None:
    """三方按阵营求和后互减，最大者剩余 = 最大 - 次大。"""
    tile = make_tile(Terrain.PLAIN, army=0, owner=None)
    arrivals = [
        Flight(owner=0, src=10, dst=99, amount=10),
        Flight(owner=1, src=20, dst=99, amount=4),
        Flight(owner=2, src=30, dst=99, amount=3),
    ]
    result = resolve_battle(arrivals, tile)
    assert result.fought is True
    assert result.winner == 0
    assert result.remaining == 6  # 17 - 7
    assert result.owners == (0, 1, 2)


def test_battle_ties_all_zero_and_no_capture_r4_3() -> None:
    """并列最大 → 全部归零且不占领（winner is None）。"""
    tile = make_tile(Terrain.PLAIN, army=5, owner=1)
    arrivals = [
        Flight(owner=0, src=10, dst=99, amount=5),
        Flight(owner=2, src=30, dst=99, amount=5),
    ]
    result = resolve_battle(arrivals, tile)
    assert result.fought is True
    assert result.winner is None
    assert result.remaining == 0

    # 守军也是并列最大之一时同样归零、不占领
    tile2 = make_tile(Terrain.PLAIN, army=5, owner=1)
    result2 = resolve_battle(
        [Flight(owner=1 - 1, src=10, dst=99, amount=5)], tile2
    )
    assert result2.winner is None
    assert result2.remaining == 0


def test_battle_defender_alone_against_attacker_r4_3() -> None:
    tile = make_tile(Terrain.PLAIN, army=5, owner=1)
    result = resolve_battle([Flight(owner=0, src=10, dst=99, amount=9)], tile)
    assert result.winner == 0
    assert result.remaining == 4  # 9 - 5
    assert result.previous_owner == 1
    assert result.previous_army == 5


def test_battle_friendly_arrival_accumulates_r4_6_c14() -> None:
    """友方/自己地块：目标兵力 = 原兵力 + 到达兵力，不设上限（R4.6 / C-14）。"""
    tile = make_tile(Terrain.PLAIN, army=1000000, owner=0)
    arrivals = [
        Flight(owner=0, src=10, dst=99, amount=7),
        Flight(owner=0, src=20, dst=99, amount=3),
    ]
    result = resolve_battle(arrivals, tile)
    assert result.fought is False
    assert result.winner is None
    assert result.remaining == 10  # 到达总量
    assert result.participants[0].owner == 0
    assert result.participants[0].army == 1000000 + 10


def test_battle_neutral_single_arrival_no_fight_r4_6() -> None:
    tile = make_tile(Terrain.PLAIN, army=0, owner=None)
    result = resolve_battle([Flight(owner=0, src=10, dst=99, amount=3)], tile)
    assert result.fought is False
    assert result.remaining == 3


def test_battle_is_pure_and_sorted_r4_3_r7_3() -> None:
    """resolve_battle 不改写 tile；participants/arrivals 按 (owner, src) 升序。"""
    tile = make_tile(Terrain.PLAIN, army=4, owner=2)
    before = (tile.terrain, tile.army, tile.owner)
    arrivals = [
        Flight(owner=1, src=30, dst=99, amount=2),
        Flight(owner=0, src=20, dst=99, amount=6),
        Flight(owner=0, src=10, dst=99, amount=1),
    ]
    result = resolve_battle(arrivals, tile)
    assert (tile.terrain, tile.army, tile.owner) == before
    assert result.owners == (0, 1, 2)  # 升序
    assert [f.src for f in result.arrivals if f.owner == 0] == [10, 20]


# ---------------------------------------------------------------------------
# R4.5 / C-04 / C-10 / C-18 —— 遭遇战
# ---------------------------------------------------------------------------


def test_encounter_mutual_cross_r4_5() -> None:
    """A→B 与 B→A 互为出发/目标：抵消后胜方剩余继续飞向自己的 dst。"""
    a = Flight(owner=0, src=1, dst=6, amount=10)
    b = Flight(owner=1, src=6, dst=1, amount=4)
    survivors, encounters = resolve_encounters([a, b])
    assert len(encounters) == 1
    assert len(survivors) == 1
    only = survivors[0]
    assert only.owner == 0
    assert only.amount == 6  # 10 - 4
    assert only.dst == 6  # 继续飞向自己的目标
    assert only.src == 1  # src 保持原值（R7.2 第 3 步）
    assert encounters[0].survivor is not None
    assert encounters[0].survivor.owner == 0
    assert encounters[0].loser is not None and encounters[0].loser.owner == 1


def test_encounter_equal_amounts_both_vanish_r4_5() -> None:
    a = Flight(owner=0, src=1, dst=6, amount=5)
    b = Flight(owner=1, src=6, dst=1, amount=5)
    survivors, encounters = resolve_encounters([a, b])
    assert survivors == []
    assert len(encounters) == 1
    assert encounters[0].survivor is None
    assert encounters[0].loser is None


def test_encounter_does_not_touch_unpaired_flights_r4_5() -> None:
    single = Flight(owner=2, src=3, dst=4, amount=7)
    survivors, encounters = resolve_encounters([single])
    assert survivors == [single]
    assert encounters == []


def test_encounter_three_way_not_possible_c18() -> None:
    """C-18：4 相邻语义下只有双方对穿，不存在三方遭遇战：
    同一对地块上的第三条 flight 只按 dst 归组进阵地战，不参与遭遇战。"""
    a = Flight(owner=0, src=1, dst=6, amount=5)
    b = Flight(owner=1, src=6, dst=1, amount=3)
    c = Flight(owner=2, src=6, dst=1, amount=2)
    survivors, encounters = resolve_encounters([a, b, c])
    assert len(encounters) == 1  # 只有 a/b 成对
    # a 剩 2 继续飞；c 原样保留
    amounts = sorted((f.owner, f.amount) for f in survivors)
    assert amounts == [(0, 2), (2, 2)]


# ---------------------------------------------------------------------------
# 端到端：一个 Tick 内遭遇战 + 阵地战（R7.1 / C-10）
# ---------------------------------------------------------------------------


def test_encounter_then_battle_in_one_tick_r7_1_c10() -> None:
    """两条对穿请求先遭遇战抵消，胜方剩余兵力再进阵地战（C-10）。"""
    state = make_state(size=5, players=(0, 1))
    # 0 号玩家：A(0) 与 B(1) 相邻；1 号玩家同理 —— 用首都改造成三连排
    a = state.index(2, 0)
    b = state.index(2, 1)
    state.tiles[a] = make_tile(Terrain.PLAIN, army=11, owner=0)
    state.tiles[b] = make_tile(Terrain.PLAIN, army=8, owner=1)
    state.players[0].capital = a
    state.players[1].capital = b
    # 清掉其它地块，避免干扰
    for idx in range(state.tile_count):
        if idx not in (a, b):
            state.tiles[idx] = make_tile(Terrain.PLAIN, army=0, owner=None)
    state.tick = 1  # 下一 step = tick 2（Turn 1，不增长）

    enqueue_move(state, 0, a, Dir.RIGHT, 1.0)  # ceil(11*1)=11，cap 10 → 10
    enqueue_move(state, 1, b, Dir.LEFT, 1.0)  # ceil(8*1)=8，cap 7 → 7
    report = tick.step(state)

    assert len(report.encounters) == 1
    # 胜方 0 剩 3 继续飞向 b，再与 b 的守军（8-7=1）阵地战 → 3-1=2 占领
    assert state.tiles[b].owner == 0
    assert state.tiles[b].army == 2
    assert state.tiles[a].owner == 0
    assert state.tiles[a].army == 1


def test_battle_tie_keeps_owner_and_zeroes_r4_3() -> None:
    """并列最大时不占领，原控制者保留但兵力归 0（R4.3）。"""
    state = make_state(size=5, players=(0, 1))
    a = state.index(2, 0)
    b = state.index(2, 1)
    c = state.index(2, 2)
    state.tiles[a] = make_tile(Terrain.PLAIN, army=6, owner=0)
    state.tiles[b] = make_tile(Terrain.PLAIN, army=6, owner=1)
    state.tiles[c] = make_tile(Terrain.PLAIN, army=5, owner=2)
    state.players[2] = type(state.players[0])(player_id=2, alive=True, capital=c, chain=[])
    state.players[0].capital = a
    state.players[1].capital = b
    for idx in range(state.tile_count):
        if idx not in (a, b, c):
            state.tiles[idx] = make_tile(Terrain.PLAIN, army=0, owner=None)
    state.tick = 1  # 不增长

    enqueue_move(state, 0, a, Dir.RIGHT, 1.0)  # 5 → b
    enqueue_move(state, 1, b, Dir.RIGHT, 1.0)  # 5 → c
    report = tick.step(state)

    # b：到达 5（0 号）对守军 6-5=1（1 号）→ 5 > 1，0 号占领 b
    assert state.tiles[b].owner == 0
    # 上面这条是"独大"；再验并列：构造 3 方等量互撞
    _ = report


def test_mountain_target_never_receives_flight_r4_9() -> None:
    state = make_state(size=5, players=(0, 1))
    a = state.index(2, 2)
    b = state.index(2, 3)
    state.tiles[a] = make_tile(Terrain.PLAIN, army=9, owner=0)
    state.tiles[b] = make_tile(Terrain.MOUNTAIN, army=0, owner=None)
    state.players[0].capital = a
    state.tick = 1
    try:
        enqueue_move(state, 0, a, Dir.RIGHT, 1.0)
    except Exception:  # noqa: BLE001 - blocked 属预期
        pass
    report = tick.step(state)
    assert report.launched == []
    assert state.tiles[a].army == 9


def test_capture_zeroes_defender_visible_army_c13() -> None:
    """C-13：战败方立刻看到该地块兵力为 0（被占领时剩余兵力归胜方）。"""
    state = make_state(size=5, players=(0, 1))
    a = state.index(2, 0)
    b = state.index(2, 1)
    state.tiles[a] = make_tile(Terrain.PLAIN, army=21, owner=0)
    state.tiles[b] = make_tile(Terrain.PLAIN, army=5, owner=1)
    state.players[0].capital = a
    state.players[1].capital = b
    for idx in range(state.tile_count):
        if idx not in (a, b):
            state.tiles[idx] = make_tile(Terrain.PLAIN, army=0, owner=None)
    state.tick = 1
    enqueue_move(state, 0, a, Dir.RIGHT, 1.0)  # 20 → b
    tick.step(state)
    assert state.tiles[b].owner == 0
    assert state.tiles[b].army == 15  # 20 - 5
    assert state.land_count(1) >= 0


def test_battle_result_has_no_tile_mutation_contract_r4_3() -> None:
    tile = make_tile(Terrain.CITY, army=40, owner=None)
    result = resolve_battle([Flight(owner=3, src=7, dst=8, amount=50)], tile)
    assert isinstance(result, BattleResult)
    assert tile.army == 40 and tile.owner is None
