"""规则原语：增长（R3.4）、攻占/继承（R4.4 / R5.2）、停止抵抗与投降（R5.3 / R5.5 / C-08）。

职责：把"单条规则"落成可组合的函数，事件（Event）作为返回值供 net/回放层广播。
契约见 docs/INTERFACES.md §1.5。
本模块无 I/O / 时间 / 随机依赖（R7.3）；所有遍历按 ``(owner_id, tile_index)`` 升序。

地形判定一律走 :class:`state.Tile` 的谓词（``is_city_like`` / ``is_mountain`` /
``is_plain``），不要在模块里写 ``Terrain`` 成员比较 —— 保持单一判定入口。
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import config
from .state import GameState, Terrain, Tile, turn_of

# ---------------------------------------------------------------------------
# 事件类型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GrowthEvent:
    """一块地按 R3.4 增长 +N 的记录。"""

    tile: int
    owner: int | None
    terrain: int
    amount: int
    before: int
    after: int


@dataclass(frozen=True)
class CaptureEvent:
    """一次控制权转移（R4.4 / R4.6 / R5.2）。

    - ``tile``：目标地块。
    - ``new_owner``：结算后控制者（``None`` = 转中立，见 C-09 投降期满）。
    - ``previous_owner``：结算前控制者。
    - ``amount``：结算后的地块兵力。
    - ``reason``：``"arrive"``（到达累加/攻占）/ ``"inherit"``（王城继承 R5.2）/
      ``"neutral"``（投降期满或出局转中立 C-09）。
    - ``captured_capital``：本次是否攻占了他人王城（触发 R5.2 全继承）。
    """

    tile: int
    new_owner: int | None
    previous_owner: int | None
    amount: int
    reason: str = "arrive"
    captured_capital: bool = False


@dataclass(frozen=True)
class EliminationEvent:
    """玩家出局/投降期满的记录（R5.3 / R5.5 / C-08）。"""

    player: int
    reason: str  # "no_land" | "surrender"
    turn: int
    half: int


@dataclass(frozen=True)
class SurrenderEvent:
    """玩家进入投降过渡期的记录（R5.5）。"""

    player: int
    ticks_left: int
    turn: int
    half: int


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _mark_city_from_capital(tile: Tile) -> None:
    """把王城转成"首都转化型城市"（C-09 / R5.4）。

    后置：``terrain = Terrain.CITY``、``city_from_capital = True``；兵力与控制者不动。
    """
    tile.terrain = Terrain.CITY
    tile.city_from_capital = True


# ---------------------------------------------------------------------------
# R3.4 / R3.7 / C-01 增长
# ---------------------------------------------------------------------------


def apply_growth(state: GameState) -> list[GrowthEvent]:
    """执行一次兵力增长（INTERFACES §1.5，R3.4 / R3.7 / C-01）。

    前置：**只应在 ``half == 1``（``Turn N.``）时调用**（R3.7 / C-01）；本函数自检，
          ``half == 0`` 时直接返回空列表，不做任何修改。
    规则：
      * 首都与城市（含"首都转化型城市"）``+1``（R3.4；C-01 每整回合一次）；
      * 平原：``(turn + 1) % 25 == 0`` 时 ``+1`` —— 即 ``turn ∈ {24, 49, ...}``，
        对应 ``Turn 25.``、``Turn 50.`` …（R3.4 / C-01）；
      * **中立地块不增长**（R3.4 的"被非中立势力控制时"）；
      * 山地不增长。
    后置：返回按 ``tile index`` 升序的 :class:`GrowthEvent` 列表；地块兵力就地修改。
    抛出：无。
    """
    _turn, half = turn_of(state.tick)
    if half != 1:
        return []

    turn = _turn
    plain_boost = (turn + 1) % config.PLAIN_GROWTH_PERIOD == 0

    events: list[GrowthEvent] = []
    # R7.3：按地块 index 升序遍历。
    for idx in range(state.tile_count):
        tile = state.tiles[idx]
        if tile.owner is None or tile.army < 0:
            continue  # R3.4：只有被非中立势力控制的地块才增长
        amount = 0
        if tile.is_city_like():
            amount = config.CITY_GROWTH_PER_TURN
        elif tile.is_plain() and plain_boost:
            amount = config.PLAIN_GROWTH_AMOUNT
        if amount <= 0:
            continue
        before = tile.army
        tile.army = before + amount
        events.append(
            GrowthEvent(
                tile=idx,
                owner=tile.owner,
                terrain=int(tile.terrain),
                amount=amount,
                before=before,
                after=tile.army,
            )
        )
    return events


# ---------------------------------------------------------------------------
# R4.4 / R4.6 / R5.2 攻占与继承
# ---------------------------------------------------------------------------


def inherit_all(state: GameState, conqueror: int, victim: int) -> list[CaptureEvent]:
    """王城继承：victim 全部地块转给 conqueror（INTERFACES §1.5，R5.2 / C-11）。

    前置：``victim`` 存在且 ``victim != conqueror``。
    规则（R5.2 / C-11）：
      * 每块地 ``army = max(army // 2, 1)``；
      * 地形与"城市/平原身份"及增长规则**不变**（C-11）；
      * 控制者改为 ``conqueror``；victim 的 ``capital`` 置 ``None``（R5.1：已无王城）。
    后置：返回按 index 升序的 :class:`CaptureEvent`（``reason="inherit"``）。
    抛出：``KeyError`` —— ``conqueror`` / ``victim`` 不存在。
    """
    if conqueror not in state.players:
        raise KeyError(f"不存在的玩家：{conqueror}")
    if victim not in state.players:
        raise KeyError(f"不存在的玩家：{victim}")
    if conqueror == victim:
        return []

    events: list[CaptureEvent] = []
    for idx in state.owned_tiles(victim):  # 已按 index 升序（R7.3）
        tile = state.tiles[idx]
        tile.army = max(tile.army // 2, config.INHERIT_MIN_ARMY)
        tile.owner = conqueror
        events.append(
            CaptureEvent(
                tile=idx,
                new_owner=conqueror,
                previous_owner=victim,
                amount=tile.army,
                reason="inherit",
            )
        )
    state.players[victim].capital = None
    return events


def apply_capture(
    state: GameState, dst: int, owner: int, amount: int
) -> tuple[CaptureEvent, list[CaptureEvent]]:
    """把 ``amount`` 兵力的所有权转给 ``owner``（INTERFACES §1.5，R4.4 / R4.6 / R5.2）。

    前置：``0 <= dst < size*size``；``owner`` 为已存在的玩家 id。
    行为：
      * ``dst`` 原本就归 ``owner`` 或为中立 → ``army += amount``（R4.6 / C-14 累加不设上限）；
      * ``dst`` 归他人 → 控制者改为 ``owner``，``army = amount``（R4.4：到达兵力 > 0 即占领）；
      * 若 ``dst`` 是他人**王城**（``is_capital`` 且原主 ``capital == dst``）→
        先执行 :func:`inherit_all` 抢占其全部领土（R5.2），并把该王城转成
        "首都转化型城市"（C-09：新主人不继承王城身份）。
    后置：返回 ``(本块的事件, 额外事件列表)``，后者为继承产生的事件（按 index 升序）。
    抛出：``IndexError`` —— ``dst`` 越界；``KeyError`` —— ``owner`` 不存在。
    """
    if not (0 <= dst < state.tile_count):
        raise IndexError(f"地块 index 越界：{dst}")
    if owner not in state.players:
        raise KeyError(f"不存在的玩家：{owner}")

    tile = state.tiles[dst]
    previous_owner = tile.owner
    extra: list[CaptureEvent] = []
    captured_capital = False

    if previous_owner != owner and previous_owner is not None:
        victim = state.players.get(previous_owner)
        is_capital_of_victim = (
            victim is not None and tile.is_capital() and victim.capital == dst
        )
        # 先转这块地的控制权与兵力，再继承其余领土（R5.2）。
        tile.owner = owner
        tile.army = max(0, amount)
        if is_capital_of_victim:
            captured_capital = True
            extra = inherit_all(state, owner, previous_owner)
            # C-09：被攻占的王城转为"首都转化型城市"，新主人不继承王城身份。
            _mark_city_from_capital(tile)
    else:
        # 自己/中立地块：累加（R4.6 / C-14）。
        tile.army = tile.army + max(0, amount)
        tile.owner = owner

    event = CaptureEvent(
        tile=dst,
        new_owner=owner,
        previous_owner=previous_owner,
        amount=tile.army,
        reason="arrive",
        captured_capital=captured_capital,
    )
    return event, extra


# ---------------------------------------------------------------------------
# R5.3 / R5.5 / C-08 停止抵抗与投降
# ---------------------------------------------------------------------------


def transfer_to_neutral(state: GameState, player_id: int) -> list[CaptureEvent]:
    """把某玩家全部地块转中立，兵力完整保留（R5.5 / C-08 / C-09）。

    前置：``player_id`` 存在。
    后置：
      * 每块地 ``owner = None``，``army`` 不变（R5.5："兵力完整保留"）；
      * 其王城（``capital`` 指向的地块）转"首都转化型城市"（C-09 / R5.4）；
      * 返回按 index 升序的 :class:`CaptureEvent`（``reason="neutral"``）。
    """
    player = state.players.get(player_id)
    if player is None:
        return []
    events: list[CaptureEvent] = []
    for idx in state.owned_tiles(player_id):
        tile = state.tiles[idx]
        tile.owner = None
        if player.capital is not None and idx == player.capital:
            _mark_city_from_capital(tile)  # C-09
        events.append(
            CaptureEvent(
                tile=idx,
                new_owner=None,
                previous_owner=player_id,
                amount=tile.army,
                reason="neutral",
            )
        )
    player.capital = None
    return events


def tick_surrender(state: GameState) -> list[int]:
    """投降过渡期递减 1 Tick（INTERFACES §1.5，R5.5 / C-08）。

    前置：无。
    后置：所有 ``surrender_ticks_left is not None`` 的玩家减 1（下限 0）；
          返回"本刻期满（减到 0）"的玩家 id 升序列表 —— 由调用方（tick 层）
          通过 :func:`check_eliminations` 完成地块转中立。
    说明：过渡期内玩家仍照常增长、参战，只是不能操作（C-08）。
    """
    expired: list[int] = []
    for pid in sorted(state.players):  # R7.3：按 player id 升序
        player = state.players[pid]
        if player.surrender_ticks_left is None:
            continue
        left = player.surrender_ticks_left - 1
        player.surrender_ticks_left = left
        if left <= 0:
            expired.append(pid)
    return expired


def check_eliminations(state: GameState) -> list[EliminationEvent]:
    """停止抵抗检查（INTERFACES §1.5，R5.3 / R5.5 / C-08 / R7.4）。

    前置：应在每刻六个阶段的最后调用（R7.1 第 6 步）。
    行为：
      * **投降期满**（``surrender_ticks_left == 0``）：全部地块转中立、兵力完整保留，
        王城转首都转化型城市（C-09），``alive = False``；
      * **无地玩家**（``alive`` 且 ``land_count == 0``）：``alive = False``，``capital = None``（R5.3）。
    后置：返回按 ``player id`` 升序的 :class:`EliminationEvent`。
    抛出：无。
    """
    events: list[EliminationEvent] = []
    turn, half = turn_of(state.tick)

    for pid in sorted(state.players):  # R7.3
        player = state.players[pid]
        if not player.alive:
            continue
        if player.surrender_ticks_left is not None and player.surrender_ticks_left <= 0:
            transfer_to_neutral(state, pid)
            player.surrender_ticks_left = None
            player.alive = False
            player.chain.clear()  # C-08：投降瞬间已清空，此处兜底
            events.append(EliminationEvent(player=pid, reason="surrender", turn=turn, half=half))
            continue
        if state.land_count(pid) == 0:
            player.alive = False
            player.capital = None
            player.chain.clear()
            events.append(EliminationEvent(player=pid, reason="no_land", turn=turn, half=half))

    return events


def begin_surrender(state: GameState, player_id: int) -> SurrenderEvent:
    """让某玩家进入投降过渡期（R5.5 / C-08）。

    前置：``player_id`` 存在且 ``alive``。
    后置：
      * ``surrender_ticks_left = config.SURRENDER_TICKS``（40 Tick）；
      * 调兵链**立刻清空**（R5.5："调兵链在投降瞬间清空"）；
      * ``alive`` 保持 ``True`` —— 过渡期内地块仍属本人、仍增长、仍参战，只是不能操作（C-08）。
    抛出：``KeyError`` —— 玩家不存在；``ValueError`` —— 玩家已出局或已在投降过渡期。
    """
    player = state.players[player_id]
    if not player.alive:
        raise ValueError(f"玩家 {player_id} 已出局")
    if player.surrender_ticks_left is not None:
        raise ValueError(f"玩家 {player_id} 已在投降过渡期")
    player.surrender_ticks_left = config.SURRENDER_TICKS
    player.chain.clear()
    turn, half = turn_of(state.tick)
    return SurrenderEvent(player=player_id, ticks_left=config.SURRENDER_TICKS, turn=turn, half=half)


def check_game_over(state: GameState) -> int | None:
    """判断对局是否结束（R7.4 / C-12）。

    前置：无。
    后置：若 ``alive`` 且拥有地块的玩家数 ``<= 1`` —— 置 ``state.ended = True``、
          ``state.winner`` = 唯一有地玩家（0 名时 ``None``），返回该 winner；否则返回 ``None``。
    抛出：无。
    """
    return verify_game_over(state)


def verify_game_over(state: GameState) -> int | None:
    """检查并（在满足 R7.4 时）落定结束状态的唯一实现。

    与 :func:`check_game_over` 同一契约；``tick`` 层在**每刻结算最末尾**调用它，
    避免"本刻第 6 阶段之前还剩 1 名有地玩家"的中间态被误判为结束（R7.4 要求
    '只剩一名拥有地块的玩家时**立即**结束'—— 只能在六阶段全部走完后判定）。
    """
    with_land = state.players_with_land()
    if len(with_land) <= 1:
        state.ended = True
        state.winner = with_land[0] if with_land else None
        return state.winner
    return None


__all__ = [
    "GrowthEvent",
    "CaptureEvent",
    "EliminationEvent",
    "SurrenderEvent",
    "apply_growth",
    "apply_capture",
    "inherit_all",
    "transfer_to_neutral",
    "tick_surrender",
    "check_eliminations",
    "begin_surrender",
    "check_game_over",
    "verify_game_over",
]
