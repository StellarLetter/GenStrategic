"""对局回放（replay）：录制 → 落盘 → 读取。

契约见 `docs/REPLAY.md`（格式与语义）与 `docs/INTERFACES.md` §2.4。

依赖方向（ARCHITECTURE.md §3）::

    net.room ──► replay ──► game（只读：view.project / turn_of）

`replay` **不认识** `net` / `ai` / `transport`：房间把「已经编码好的广播帧」交给
`ReplayRecorder`，策略调试条目由 `Room.record_ai_*` 转交。因此本包可被离线工具复用
（例如 `tools/replay.py`）。

导出：

- `ReplayStore`：进程内唯一的回放仓库（目录、索引、保留策略、**对局中不可读**守卫）
- `ReplayRecorder` / `ReplayLimits`：单局录制缓冲与上限
- `ReplayError` / `ReplayNotFound` / `ReplayRunning`：读失败的三类原因
- `default_replay_dir()`：解析回放目录（`GENERALS_REPLAY_DIR` > `<repo>/replays`）
- `summarize()`：把完整回放压成列表行
"""

from __future__ import annotations

from .record import FORMAT, VERSION, ReplayLimits, ReplayRecorder, summarize
from .store import (
    ReplayError,
    ReplayNotFound,
    ReplayRunning,
    ReplayStore,
    default_replay_dir,
)

__all__ = [
    "FORMAT",
    "VERSION",
    "ReplayError",
    "ReplayLimits",
    "ReplayNotFound",
    "ReplayRecorder",
    "ReplayRunning",
    "ReplayStore",
    "default_replay_dir",
    "summarize",
]