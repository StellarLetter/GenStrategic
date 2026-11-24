"""自研对手（zoo）：只用于**离线对局台**的陪练，不属于 `ai/` 策略目录。

为什么单独放在 `tools/`：`ai/` 下的文件会被服务端自动挂载成可选策略、出现在界面上；
这里只是调参用的陪练，注册名统一 `zoo-*` 前缀，只在 `tools/bench.py` 里 `import` 时注册。

四个原型分别代表四种常见打法，用来压力测试主策略的短板：
  * `zoo-turtle`：全部兵力屯首都，够强才出击（考验主策略能否打破龟壳）；
  * `zoo-rush`  ：开局就全线扑向敌方王城（考验主策略的防守与反打）；
  * `zoo-snake` ：贪心蛇形扩张，铺满全图（考验主策略能否直接斩首）；
  * `zoo-city`  ：扩张 + 抢城市，攒经济（考验主策略的经济节奏）。

它们与主策略一样**只读 `Observation`**、只返回 `Action`，没有额外信息。
"""

from __future__ import annotations

from collections import deque

from generals.ai import register
from generals.ai.api import Action, Observation, neighbours4

MOUNTAIN = 2


def _grid(obs: Observation):
    return obs.tiles


def _dirs_of(obs: Observation, idx: int) -> dict[str, int]:
    return neighbours4(idx, obs.size)


def _bfs_next_step(obs: Observation, start: int, goal: int, *, avoid_enemy: bool = True) -> int | None:
    """从 `start` 出发、按"能不能走过去"的粗略口径 BFS，返回朝 `goal` 的下一步地块。

    可走：视野内非山地；视野外只走 `terrain != 2`（R6.9 的折叠外观：雾里 2 可能是山也可能是城）。
    敌方地块默认**不进**（绕开），除非它就是要打的目标。
    """
    tiles = _grid(obs)
    size = obs.size
    if start == goal:
        return None
    seen = {start}
    queue: deque[tuple[int, int]] = deque([(start, -1)])
    while queue:
        idx, first = queue.popleft()
        for direction, nb in _dirs_of(obs, idx).items():
            if nb in seen:
                continue
            tile = tiles[nb]
            if tile.terrain == MOUNTAIN:
                continue
            if nb != goal and avoid_enemy and tile.owner not in (None, obs.me):
                continue
            step = nb if first < 0 else first
            if nb == goal:
                return step
            seen.add(nb)
            queue.append((nb, step))
    return None


def _move(obs: Observation, src: int, dst: int) -> Action | None:
    """返回从 `src` 到 4 相邻 `dst` 的 `move`（比例 100%）。"""
    for direction, nb in _dirs_of(obs, src).items():
        if nb == dst:
            return Action(op="move", src=src, dir=direction, ratio=1.0)
    return None


def _my_capital(obs: Observation) -> int | None:
    for tile in _grid(obs):
        if tile.owner == obs.me and tile.terrain == 1:
            return tile.index
    return None


def _enemy_tiles(obs: Observation) -> list:
    return [t for t in _grid(obs) if t.owner is not None and t.owner != obs.me]


def _enemy_capital(obs: Observation) -> int | None:
    for tile in _enemy_tiles(obs):
        if tile.terrain == 1:
            return tile.index
    return None


def _enemy_total(obs: Observation) -> int:
    return sum(row.army for row in obs.leaderboard if row.player_id != obs.me and row.alive)


def _nearest(obs: Observation, src: int, cands: list[int]) -> int | None:
    if not cands:
        return None
    size = obs.size
    sr, sc = divmod(src, size)
    return min(cands, key=lambda i: (abs(i // size - sr) + abs(i % size - sc), i))


class _Zoo:
    """陪练基类：记录边长 + 一个"本刻只出一条"的小工具。"""

    name = "zoo"
    label = "Zoo"
    description = "调参用陪练"

    def reset(self, spec) -> None:
        self.size = spec.size

    # --- 工具 -------------------------------------------------------------
    def _biggest(self, obs: Observation, *, exclude: int | None = None) -> tuple[int, int] | None:
        best: tuple[int, int] | None = None
        for tile in _grid(obs):
            if tile.owner != obs.me or tile.army is None or tile.army <= 1:
                continue
            if exclude is not None and tile.index == exclude:
                continue
            if best is None or tile.army > best[1]:
                best = (tile.index, tile.army)
        return best

    def _push_toward(self, obs: Observation, goal: int, *, src: int | None = None) -> Action | None:
        """把最大的一坨兵往 `goal` 推一步。"""
        blob = self._biggest(obs) if src is None else (src, obs.tiles[src].army or 0)
        if blob is None:
            return None
        src_idx, army = blob
        if army is None or army <= 1:
            return None
        step = _bfs_next_step(obs, src_idx, goal)
        if step is None:
            return None
        return _move(obs, src_idx, step)


@register("zoo-turtle")
class ZooTurtle(_Zoo):
    """屯兵首都，等兵力远超对手再一路推平。"""

    name = "zoo-turtle"
    label = "Zoo · 龟缩反击"
    description = "全部兵力屯首都；兵力超过对手总量 1.6 倍才出击。"

    def act(self, obs: Observation) -> Action | None:
        capital = _my_capital(obs)
        if capital is None:
            return None
        enemy_total = _enemy_total(obs)
        my_army = obs.tiles[capital].army or 0
        enemies = _enemy_tiles(obs)

        # 早期象征性占几格，拿到一点视野。
        if obs.turn < 8 and my_army > 1:
            for direction, nb in _dirs_of(obs, capital).items():
                tile = obs.tiles[nb]
                if tile.owner is None and (tile.army or 0) == 0 and tile.terrain != MOUNTAIN:
                    return Action(op="move", src=capital, dir=direction, ratio=1.0)

        if not enemies:
            return None
        # 敌方王城已知 → 直接打王城；否则打最近的敌方地块。
        target = _enemy_capital(obs) or _nearest(obs, capital, [t.index for t in enemies])
        if target is None:
            return None
        if my_army > 1.6 * enemy_total + 8:
            return self._push_toward(obs, target)
        return None


@register("zoo-rush")
class ZooRush(_Zoo):
    """开局扑向敌方王城：所有兵力往前压。"""

    name = "zoo-rush"
    label = "Zoo · 开局冲锋"
    description = "第 3 回合起把最大的一坨兵持续推向敌方王城/最近敌方地块。"

    def act(self, obs: Observation) -> Action | None:
        capital = _my_capital(obs)
        if capital is None:
            return None
        enemies = _enemy_tiles(obs)
        if not enemies:
            # 还没看到敌人：朝地图中心方向探。
            center = (obs.size * obs.size) // 2
            return self._push_toward(obs, center)
        target = _enemy_capital(obs) or _nearest(obs, capital, [t.index for t in enemies])
        if obs.turn < 3:
            return None
        return self._push_toward(obs, target)


@register("zoo-raider")
class ZooRaider(_Zoo):
    """像真人一样打：扩张攒兵，看到(或猜到)对手王城就整包砸过去。"""

    name = "zoo-raider"
    label = "Zoo · 突袭者"
    description = "扩张攒兵；一旦看见对手王城就把最大的一坨兵整包砸过去，专打偷家。"

    def act(self, obs: Observation) -> Action | None:
        capital = _my_capital(obs)
        if capital is None:
            return None
        enemies = _enemy_tiles(obs)
        # 1) 看见对手王城 → 全力扑上去（够兵才动，否则先攒）。
        target = _enemy_capital(obs)
        if target is not None:
            blob = self._biggest(obs)
            garrison = obs.tiles[target].army or 0
            if blob is None:
                return None
            src, army = blob
            if src == target:
                return None
            if army > garrison + 1 or obs.turn > 120:
                step = _bfs_next_step(obs, src, target, avoid_enemy=False)
                if step is not None:
                    return _move(obs, src, step)
            return None
        # 2) 没看见王城：往最近的敌方地块压（贴脸就能看见）。
        if obs.turn >= 25 and enemies:
            blob = self._biggest(obs)
            if blob is not None:
                src, army = blob
                cands = [t.index for t in enemies if (t.army or 0) + 1 < army]
                if cands:
                    goal = _nearest(obs, src, cands)
                    if goal is not None:
                        step = _bfs_next_step(obs, src, goal, avoid_enemy=False)
                        if step is not None:
                            return _move(obs, src, step)
        # 3) 否则扩张。
        return _expand_best(obs)


def _expand_best(obs: Observation) -> Action | None:
    """占一格相邻空地（比例 10%，尽量少送兵）。"""
    for direction_tile in obs.tiles:
        if direction_tile.owner != obs.me or (direction_tile.army or 0) <= 1:
            continue
        for direction, nb in _dirs_of(obs, direction_tile.index).items():
            tile = obs.tiles[nb]
            if tile.owner is None and (tile.army or 0) == 0 and tile.terrain != MOUNTAIN:
                return Action(op="move", src=direction_tile.index, dir=direction, ratio=0.1)
    return None


@register("zoo-snake")
class ZooSnake(_Zoo):
    """贪心蛇形扩张：优先占离首都最远的空地。"""

    name = "zoo-snake"
    label = "Zoo · 贪心蛇"
    description = "每刻把可动兵力往最远的相邻空地铺，其次打最弱的相邻敌格。"

    def act(self, obs: Observation) -> Action | None:
        capital = _my_capital(obs) or 0
        size = obs.size
        cr, cc = divmod(capital, size)
        best: tuple[tuple[int, int, int], int, str] | None = None
        for tile in _grid(obs):
            if tile.owner != obs.me or tile.army is None or tile.army <= 1:
                continue
            for direction, nb in _dirs_of(obs, tile.index).items():
                target = obs.tiles[nb]
                if target.owner == obs.me or target.terrain == MOUNTAIN:
                    continue
                far = abs(nb // size - cr) + abs(nb % size - cc)
                if target.owner is None and (target.army or 0) == 0:
                    key = (0, -far, nb)
                elif target.owner is not None and (target.army or 0) < tile.army - 1:
                    key = (1, int(target.army or 0), nb)
                else:
                    continue
                if best is None or key < best[0]:
                    best = (key, tile.index, direction)
        if best is None:
            return None
        return Action(op="move", src=best[1], dir=best[2], ratio=1.0)


@register("zoo-city")
class ZooCity(_Zoo):
    """扩张 + 抢城：把兵往最近的中立城市送，够数就吃。"""

    name = "zoo-city"
    label = "Zoo · 抢城流"
    description = "优先吃相邻的中立城市（够兵就吃），否则往空地扩张。"

    def act(self, obs: Observation) -> Action | None:
        capital = _my_capital(obs)
        if capital is None:
            return None

        # 1) 相邻的中立城市：兵力够就直接吃。
        for tile in _grid(obs):
            if tile.owner != obs.me or tile.army is None or tile.army <= 1:
                continue
            for direction, nb in _dirs_of(obs, tile.index).items():
                target = obs.tiles[nb]
                if target.terrain == 3 and target.owner is None:
                    need = int(target.army or 0) + 1
                    if tile.army > need:
                        return Action(op="move", src=tile.index, dir=direction, ratio=1.0)

        # 2) 已经贴着中立城市的己方格 = 集结点：把兵往里送。
        for tile in _grid(obs):
            if tile.owner != obs.me:
                continue
            city_nb = [
                nb
                for _d, nb in _dirs_of(obs, tile.index).items()
                if obs.tiles[nb].terrain == 3 and obs.tiles[nb].owner is None
            ]
            if not city_nb:
                continue
            collector = tile.index
            # 集结点本身的兵先往城里送（凑够之前会一直送，等于分批消耗守军）。
            if (tile.army or 0) > 1:
                return Action(
                    op="move",
                    src=collector,
                    dir=next(d for d, nb in _dirs_of(obs, collector).items() if nb == city_nb[0]),
                    ratio=1.0,
                )
            # 否则把最大的一坨兵往集结点推。
            return self._push_toward(obs, collector, src=capital)

        # 3) 没城市可打 → 扩张。
        return self._expand(obs, capital)

    def _expand(self, obs: Observation, capital: int) -> Action | None:
        for tile in sorted(_grid(obs), key=lambda t: t.index):
            if tile.owner != obs.me or tile.army is None or tile.army <= 1:
                continue
            for direction, nb in _dirs_of(obs, tile.index).items():
                target = obs.tiles[nb]
                if target.owner is None and (target.army or 0) == 0 and target.terrain != MOUNTAIN:
                    return Action(op="move", src=tile.index, dir=direction, ratio=1.0)
        return None


__all__ = ["ZooTurtle", "ZooRush", "ZooSnake", "ZooCity"]