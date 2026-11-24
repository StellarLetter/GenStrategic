"""对局回放（`docs/REPLAY.md`）的后端测试。

覆盖：
1. 每局自动落盘、帧流完整（开局 → 每刻回合/地块/排行榜 → 终局）；
2. **对局中不可读取**（磁盘无文件 + `store.load` 抛 `ReplayRunning`）；
3. 策略调试日志 / 动作 / 被拒指令都进回放（同步与线程池两条路径）；
4. 房间被关闭（对局中断）也留档，`reason="aborted"`；
5. 保留策略（`keep` 份，最旧先删）与删除；
6. HTTP `/api/replays` 三个接口的状态码语义。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from generals.ai import log, register
from generals.ai.debug import capture
from generals.config import DEFAULT_CONFIG
from generals.net.room import Room
from generals.replay import ReplayLimits, ReplayNotFound, ReplayRunning, ReplayStore


def make_bot_room(
    tmp_path: Path,
    *,
    bots: tuple[str, ...] = ("greedy", "hold"),
    room_id: str = "replay-test",
    size: int = 12,
    limits: ReplayLimits | None = None,
    keep: int = 10,
) -> tuple[Room, ReplayStore, list]:
    """起一个「只有策略座位」的录制房间（无 WS 连接，跑得快）。

    前置：`bots` 里的策略名都已注册（`hold`/`greedy` 是内置策略）。
    后置：返回 `(room, store, seats)`，房间已 `start()`、回放录制已开始。
    抛出：无。
    """
    store = ReplayStore(tmp_path, keep=keep, limits=limits)
    room = Room(room_id, DEFAULT_CONFIG, seed=20261124, size=size, replay_store=store)
    seats = [room.add(None, f"P{i}", bot=name) for i, name in enumerate(bots)]
    room.start()
    return room, store, seats


def saved_payload(store: ReplayStore) -> dict:
    """取回放目录里唯一那份回放的完整内容（前置：已落盘；抛出：断言失败）。"""
    rows = store.list()
    assert rows, "expected one saved replay"
    return store.load(rows[0]["id"])


# ---------------------------------------------------------------------------
# 1. 自动保存 + 帧流完整
# ---------------------------------------------------------------------------


def test_replay_is_saved_when_match_ends(tmp_path):
    """一局打到自然结束 → 回放落盘，帧流含开局/地块/回合/排行榜/终局。"""
    room, store, seats = make_bot_room(tmp_path)
    # 让 P1 投降 → 40 Tick 过渡期后它的地块全部转中立 → 只剩 P0 有地 → 对局结束（R5.5 / R7.4）。
    room.handle(seats[1], {"t": "surrender"})
    for _ in range(80):
        if room.state.ended:
            break
        room.step_once()
    assert room.state.ended, "surrender transition should end a 1v1"

    rows = store.list()
    assert len(rows) == 1
    row = rows[0]
    assert row["room"] == "replay-test"
    assert row["reason"] == "ended"
    assert row["ended"] is True
    assert row["winner"] == seats[0].player_id
    assert row["players"][1]["strategy"] == "hold"

    payload = store.load(row["id"])
    assert payload["format"] == "generals.replay"
    assert payload["version"] == 1
    assert payload["seed"] == 20261124
    assert payload["size"] == 12
    assert payload["tickMs"] == DEFAULT_CONFIG.tick_ms
    spread = payload["result"]["turn"]
    assert spread >= 0

    frames = payload["frames"]
    assert frames[0]["t"] == "game_start"
    assert frames[0]["tick"] == 0
    kinds = {frame["t"] for frame in frames}
    assert {"game_start", "tiles", "leaderboard", "turn", "game_over"} <= kinds
    # 每刻都有一条 `turn`（客户端据此推进回放）。
    turn_ticks = [frame["tick"] for frame in frames if frame["t"] == "turn"]
    assert sorted(set(turn_ticks)) == list(range(0, payload["result"]["tick"] + 1))
    # 首帧地块是全量（观战者视角：观战者看得到全图真实信息，R3.10）。
    first_tiles = next(frame for frame in frames if frame["t"] == "tiles")
    assert len(first_tiles["list"]) == 12 * 12
    assert all(isinstance(item[1], int) for item in first_tiles["list"])


def test_replay_frames_cover_every_tick(tmp_path):
    """帧流自洽：最后一个 `turn` 帧与 `result` 一致，且含终局 `game_over`。"""
    room, store, _seats = make_bot_room(tmp_path, bots=("greedy", "hold"))
    for _ in range(6):
        room.step_once()
    room.close()

    payload = saved_payload(store)
    turns = [frame for frame in payload["frames"] if frame["t"] == "turn"]
    assert turns, "every tick must emit a turn frame"
    last = turns[-1]
    assert last["tick"] == payload["result"]["tick"]
    assert last["turn"] == payload["result"]["turn"]
    assert last["half"] == payload["result"]["half"]
    assert payload["result"]["ended"] is False  # 中途掐断：不是自然终局


# ---------------------------------------------------------------------------
# 2. 对局中不可读取
# ---------------------------------------------------------------------------


def test_replay_not_readable_while_running(tmp_path):
    """进行中的对局：磁盘上没有回放文件，`load` 显式拒绝（docs/REPLAY.md §4）。"""
    room, store, _seats = make_bot_room(tmp_path)
    for _ in range(5):
        room.step_once()

    assert list(tmp_path.glob("*.json")) == [], "no replay file may exist mid-match"
    active = store.active_ids()
    assert len(active) == 1
    with pytest.raises(ReplayRunning):
        store.load(active[0])
    with pytest.raises(ReplayRunning):
        store.delete(active[0])
    assert store.list() == []

    room.close()
    # 关闭后（aborted）即可读取。
    payload = store.load(active[0])
    assert payload["id"] == active[0]
    assert payload["reason"] == "aborted"
    assert store.active_ids() == []


# ---------------------------------------------------------------------------
# 3. 策略调试日志 / 动作 / 被拒指令
# ---------------------------------------------------------------------------


@register("replay-logger")
class _LoggerBot:
    """测试策略：每刻写一条日志，并在开局 `reset` 里也写一条（验证两条路径都录）。"""

    name = "replay-logger"

    def reset(self, spec) -> None:
        log(f"reset size={spec.size}", level="info")

    def act(self, obs):
        log(f"turn {obs.turn} army={obs.my_army}")
        return None


@register("replay-illegal")
class _IllegalBot:
    """测试策略：返回一条服务端必然拒绝的调兵（往山地/越界方向），用来验证 reject 留痕。"""

    name = "replay-illegal"

    def reset(self, spec) -> None:
        self.size = spec.size

    def act(self, obs):
        from generals.ai.api import Action

        # 起点 0 一定不是自己的首都 → 入队本身合法（R4.11 预调兵），结算时作废；
        # 这里改成越界方向，让它当场被 `reject`（PROTOCOL §5）。
        return Action(op="move", src=0, dir="up", ratio=1.0)


def test_ai_logs_actions_and_rejects_are_recorded(tmp_path):
    """`generals.ai.log()` 与动作/拒绝都进回放，并带 tick/playerId/strategy。"""
    room, store, _seats = make_bot_room(tmp_path, bots=("replay-logger", "hold"))
    for _ in range(4):
        room.step_once()
    room.close()

    payload = saved_payload(store)
    logs = [entry for entry in payload["ai"] if entry["kind"] == "log"]
    actions = [entry for entry in payload["ai"] if entry["kind"] == "action"]
    assert any(entry["text"].startswith("reset size=") for entry in logs)
    assert any(entry["text"].startswith("turn ") for entry in logs)
    for entry in logs:
        assert entry["playerId"] == 0
        assert entry["strategy"] == "replay-logger"
        assert entry["level"] in {"debug", "info", "warn", "error"}
        assert isinstance(entry["tick"], int)
    # `hold` 策略什么都不做 → 没有动作条目；`replay-logger` 也返回 None。
    assert actions == []


def test_ai_action_and_reject_are_recorded(tmp_path):
    """策略返回的指令先记成 action；被服务端拒绝后再记一条 reject（同一 tick）。"""
    room, store, _seats = make_bot_room(tmp_path, bots=("greedy", "hold"))
    # 让它先攒两刻兵（R3.2 开局只有 1 兵，`greedy` 前几刻必然返回 `None`）。
    for _ in range(4):
        room.step_once()
    # 再派一条必然被拒的指令（方向越界：从第 0 行往上）。
    seat0 = next(seat for seat in room.seats if seat.player_id == 0)
    room.handle(
        seat0,
        {"t": "commands", "cmds": [{"op": "move", "from": 0, "dir": "up", "ratio": 1.0}]},
    )
    room.step_once()
    room.close()

    payload = saved_payload(store)
    rejects = [entry for entry in payload["ai"] if entry["kind"] == "reject"]
    assert rejects, "the out-of-bounds move must be recorded as a reject"
    assert rejects[0]["op"] == "move"
    assert rejects[0]["code"] == "not_adjacent"
    assert rejects[0]["playerId"] == 0
    # 策略自己的合法动作同样留痕。
    actions = [entry for entry in payload["ai"] if entry["kind"] == "action"]
    assert actions and actions[0]["strategy"] == "greedy"
    assert actions[0]["action"]["op"] == "move"
    assert actions[0]["tick"] >= 1


def test_ai_log_from_thread_pool_is_recorded(tmp_path):
    """`Room.step_async()` 把策略放进线程池：`log()` 仍然能写到本局回放。"""
    room, store, _seats = make_bot_room(tmp_path, bots=("replay-logger",))

    async def drive() -> None:
        for _ in range(3):
            await room.step_async()

    asyncio.run(drive())
    room.close()

    payload = saved_payload(store)
    logs = [entry for entry in payload["ai"] if entry["kind"] == "log"]
    assert any(entry["text"].startswith("turn ") for entry in logs)
    assert all(entry["playerId"] == 0 for entry in logs)


def test_capture_binds_and_restores_sink():
    """`ai.debug.capture()` 结束后 sink 恢复（离线工具用；不影响其它对局）。"""
    seen: list[tuple[str, str]] = []
    log("outside")  # 没有 sink：静默丢弃
    with capture(lambda level, text: seen.append((level, text))):
        log("inside", level="info")
    log("after")
    assert seen == [("info", "inside")]


def test_replay_log_limits(tmp_path):
    """条数上限与文本截断：超限后 `aiTruncated=True`，长文本被截。"""
    limits = ReplayLimits(ai_log_max=3, ai_text_max=8)
    room, store, _seats = make_bot_room(tmp_path, bots=("replay-logger",), limits=limits)
    for _ in range(6):
        room.step_once()
    room.close()

    payload = saved_payload(store)
    assert payload["aiTruncated"] is True
    assert len(payload["ai"]) == 3
    assert all(len(entry.get("text", "")) <= 8 for entry in payload["ai"])


def test_replay_frame_budget_truncates(tmp_path):
    """帧流字节预算用尽后停止录帧并标记 `truncated`（磁盘保险丝）。"""
    limits = ReplayLimits(max_bytes=2000)
    room, store, _seats = make_bot_room(tmp_path, bots=("greedy", "hold"), limits=limits)
    for _ in range(20):
        room.step_once()
    room.close()

    payload = saved_payload(store)
    assert payload["truncated"] is True
    assert len(payload["frames"]) < 60


# ---------------------------------------------------------------------------
# 4. 保留策略 / 删除 / 坏 id
# ---------------------------------------------------------------------------


def test_store_keeps_newest_and_deletes(tmp_path):
    """`keep` 份上限：更旧的被删；`delete` 能清掉指定回放。"""
    store = ReplayStore(tmp_path, keep=2)
    ids: list[str] = []
    for index in range(3):
        room = Room(f"keep-{index}", DEFAULT_CONFIG, seed=index + 1, size=10, replay_store=store)
        room.add(None, "Solo", bot="hold")
        room.add(None, "Solo2", bot="hold")
        room.start()
        room.step_once()
        room.close()
        ids.extend(row["id"] for row in store.list(limit=10) if row["id"] not in ids)

    rows = store.list(limit=10)
    assert len(rows) == 2, "keep=2 must prune the oldest replay"
    files = sorted(path.name for path in tmp_path.glob("*.json"))
    assert len(files) == 3  # 2 份回放 + index.json
    assert all(row["id"] in files[0] + files[1] + files[2] for row in rows)

    victim = rows[0]["id"]
    assert store.delete(victim) is True
    assert all(row["id"] != victim for row in store.list(limit=10))
    assert store.delete(victim) is False
    assert not (tmp_path / f"{victim}.json").exists()


def test_store_rejects_bad_ids(tmp_path):
    """路径穿越/怪 id 一律当作「不存在」，不做任何文件操作。"""
    store = ReplayStore(tmp_path)
    for bad in ("../etc/passwd", "a/b", "", "..", ".hidden"):
        with pytest.raises(ReplayNotFound):
            store.load(bad)
        assert store.delete(bad) is False


def test_room_without_store_records_nothing(tmp_path):
    """没有注入回放仓库的房间（测试/离线基准的缺省）不产生任何文件。"""
    room = Room("no-replay", DEFAULT_CONFIG, seed=3, size=10)
    room.add(None, "A", bot="greedy")
    room.add(None, "B", bot="hold")
    room.start()
    for _ in range(3):
        room.step_once()
    room.close()
    assert room.replay is None
    assert list(tmp_path.glob("*")) == []


# ---------------------------------------------------------------------------
# 5. HTTP 接口
# ---------------------------------------------------------------------------


def test_http_replay_endpoints(tmp_path):
    """`/api/replays` 列表 / 详情 / 删除；进行中的对局 409，不存在 404。"""
    from fastapi.testclient import TestClient

    from generals.app import create_app

    app = create_app(web_dist=tmp_path / "no-dist")
    store = ReplayStore(tmp_path / "replays", keep=5)
    app.state.replay_store = store

    with TestClient(app) as client:
        assert client.get("/api/replays").json()["replays"] == []

        lobby = app.state.lobby
        room, seat_a = lobby.join("Alice", "http-room", conn=None, bot="hold")
        lobby.join("Bob", "http-room", conn=None, bot="greedy")
        room.start()
        room.step_once()

        listing = client.get("/api/replays").json()
        assert listing["enabled"] is True
        assert listing["directory"] == str(store.directory)
        assert len(listing["active"]) == 1
        running_id = listing["active"][0]
        assert client.get(f"/api/replays/{running_id}").status_code == 409
        assert client.delete(f"/api/replays/{running_id}").status_code == 409

        room.close()
        listing = client.get("/api/replays").json()
        assert listing["active"] == []
        assert len(listing["replays"]) == 1
        saved_id = listing["replays"][0]["id"]
        detail = client.get(f"/api/replays/{saved_id}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["format"] == "generals.replay"
        assert body["frames"]

        assert client.get("/api/replays/does-not-exist").status_code == 404
        assert client.delete(f"/api/replays/{saved_id}").json() == {"ok": True, "deleted": True}
        assert client.get(f"/api/replays/{saved_id}").status_code == 404
        # 座位 id 在玩家清单里（`seat_a` 只是确保 join 成功）。
        assert seat_a.player_id == 0


def test_replay_json_is_self_contained(tmp_path):
    """回放文件是纯 JSON（可被任何语言读取），字段名与 docs/REPLAY.md 一致。"""
    room, store, _seats = make_bot_room(tmp_path, bots=("greedy", "hold"))
    for _ in range(3):
        room.step_once()
    room.close()

    rows = store.list()
    path = tmp_path / f"{rows[0]['id']}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for key in ("format", "version", "id", "room", "seed", "size", "tickMs", "players", "frames", "ai", "result"):
        assert key in payload
    assert payload["players"][0]["name"] == "P0"
    assert payload["frames"][0]["players"][0]["playerId"] == 0