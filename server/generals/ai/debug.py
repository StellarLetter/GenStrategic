"""策略调试日志：写进**当前对局的回放**（`docs/REPLAY.md` / `docs/AI_API.md` §11）。

为什么不是 `print`
------------------
`print` 只会打到服务端控制台，对局结束就散了，也没法按玩家/回合检索。这里的 `log()`
把条目记进本局回放的 `ai` 流：`{tick, playerId, seat, strategy, kind:"log", level, text}`，
复盘时可以按玩家、按回合筛。**对局中磁盘上还没有回放文件**（录制缓冲在内存里，
落盘发生在对局结束），所以策略没法用回放读写搞信息作弊。

用法（策略文件里）::

    from generals.ai import log

    def act(self, obs):
        log(f"turn {obs.turn}: army={obs.my_army} land={obs.my_land}")
        log("主力抵近王城", level="info")
        ...

约束
----
- **没有 sink 时静默丢弃**（离线脚本、单元测试、服务端未录制回放时），绝不抛异常；
- 文本超过 `config.REPLAY_AI_LOG_MAX_LEN` 会截断（落盘时由记录器做）；
- 事件循环外的线程（`Room.step_async` 的线程池）也能用：`AIRunner` 在 `asct`
  前后用 `capture()` 绑定 sink，`asyncio.to_thread` 会复制 context（Python ≥3.9）。

设置环境变量 `GENERALS_AI_LOG_ECHO=1` 可以让同样的日志**同时**打到 stderr
（开发时想边打边看就用它；默认关闭）。
"""

from __future__ import annotations

import contextlib
import os
import sys
from contextvars import ContextVar
from typing import Any, Callable, Final, Iterator

__all__ = [
    "LEVELS",
    "LogSink",
    "capture",
    "current_sink",
    "debug",
    "echo_enabled",
    "error",
    "info",
    "log",
    "warn",
]

#: 日志级别（只做标记，不过滤——回放里一条都不丢）。
LEVELS: Final[tuple[str, ...]] = ("debug", "info", "warn", "error")

#: sink 签名：`(level, text) -> None`。由 `ai.runner.AIRunner` 绑定到房间的录制器。
LogSink = Callable[[str, str], None]

#: 当前上下文的 sink；`None` = 没人接（日志静默丢弃）。
_SINK: ContextVar[LogSink | None] = ContextVar("generals_ai_log_sink", default=None)

#: 是否同时回显到 stderr（`GENERALS_AI_LOG_ECHO=1`）。在 import 时定下，运行期只读。
_ECHO: Final[bool] = os.environ.get("GENERALS_AI_LOG_ECHO", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)


def echo_enabled() -> bool:
    """是否开启 stderr 回显（`GENERALS_AI_LOG_ECHO`）。前置/后置：无；抛出：无。"""
    return _ECHO


def current_sink() -> LogSink | None:
    """当前绑定的 sink（`None` 表示没有对局在接日志）。"""
    return _SINK.get()


@contextlib.contextmanager
def capture(sink: LogSink | None) -> Iterator[None]:
    """在 `with` 块内把策略日志接到 `sink`（内部；`ai.runner` 与离线工具用）。

    前置：`sink` 为 `None` 或 `(level, text) -> None` 可调用对象。
    后置：块内 `log()` 调到 `sink`；块结束（含异常）后恢复上一个 sink。
    抛出：无 —— `ContextVar.reset` 之外不抛。
    """
    token = _SINK.set(sink)
    try:
        yield
    finally:
        _SINK.reset(token)


def log(message: Any, *, level: str = "debug") -> None:
    """记录一条策略调试日志（**对局结束后可在回放里读到**）。

    前置：`message` 任意对象（按 `str()` 转文本）；`level` 建议取 `LEVELS` 之一。
    后置：
    - 有 sink（即处在被录制的对局里）→ 追加到回放的 `ai` 流；
    - `GENERALS_AI_LOG_ECHO=1` → 同时写 stderr（`[ai:<level>] <text>`）；
    - 两者都没有 → 静默丢弃。
    抛出：无 —— 日志绝不允许打断策略（任何内部异常都被吞掉）。
    """
    try:
        text = message if isinstance(message, str) else str(message)
    except Exception:  # noqa: BLE001 - 策略可能传了 __str__ 会炸的对象
        text = repr(message)
    lvl = str(level or "debug")
    sink = _SINK.get()
    if sink is not None:
        try:
            sink(lvl, text)
        except Exception:  # noqa: BLE001 - 录制器故障不影响策略
            pass
    if _ECHO:
        try:
            sys.stderr.write(f"[ai:{lvl}] {text}\n")
        except Exception:  # noqa: BLE001
            pass


def debug(message: Any) -> None:
    """`log(..., level="debug")` 的简写。前置/后置/抛出：同 `log`。"""
    log(message, level="debug")


def info(message: Any) -> None:
    """`log(..., level="info")` 的简写。前置/后置/抛出：同 `log`。"""
    log(message, level="info")


def warn(message: Any) -> None:
    """`log(..., level="warn")` 的简写。前置/后置/抛出：同 `log`。"""
    log(message, level="warn")


def error(message: Any) -> None:
    """`log(..., level="error")` 的简写。前置/后置/抛出：同 `log`。"""
    log(message, level="error")