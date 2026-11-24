"""策略注册表：`name -> Agent 类`（INTERFACES §3 / AI_API.md §2）。

用法::

    from generals.ai import register

    @register("mybot")
    class MyBot:
        name = "mybot"
        def reset(self, spec): ...
        def act(self, obs): ...

注册后即可在 `join` 帧里写 `{"t": "join", "name": "MyBot", "bot": "mybot"}` 让 AI 占座。
"""

from __future__ import annotations

from typing import Callable, TypeVar

from .api import Agent

__all__ = [
    "all_names",
    "clear",
    "get",
    "register",
    "registered",
    "snapshot",
    "unregister",
]

T = TypeVar("T", bound=type)

#: 进程内策略表。键为注册名（小写），值为类对象（**不是**实例：每个座位要独立实例，避免状态互串）。
_REGISTRY: dict[str, type] = {}


def register(name: str) -> Callable[[T], T]:
    """装饰器：把策略类注册到全局表（INTERFACES §3）。

    前置：`name` 非空；同名策略可被覆盖（后注册者生效，便于开发时热替换）。
    后置：返回原类（装饰器不改变类对象），`get(name)` / `all_names()` 可见。
    抛出：`ValueError` —— `name` 为空字符串或非字符串。
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("strategy name must be a non-empty string")
    key = name.strip().lower()

    def _decorator(cls: T) -> T:
        _REGISTRY[key] = cls
        return cls

    return _decorator


def get(name: str) -> type[Agent]:
    """按名字取策略类。

    前置：无。
    后置：返回注册的类；`name` 大小写不敏感。
    抛出：`KeyError` —— 未注册（错误信息里附带全部可用名字，便于排错）。
    """
    key = (name or "").strip().lower()
    try:
        return _REGISTRY[key]  # type: ignore[return-value]
    except KeyError:
        raise KeyError(f"unknown strategy {name!r}; available: {sorted(_REGISTRY)}") from None


def all_names() -> list[str]:
    """列出全部已注册策略名（升序，R7.3 确定性）。

    前置：无。后置：返回 `list[str]`。抛出：无。
    """
    return sorted(_REGISTRY)


def registered(name: str) -> bool:
    """判断某策略名是否已注册。前置：无。后置：返回 bool。抛出：无。"""
    return (name or "").strip().lower() in _REGISTRY


def snapshot() -> dict[str, type]:
    """返回注册表的浅拷贝（`name -> 类对象`）。

    前置：无。
    后置：返回**新的** dict（修改它不影响注册表）；键已小写化。
          供 `ai.loader` 对比「一个策略文件注册了哪些名字」。
    抛出：无。
    """
    return dict(_REGISTRY)


def unregister(name: str) -> bool:
    """从注册表摘掉一个策略（**供 `ai.loader` 热重载使用**）。

    前置：无。
    后置：返回 `True` = 确实摘掉了；`False` = 本来就没有该名字（幂等）。
    抛出：无。
    """
    return _REGISTRY.pop((name or "").strip().lower(), None) is not None


def clear() -> None:
    """清空注册表（**仅供测试**使用，避免测试间互相污染）。前置/后置：无。抛出：无。"""
    _REGISTRY.clear()
