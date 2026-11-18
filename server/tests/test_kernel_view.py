"""视野与迷雾、快照投影、增量下发（R6.1..R6.9 / C-17 / R3.5 / R3.6）。"""

from __future__ import annotations

from helpers_kernel import make_state, make_tile
from generals.game import (
    TERRAIN_CITY_FROM_CAPITAL,
    Terrain,
    TileView,
    diff_views,
    project,
    project_map,
    tile_views_to_tuples,
    visible_diff,
)


def _map_with_city_from_capital() -> tuple[object, int, int]:
    state = make_state(size=5, players=(0, 1))
    plain = state.index(2, 2)
    derived = state.index(2, 3)
    state.tiles[plain] = make_tile(Terrain.PLAIN, army=3, owner=0)
    state.tiles[derived] = make_tile(
        Terrain.CITY, army=6, owner=0, city_from_capital=True
    )
    return state, plain, derived


# ---------------------------------------------------------------------------
# C-17 / R6.9 —— 迷雾：视野外 arm y 隐藏
# ---------------------------------------------------------------------------


def test_out_of_vision_army_is_hidden_c17() -> None:
    state = make_state(size=9, players=(0, 1))
    cap = state.players[0].capital
    assert cap is not None
    v0 = project(state, 0)
    for view in v0:
        assert isinstance(view, TileView)
    # 视野外的地块 army 必须为 None（C-17）
    visible = set(state.visible_tiles(0))
    for view in v0:
        if view.index not in visible:
            assert view.army is None


def test_in_vision_army_is_real_c17() -> None:
    state = make_state(size=9, players=(0, 1))
    cap = state.players[0].capital
    assert cap is not None
    state.tiles[cap].army = 17
    v0 = {view.index: view for view in project(state, 0)}
    assert v0[cap].army == 17
    assert v0[cap].owner == 0


def test_spectator_sees_full_truth_c17() -> None:
    state = make_state(size=5, players=(0, 1))
    state.tiles[state.index(2, 2)] = make_tile(Terrain.CITY, army=44, owner=None)
    views = project(state, None)
    assert all(view.army is not None for view in views)
    assert views[state.index(2, 2)].army == 44


# ---------------------------------------------------------------------------
# R3.5 / R3.6 / C-17 —— 视野外的地形折叠
# ---------------------------------------------------------------------------


def test_fog_folds_terrain_r3_6_c17() -> None:
    """视野外：山地与普通城市折叠为同一"不可通行/未知"码；平原与王城折叠为平原码。"""
    state = make_state(size=11, players=(0, 1))
    cap = state.players[0].capital
    assert cap is not None
    far = state.index(10, 10)
    state.tiles[far] = make_tile(Terrain.MOUNTAIN, army=0, owner=None)
    far_city = state.index(10, 9)
    state.tiles[far_city] = make_tile(Terrain.CITY, army=45, owner=None)
    far_plain = state.index(10, 8)
    state.tiles[far_plain] = make_tile(Terrain.PLAIN, army=2, owner=1)

    views = {view.index: view for view in project(state, 0)}
    visible = set(state.visible_tiles(0))
    assert far not in visible and far_city not in visible and far_plain not in visible
    assert views[far].terrain == views[far_city].terrain  # 山地/普通城市折叠一致
    assert views[far_plain].terrain == 0  # 平原
    assert views[far_plain].army is None


def test_city_from_capital_visible_code_r5_4() -> None:
    """视野内的"首都转化型城市"用专用地形码 4（前端要能画出来）。"""
    state, plain, derived = _map_with_city_from_capital()
    views = {view.index: view for view in project(state, 0)}
    assert views[derived].terrain == TERRAIN_CITY_FROM_CAPITAL == 4
    assert views[plain].terrain == 0


def test_city_from_capital_fogged_folds_to_plain_c17() -> None:
    state = make_state(size=11, players=(0, 1))
    far = state.index(10, 10)
    state.tiles[far] = make_tile(
        Terrain.CITY, army=8, owner=1, city_from_capital=True
    )
    views = {view.index: view for view in project(state, 0)}
    assert far not in set(state.visible_tiles(0))
    assert views[far].terrain == 0  # 视野外的转化型城市折叠为平原码
    assert views[far].army is None


# ---------------------------------------------------------------------------
# INTERFACES §1.7 —— 投影结构
# ---------------------------------------------------------------------------


def test_project_returns_one_view_per_tile_in_index_order() -> None:
    state = make_state(size=6, players=(0, 1))
    views = project(state, 0)
    assert len(views) == state.tile_count
    assert [v.index for v in views] == list(range(state.tile_count))


def test_project_map_shape() -> None:
    state = make_state(size=4, players=(0, 1))
    grid = project_map(state, None)
    # project_map 返回 {index: TileView}，便于按 index 查（INTERFACES §1.7）
    assert isinstance(grid, dict)
    assert len(grid) == state.tile_count
    assert grid[0].index == 0
    assert grid[15].index == 15
    assert [grid[i].index for i in sorted(grid)] == list(range(state.tile_count))


def test_tile_views_to_tuples_shape() -> None:
    state = make_state(size=4, players=(0, 1))
    rows = tile_views_to_tuples(project(state, None))
    assert len(rows) == state.tile_count
    for row in rows:
        assert len(row) == 4  # index / army / owner / terrain
        assert isinstance(row[0], int)


def test_project_owner_is_none_for_neutral() -> None:
    state = make_state(size=5, players=(0, 1))
    views = {v.index: v for v in project(state, None)}
    neutral = state.index(2, 2)
    assert views[neutral].owner is None


# ---------------------------------------------------------------------------
# R6.9 / PROTOCOL §3.3 —— 增量下发
# ---------------------------------------------------------------------------


def test_visible_diff_reports_newly_visible_tiles_r6_9() -> None:
    """视野变化被拆成 (新增, 移除) 两个升序列表（PROTOCOL §3.4 sight 帧）。"""
    from generals.game import Dir

    state = make_state(size=9, players=(0, 1))
    cap = state.players[0].capital
    assert cap is not None
    before = set(state.visible_tiles(0))
    right = state.step_index(cap, Dir.RIGHT)
    assert right is not None
    state.tiles[right] = make_tile(Terrain.PLAIN, army=5, owner=0)
    after = set(state.visible_tiles(0))

    added, removed = visible_diff(before, after)
    assert added == sorted(after - before)
    assert removed == sorted(before - after)
    assert added == sorted(added)
    assert removed == sorted(removed)


def test_diff_views_detects_army_change() -> None:
    state = make_state(size=5, players=(0, 1))
    cap = state.players[0].capital
    assert cap is not None
    before = project(state, 0)
    state.tiles[cap].army += 5
    after = project(state, 0)
    changed = diff_views(before, after)
    assert [row[0] for row in changed] == [cap]


def test_diff_views_empty_when_nothing_changes() -> None:
    state = make_state(size=5, players=(0, 1))
    a = project(state, 0)
    b = project(state, 0)
    assert diff_views(a, b) == []


def test_diff_views_sorted_by_index_r7_3() -> None:
    state = make_state(size=5, players=(0, 1))
    before = project(state, None)
    for idx in (7, 1, 12):
        state.tiles[idx].army += 3
    changed = diff_views(before, project(state, None))
    idxs = [row[0] for row in changed]
    assert idxs == sorted(idxs)


# ---------------------------------------------------------------------------
# 视野与相邻判定
# ---------------------------------------------------------------------------


def test_visible_tiles_include_capital_and_neighbours() -> None:
    state = make_state(size=9, players=(0, 1))
    cap = state.players[0].capital
    assert cap is not None
    visible = set(state.visible_tiles(0))
    assert cap in visible
    for n in state.neighbours4(cap):
        assert n in visible


def test_neighbours4_orders_and_drops_out_of_board() -> None:
    """neighbours4 按 [上, 下, 左, 右] 顺序返回，越界方向被过滤（不 wrap）。"""
    state = make_state(size=3, players=(0, 1))
    # 角 (0,0)：只有下(3) 与右(1)
    assert state.neighbours4(0) == [3, 1]
    # 中心 (1,1)：四个方向齐全，顺序 上/下/左/右
    assert state.neighbours4(4) == [1, 7, 3, 5]
    # 角 (2,2)=8：只有上(5) 与左(7)
    assert state.neighbours4(8) == [5, 7]
    # 边中点 (0,1)=1：下、左、右（无上）
    assert state.neighbours4(1) == [4, 0, 2]


def test_step_index_wraps_correctly() -> None:
    state = make_state(size=3, players=(0, 1))
    from generals.game import Dir

    assert state.step_index(4, Dir.UP) == 1
    assert state.step_index(4, Dir.DOWN) == 7
    assert state.step_index(4, Dir.LEFT) == 3
    assert state.step_index(4, Dir.RIGHT) == 5
    assert state.step_index(1, Dir.UP) is None  # 顶行无上
    assert state.step_index(3, Dir.LEFT) is None  # 左列无左
