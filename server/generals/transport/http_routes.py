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
    后置：`{tickMs, maxSize, minSize, maxPlayers, colors}`。
          地图边长不在此处（R2.3：由开局玩家数决定，实际值从 `game_start.size` 读取）。
    抛出：无。
    """
    cfg = DEFAULT_CONFIG
    return JSONResponse(
        {
            "tickMs": cfg.tick_ms,
            "maxSize": cfg.max_size,
            "minSize": cfg.min_size,
            "maxPlayers": cfg.max_players,
            "colors": list(COLORS),
        }
    )


@router.get("/healthz")
async def healthz() -> JSONResponse:
    """存活探针（带 `/api` 前缀的版本，等价于 `GET /healthz`）。"""
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# 策略（AI_API.md §3 自动挂载）
# ---------------------------------------------------------------------------


def _loader(request: Request) -> Any:
    """从应用状态取 `StrategyLoader`（前置：`app.state.strategy_loader` 已装配）。"""
    return request.app.state.strategy_loader


@router.get("/ai/strategies")
async def list_strategies(request: Request) -> JSONResponse:
    """可用策略清单（Web 界面的策略下拉框数据源）。

    前置：无。
    后置：`{directory, strategies: [{name, label, description, source, builtin}], errors, files}`；
           `strategies` 按 `name` 升序（含内置 `hold`/`greedy`/`random`）。
    抛出：无。
    """
    return JSONResponse(_loader(request).report.to_wire())


@router.post("/ai/reload")
async def reload_strategies(request: Request) -> JSONResponse:
    """重新扫描策略目录（改完 `ai/*.py` 后无须重启服务）。

    前置：无。
    后置：返回与 `GET /api/ai/strategies` 同构的响应体，内容为**本次**扫描结果。
    抛出：无 —— 文件级错误收敛进响应体的 `errors`，不影响已在进行的对局。
    """
    return JSONResponse(_loader(request).scan().to_wire())


# 存活探针同时挂在根路径 `/healthz`（部署平台与反代通常按这个约定探测）。
root_router = APIRouter(tags=["ops"])


@root_router.get("/healthz")
async def healthz_root() -> JSONResponse:
    """存活探针。

    前置：无。
    后置：返回 `{"ok": true}`，HTTP 200；不触达 lobby / 房间，可在任意时刻调用。
    """
    return JSONResponse({"ok": True})
