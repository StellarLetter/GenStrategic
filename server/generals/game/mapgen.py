"""地图生成：**无对称**随机地图，可复现（R2.1–R2.7 / R7.3）。

职责：只要给一个**已播种**的 ``random.Random`` 实例，就产出确定的地图。
契约见 docs/INTERFACES.md §1.2、docs/RULES.md §2。

本模块**不自行创建 Random、不调用 random 模块级函数**（BRIEF 硬性要求），
随机性完全来自调用方注入的 ``rng``（房间号派生），因此对局结算依然无随机。

规则要点：
  * R2.3 边长由玩家数决定：``size = clamp(SIZE_BASE + SIZE_PER_PLAYER*N + rand(0..6), 10, 40)``；
  * R2.4 地图**无任何对称结构**：地形与首都位置均独立随机；
  * R2.5 任意两座首都的**曼哈顿**距离 ``>= CAPITAL_MIN_MANHATTAN``（13），
    首都可落在任意格子（含边缘与角落）；
  * R2.6 放不下就**换随机序列重试**（边长保持不变），全部失败抛 :class:`MapGenError`。
"""

from __future__ import annotations

import random

from .. import config
from .state import GameState, PlayerState, Terrain, Tile

__all__ = [
    "generate",
    "MapGenError",
    "MIN_SIZE",
    "MAX_SIZE",
    "DEFAULT_SIZE",
    "CAPITAL_MIN_MANHATTAN",
    "MAX_ATTEMPTS",
    "size_for_players",
    "manhattan",
]

#: 对外暴露的地图尺寸常量（与 config 同源，方便 `mapgen.MIN_SIZE` 用法）。
MIN_SIZE: int = config.MIN_SIZE
MAX_SIZE: int = config.MAX_SIZE
DEFAULT_SIZE: int = config.DEFAULT_SIZE
#: 首都最小曼哈顿间距（R2.5）。
CAPITAL_MIN_MANHATTAN: int = config.CAPITAL_MIN_MANHATTAN
#: 换种子重试上限（R2.6）。
MAX_ATTEMPTS: int = config.MAPGEN_MAX_ATTEMPTS


class MapGenError(ValueError):
    """地图生成失败（玩家数越界，或随机重试用尽仍摆不下首都）。"""


def manhattan(a: int, b: int, size: int) -> int:
    """两格的**曼哈顿距离**（L1，4 相邻步数）。R2.5 用它判定首都间距。

    前置：``a`` / ``b`` 为合法地块 index，``size`` 为地图边长。
    后置：返回 ``|Δrow| + |Δcol|``，与切比雪夫距离**不等价**（勿混用）。
    抛出：无。
    """
    ar, ac = divmod(a, size)
    br, bc = divmod(b, size)
    return abs(ar - br) + abs(ac - bc)


def _size_bounds() -> tuple[int, int]:
    """R2.3 clamp 的上下界。"""
    return config.MIN_SIZE, config.MAX_SIZE


def _compute_size(player_count: int, rng: random.Random) -> int:
    """按 R2.3 计算边长：``clamp(6 + 3N + rand(0..6), 10, 40)``。

    前置：``player_count`` 已校验在 ``[1, MAX_PLAYERS]``。
    后置：返回 ``[MIN_SIZE, MAX_SIZE]`` 内的整数；``rand`` 取自 ``rng``。
    抛出：无。
    """
    jitter = rng.randint(0, config.SIZE_JITTER_MAX)  # R2.3：rand(0..6) 取自地图种子
    raw = config.SIZE_BASE + config.SIZE_PER_PLAYER * player_count + jitter
    lo, hi = _size_bounds()
    return max(lo, min(hi, raw))


def size_for_players(player_count: int, rng: random.Random) -> int:
    """对外暴露的 R2.3 边长计算（会消费 ``rng`` 一次 jitter）。

    前置：``1 <= player_count <= config.MAX_PLAYERS``。
    后置：返回该玩家数下按公式算出的边长（``[MIN_SIZE, MAX_SIZE]``）。
    抛出：``MapGenError`` —— 玩家数越界。
    """
    if not isinstance(player_count, int) or not (
        1 <= player_count <= config.MAX_PLAYERS
    ):
        raise MapGenError(
            f"玩家数必须在 [1, {config.MAX_PLAYERS}]，收到 {player_count!r}"
        )
    return _compute_size(player_count, rng)


def _place_capitals(
    size: int, player_count: int, rng: random.Random
) -> list[int] | None:
    """**拒绝采样**摆放 ``player_count`` 座首都（R2.4 / R2.5）。

    算法：
      1. 从全部 ``size²`` 格中均匀随机抽格子（**含边缘与角落**，不做内部优先）；
      2. 若与已选全部首都的**曼哈顿**距离 ``>= CAPITAL_MIN_MANHATTAN`` 则接受；
      3. 抽取次数用尽仍不满 ``player_count`` 座 → 返回 ``None``（由调用方换序列重试，R2.6）。

    前置：``size`` 合法；``player_count >= 1``。
    后置：成功时返回**升序**的 ``player_count`` 个 index（两两曼哈顿距离 >= 阈值）；
          失败时返回 ``None``（不抛异常，交由 R2.6 重试）。
    抛出：无。
    """
    total = size * size
    min_dist = CAPITAL_MIN_MANHATTAN
    chosen: list[int] = []
    # 每次尝试给一个宽松但有界的抽样预算：平均命中率高，卡死也能及时换序列。
    budget = max(total * 4, player_count * 64)
    for _ in range(budget):
        if len(chosen) >= player_count:
            break
        spot = rng.randrange(total)
        if spot in chosen:
            continue
        if all(manhattan(spot, other, size) >= min_dist for other in chosen):
            chosen.append(spot)
    if len(chosen) < player_count:
        return None
    return sorted(chosen)


def _apply_terrain(
    state: GameState, capitals: set[int], rng: random.Random
) -> None:
    """按 R2.7 比例随机落山脉与中立城市（**无对称**，独立随机）。

    前置：``state.tiles`` 已全部初始化为平原；``capitals`` 为已放好的首都 index。
    后置：
      * 首都 8 邻 ``MAPGEN_CAPITAL_CLEAR_RADIUS`` 步内的格子不放山地（避免被闷死）；
      * 山地约占 ``MAPGEN_MOUNTAIN_RATIO``、城市约占 ``MAPGEN_CITY_RATIO``；
      * 城市（N2.2 "超市"）为中立、兵力 ∈ [CITY_NEUTRAL_ARMY_MIN, MAX]（R3.3）；
      * 其余格子为平原、无控制者、兵力 0（R3.2）。
    抛出：无。
    """
    total = state.tile_count
    size = state.size

    # ---- 首都保护圈：8 邻 CLEAR_RADIUS 步内禁山（R2.7）--------------------
    protected: set[int] = set()
    radius = config.MAPGEN_CAPITAL_CLEAR_RADIUS
    for cap in capitals:
        cr, cc = divmod(cap, size)
        for dr in range(-radius, radius + 1):
            for dc in range(-radius, radius + 1):
                r, c = cr + dr, cc + dc
                if 0 <= r < size and 0 <= c < size:
                    protected.add(r * size + c)
    protected.update(capitals)

    # ---- 候选池：除首都（及其保护圈对山地而言）外的全部格子 -----------------
    # 地形独立随机（R2.4）：打乱候选顺序后线性切分，保证比例稳定。
    order = [idx for idx in range(total) if idx not in capitals]
    rng.shuffle(order)  # 种子决定地形分布（R2.3 的随机来源）

    mountain_target = int(total * config.MAPGEN_MOUNTAIN_RATIO)
    city_target = max(1, int(total * config.MAPGEN_CITY_RATIO))

    cursor = 0
    mountains = 0
    while mountains < mountain_target and cursor < len(order):
        idx = order[cursor]
        cursor += 1
        if idx in protected:  # 首都保护圈内禁山
            continue
        state.tiles[idx] = Tile(terrain=Terrain.MOUNTAIN, army=0, owner=None)
        mountains += 1

    cities = 0
    while cities < city_target and cursor < len(order):
        idx = order[cursor]
        cursor += 1
        if int(state.tiles[idx].terrain) == int(Terrain.MOUNTAIN):
            continue
        army = rng.randint(config.CITY_NEUTRAL_ARMY_MIN, config.CITY_NEUTRAL_ARMY_MAX)
        state.tiles[idx] = Tile(terrain=Terrain.CITY, army=army, owner=None)
        cities += 1


def _build(player_count: int, size: int, rng: random.Random) -> GameState:
    """用给定边长尝试生成一次；首都放不下时返回 ``None``（R2.6 换序列重试）。

    前置：``size`` 合法；``player_count`` 合法。
    后置：成功返回完整 ``GameState``；失败返回 ``None``。
    抛出：无。
    """
    capitals = _place_capitals(size, player_count, rng)
    if capitals is None:
        return None

    state = GameState(size=size, tiles=[Tile() for _ in range(size * size)])
    _apply_terrain(state, set(capitals), rng)

    # ---- 玩家与首都归属（R3.2：首都兵力 1）-------------------------------
    for pid in range(player_count):
        cap = capitals[pid]
        tile = state.tiles[cap]
        tile.terrain = Terrain.CAPITAL
        tile.owner = pid
        tile.army = config.CAPITAL_START_ARMY
        tile.city_from_capital = False
        state.players[pid] = PlayerState(
            player_id=pid,
            name=f"Player {pid + 1}",
            color=config.COLORS[pid % len(config.COLORS)],
            alive=True,
            capital=cap,
            chain=[],
            surrender_ticks_left=None,
        )
    return state


def generate(
    player_count: int,
    rng: random.Random,
    *,
    size: int | None = None,
) -> GameState:
    """生成**无对称**随机地图（R2.1–R2.7 / R7.3，INTERFACES §1.2）。

    前置：
      * ``1 <= player_count <= config.MAX_PLAYERS``；
      * ``rng`` 为已播种的 ``random.Random`` 实例（本函数不新建、不调用模块级 random）；
      * ``size`` 为 ``None`` 时按 R2.3 公式计算（jitter 取自同一个 ``rng``）；
        显式传入时仅用于测试/回放，必须落在 ``[MIN_SIZE, MAX_SIZE]``。
    后置：
      * **无对称**：不存在任何镜像/旋转对称保证（R2.4）；
      * 恰好 ``player_count`` 名 ``alive=True`` 的玩家，各拥有 1 座 ``Terrain.CAPITAL``、
        ``owner = pid``、``army = CAPITAL_START_ARMY`` 的首都（R3.2），
        ``players[pid].capital`` 指向它；
      * 任意两座首都的**曼哈顿**距离 ``>= CAPITAL_MIN_MANHATTAN``（R2.5）；
      * 中立城市 ``owner = None``、``army ∈ [40, 50]``（R3.3）；山地 ``owner = None``、``army = 0``；
      * 其余格子为平原、``owner = None``、``army = 0``；
      * ``tick = 0``、``ended = False``、``winner = None``；
      * 同一 ``rng`` 种子两次调用，逐格地形/兵力/归属完全相同（R7.3）。
    抛出：
      * :class:`MapGenError` —— 玩家数越界、``size`` 越界，
        或 ``MAX_ATTEMPTS`` 次换序列重试后仍摆不下满足 R2.5 的首都；
      * ``AttributeError`` —— ``rng`` 不是 ``random.Random`` 兼容对象。
    """
    if not isinstance(player_count, int) or not (
        1 <= player_count <= config.MAX_PLAYERS
    ):
        raise MapGenError(
            f"玩家数必须在 [1, {config.MAX_PLAYERS}]，收到 {player_count!r}"
        )
    if size is None:
        size = _compute_size(player_count, rng)  # R2.3
    elif not isinstance(size, int) or not (MIN_SIZE <= size <= MAX_SIZE):
        raise MapGenError(f"地图尺寸必须在 [{MIN_SIZE}, {MAX_SIZE}]，收到 {size!r}")

    # ---- R2.6：放不下就换随机序列重试，**边长保持不变** --------------------
    for _attempt in range(MAX_ATTEMPTS):
        state = _build(player_count, size, rng)
        if state is not None:
            return state

    raise MapGenError(
        f"地图 {size}x{size} 在 {MAX_ATTEMPTS} 次随机重试后仍放不下 "
        f"{player_count} 座曼哈顿间距 >= {CAPITAL_MIN_MANHATTAN} 的首都（R2.5/R2.6）"
    )