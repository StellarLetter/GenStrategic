"""HTTP 路由：房间列表与客户端启动参数（PROTOCOL.md §4）。

职责：把 `net.lobby` 的只读查询暴露成 REST；**不含任何业务规则**。
契约见 docs/INTERFACES.md §5（装配点）与 docs/PROTOCOL.md §4。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..config import COLORS, DEFAULT_CONFIG

__all__ = ["router"]

router = APIRouter(prefix="/api", tags=["api"])


def _lobby(request: Request) -> Any:
    """从应用状态取 Lobby（前置：`app.state.lobby` 已装配）。"""
    return request.app.state.lobby


@router.get("/rooms")
async def list_rooms(request: Request) -> JSONResponse:
    """房间列表。

    前置：无。
    后置：`[{id, name, players, maxPlayers, state}]`，按 id 升序（PROTOCOL.md §4）。
    抛出：无。
    """
    lobby = _lobby(request)
    rows = [summary.to_wire() for summary in lobby.list_rooms()]
    return JSONResponse(rows)


@router.get("/config")
async def get_config() -> JSONResponse:
    """客户端启动参数（PROTOCOL.md §4）。

    前置：无。
    后置：`{tickMs, maxSize, minSize, defaultSize, maxPlayers, colors}`。
    抛出：无。
    """
    cfg = DEFAULT_CONFIG
    return JSONResponse(
        {
            "tickMs": cfg.tick_ms,
            "maxSize": cfg.max_size,
            "minSize": cfg.min_size,
            "defaultSize": cfg.default_size,
            "maxPlayers": cfg.max_players,
            "colors": list(COLORS),
        }
    )


@router.get("/healthz")
async def healthz() -> JSONResponse:
    """存活探针。"""
    return JSONResponse({"ok": True})
