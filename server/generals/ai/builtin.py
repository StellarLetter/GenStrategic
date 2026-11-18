"""内置示例策略：`hold` / `greedy` / `random`（AI_API.md §4）。

纪律
----
- 这些策略只读 `Observation`，输出 `Action`，**从不触碰 `GameState`**（ARCHITECTURE.md §3）。
- `random` 使用**显式播种**的 `random.Random`（AI_API.md §4 注：AI 的随机性属于输入，
  不属于结算，不影响 R7.3 的结算确定性）。
- 所有遍历都按 `index` 升序进行（R7.3 的"无隐式顺序依赖"精神）。

坑（AI_API.md §5）
------------------
1. 视野外 `army is None`，不能做算术；
2. `terrain == 2` 是**折叠后**的山地外观，可能是真山地也可能是视野外城市（C-17），不能断言；
3. 调兵链异步：实际兵力 = `min(ceil(army*ratio), army-1)`（C-02 / C-07）；
4. 不可向山地调兵（R4.9），否则服务端 `reject(code='blocked')`；
5. 每刻只执行 1 条（R4.7）。
"""

from __future__ import annotations

import math
import random
from typing import Any, Final

from .api import Action, GameSpec, Observation
from .registry import register

__all__ = [
    "DIR_DELTAS",
    "Greedy",
    "Hold",
    "Random",
    "neighbours4",
]

#: 方向 → (drow, dcol)。顺序固定为 `up, down, left, right`（与 `game.state.neighbours4` 一致）。
DIR_DELTAS: Final[dict[str, tuple[int, int]]] = {
    "up": (-1, 0),
    "down": (1, 0),
    "left": (0, -1),
    "right": (0, 1),
}

#: 渲染折叠后的山地地形值（PROTOCOL.md §3.3 / C-17）。AI 侧只能看到它。
TERRAIN_MOUNTAIN_VISIBLE: Final[int] = 2


def neighbours4(index: int, size: int) -> dict[str, int]:
    """返回 4 相邻地块（AI_API.md §2 示例里的同名工具）。

    前置：`0 <= index < size*size`。
    后置：返回 `{dir: neighbour_index}`，仅包含**未越界**的方向；键顺序固定 `up/down/left/right`。
    抛出：无（越界输入返回空 dict）。
    """
    row, col = divmod(int(index), int(size))
    out: dict[str, int] = {}
    for direction in ("up", "down", "left", "right"):
        dr, dc = DIR_DELTAS[direction]
        r, c = row + dr, col + dc
        if 0 <= r < size and 0 <= c < size:
            out[direction] = r * size + c
    return out


@register("hold")
class Hold:
    """`hold`：什么都不做（压测/对照组，AI_API.md §4）。"""

    name = "hold"

    def reset(self, spec: GameSpec) -> None:
        """开局初始化：记录地图边长。前置/后置/抛出：无。"""
        self.size = spec.size

    def act(self, obs: Observation) -> Action | None:
        """永远返回 `None`（本 Tick 不操作）。前置/后置/抛出：无。"""
        return None


@register("greedy")
class Greedy:
    """`greedy`：贪心扩张 —— 先占空地，其次打最弱的邻格（AI_API.md §2 / §4）。

    策略（确定性，无随机）：
    1. 遍历自己的地块（按 index 升序），兵力 `> 1` 的才有意义（否则实际出兵 0，C-02）；
    2. 对每个 4 相邻格：
       - 目标可见（`army is not None`）且 `terrain != 2`（不是山地外观，R4.9）；
       - 目标非己方控制；
    3. 打分：空地（`owner is None` 且 `army in (None, 0)`）优先；否则按目标兵力升序；
    4. 返回一条 `move`（比例 1.0）。
    """

    name = "greedy"

    def reset(self, spec: GameSpec) -> None:
        """开局初始化：记录地图边长。前置/后置/抛出：无。"""
        self.size = spec.size

    def act(self, obs: Observation) -> Action | None:
        """返回一条 `move` 或 `None`。前置：`obs.tiles` 长度 `size²`。后置：合法 Action/None。抛出：无。"""
        best: tuple[tuple[int, int, int, int], dict[str, int]] | None = None
        size = obs.size

        for tile in obs.tiles:
            if tile.owner != obs.me:
                continue
            army = tile.army
            if army is None or army <= 1:
                continue
            for direction, nb_idx in neighbours4(tile.index, size).items():
                target = obs.tiles[nb_idx]
                if target.owner == obs.me:
                    continue
                if target.terrain == TERRAIN_MOUNTAIN_VISIBLE:
                    continue  # R4.9：山地不可通行（可见地形判定，客户端视角）
                if target.army is None:
                    # 视野外目标：本策略不去碰未知格（避免撞上城市/山地的额外消耗）。
                    # 注意 C-17：`terrain==2` 已在上方排除，这里只剩「视野外平原外观」。
                    pass
                is_empty = target.owner is None and (target.army or 0) == 0
                # 排序键：(是否空地(0 优先), 目标兵力, 目标index, 方向序)
                key = (
                    0 if is_empty else 1,
                    int(target.army or 0),
                    int(nb_idx),
                    list(DIR_DELTAS).index(direction),
                )
                if best is None or key < best[0]:
                    best = (key, {"src": tile.index, "dir": direction})
        if best is None:
            return None
        return Action(op="move", src=best[1]["src"], dir=best[1]["dir"], ratio=1.0)


@register("random")
class Random:
    """`random`：合法随机（**仅用于测试**，AI_API.md §4）。

    前置：`seed` 由构造方显式给出（默认 0），保证可复现（AI_API.md §4 注）。
    后置：`act()` 在「可行动作」里等概率选一个；无可行动作时返回 `None`。
    抛出：`TypeError` —— `seed` 不是 int（编程错误）。
    """

    name = "random"

    def __init__(self, seed: int = 0) -> None:
        self._rng = random.Random(int(seed))
        self.size = 0

    def reset(self, spec: GameSpec) -> None:
        """开局初始化：记录地图边长（**不重置随机数状态**，保证跨局可复现）。"""
        self.size = spec.size

    def act(self, obs: Observation) -> Action | None:
        """在合法 `move` 中随机挑一条。前置/后置：同上。抛出：无。"""
        options: list[Action] = []
        for tile in obs.tiles:
            if tile.owner != obs.me:
                continue
            army = tile.army
            if army is None or army <= 1:
                continue
            for direction, nb_idx in neighbours4(tile.index, obs.size).items():
                target = obs.tiles[nb_idx]
                if target.owner == obs.me or target.terrain == TERRAIN_MOUNTAIN_VISIBLE:
                    continue
                options.append(Action(op="move", src=tile.index, dir=direction, ratio=1.0))
        if not options:
            return None
        return options[self._rng.randrange(len(options))]


def _ceilmul(army: int, ratio: float) -> int:  # pragma: no cover - 工具函数，供策略参考
    """`min(ceil(army*ratio), army-1)`（R4.2 / C-02 / C-07）。**不参与决策**，仅便于自测对照。"""
    if army <= 0:
        return 0
    return max(0, min(math.ceil(army * ratio), army - 1))
