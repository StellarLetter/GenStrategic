"""示例策略：贪心扩张（本文件被 `generals.ai.loader` 自动挂载，无需注册到服务端代码）。

复制本文件改个名字（例如 `my_bot.py`）与注册名，就能得到一份属于自己的策略。
编写规则见 `docs/AI_API.md`；这里演示的是最小可跑通的写法。
"""

from __future__ import annotations

from generals.ai import register
from generals.ai.api import Action, Observation, neighbours4

#: 渲染折叠后的山地外观（R6.9）。AI 只能看到这个编码，不能断言"那里一定是山"。
MOUNTAIN = 2


@register("sample-greedy")
class SampleGreedy:
    """先占相邻空地，其次打兵力最弱的相邻敌格；每刻只提交 1 条调兵（R4.7）。"""

    name = "sample-greedy"
    label = "示例 · 贪心扩张"
    description = "占空地优先，其次打最弱的相邻敌格；比例固定 100%。"

    def reset(self, spec) -> None:
        """开局调用一次：把地图边长记下来（`act` 里用它算行列）。"""
        self.size = spec.size

    def act(self, obs: Observation) -> Action | None:
        """返回一条合法 `move`，或 `None`（本刻不操作）。"""
        best: tuple[tuple[int, int, int], str, int] | None = None
        for tile in obs.tiles:
            if tile.owner != obs.me:
                continue
            # 兵力 1 的地块按 R4.2 算出的调兵量为 0，直接跳过。
            if tile.army is None or tile.army <= 1:
                continue
            for direction, nb in neighbours4(tile.index, obs.size).items():
                target = obs.tiles[nb]
                if target.owner == obs.me:
                    continue
                # R4.9：不可向山地调兵，否则服务端回 blocked。
                if target.terrain == MOUNTAIN:
                    continue
                empty = target.owner is None and (target.army or 0) == 0
                key = (0 if empty else 1, int(target.army or 0), nb)
                if best is None or key < best[0]:
                    best = (key, direction, tile.index)
        if best is None:
            return None
        _key, direction, src = best
        return Action(op="move", src=src, dir=direction, ratio=1.0)
