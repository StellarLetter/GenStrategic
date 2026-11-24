"""单刻结算编排：严格按 R7.1 的六阶段顺序推进一个 Tick。

职责：把 combat / rules 的原语串成一步结算，并产出可广播的 :class:`TickReport`。
契约见 docs/INTERFACES.md §1.6。
本模块无 I/O / 时间 / 随机依赖（R7.3）；所有遍历按 ``(owner_id, tile_index)`` 升序。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .commands import MoveRequest, launched_amount
from .combat import (
    NEUTRAL_OWNER,
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


@dataclass(frozen=True)
class DroppedRequest:
    """一条在发起调兵阶段**作废**的调兵请求（R4.12 / R7.2）。

    字段：
      * ``player_id``：发起该请求的玩家（链的拥有者）；
      * ``src`` / ``dir`` / ``ratio``：原请求内容（``dir`` 为协议字符串，便于直接下发）；
      * ``reason``：作废原因 —— ``"src_lost"``（起点已不归该玩家，R4.11/R4.12①）
        或 ``"zero_army"``（按 R4.2 算出调兵量 ``<= 0``，R4.12①）；
      * ``cascade``：因本请求被**级联**作废的下游请求条数（R4.12③，不含自己）。
    """

    player_id: int
    src: int
    dir: str
    ratio: float
    reason: str
    cascade: int = 0

    def sort_key(self) -> tuple[int, int]:
        """确定性排序键：``(player_id, src)``（R7.3）。"""
        return (self.player_id, self.src)


#: 作废原因常量（R4.12），供 net 层映射到 PROTOCOL 事件。
DROP_SRC_LOST = "src_lost"
DROP_ZERO_ARMY = "zero_army"


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
    dropped: list[DroppedRequest] = field(default_factory=list)
    encounters: list[EncounterResult] = field(default_factory=list)
    battles: list[BattleResult] = field(default_factory=list)
    captures: list[CaptureEvent] = field(default_factory=list)
    eliminations: list[EliminationEvent] = field(default_factory=list)
    changed: set[int] = field(default_factory=set)
    ended: bool = False
    winner: int | None = None

    def dropped_for(self, player_id: int) -> list[DroppedRequest]:
        """该玩家本刻作废的请求（按 ``src`` 升序，R7.3），供下发"作废"提示。"""
        return sorted(
            (d for d in self.dropped if d.player_id == player_id),
            key=lambda d: d.sort_key(),
        )

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
# 阶段 2：发起调兵（R4.7 / R7.2）
# ---------------------------------------------------------------------------


def _pop_requests(state: GameState) -> list[tuple[int, MoveRequest]]:
    """按玩家 id 升序，每名存活玩家 FIFO 弹出 1 条请求（R4.7 / R6.3）。

    后置：返回 ``[(player_id, request), ...]``，按 ``player_id`` 升序（R7.3）。
    说明：投降过渡期内的玩家仍会消费链——但链在投降瞬间已被清空（R5.5），
          故实际不会弹出任何请求。
    """
    popped: list[tuple[int, MoveRequest]] = []
    for pid in sorted(state.players):  # R7.3
        player = state.players[pid]
        if not player.alive or not player.chain:
            continue
        popped.append((pid, player.chain.pop(0)))
    return popped


def _target_of(state: GameState, req: MoveRequest) -> int | None:
    """请求的目标地块 index；方向越界返回 ``None``（防御，R4.1）。"""
    return state.step_index(req.src, req.dir)


def _cascade_drop(state: GameState, player_id: int, doomed_dst: int) -> int:
    """级联作废链中所有「起点 == 本请求目标」的请求（R4.12③）。

    递归传递：被作废的请求若还有下游（起点 == 它的目标），继续作废。
    **链允许成环（R4.11）**，故用 visited 集合（按起点）防死循环：
    同一起点只扩散一次，环会被自然剪断。

    前置：``doomed_dst`` 是刚作废请求的目标地块 index。
    后置：链中所有以此为起点/间接下游的请求被移除；返回被级联作废的**条数**（不含自己）。
    抛出：无。
    """
    player = state.players.get(player_id)
    if player is None or not player.chain:
        return 0

    doomed_srcs: set[int] = set()
    frontier = [doomed_dst]
    while frontier:
        src_to_kill = frontier.pop()
        if src_to_kill in doomed_srcs:
            continue  # 环：已处理过，直接剪断（R4.11 链允许环）
        doomed_srcs.add(src_to_kill)
        for req in player.chain:
            if req.src != src_to_kill:
                continue
            nxt = _target_of(state, req)
            if nxt is not None and nxt not in doomed_srcs:
                frontier.append(nxt)

    if not doomed_srcs:
        return 0
    keep: list[MoveRequest] = []
    removed = 0
    for req in player.chain:
        if req.src in doomed_srcs:
            removed += 1
        else:
            keep.append(req)
    player.chain[:] = keep
    return removed


def launch_moves(
    state: GameState, dropped: list[DroppedRequest] | None = None
) -> list[Flight]:
    """阶段 2「根据请求发起调兵」——**原子阶段**（R7.2 / R4.7 / R4.11 / R4.12）。

    前置：``state`` 已进入本刻结算；``dropped`` 为可选收集列表（``None`` 时自建、不返回）。
    行为：按玩家 id 升序，各从调兵链 FIFO 弹 1 条，并按 R4.11 / R4.12 判定：
      1. 起点 ``req.src`` 已不归该玩家 → ``reason="src_lost"`` 作废；
      2. 否则按 R4.2 算 ``launched_amount``，``<= 0`` → ``reason="zero_army"`` 作废；
      3. 作废时：本条不产生任何移动、本 Tick 不再为该玩家弹下一条（R4.12②）、
         并级联作废链中起点等于本条目标的请求（递归，``cascade`` 记条数，R4.12③）；
      4. 合法请求从起点扣除兵力，生成 :class:`Flight`（``src`` 保持原值，R7.2）。
      **本阶段不更新任何占领、不结算战斗**（R7.2）。
    后置：返回按 ``(owner, dst, src)`` 升序的 flight 列表；``state.tiles[src].army`` 已扣减；
          ``dropped`` 按 ``(player_id, src)`` 升序（R7.3）。
    抛出：无（入队阶段的校验见 :func:`commands.enqueue_move`）。
    """
    if dropped is None:
        dropped = []
    flights: list[Flight] = []
    for pid, req in _pop_requests(state):
        src_tile = state.tiles[req.src]
        dst = _target_of(state, req)
        if dst is None:  # 防御：入队时已校验（R4.1）
            dropped.append(
                DroppedRequest(
                    player_id=pid,
                    src=req.src,
                    dir=req.dir.value,
                    ratio=req.ratio,
                    reason=DROP_SRC_LOST,
                )
            )
            continue
        if state.tiles[dst].is_mountain():  # 防御：入队时已 blocked（R4.9）
            dropped.append(
                DroppedRequest(
                    player_id=pid,
                    src=req.src,
                    dir=req.dir.value,
                    ratio=req.ratio,
                    reason=DROP_ZERO_ARMY,
                )
            )
            continue

        # ---- R4.11 / R4.12①：起点归属在**此时**才判定 ----------------------
        if src_tile.owner != pid:
            cascade = _cascade_drop(state, pid, dst)
            dropped.append(
                DroppedRequest(
                    player_id=pid,
                    src=req.src,
                    dir=req.dir.value,
                    ratio=req.ratio,
                    reason=DROP_SRC_LOST,
                    cascade=cascade,
                )
            )
            continue  # R4.12②：本 Tick 不再为该玩家弹下一条

        amount = launched_amount(src_tile.army, req.ratio)
        if amount <= 0:
            # R4.2：结果 <= 0 的请求不处理；R4.12 作废 + 级联。
            cascade = _cascade_drop(state, pid, dst)
            dropped.append(
                DroppedRequest(
                    player_id=pid,
                    src=req.src,
                    dir=req.dir.value,
                    ratio=req.ratio,
                    reason=DROP_ZERO_ARMY,
                    cascade=cascade,
                )
            )
            continue

        src_tile.army -= amount
        flights.append(Flight(owner=pid, src=req.src, dst=dst, amount=amount))

    flights.sort(key=lambda f: (f.owner, f.dst, f.src))
    dropped.sort(key=lambda d: d.sort_key())
    return flights


# ---------------------------------------------------------------------------
# 单刻编排
# ---------------------------------------------------------------------------


def _apply_dst_result(
    state: GameState,
    dst: int,
    result: BattleResult,
    captures: list[CaptureEvent],
    changed: set[int],
) -> None:
    """把一块地的阵地战结果落到权威状态上（阶段 4 判定 + 阶段 5 更新占领）。

    规则映射：
      * 无战斗（参战 owner ≤ 1）且到达方**就是** dst 的控制者 → 累加（R4.6）；
      * 无战斗且到达方**不是** dst 的控制者 → 到达方占领（R4.4）。这里包含两种地块：
        中立空地（``owner is None, army == 0``）与**敌方 0 兵力地块**——后者守军为 0
        故不入 :func:`resolve_battle` 的参战集合，同属"无战斗"，但控制权必须转移。
      * 并列最大 → 兵力归零、**不占领**，原控制者（或中立）保留（R4.3）。
      * 中立驻军独大 → 地块仍中立，兵力 = 剩余兵力（R3.8 / R4.3）。
      * 玩家独大 → 剩余兵力 > 0 即攻占（R4.4）；攻占他人王城触发全继承（R5.2）与
        王城转"首都转化型城市"（R5.4）。

    前置：``dst`` 是本刻有航班到达的地块；``result`` 由 :func:`resolve_battle` 对
          ``dst`` 算出，且 ``result.tile == dst``。
    后置：改写 ``state.tiles[dst]``（必要时连带 R5.2 全继承），并把变化记入
          ``captures`` / ``changed``。
    抛出：无（``apply_capture`` 的前置由本函数保证）。
    """
    dst_tile = state.tiles[dst]
    participants = result.participants

    if not result.fought:
        # --- 无战斗：只剩一个 owner 在场（R4.3 的判定前提不成立）---------------
        if result.remaining <= 0:
            return
        # 单方在场：participants 恰有 1 项，``remaining`` 即该方到达总量（R4.6）。
        sole_owner = participants[0].owner
        if sole_owner == NEUTRAL_OWNER:
            # 防御：中立驻军单独在场（无任何到达方）不改变地块。
            return
        if dst_tile.owner == sole_owner:
            # 己方/友方地块：累加（R4.6，不设上限）。
            dst_tile.army += result.remaining
            changed.add(dst)
            return
        # 到达方并非 dst 的控制者 → 到达兵力 > 0 即占领（R4.4）；
        # **绝不可**把到达兵力累加给原控制者（R4.4："占领后兵力 = 剩余兵力，不与原驻军累加"）。
        ev, extra = apply_capture(state, dst, sole_owner, result.remaining)
        captures.append(ev)
        captures.extend(extra)
        for item in extra:
            changed.add(item.tile)
        changed.add(dst)
        return

    if result.winner is None:
        # --- 并列最大：兵力归零、不占领（R4.3）--------------------------
        if dst_tile.army != 0:
            dst_tile.army = 0
        changed.add(dst)
        return

    if result.winner == NEUTRAL_OWNER:
        # --- 中立驻军守住了：不换主，兵力 = 驻军 - 到达方（R3.8 / R4.3）-----
        if dst_tile.owner is not None or dst_tile.army != result.remaining:
            dst_tile.owner = None
            dst_tile.army = result.remaining
            changed.add(dst)
        return

    # --- 独大：剩余兵力 > 0 即攻占（R4.4）-------------------------------
    prev_owner = dst_tile.owner
    prev_army = dst_tile.army
    if prev_owner == result.winner:
        # 战斗后由**原控制者**守住：兵力 = 战斗结算的剩余兵力（R4.3 winner = 最大 − 次大；
        # R4.4"占领后兵力 = 结算剩余兵力，**不与原驻军累加**"）。
        # 注意：只有"无战斗"的友军累加才用加法（R4.6）；打过的仗一律以剩余为准，
        # 否则守军会凭空多出"原驻军"那一份（曾导致守方每被打一次就白涨一截）。
        dst_tile.army = result.remaining
        captures.append(
            CaptureEvent(
                tile=dst,
                new_owner=result.winner,
                previous_owner=prev_owner,
                amount=result.remaining,
                reason="arrive",
                captured_capital=False,
            )
        )
        if prev_army != dst_tile.army:
            changed.add(dst)
        return
    ev, extra = apply_capture(state, dst, result.winner, result.remaining)
    captures.append(ev)
    captures.extend(extra)
    for item in extra:
        changed.add(item.tile)
    if prev_owner != result.winner or prev_army != dst_tile.army:
        changed.add(dst)


def _should_end(state: GameState) -> bool:
    """对局是否满足结束条件（R7.4）。

    判定：**参与过对局的玩家数 >= 2**，且 有地玩家数 ``<= 1`` 且没有别的 ``alive``
    玩家还能再抢地（即除那名有地玩家外，剩下的全是已出局玩家）。

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
    """推进 1 个 Tick，严格按 R7.1 的六阶段顺序（INTERFACES §1.6）。

    前置：``not state.ended``（已结束时直接返回"空报告"，``tick`` 不变）。
    后置：
      * ``state.tick += 1`` 在本刻**结算之前**完成：本刻结算依据的是"刚进入的这一刻"
        的 ``(turn, half)``（R1.2 / R3.7）。开局 ``tick=0`` 即 ``Turn 0``，第一次
        ``step()`` 落入 ``tick=1`` = ``Turn 0.``，因此**第一次 step 就会增长**；
      * 报告按阶段顺序给出：增长 → 发起调兵 → 遭遇战 → 阵地战 → 更新占领 → 停止抵抗；
      * ``report.dropped`` 列出本刻按 R4.12 作废的请求（含级联条数）；
      * ``report.changed`` 汇总本刻所有发生变化的地块 index；
      * ``report.ended`` / ``report.winner`` 反映 R7.4 的结束判定。
    确定性：无随机、无时间依赖；相同输入必然相同输出（R7.3）。
    抛出：无。
    """
    if state.ended:
        turn, half = turn_of(state.tick)
        return TickReport(
            tick=state.tick, turn=turn, half=half, ended=True, winner=state.winner
        )

    tile_total = len(state.tiles)  # 用于收尾断言地块总数不变（R2.1）

    # 本刻的时钟：先推进 tick，再按新 (turn, half) 结算（R1.2 / R3.7）。
    state.tick += 1
    turn, half = turn_of(state.tick)

    changed: set[int] = set()

    # ---- 阶段 1：兵力增长（R3.4 / R3.7）-----------------------------------
    growth = apply_growth(state)
    for ev in growth:
        changed.add(ev.tile)  # 兵力变化也要下发（PROTOCOL §3.3）

    # ---- 阶段 2：根据请求发起调兵（R7.2 / R4.7 / R4.11 / R4.12）------------
    dropped: list[DroppedRequest] = []
    launched = launch_moves(state, dropped)
    for f in launched:
        changed.add(f.src)  # 起点兵力已扣减

    # ---- 阶段 3：结算遭遇战（R4.5 / R7.2）--------------------
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
        _apply_dst_result(state, dst, result, captures, changed)

    # ---- 阶段 6：停止抵抗（R5.3 / R5.5）----------------------------------
    expired = tick_surrender(state)
    # 投降期满玩家在转中立之前的全部地块快照（按 index 升序，R7.3）。
    expiring_tiles: dict[int, list[int]] = {
        pid: state.owned_tiles(pid) for pid in expired
    }
    eliminations = check_eliminations(state)
    for pid in expired:
        # R5.5：期满地块全部转中立（兵力完整保留），控制者变化必须下发。
        changed.update(expiring_tiles.get(pid, []))

    # ---- 结束判定（R7.4）-------------------------------------------------
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
        dropped=dropped,
        encounters=encounters,
        battles=battles,
        captures=captures,
        eliminations=eliminations,
        changed=changed,
        ended=state.ended,
        winner=state.winner,
    )


__all__ = [
    "TickReport",
    "DroppedRequest",
    "DROP_SRC_LOST",
    "DROP_ZERO_ARMY",
    "step",
    "launch_moves",
]
