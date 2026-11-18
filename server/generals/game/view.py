"""视野裁剪：把权威 GameState 裁成"某个座位能看到的视图"（R3.5 / R3.6 / C-17 / R6.9）。

职责：唯一的裁剪实现，net / ai / 回放都只从这里取可见信息，保证各端一致。
契约见 docs/INTERFACES.md §1.7；地形编码见 docs/PROTOCOL.md §3.3。
本模块无 I/O / 时间 / 随机依赖（R7.3）。
"""

from __future__ import annotations

from dataclasses import dataclass

from .state import GameState, Terrain, Tile

#: PROTOCOL §3.3 中"首都转化型城市"的渲染码（R5.4）。
TERRAIN_CITY_FROM_CAPITAL: int = 4

#: 视野外折叠后的两种可见外观：平原（0）与山地（2）（C-17 / R6.9）。
_FOG_PLAIN: int = int(Terrain.PLAIN)
_FOG_MOUNTAIN: int = int(Terrain.MOUNTAIN)


@dataclass
class TileView:
    """一个地块在某座位视角下的样子（PROTOCOL §3.3 四元组）。

    - ``index``：地块 index（= 列表下标）。
    - ``army``：兵力；**视野外为 ``None``**（R3.6）。
    - ``owner``：控制者；``None`` = 中立。视野外仍给出 owner（PROTOCOL 的 ``ownerId`` 语义，
      便于客户端画"雾中的敌方领土"——R3.6 只说"不给兵量"，未说隐藏归属）。
    - ``terrain``：可见地形编码：0 平原 / 1 首都 / 2 山地 / 3 城市 / 4 首都转化型城市。
    """

    index: int
    army: int | None
    owner: int | None
    terrain: int


def visible_diff(before: set[int], after: set[int]) -> tuple[list[int], list[int]]:
    """视野集合变化 → ``(新增, 移除)``（INTERFACES §1.7，PROTOCOL §3.4 ``sight`` 帧）。

    后置：两个列表都按 index 升序（R7.3），便于客户端稳定 diff。
    """
    add = sorted(set(after) - set(before))
    remove = sorted(set(before) - set(after))
    return add, remove


def _visible_terrain(tile: Tile) -> int:
    """视野内的地形编码（R6.9：视野内按真实地形渲染）。"""
    if int(tile.terrain) == int(Terrain.CITY) and tile.city_from_capital:
        return TERRAIN_CITY_FROM_CAPITAL  # R5.4：逻辑同城市，但需要区分渲染/识别
    return int(tile.terrain)


def _fog_terrain(tile: Tile) -> int:
    """视野外的地形折叠（C-17 / R6.9）。

    - 山地、普通/中立城市 → ``2``（山地外观）；
    - 平原、首都、**首都转化型城市** → ``0``（平原外观）。
    """
    if tile.is_mountain():
        return _FOG_MOUNTAIN
    if tile.is_ordinary_city():
        return _FOG_MOUNTAIN
    return _FOG_PLAIN


def project(state: GameState, seat_player: int | None) -> list[TileView]:
    """把权威状态裁剪成某玩家视野内的地块列表（INTERFACES §1.7）。

    前置：无。``seat_player is None`` 表示观战者。
    后置：长度为 ``size * size``，按 index 升序（列表下标 == index）。

      * **观战者**（``None``）：全图真实信息——``army`` 真实，``terrain`` 为真实编码
        （0/1/2/3，首都转化型城市给 ``4``）（P-02：观战者全图可见）。
      * **视野内**（R3.5：己方地块 ∪ 其 8 相邻）：``army`` 真实；``terrain = 4``
        当且仅当地块是首都转化型城市（R5.4），否则为真实地形编码。
      * **视野外**（C-17）：``army = None``；``terrain = 2`` 若真实地形为山地或普通城市，
        否则 ``0``（PLAIN / CAPITAL / 首都转化型城市）。
      * 已出局玩家的 ``seat_player``：视野为空集，因此全部地块走"视野外"分支。
    抛出：无。
    """
    if seat_player is None:
        return [
            TileView(
                index=idx,
                army=tile.army,
                owner=tile.owner,
                terrain=_visible_terrain(tile),
            )
            for idx, tile in enumerate(state.tiles)
        ]

    visible = state.visible_tiles(seat_player)
    out: list[TileView] = []
    for idx, tile in enumerate(state.tiles):  # R7.3：按 index 升序
        if idx in visible:
            out.append(
                TileView(
                    index=idx,
                    army=tile.army,
                    owner=tile.owner,
                    terrain=_visible_terrain(tile),
                )
            )
        else:
            out.append(
                TileView(
                    index=idx,
                    army=None,
                    owner=tile.owner,
                    terrain=_fog_terrain(tile),
                )
            )
    return out


def project_map(state: GameState, seat_player: int | None) -> dict[int, TileView]:
    """同 :func:`project`，但返回 ``{index: TileView}``（便于按 index 查）。"""
    return {v.index: v for v in project(state, seat_player)}


def tile_views_to_tuples(views: list[TileView]) -> list[list[int | None]]:
    """把视图压成 PROTOCOL §3.3 的 ``[index, army, ownerId, terrain]`` 四元组列表。"""
    return [[v.index, v.army, v.owner, v.terrain] for v in views]


def diff_views(before: list[TileView], after: list[TileView]) -> list[list[int | None]]:
    """比较同座位的前后两帧视图，返回变化地块的四元组（PROTOCOL §3.3 ``tiles`` 帧）。

    前置：两帧长度相同且按 index 升序（均由 :func:`project` 产出）。
    后置：按 index 升序返回**有差异**的地块；``army`` 从 ``None`` 变为数字也算变化
          （视野重入时客户端必须拿到真实兵量）。
    """
    changed: list[list[int | None]] = []
    for old, new in zip(before, after):
        if old.army != new.army or old.owner != new.owner or old.terrain != new.terrain:
            changed.append([new.index, new.army, new.owner, new.terrain])
    return changed


__all__ = [
    "TileView",
    "TERRAIN_CITY_FROM_CAPITAL",
    "visible_diff",
    "project",
    "project_map",
    "tile_views_to_tuples",
    "diff_views",
]
