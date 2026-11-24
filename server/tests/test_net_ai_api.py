"""HTTP 层：策略清单与热重载接口（docs/AI_API.md §3 / PROTOCOL.md §4）。

覆盖点：
- `GET /api/ai/strategies` 返回内置 + `ai/` 目录策略，字段与前端约定一致；
- `POST /api/ai/reload` 重新扫描后能发现新增文件、摘掉删除的文件；
- 坏文件只出现在 `errors` 里，接口本身仍 200。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from generals.ai import registry
from generals.app import create_app


BOT_SOURCE = '''
from generals.ai import register

@register("{name}")
class Bot:
    name = "{name}"
    label = "HTTP 测试策略"
    description = "只为接口测试而存在。"

    def reset(self, spec):
        self.size = spec.size

    def act(self, obs):
        return None
'''


def _app_with(ai_dir: Path):
    """构造一个把策略目录指向 `ai_dir` 的应用（不触碰仓库里的 `ai/`）。"""
    app = create_app()
    # `create_app` 在 lifespan 里扫描；这里把 loader 换成本次测试的目录再手动扫描。
    from generals.ai import StrategyLoader

    app.state.strategy_loader = StrategyLoader(ai_dir)
    return app


def test_strategies_endpoint_lists_builtins(tmp_path: Path) -> None:
    """空目录也必须有内置策略（Interface 稳定，前端下拉框不会空着）。"""
    directory = tmp_path / "ai"
    directory.mkdir()
    with TestClient(_app_with(directory)) as client:
        res = client.get("/api/ai/strategies")
    assert res.status_code == 200
    body = res.json()
    assert body["directory"] == str(directory)
    names = {row["name"] for row in body["strategies"]}
    assert {"hold", "greedy", "random"} <= names
    row = next(r for r in body["strategies"] if r["name"] == "greedy")
    assert set(row) == {"name", "label", "description", "source", "builtin"}
    assert row["builtin"] is True
    assert body["errors"] == []


def test_reload_discovers_new_file(tmp_path: Path) -> None:
    """新增策略文件后 `POST /api/ai/reload` 立刻生效（不必重启服务）。"""
    directory = tmp_path / "ai"
    directory.mkdir()
    try:
        with TestClient(_app_with(directory)) as client:
            first = client.get("/api/ai/strategies").json()
            assert "http-new" not in {r["name"] for r in first["strategies"]}

            (directory / "new.py").write_text(BOT_SOURCE.format(name="http-new"), encoding="utf-8")
            res = client.post("/api/ai/reload")
            assert res.status_code == 200
            body = res.json()
            names = {r["name"] for r in body["strategies"]}
            assert "http-new" in names
            assert body["files"] == ["new.py"]
            registered = next(r for r in body["strategies"] if r["name"] == "http-new")
            assert registered["source"] == "new.py"
            assert registered["label"] == "HTTP 测试策略"
    finally:
        registry.unregister("http-new")


def test_reload_reports_broken_file_without_failing(tmp_path: Path) -> None:
    """坏文件只进 `errors`，接口仍 200，其它策略照常可用。"""
    directory = tmp_path / "ai"
    directory.mkdir()
    (directory / "bad.py").write_text("def oops(:\n", encoding="utf-8")
    try:
        with TestClient(_app_with(directory)) as client:
            res = client.post("/api/ai/reload")
        assert res.status_code == 200
        body = res.json()
        assert len(body["errors"]) == 1
        assert body["errors"][0]["file"] == "bad.py"
        assert "SyntaxError" in body["errors"][0]["error"]
    finally:
        pass


def test_strategies_endpoint_is_json_serializable_with_unicode(tmp_path: Path) -> None:
    """中文 `label`/`description` 必须原样返回（前端下拉框直接显示）。"""
    directory = tmp_path / "ai"
    directory.mkdir()
    with TestClient(_app_with(directory)) as client:
        body = client.get("/api/ai/strategies").json()
    labels = {r["name"]: r["label"] for r in body["strategies"]}
    assert "贪心" in labels["greedy"]


def test_autopilot_over_websocket(tmp_path: Path, ) -> None:
    """端到端：WS 发 `autopilot` 帧 → 收到广播的 `autopilot` 回执帧。"""
    directory = tmp_path / "ai"
    directory.mkdir()
    with TestClient(_app_with(directory)) as client:
        with client.websocket_connect("/ws") as ws:
            ws.send_text('{"t":"join","name":"Alice","room":"ws-auto"}')
            # 依次读出 joined 若干帧，直到拿到 joined。
            hello = ws.receive_json()
            while hello.get("t") != "joined":
                hello = ws.receive_json()
            ws.send_text('{"t":"autopilot","strategy":"greedy"}')
            frame = ws.receive_json()
            for _ in range(6):
                if frame.get("t") == "autopilot":
                    break
                frame = ws.receive_json()
            assert frame == {
                "t": "autopilot",
                "playerId": hello["playerId"],
                "strategy": "greedy",
            }
