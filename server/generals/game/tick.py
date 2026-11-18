"""单刻结算编排：严格按 R7.1 / C-10 的六阶段顺序推进一个 Tick。

职责：把 combat / rules 的原语串成一步结算，并产出可广播的 :class:`TickReport`。
契约见 docs/INTERFACES.md §1.6。
本模块无 I/O / 时间 / 随机依赖（R7.3）；所有遍历按 ``(owner_id, tile_index)`` 升序。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .commands import MoveRequest, launched_amount
from .combat import (
    BattleResult,
    EncounterResult,
    Flight,
    group_arrivals_by_dst,
    resolve_battle,
    resolve_encounters,
)
from .rules import (
    CaptureEvent,
    EliminationEvent,
    GrowthEvent,
    apply_capture,
    apply_growth,
    check_eliminations,
    verify_game_over,
    tick_surrender,
)
from .state import GameState, turn_of


@dataclass
class TickReport:
    """一个 Tick 的完整结算报告（INTERFACES §1.6）。

    net 层据此生成 ``turn`` / ``tiles`` / ``leaderboard`` / ``event`` 帧；
    回放器可直接序列化本对象。
    """

    tick: int
    turn: int
    half: int
    growth: list[GrowthEvent] = field(default_factory=list)
    launched: list[Flight] = field(default_factory=list)
    encounters: list[EncounterResult] = field(default_factory=list)
    battles: list[BattleResult] = field(default_factory=list)
    captures: list[CaptureEvent] = field(default_factory=list)
    eliminations: list[EliminationEvent] = field(default_factory=list)
    changed: set[int] = field(default_factory=set)
    ended: bool = False
    winner: int | None = None

    def owners_changed(self) -> list[CaptureEvent]:
        """只挑"控制者真的变了"的攻占事件（R4.4），供 net 广播 captured 事件。"""
        return [c for c in self.captures if c.previous_owner != c.new_owner]

    def captured_capitals(self) -> list[CaptureEvent]:
        """本刻攻占他人王城的事件（R5.2 → 广播 captured 事件）。"""
        return [c for c in self.captures if c.captured_capital]

    def gained_tiles(self, player_id: int) -> list[int]:
        """该玩家本刻新获得的地块 index 升序列表（声望/统计用）。"""
        return sorted(
            {c.tile for c in self.captures if c.new_owner == player_id and c.previous_owner != player_id}
        )

    def lost_tiles(self, player_id: int) -> list[int]:
        """该玩家本刻失去的地块 index 升序列表。"""
        return sorted(
            {c.tile for c in self.captures if c.previous_owner == player_id and c.new_owner != player_id}
        )


# ---------------------------------------------------------------------------
# 阶段 2：发起调兵（R4.7 / R7.2 / C-05）
# ---------------------------------------------------------------------------


def _pop_requests(state: GameState) -> list[tuple[int, MoveRequest]]:
    """按玩家 id 升序，每名存活玩家 FIFO 弹出 1 条请求（R4.7 / C-05）。

    后置：返回 ``[(player_id, request), ...]``，按 ``(player_id, src)`` 升序（R7.3）。
    说明：投降过渡期内的玩家仍会消费链——但链在投降瞬间已被清空（R5.5 / C-08），
          故实际不会弹出任何请求。
    """
    popped: list[tuple[int, MoveRequest]] = []
    for pid in sorted(state.players):  # R7.3
        player = state.players[pid]
        if not player.alive or not player.chain:
            continue
        popped.append((pid, player.chain.pop(0)))
    return popped


def launch_moves(state: GameState) -> list[Flight]:
    """阶段 2「根据请求发起调兵」——**原子阶段**（R7.2 / R4.7 / C-05 / C-10）。

    前置：``state`` 已进入本刻结算。
    行为：按玩家 id 升序，各从调兵链 FIFO 弹 1 条；从起点扣除
          ``min(ceil(army*ratio), army-1)``（R4.2 / C-02 / C-07）并生成 :class:`Flight`。
          **本阶段不更新任何占领、不结算战斗**（R7.2）。
          起点兵量为 1 或 0 时得 ``amount == 0``：该请求被消费但不产生飞行兵力。
    后置：返回按 ``(owner_id, dst)`` 升序的 flight 列表；``state.tiles[src].army`` 已扣减。
    抛出：无（请求合法性已在 :func:`commands.enqueue_move` 阶段校验）。
    """
    flights: list[Flight] = []
    for pid, req in _pop_requests(state):
        src_tile = state.tiles[req.src]
        dst = state.step_index(req.src, req.dir)
        if dst is None:  # 防御：理论上入队时已校验（R4.1）
            continue
        if state.tiles[dst].is_mountain():  # 防御：入队时已 blocked（R4.9）
            continue
        amount = launched_amount(src_tile.army, req.ratio)
        if amount <= 0:
            # C-02：允许 0 兵力请求 —— 请求被消费，但不产生飞行兵力。
            continue
        src_tile.army -= amount
        flights.append(Flight(owner=pid, src=req.src, dst=dst, amount=amount))
    flights.sort(key=lambda f: (f.owner, f.dst, f.src))
    return flights


# ---------------------------------------------------------------------------
# 单刻编排
# ---------------------------------------------------------------------------


def _apply_dst_result(
    state: GameState,
    dst: int,
    arrivals: list[Flight],
    result: BattleResult,
    captures: list[CaptureEvent],
    changed: set[int],
) -> None:
    """把一块地的阵地战结果落到权威状态上（阶段 4 判定 + 阶段 5 更新占领）。

    规则映射：
      * 无战斗（参战 owner ≤ 1）→ 累加（R4.6 / C-14）；若 dst 中立则到达方占领（R4.4）。
      * 并列最大 → 兵力归零、**不占领**，原控制者保留（R4.3）。
      * 独大 → 剩余兵力 > 0 即攻占（R4.4）；攻占他人王城触发全继承（R5.2）与
        王城转"首都转化型城市"（C-09）。
    """
    dst_tile = state.tiles[dst]
    participants = result.participants

    if not result.fought:
        # --- 无战斗：单一 owner 的累加（R4.6 / C-14）----------------------
        if result.remaining <= 0:
            return
        if dst_tile.owner is None:
            # 中立且只有 1 方到达 → 该方占领（R4.4：到达兵力 > 0 即被到达方占领）。
            # 中立兵力不在参战集合里（owner=None），故占领后 dst 兵力 = 到达量。
            arriving_owner = arrivals[0].owner if arrivals else participants[0].owner
            ev, extra = apply_capture(state, dst, arriving_owner, result.remaining)
            captures.append(ev)
            captures.extend(extra)
            for item in extra:
                changed.add(item.tile)
            changed.add(dst)
        else:
            # 友方/自己地块：累加（R4.6 / C-14）。
            dst_tile.army += result.remaining
            changed.add(dst)
        return

    if result.winner is None:
        # --- 并列最大：兵力归零、不占领（R4.3）--------------------------
        if dst_tile.army != 0:
            dst_tile.army = 0
        changed.add(dst)
        return

    # --- 独大：剩余兵力 > 0 即攻占（R4.4）-------------------------------
    prev_owner = dst_tile.owner
    prev_army = dst_tile.army
    ev, extra = apply_capture(state, dst, result.winner, result.remaining)
    captures.append(ev)
    captures.extend(extra)
    for item in extra:
        changed.add(item.tile)
    if prev_owner != result.winner or prev_army != dst_tile.army:
        changed.add(dst)


def _should_end(state: GameState) -> bool:
    """对局是否满足结束条件（R7.4 / C-12）。

    判定：**参与过对局的玩家数 >= 2**（即房间里死了不止一人），且 有地玩家数 ``<= 1``
    且没有别的 ``alive`` 玩家还能再抢地。

    说明：单人测试局 / 单人房（``len(players) == 1``）永远不结束 —— 这类局要能一直跑
    （增长、调兵、战斗都要能验证；AI_API §2.1 的离线自对弈也依赖它）。真正的多人终局
    （R7.4：只剩一名有地玩家、其余全部出局）仍会**立即**触发。
    """
    if len(state.players) < 2:
        return False
    with_land = state.players_with_land()
    if len(with_land) > 1:
        return False
    # 除有地玩家外，只要还有 alive 玩家（哪怕是 0 块地、正在被追着打），就继续。
    held = set(with_land)
    return not [pid for pid in state.alive_players() if pid not in held]


def step(state: GameState) -> TickReport:
    """推进 1 个 Tick，严格按 R7.1 / C-10 的六阶段顺序（INTERFACES §1.6）。

    前置：``not state.ended``（已结束时直接返回"空报告"，``tick`` 不变）。
    后置：
      * ``state.tick += 1`` 在本刻**结算之前**完成：本刻结算依据的是"刚进入的这一刻"
        的 ``(turn, half)``（R1.2 / C-01）。开局 ``tick=0`` 即 ``Turn 0``，第一次
        ``step()`` 落入 ``tick=1`` = ``Turn 0.``，因此**第一次 step 就会增长**；
      * 报告按阶段顺序给出：增长 → 发起调兵 → 遭遇战 → 阵地战 → 更新占领 → 停止抵抗；
      * ``report.changed`` 汇总本刻所有发生变化的地块 index；
      * ``report.ended`` / ``report.winner`` 反映 R7.4 / C-12 的结束判定。
    确定性：无随机、无时间依赖；相同输入必然相同输出（R7.3）。
    抛出：无。
    """
    if state.ended:
        turn, half = turn_of(state.tick)
        return TickReport(
            tick=state.tick, turn=turn, half=half, ended=True, winner=state.winner
        )

    tile_total = len(state.tiles)  # 用于收尾断言地块总数不变（R2.1）

    # 本刻的时钟：先推进 tick，再按新 (turn, half) 结算（R1.2 / C-01）。
    state.tick += 1
    turn, half = turn_of(state.tick)

    changed: set[int] = set()

    # ---- 阶段 1：兵力增长（R3.4 / R3.7 / C-01）----------------------------
    growth = apply_growth(state)
    for ev in growth:
        changed.add(ev.tile)  # 兵力变化也要下发（PROTOCOL §3.3）

    # ---- 阶段 2：根据请求发起调兵（R7.2 / R4.7 / C-05）---------------------
    launched = launch_moves(state)
    for f in launched:
        changed.add(f.src)  # 起点兵力已扣减

    # ---- 阶段 3：结算遭遇战（R4.5 / C-04 / C-10 / C-18）--------------------
    survivors, encounters = resolve_encounters(launched)
    for enc in encounters:
        t1, t2 = enc.involved_tiles
        changed.add(t1)
        changed.add(t2)

    # ---- 阶段 4 + 阶段 5：阵地战判定 + 更新占领（R4.3 / R4.4 / R4.6 / R5.2）--
    battles: list[BattleResult] = []
    captures: list[CaptureEvent] = []
    arrivals_by_dst = group_arrivals_by_dst(survivors)
    for dst in sorted(arrivals_by_dst):  # R7.3：按 dst 升序
        arrivals = arrivals_by_dst[dst]
        result = resolve_battle(arrivals, state.tiles[dst], tile_index=dst)
        battles.append(result)
        _apply_dst_result(state, dst, arrivals, result, captures, changed)

    # ---- 阶段 6：停止抵抗（R5.3 / R5.5 / C-08 / C-09）---------------------
    expired = tick_surrender(state)
    # 投降期满玩家在转中立之前的全部地块快照（按 index 升序，R7.3）。
    expiring_tiles: dict[int, list[int]] = {
        pid: state.owned_tiles(pid) for pid in expired
    }
    eliminations = check_eliminations(state)
    for pid in expired:
        # C-08：期满地块全部转中立（兵力保留），控制者变化必须下发。
        changed.update(expiring_tiles.get(pid, []))

    # ---- 结束判定（R7.4 / C-12）------------------------------------------
    # 只能在六阶段**全部走完**后判定：否则"本刻开始时只剩 1 名有地玩家"的中间态会在
    # 增长之前就把对局判死（此时还可能有兵力在飞）。
    # 单人局（``len(players) < 2``）永不结束，便于离线驱动/单测（AI_API §2.1）。
    if _should_end(state):
        verify_game_over(state)

    assert len(state.tiles) == tile_total  # 地块总数不变（R2.1）
    assert all(0 <= i < state.tile_count for i in changed)

    return TickReport(
        tick=state.tick,
        turn=turn,
        half=half,
        growth=growth,
        launched=launched,
        encounters=encounters,
        battles=battles,
        captures=captures,
        eliminations=eliminations,
        changed=changed,
        ended=state.ended,
        winner=state.winner,
    )


__all__ = ["TickReport", "step", "launch_moves"]
