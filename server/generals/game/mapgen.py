"""地图生成：中心对称（180° 旋转）地图，可复现（R2 / R7.3）。

职责：只要给一个**已播种**的 ``random.Random`` 实例，就产出确定的地图。
契约见 docs/INTERFACES.md §1.2。
本模块**不自行创建 Random、不调用 random 模块级函数**（BRIEF 硬性要求），
随机性完全来自调用方注入的 ``rng``（房间号派生），因此对局结算依然无随机。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .. import config
from .state import GameState, PlayerState, Terrain, Tile

#: 对外暴露的地图尺寸常量（与 config 同源，方便 `mapgen.MIN_SIZE` 用法）。
MIN_SIZE: int = config.MIN_SIZE
MAX_SIZE: int = config.MAX_SIZE
DEFAULT_SIZE: int = config.DEFAULT_SIZE


class MapGenError(ValueError):
    """地图生成参数非法（尺寸/玩家数越界）。"""


@dataclass(frozen=True)
class _Candidate:
    """候选地块（用于分层摆放，避免依赖随机抽样的顺序）。"""

    idx: int
    score: int


def _canonical(idx: int, size: int) -> int:
    """180° 旋转对称下的规范代表元：同一条对称轨道内两格取较小者（R7.3）。"""
    return min(idx, size * size - 1 - idx)


def _build_orbits(size: int) -> list[tuple[int, int]]:
    """把全部地块切成 180° 对称轨道，返回 ``[(canonical, mirror), ...]``（按 canonical 升序）。

    中心格（``size`` 为奇数时存在）的镜像就是它自己。
    """
    total = size * size
    seen: set[int] = set()
    orbits: list[tuple[int, int]] = []
    for idx in range(total):
        if idx in seen:
            continue
        mirror = total - 1 - idx
        seen.add(idx)
        seen.add(mirror)
        orbits.append((min(idx, mirror), max(idx, mirror)))
    orbits.sort()
    return orbits


def _chebyshev(a: int, b: int, size: int) -> int:
    """两格的切比雪夫距离（8 方向步数），用于首都间距与保护圈。"""
    ar, ac = divmod(a, size)
    br, bc = divmod(b, size)
    return max(abs(ar - br), abs(ac - bc))


def _ring_distance(idx: int, size: int) -> int:
    """地块到地图边界的层数（0 = 最外圈），用于让首都靠近地图中部。"""
    row, col = divmod(idx, size)
    return min(row, col, size - 1 - row, size - 1 - col)


def _center_index(size: int) -> int:
    """地图正中心地块 index（``generate`` 中供奇数玩家的"多出来的那座首都"使用）。"""
    return (size // 2) * size + size // 2


def _place_capitals(size: int, player_count: int, rng: random.Random) -> list[int]:
    """选出 ``player_count`` 座首都的 index 列表（升序）。

    策略（BRIEF：对称放置 + 两两距离足够；lead 裁决支持任意 1..8 人）：
      1. **偶数**：180° 中心对称成对放置，返回 ``player_count`` 个位置；
      2. **奇数**：先成对放置 ``player_count - 1`` 座，剩下那座放在**地图正中心**
         （``(size//2, size//2)``）；若正中心与镜像对过近，则在中心 3×3 内沿固定顺序
         （先对角线、再正交）挑第一个满足间距的格子，保证可复现（只用传入 ``rng``）；
      3. 候选按"离边界层数降序 + index 升序"排序后，对前若干候选做一次注入式洗牌
         （种子决定），其余保持确定顺序；
      4. 所有首都两两切比雪夫距离 >= ``MAPGEN_CAPITAL_MIN_DIST``（随地图尺寸缩放）。
    抛出：:class:`MapGenError` —— 该尺寸下放不下这么多互不干扰的首都。
    """
    pairs = player_count // 2
    needs_center = player_count % 2 == 1
    # 奇数玩家时先成对放 ``player_count - 1`` 座，最后那座交给中心逻辑。
    paired_target = player_count - 1 if needs_center else player_count
    total = size * size
    min_dist = max(2, int(config.MAPGEN_CAPITAL_MIN_DIST * size / 20) or 2)
    center = _center_index(size)

    orbits = _build_orbits(size)
    candidates = [c for c in orbits if c[0] != c[1]]
    # 奇数玩家时把中心格让给"多出来的那座首都"，成对候选不得占用它。
    if needs_center:
        candidates = [c for c in candidates if center not in c]
    # 稳定的池化：先按"中部优先"排序，再对同层候选做一次注入式洗牌（种子决定）。
    candidates.sort(key=lambda c: (-_ring_distance(c[0], size), c[0]))
    pool = max(pairs * 6, 12)
    if len(candidates) > pool:
        head = candidates[:pool]
        tail = candidates[pool:]
        rng.shuffle(head)
        candidates = head + tail

    chosen: list[int] = []
    for canon, mirror in candidates:
        if len(chosen) >= paired_target:
            break
        trial = [canon, mirror]
        if any(
            _chebyshev(a, b, size) < min_dist
            for i, a in enumerate(trial)
            for b in trial[i + 1 :]
        ):
            continue
        if any(_chebyshev(a, b, size) < min_dist for a in trial for b in chosen):
            continue
        chosen.extend(trial)

    if needs_center:
        # 中心座必须与**已选成对首都及其镜像位置**都拉开距离：镜像位置虽然不一定是
        # 首都，但它会被对称地复制成同等地形，落在那里会破坏镜像一致性。
        blocked = set(chosen)
        blocked.update(total - 1 - idx for idx in chosen)
        spot = _pick_center_spot(size, sorted(blocked), min_dist, center)
        if spot is None:
            raise MapGenError(
                f"地图 {size}x{size} 的中心 3x3 内找不到与现有首都拉开 {min_dist} 格的位置"
            )
        chosen.append(spot)

    if len(chosen) < player_count:
        raise MapGenError(
            f"地图 {size}x{size} 放不下 {player_count} 座互不干扰的首都"
        )
    return sorted(chosen)


#: 中心微调的固定搜索顺序：先对角线，再正交（都在中心 3×3 内）。
_CENTER_OFFSETS: tuple[tuple[int, int], ...] = (
    (0, 0),
    (-1, -1),
    (1, 1),
    (-1, 1),
    (1, -1),
    (0, -1),
    (0, 1),
    (-1, 0),
    (1, 0),
)


def _pick_center_spot(
    size: int, blocked: list[int], min_dist: int, center: int
) -> int | None:
    """为奇数玩家的"多出来的那座首都"在中心 3×3 内选一格。

    规则（lead 裁决：允许沿对角线微调、必须可复现）：
      1. 候选固定为 :data:`_CENTER_OFFSETS` 给出的中心 3×3 顺序（先中心、再对角线、后正交）；
      2. 跳过与 ``blocked`` 重合的格子，以及**对称不动点**（``i == total-1-i``，即正中心格）；
         因为正中心格没有镜像伙伴，把它做成王城会让 180° 对称自检必然失败；
      3. 在剩下的候选里取"到 ``blocked`` 的切比雪夫距离最大"者（并列取 index 最小 → 可复现）；
         距离 >= ``min_dist`` 者优先。
    说明：中心王城附近（中心 3×3）在 :func:`generate` 的对称自检里被整体豁免，因为
          中心微调必然让该局部不再是 180° 对称（这是奇偶玩家数共存的代价，lead 已批准）。
    """
    crow, ccol = divmod(center, size)
    total = size * size
    scored: list[tuple[int, int]] = []
    for dr, dc in _CENTER_OFFSETS:
        row, col = crow + dr, ccol + dc
        if not (0 <= row < size and 0 <= col < size):
            continue
        spot = row * size + col
        if spot in blocked or spot == total - 1 - spot:
            continue  # 被占 / 对称不动点
        nearest = min((_chebyshev(spot, other, size) for other in blocked), default=size)
        scored.append((nearest, spot))
    if not scored:
        return None
    good = [spot for dist, spot in scored if dist >= min_dist]
    if good:
        return min(good)
    best = max(dist for dist, _ in scored)
    return min(spot for dist, spot in scored if dist == best)


def _place_capitals_with_center(
    size: int, player_count: int, rng: random.Random
) -> tuple[list[int], int | None]:
    """``_place_capitals`` 的包装：额外返回"奇数玩家时多出来的那座中心王城"。

    后置：第一个元素是升序的首都 index 列表（长度恰为 ``player_count``）；
          第二个元素仅为奇数玩家数时非 ``None``，即那座不在镜像对里的中心王城。
    """
    capitals = _place_capitals(size, player_count, rng)
    if player_count % 2 == 0:
        return capitals, None
    total = size * size
    extra = [c for c in capitals if (total - 1 - c) not in capitals]
    return capitals, (extra[0] if extra else None)


def generate(size: int, player_count: int, rng: random.Random) -> GameState:
    """生成对称地图（INTERFACES §1.2，R2 / R7.3）。

    前置：
      * ``config.MIN_SIZE <= size <= config.MAX_SIZE``；
      * ``1 <= player_count <= config.MAX_PLAYERS``（**任意人数，含奇数**）；
        偶数为 180° 旋转成对放置首都；奇数时多出的那座落在中心 3×3 内（lead 裁决）；
      * ``rng`` 为已播种的 ``random.Random`` 实例（本函数不新建、不调用模块级 random 函数）。
    后置：
      * 地图**中心对称**：``tiles[i]`` 与 ``tiles[size²-1-i]`` 地形相同（R7.3）；
        **例外**：奇数玩家时中心 3×3 整块豁免（中心王城是人为微调的不对称点）；
      * 恰好 ``player_count`` 名 ``alive=True`` 的玩家，各拥有 1 座 ``terrain = CAPITAL``、
        ``owner = pid``、``army = 1`` 的首都（R3.2），``players[pid].capital`` 指向它；
      * 其余城市 ``terrain = CITY``、``owner = None``、``army = rng.randint(40, 50)``（R3.3）；
      * 山地比例约 20%、城市约 4%（BRIEF）；首都周围 3 格内无山地（不被闷死）；
      * 其余地块为平原，``owner = None``、``army = 0``；
      * 返回的 ``GameState`` 的 ``tick = 0``、``ended = False``、``winner = None``；
      * 同一 ``rng`` 种子两次调用，逐格地形/兵力/归属完全相同（R7.3）。
    抛出：
      * :class:`MapGenError`（``ValueError``）—— 尺寸/玩家数越界、该尺寸放不下这么多首都；
      * ``AttributeError`` —— ``rng`` 不是 ``random.Random`` 兼容对象。
    """
    if not isinstance(size, int) or not (MIN_SIZE <= size <= MAX_SIZE):
        raise MapGenError(f"地图尺寸必须在 [{MIN_SIZE}, {MAX_SIZE}]，收到 {size!r}")
    if not isinstance(player_count, int) or not (1 <= player_count <= config.MAX_PLAYERS):
        raise MapGenError(
            f"玩家数必须在 [1, {config.MAX_PLAYERS}]，收到 {player_count!r}"
        )

    total = size * size
    state = GameState(size=size, tiles=[Tile() for _ in range(total)])

    # ---- 1. 首都 ---------------------------------------------------------
    capitals, center_cap = _place_capitals_with_center(size, player_count, rng)

    # ---- 2. 保护圈：首都周围 3 格内（切比雪夫距离 2）不得是山地 ----------
    protected: set[int] = set()
    for cap in capitals:
        cr, cc = divmod(cap, size)
        for dr in range(-config.MAPGEN_CAPITAL_CLEAR_RADIUS, config.MAPGEN_CAPITAL_CLEAR_RADIUS + 1):
            for dc in range(-config.MAPGEN_CAPITAL_CLEAR_RADIUS, config.MAPGEN_CAPITAL_CLEAR_RADIUS + 1):
                r, c = cr + dr, cc + dc
                if 0 <= r < size and 0 <= c < size:
                    protected.add(r * size + c)
    protected.update(capitals)

    # ---- 3. 对称选择山地与城市轨道（约 20% 山地 / 4% 城市）----------------
    orbits = _build_orbits(size)
    free_orbits = [
        o
        for o in orbits
        if o[0] not in protected
        and o[0] not in capitals
        and o[1] not in protected
        and o[1] not in capitals
    ]
    rng.shuffle(free_orbits)  # 种子决定地形分布

    target_mountain = int(total * config.MAPGEN_MOUNTAIN_RATIO / 2)
    target_city = max(1, int(total * config.MAPGEN_CITY_RATIO / 2))
    mountains: list[tuple[int, int]] = []
    cities: list[tuple[int, int]] = []
    for orbit in free_orbits:
        if len(mountains) < target_mountain:
            mountains.append(orbit)
        elif len(cities) < target_city:
            cities.append(orbit)

    # ---- 4. 落盘 ---------------------------------------------------------
    for idx in capitals:
        state.tiles[idx].terrain = Terrain.CAPITAL
    for canon, mirror in mountains:
        state.tiles[canon].terrain = Terrain.MOUNTAIN
        state.tiles[mirror].terrain = Terrain.MOUNTAIN
    for canon, mirror in cities:
        for idx in (canon, mirror):
            tile = state.tiles[idx]
            tile.terrain = Terrain.CITY
            tile.owner = None
            tile.army = int(
                rng.randint(config.CITY_NEUTRAL_ARMY_MIN, config.CITY_NEUTRAL_ARMY_MAX)
            )

    # ---- 5. 玩家与首都归属 -----------------------------------------------
    for pid in range(player_count):
        cap = capitals[pid]
        tile = state.tiles[cap]
        tile.owner = pid
        tile.army = config.CAPITAL_START_ARMY  # R3.2
        state.players[pid] = PlayerState(
            player_id=pid,
            name=f"Player {pid + 1}",
            color=config.COLORS[pid % len(config.COLORS)],
            alive=True,
            capital=cap,
            chain=[],
            surrender_ticks_left=None,
        )

    # ---- 6. 收尾不变量自检（构造错误应尽早暴露，而不是拖到结算） ----------
    # 奇数玩家时"多出来的那座首都"落在中心 3×3 内（lead 裁决允许中心微调），
    # 该局部必然不再 180° 对称，因此整块豁免；其余所有地块仍必须严格对称（R7.3）。
    exempt: set[int] = set()
    if center_cap is not None:
        crow, ccol = divmod(_center_index(size), size)
        for dr in range(-1, 2):
            for dc in range(-1, 2):
                row, col = crow + dr, ccol + dc
                if 0 <= row < size and 0 <= col < size:
                    exempt.add(row * size + col)
    for idx in range(total):
        mirror = total - 1 - idx
        if idx in exempt or mirror in exempt:
            continue
        if int(state.tiles[idx].terrain) != int(state.tiles[mirror].terrain):
            raise MapGenError(f"地图不是中心对称：{idx} 与 {mirror} 地形不一致（R7.3）")
    for cap in capitals:
        if not state.tiles[cap].is_capital():
            raise MapGenError(f"首都 {cap} 地形错误")

    return state


__all__ = ["generate", "MapGenError", "MIN_SIZE", "MAX_SIZE", "DEFAULT_SIZE"]
