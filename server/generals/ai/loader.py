"""策略自动挂载：把策略根目录（默认 `<repo>/ai`）下的 Python 文件加载进注册表。

契约见 `docs/AI_API.md` §3（自动挂载）。设计要点
------------------------------------------------
1. **零配置**：把 `my_bot.py` 丢进 `ai/` 目录，重启（或 `POST /api/ai/reload`）后
   自动出现在 Web 界面的策略下拉框里，无需改任何服务端代码、无需改 `pyproject.toml`。
2. **错误隔离**：单个文件语法错/运行期异常只记录到 `LoadReport.errors`，
   **不影响**其它策略与服务器启动（一个坏文件不该让整局游戏起不来）。
3. **可重载**：`scan()` 会先把上一次由文件注册的策略摘掉，再重新扫描，
   因此删除/改名/覆盖策略都能生效（内置 `hold`/`greedy`/`random` 永不受影响）。
4. **来源可追溯**：加载时对比注册表前后快照，得到「该文件注册了哪些名字」，
   供 `GET /api/ai/strategies` 显示来源文件。

策略文件的契约（详见 `docs/AI_API.md`）：

```python
from generals.ai import register
from generals.ai.api import Action, Observation

@register("my-bot")
class MyBot:
    label = "My Bot"            # 可选：界面显示名
    description = "一句话说明"   # 可选：界面提示
    def reset(self, spec): ...
    def act(self, obs): ...
```

本模块无游戏逻辑，只做「文件 → 模块 → 注册表」的搬运。
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Final

from . import registry

__all__ = [
    "DEFAULT_AI_DIR",
    "ENV_AI_DIR",
    "LoadReport",
    "StrategyInfo",
    "StrategyLoader",
    "ai_dir",
]

#: 默认策略根目录：仓库根下的 `ai/`（本文件位于 `server/generals/ai/loader.py`）。
DEFAULT_AI_DIR: Final[Path] = Path(__file__).resolve().parents[3] / "ai"

#: 覆盖策略根目录的环境变量（部署到别处时用）。
ENV_AI_DIR: Final[str] = "GENERALS_AI_DIR"

#: 不当作策略文件加载的文件名。
_SKIP_FILES: Final[frozenset[str]] = frozenset({"__init__.py", "setup.py", "conftest.py"})

#: 不扫描的子目录名。
_SKIP_DIRS: Final[frozenset[str]] = frozenset({"__pycache__", "node_modules", ".git"})

#: 策略名过长时截断成合法模块名片段的判定用正则。
_SLUG_RE: Final[re.Pattern[str]] = re.compile(r"[^0-9a-zA-Z]+")


def ai_dir() -> Path:
    """返回策略根目录。

    前置：无。
    后置：`GENERALS_AI_DIR` 非空则用它（展开 `~` 并解析绝对路径），否则用 `<repo>/ai`。
    抛出：无。
    """
    raw = os.environ.get(ENV_AI_DIR, "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return DEFAULT_AI_DIR


@dataclass(frozen=True)
class StrategyInfo:
    """一条可用策略的元信息（`GET /api/ai/strategies` 的一行）。

    - `name`：注册名（就是 `autopilot` 帧 / `join.bot` 里要写的字符串）。
    - `label`：显示名；策略类没声明 `label` 时等于 `name`。
    - `description`：一句话说明；没声明时取类 docstring 首行。
    - `source`：来源文件（相对策略根目录的路径）；内置策略为 `"builtin"`。
    - `builtin`：是否随服务端内置（不可被 `ai/` 下的同名文件以外的方式删除）。
    """

    name: str
    label: str
    description: str
    source: str
    builtin: bool

    def to_wire(self) -> dict:
        """转成 JSON 行（camelCase 以外全小写，与前端一致）。"""
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "source": self.source,
            "builtin": self.builtin,
        }


@dataclass
class LoadReport:
    """一次 `scan()` 的结果。

    - `directory`：本次扫描的根目录（绝对路径字符串）。
    - `strategies`：全部可用策略（含内置），按 `name` 升序（R7.3 确定性）。
    - `errors`：加载失败的文件 `[{file, error}]`（按文件名升序）。
    - `files`：本次实际尝试加载的文件（相对路径，按名字升序）。
    """

    directory: str
    strategies: list[StrategyInfo] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    files: list[str] = field(default_factory=list)

    def to_wire(self) -> dict:
        """转成 `GET /api/ai/strategies` 的响应体。"""
        return {
            "directory": self.directory,
            "strategies": [s.to_wire() for s in self.strategies],
            "errors": list(self.errors),
            "files": list(self.files),
        }


class StrategyLoader:
    """策略目录加载器（可重复调用 `scan()` 热重载）。

    前置（构造）：`directory` 可以是任意路径；不存在时 `scan()` 返回空清单而不抛。
    后置：`scan()` 之后，注册表里同时含有内置策略与目录里的策略。
    抛出：构造/扫描都不抛（文件级错误收敛进 `LoadReport.errors`）。

    线程约束：只在事件循环线程里调用（`app` 装配与 HTTP 重载入口都如此），无需加锁。
    """

    def __init__(self, directory: Path | None = None) -> None:
        self.directory: Path = Path(directory) if directory is not None else ai_dir()
        #: 策略名 → 来源文件（相对路径）。只记**由文件加载**的策略。
        self._sources: dict[str, str] = {}
        self._modules: dict[str, ModuleType] = {}
        self.errors: list[dict] = []
        self.files: list[str] = []
        self.report: LoadReport = LoadReport(directory=str(self.directory))

    # ------------------------------------------------------------------
    def scan(self) -> LoadReport:
        """（重新）扫描策略目录并加载全部策略文件。

        前置：无。
        后置：
        - 目录里每个策略文件被执行一次，其 `@register(...)` 结果进入注册表；
        - 上一次由文件注册、本次不再出现的策略被摘除（文件删除/改名即时生效）；
        - 返回的 `LoadReport.strategies` 含**内置 + 文件**全部策略，按名字升序。
        抛出：无 —— 单个文件失败只记进 `errors`。
        """
        previous = dict(self._sources)
        self._sources = {}
        self.errors = []
        self.files = []
        loaded_names: set[str] = set()

        if self.directory.is_dir():
            _ensure_importable(self.directory)
            for path in _iter_strategy_files(self.directory):
                rel = _rel_path(self.directory, path)
                self.files.append(rel)
                before = registry.snapshot()
                module = self._import_file(path, rel)
                if module is None:
                    continue
                self._modules[rel] = module
                after = registry.snapshot()
                # 「新增或替换」都算这个文件注册的名字（同文件重载时类对象会变）。
                for name in sorted(n for n, cls in after.items() if before.get(n) is not cls):
                    self._sources[name] = rel
                    loaded_names.add(name)

        # 上一轮有、这一轮没有任何文件再注册 → 摘掉（内置策略不在 `previous` 里）。
        for name in sorted(set(previous) - loaded_names):
            if registry.unregister(name):
                self._modules.pop(previous[name], None)

        self.report = LoadReport(
            directory=str(self.directory),
            strategies=self._collect(),
            errors=sorted(self.errors, key=lambda e: str(e.get("file", ""))),
            files=sorted(self.files),
        )
        return self.report

    # ------------------------------------------------------------------
    def _import_file(self, path: Path, rel: str) -> ModuleType | None:
        """把单个文件当独立模块执行（内部）。

        前置：`path` 已确认是文件。
        后置：成功返回模块对象并把该模块登记在 `sys.modules`（同名先摘除，保证重载拿到新代码）；
              失败返回 `None` 并把 `{file, error}` 追加进 `self.errors`。
        抛出：无（`KeyboardInterrupt` 除外 —— 它是用户主动中断，不该被吞）。
        """
        module_name = f"generals_strategy_{_slug(rel)}"
        # 先摘除旧模块：否则重载时 `exec_module` 拿到的是缓存对象，改了文件也不生效。
        sys.modules.pop(module_name, None)
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:  # pragma: no cover - 极端文件系统
                raise ImportError(f"无法为 {rel} 创建模块 spec")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - 用户代码的任何失败都只记一条
            sys.modules.pop(module_name, None)
            self.errors.append({"file": rel, "error": _format_error(exc)})
            return None
        return module

    def _collect(self) -> list[StrategyInfo]:
        """汇总「内置 + 文件」策略清单（内部，按名字升序）。"""
        builtin_names = set(builtin_strategy_names())
        rows: list[StrategyInfo] = []
        for name in registry.all_names():
            cls = registry.get(name)
            source = self._sources.get(name, "builtin")
            rows.append(
                StrategyInfo(
                    name=name,
                    label=str(getattr(cls, "label", "") or name),
                    description=str(getattr(cls, "description", "") or _doc_line(cls)),
                    source=source,
                    builtin=name in builtin_names and name not in self._sources,
                )
            )
        return rows


# ----------------------------------------------------------------------
# 目录遍历 / 命名工具
# ----------------------------------------------------------------------


def builtin_strategy_names() -> tuple[str, ...]:
    """返回随服务端内置的策略名（`hold` / `greedy` / `random`）。

    前置：`generals.ai.builtin` 已导入（`generals.ai` 的 `__init__` 保证）。
    后置：返回按名字升序的元组。
    抛出：无。
    """
    from .builtin import BUILTIN_NAMES

    return BUILTIN_NAMES


def _iter_strategy_files(directory: Path) -> list[Path]:
    """列出目录下所有策略文件（单层 `*.py` + 一层子包）。

    前置：`directory` 是目录。
    后置：返回按路径名升序的文件列表；跳过 `_` / `.` 开头的文件与 `__init__.py`、
          以及 `__pycache__` 等目录。子目录若含 `__init__.py` 则整体作为一个包加载，
          否则扫描它下面的 `*.py`。
    抛出：`OSError` —— 目录不可读（调用方 `scan()` 已用 `is_dir()` 兜底）。
    """
    out: list[Path] = []
    for entry in sorted(directory.iterdir(), key=lambda p: p.name):
        if entry.is_dir():
            if entry.name.startswith((".", "_")) or entry.name in _SKIP_DIRS:
                continue
            init = entry / "__init__.py"
            if init.is_file():
                out.append(init)
            else:
                out.extend(sorted(entry.glob("*.py"), key=lambda p: p.name))
        elif entry.suffix == ".py":
            if entry.name.startswith("_") or entry.name in _SKIP_FILES:
                continue
            out.append(entry)
    return out


def _ensure_importable(directory: Path) -> None:
    """把策略根目录加入 `sys.path`，让策略文件可以 `import` 同目录的辅助模块。

    前置：`directory` 是目录。
    后置：`sys.path` 里最多出现一次该目录（幂等）。
    抛出：无。
    """
    text = str(directory)
    if text not in sys.path:
        sys.path.insert(0, text)


def _rel_path(directory: Path, path: Path) -> str:
    """返回 `path` 相对 `directory` 的 POSIX 风格路径（跨平台稳定，便于展示）。"""
    try:
        return path.relative_to(directory).as_posix()
    except ValueError:  # pragma: no cover - 同一目录下不会发生
        return path.name


def _slug(text: str) -> str:
    """把相对路径压成合法模块名片段（`a/b.py` → `a_b`）。"""
    stem = text[:-3] if text.endswith(".py") else text
    slug = _SLUG_RE.sub("_", stem).strip("_").lower()
    return slug or "strategy"


def _doc_line(cls: Any) -> str:
    """取类 docstring 的首个非空行（作为缺省说明）；没有则返回空串。"""
    doc = getattr(cls, "__doc__", None) or ""
    for line in str(doc).splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _format_error(exc: BaseException) -> str:
    """把异常压成一行便于界面展示的文本（`SyntaxError` 带行号）。"""
    if isinstance(exc, SyntaxError):
        where = f"{exc.filename or ''}:{exc.lineno or '?'}"
        return f"SyntaxError: {exc.msg} ({where})"
    text = str(exc).strip().splitlines()
    head = text[0] if text else ""
    return f"{type(exc).__name__}: {head}" if head else type(exc).__name__
