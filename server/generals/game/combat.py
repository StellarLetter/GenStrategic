"""战斗结算纯函数：遭遇战（R4.5 / R7.2）与阵地战（R4.3 / R4.4）。

职责：只做"给定兵力/守军 → 谁赢、剩多少"的纯计算，不读写 ``GameState``。
契约见 docs/INTERFACES.md §1.4。
本模块无 I/O / 时间 / 随机依赖（R7.3）；所有遍历与聚合按 ``(owner_id, tile_index)`` 排序。
"""

from __future__ import annotations

from dataclasses import dataclass

from .state import Tile


@dataclass(frozen=True)
class Flight:
    """飞行中的兵力（R7.2 第 2 步产物）。

    - ``owner``：势力 player_id。
    - ``src``：出发地块 index（**保持出发时的原值**，即便遭遇战后继续飞向目标也不改写）。
    - ``dst``：目标地块 index。
    - ``amount``：剩余兵力，``>= 1``（0 兵力请求在发起阶段就被丢弃，见 R4.2）。
    """

    owner: int
    src: int
    dst: int
    amount: int

    def sort_key(self) -> tuple[int, int, int]:
        """确定性排序键 ``(owner_id, src, dst)``（R7.3）。"""
        return (self.owner, self.src, self.dst)


@dataclass(frozen=True)
class EncounterResult:
    """一次遭遇战（R4.5）的交锋记录。

    ``a`` / ``b`` 为两条**互相穿越**的 flight（``a.src == b.dst`` 且 ``a.dst == b.src``）。
    - ``survivor``：胜方 flight（已扣除败方兵力）；若双方持平则为 ``None``。
    - ``loser``：败方 flight；持平为 ``None``。
    - ``tile_a`` / ``tile_b``：两个"途经空间"对应的地块（即 a.src/b.dst 与 a.dst/b.src）。
    """

    tile_a: int
    tile_b: int
    a: Flight
    b: Flight
    survivor: Flight | None
    loser: Flight | None

    @property
    def involved_tiles(self) -> tuple[int, int]:
        """遭遇战涉及的两个地块，按 index 升序（R7.3）。"""
        return (min(self.tile_a, self.tile_b), max(self.tile_a, self.tile_b))


@dataclass(frozen=True)
class BattleParticipant:
    """阵地战中的一方（按 owner 聚合后）。"""

    owner: int
    army: int


@dataclass(frozen=True)
class BattleResult:
    """阵地战结算结果（R4.3 / R4.4）。**不直接改写 tile**。

    - ``tile``：目标地块 index；``-1`` 表示这是遭遇战记录（不来自阵地战）。
    - ``participants``：参战各方（按 ``owner`` 升序），含守军与控制者；
      若只有 1 个 owner（无战斗，R4.6 累加）则只含该 owner。
    - ``winner``：胜方 owner；并列最大或单方累加时为 ``None``。
    - ``remaining``：胜方剩余兵力（``max - second``）；并列时为 0；累加时为到达总量。
    - ``previous_owner``：结算前该地块的控制者（``None`` = 中立）。
    - ``previous_army``：结算前该地块的兵力。
    - ``fought``：是否真的发生了战斗（False = 单方累加/空结算）。
    - ``arrivals``：本刻到达该地块的 flight（按 ``(owner, src)`` 排序）。
    """

    tile: int
    participants: tuple[BattleParticipant, ...] = ()
    winner: int | None = None
    remaining: int = 0
    previous_owner: int | None = None
    previous_army: int = 0
    fought: bool = False
    arrivals: tuple[Flight, ...] = ()

    @property
    def owners(self) -> tuple[int, ...]:
        """参战 owner 列表（升序）。"""
        return tuple(p.owner for p in self.participants)


def _pair_flights(flights: list[Flight]) -> tuple[list[tuple[Flight, Flight]], list[Flight]]:
    """把 flights 分成"成对（a→b 与 b→a）"与"落单"两组（R4.5）。

    配对规则：按 ``(owner_id, src, dst)`` 排序后贪心匹配——对每条未消费的 flight ``f``，
    找一条 ``dst == f.src and src == f.dst`` 的未消费 flight ``g``（同 owner 或不同 owner
    均可，规则字面只说"互为出发地和目标"），取排序最靠前者配对。
    后置：落单列表保持排序顺序。
    """
    remaining = sorted(flights, key=Flight.sort_key)
    used: list[bool] = [False] * len(remaining)
    pairs: list[tuple[Flight, Flight]] = []
    for i, f in enumerate(remaining):
        if used[i]:
            continue
        for j in range(i + 1, len(remaining)):
            if used[j]:
                continue
            g = remaining[j]
            if g.src == f.dst and g.dst == f.src:
                used[i] = used[j] = True
                pairs.append((f, g))
                break
    singles = [f for i, f in enumerate(remaining) if not used[i]]
    return pairs, singles


def resolve_encounters(
    flights: list[Flight],
) -> tuple[list[Flight], list[EncounterResult]]:
    """结算遭遇战（R4.5 / R7.2）。

    前置：``flights`` 为本刻第 2 阶段产出的全部飞行兵力。
    后置：返回 ``(继续飞行的 flights, 交锋记录)``——
      * 互为出发↔目标的成对兵力当场抵消（"途经空间"抵消）；
      * 胜方剩余 ``amount_a - amount_b`` 继续飞向**自己的 dst**，``src`` 保持原值（R7.2 第 3 步）；
      * 平局双方同时消失；
      * 未配对的 flight 原样保留；
      * 返回值按 ``(owner_id, tile_index)`` 排序（R7.3），输入列表不被修改。
    """
    pairs, singles = _pair_flights(list(flights))

    survivors: list[Flight] = list(singles)
    results: list[EncounterResult] = []

    for f, g in pairs:
        # f 与 g 互为出发↔目标。先比兵力，胜者扣除败者后继续飞向自己的 dst。
        if f.amount > g.amount:
            winner: Flight | None = f
            loser: Flight | None = g
            rest = f.amount - g.amount
        elif g.amount > f.amount:
            winner, loser = g, f
            rest = g.amount - f.amount
        else:
            winner = loser = None
            rest = 0
        if winner is not None and rest > 0:
            survivors.append(
                Flight(owner=winner.owner, src=winner.src, dst=winner.dst, amount=rest)
            )
        results.append(
            EncounterResult(
                tile_a=f.src,
                tile_b=f.dst,
                a=f,
                b=g,
                survivor=(
                    Flight(
                        owner=winner.owner,
                        src=winner.src,
                        dst=winner.dst,
                        amount=rest,
                    )
                    if winner is not None and rest > 0
                    else None
                ),
                loser=loser,
            )
        )

    survivors.sort(key=lambda f: (f.owner, f.dst, f.src, f.amount))
    results.sort(key=lambda r: (r.involved_tiles, r.a.owner, r.b.owner))
    return survivors, results


def resolve_battle(arrivals: list[Flight], tile: Tile, tile_index: int = -1) -> BattleResult:
    """结算某个目标地块的阵地战（R4.3 / R4.4 / R4.6）。

    前置：``arrivals`` 为本刻到达该地块的全部 flight（可为空）。
    计算：
      1. 按 ``owner`` 求和（到达各方 + 守军：``tile.owner`` 的 ``tile.army``），
         得到 ``totals: dict[owner, army]``；
      2. 若参与 owner 数 ``<= 1`` → **无战斗**：``winner=None``，
         ``remaining`` 为到达总量（R4.6：友方/自己累加，不设上限）；
      3. 否则取最大与次大：独大者 ``remaining = max - second``、``winner = 该 owner``；
         **并列最大 → remaining = 0、winner = None（不占领）**（R4.3）。
    后置：返回 :class:`BattleResult`，**不修改 ``tile``**；
          ``participants`` 与 ``arrivals`` 均按 ``(owner_id, tile_index)`` 升序（R7.3）。
    抛出：``ValueError`` —— ``arrivals`` 中存在非正兵力（R4.2 保证不会出现）。
    """
    ordered = sorted(arrivals, key=lambda f: (f.owner, f.src, f.dst))
    for f in ordered:
        if f.amount <= 0:
            raise ValueError(f"到达兵力必须为正：{f}")

    totals: dict[int, int] = {}
    for f in ordered:
        totals[f.owner] = totals.get(f.owner, 0) + f.amount
    if tile.owner is not None and tile.army > 0:
        totals[tile.owner] = totals.get(tile.owner, 0) + tile.army

    participants = tuple(
        BattleParticipant(owner=owner, army=totals[owner]) for owner in sorted(totals)
    )

    if len(totals) <= 1:
        # 无战斗：友方/自己地块累加（R4.6）。
        total_arrivals = sum(f.amount for f in ordered)
        return BattleResult(
            tile=tile_index,
            participants=participants,
            winner=None,
            remaining=total_arrivals,
            previous_owner=tile.owner,
            previous_army=tile.army,
            fought=False,
            arrivals=tuple(ordered),
        )

    # 找最大与次大：按 (兵力降序, owner 升序) 排序，保证并列判定与平局顺序确定（R7.3）。
    ranked = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    top_owner, top_army = ranked[0]
    second_army = ranked[1][1]
    if top_army == second_army:
        # 并列最大：全部归零且不占领（R4.3）。
        return BattleResult(
            tile=tile_index,
            participants=participants,
            winner=None,
            remaining=0,
            previous_owner=tile.owner,
            previous_army=tile.army,
            fought=True,
            arrivals=tuple(ordered),
        )
    return BattleResult(
        tile=tile_index,
        participants=participants,
        winner=top_owner,
        remaining=top_army - second_army,
        previous_owner=tile.owner,
        previous_army=tile.army,
        fought=True,
        arrivals=tuple(ordered),
    )


def group_arrivals_by_dst(flights: list[Flight]) -> dict[int, list[Flight]]:
    """把 flight 按 dst 分组；每组内部按 ``(owner_id, src)`` 排序（R7.3）。

    后置：返回普通 dict，但键按 dst 升序插入，调用方遍历时可保持确定性。
    """
    buckets: dict[int, list[Flight]] = {}
    for f in sorted(flights, key=lambda x: (x.dst, x.owner, x.src)):
        buckets.setdefault(f.dst, []).append(f)
    return buckets


__all__ = [
    "Flight",
    "EncounterResult",
    "BattleParticipant",
    "BattleResult",
    "resolve_encounters",
    "resolve_battle",
    "group_arrivals_by_dst",
]
