"""transport 层：FastAPI 适配（HTTP / WebSocket）。

只依赖 `net` 的公开 API；不含业务规则。装配发生在 `generals.app`。
"""

from __future__ import annotations

from . import http_routes, ws_routes

__all__ = ["http_routes", "ws_routes"]
