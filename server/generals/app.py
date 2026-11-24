"""应用装配：唯一组装点（AGENT.md §3 / docs/ARCHITECTURE.md §2）。

职责：创建 `Lobby`、挂载 HTTP / WS 路由、静态托管前端构建产物。
**本文件是唯一允许同时 import `net` 与 `transport` 的地方**；它不含游戏逻辑。

依赖方向（ARCHITECTURE.md §3）：
    app ──► transport ──► net ──► game
                    └──► ai ──► game

运行：
    uvicorn generals.app:app --app-dir server --port 8000
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import DEFAULT_CONFIG, GameConfig
from .ai.loader import StrategyLoader
from .net.lobby import Lobby
from .transport import http_routes, ws_routes

__all__ = ["app", "create_app"]

#: 前端构建产物的默认位置（`web/dist`）。可用环境变量 `GENERALS_WEB_DIST` 覆盖。
_DEFAULT_WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"

def create_app(config: GameConfig | None = None, web_dist: Path | None = None) -> FastAPI:
    """构造 FastAPI 应用。

    前置：无。
    后置：返回已装配好的应用；`app.state.lobby` 是唯一的 `Lobby` 实例，
           `app.state.strategy_loader` 是唯一的 `ai.loader.StrategyLoader`（已扫描完策略目录）。
    抛出：无（静态目录缺失时只登记 API 与 WS 路由，便于纯后端测试）。
    """
    cfg = config or DEFAULT_CONFIG
    dist = Path(web_dist) if web_dist is not None else _DEFAULT_WEB_DIST

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.lobby = Lobby(cfg)
        # 策略目录在启动时自动挂载（AI_API.md §3）：`ai/` 下的每个文件都会进注册表。
        app.state.strategy_loader.scan()
        try:
            yield
        finally:
            for room in app.state.lobby.rooms():
                room.close()

    app = FastAPI(title="generals", version="1.0.0", lifespan=lifespan)
    app.state.config = cfg
    app.state.web_dist = dist
    app.state.strategy_loader = StrategyLoader()

    app.include_router(http_routes.router)
    app.include_router(http_routes.root_router)
    app.include_router(ws_routes.router)

    if dist.is_dir() and (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
        for sub in ("fonts", "img"):
            candidate = dist / sub
            if candidate.is_dir():
                app.mount(f"/{sub}", StaticFiles(directory=candidate), name=sub)

        @app.get("/", response_model=None)
        async def index() -> FileResponse:
            return FileResponse(dist / "index.html")

        @app.get("/waiting.html", response_model=None)
        async def waiting() -> FileResponse:
            return FileResponse(dist / "waiting.html")

    return app


app = create_app()
