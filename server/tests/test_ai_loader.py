"""策略自动挂载（`ai.loader`）与座位接管（`bot` / `autopilot`）测试。

覆盖点（docs/AI_API.md §3 / §6）：
- `ai/` 目录下的策略文件在扫描后自动出现在注册表与清单里（**无需**改服务端代码）；
- 单个坏文件只记一条错误，不影响其它策略与目录扫描；
- 删除/改名策略文件后 `scan()` 会把策略摘掉（热重载）；
- `Room` 真的在驱动 `bot` 座位与 `autopilot` 托管座位（不是只占个位）；
- `autopilot` 帧可以在**开局前**指定接管，且广播 `autopilot` 回执；
- 托管时可随时收回操作权（`strategy: null`），策略实例被释放。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from conftest import FakeConn, make_room, pump
from generals.ai import StrategyLoader, all_names, registered, registry
from generals.ai.loader import StrategyLoader as _Loader
from generals.net import protocol

#: 一个「立刻向右扩张」的最小策略（测试专用，避免依赖示例策略的行为）。
_BOT_SOURCE = '''
from generals.ai import register
from generals.ai.api import Action

@register("{name}")
class Bot:
    name = "{name}"
    label = "测试机器人"
    description = "每刻把第一个 4 相邻方向的兵力全推出去。"

    def reset(self, spec):
        self.size = spec.size
        self.calls = 0

    def act(self, obs):
        self.calls += 1
        for tile in obs.tiles:
            if tile.owner != obs.me or tile.army is None or tile.army <= 1:
                continue
            from generals.ai.api import neighbours4
            for direction in ("right", "down", "left", "up"):
                nb = neighbours4(tile.index, obs.size).get(direction)
                if nb is None:
                    continue
                target = obs.tiles[nb]
                if target.owner == obs.me or target.terrain == 2:
                    continue
                return Action(op="move", src=tile.index, dir=direction, ratio=1.0)
        return None
'''

_BROKEN_SOURCE = "def oops(:\n    pass\n"


def _write(path: Path, text: str) -> None:
    """写 UTF-8（无 BOM）文本文件。"""
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def ai_dir(tmp_path: Path) -> Path:
    """临时策略目录（每个测试独立，避免污染仓库里的 `ai/`）。"""
    directory = tmp_path / "ai"
    directory.mkdir()
    return directory


def _cleanup(names: tuple[str, ...]) -> None:
    """测试后摘掉临时策略，避免污染其它用例。"""
    for name in names:
        registry.unregister(name)


# ---------------------------------------------------------------------------
# loader：自动挂载
# ---------------------------------------------------------------------------


def test_loader_mounts_every_strategy_file(ai_dir: Path) -> None:
    """`ai/` 下的每个文件都会被加载，并在清单里能看到来源文件。"""
    _write(ai_dir / "alpha.py", _BOT_SOURCE.format(name="mount-alpha"))
    _write(ai_dir / "beta.py", _BOT_SOURCE.format(name="mount-beta"))
    try:
        report = StrategyLoader(ai_dir).scan()
        names = {s.name: s for s in report.strategies}
        assert "mount-alpha" in names and "mount-beta" in names
        assert names["mount-alpha"].source == "alpha.py"
        assert names["mount-beta"].source == "beta.py"
        assert registered("mount-alpha") and registered("mount-beta")
        assert report.errors == []
        assert report.files == ["alpha.py", "beta.py"]
    finally:
        _cleanup(("mount-alpha", "mount-beta"))


def test_loader_keeps_builtin_strategies(ai_dir: Path) -> None:
    """内置策略始终可见，且标记为 `builtin`（即使目录为空）。"""
    report = StrategyLoader(ai_dir).scan()
    by_name = {s.name: s for s in report.strategies}
    assert {"hold", "greedy", "random"} <= set(by_name)
    assert all(by_name[n].builtin for n in ("hold", "greedy", "random"))


def test_loader_isolates_broken_file(ai_dir: Path) -> None:
    """一个语法错的文件只记一条错误，不影响同目录的其它策略与服务器。"""
    _write(ai_dir / "broken.py", _BROKEN_SOURCE)
    _write(ai_dir / "good.py", _BOT_SOURCE.format(name="mount-good"))
    try:
        report = StrategyLoader(ai_dir).scan()
        assert registered("mount-good")
        assert len(report.errors) == 1
        assert report.errors[0]["file"] == "broken.py"
        assert "SyntaxError" in report.errors[0]["error"]
    finally:
        _cleanup(("mount-good",))


def test_loader_unmounts_removed_file(ai_dir: Path) -> None:
    """删掉策略文件后重新扫描，策略会从注册表与清单里消失（热重载）。"""
    path = ai_dir / "temp.py"
    _write(path, _BOT_SOURCE.format(name="mount-temp"))
    loader = StrategyLoader(ai_dir)
    try:
        loader.scan()
        assert registered("mount-temp")

        path.unlink()
        report = loader.scan()
        assert not registered("mount-temp")
        assert "mount-temp" not in {s.name for s in report.strategies}
    finally:
        _cleanup(("mount-temp",))


def test_loader_picks_up_edited_file(ai_dir: Path) -> None:
    """改动文件后重新扫描会用**新代码**（不被 `sys.modules` 缓存挡住）。"""
    path = ai_dir / "edit.py"
    _write(path, _BOT_SOURCE.format(name="mount-edit"))
    loader = StrategyLoader(ai_dir)
    try:
        loader.scan()
        first = registry.get("mount-edit")

        _write(path, _BOT_SOURCE.format(name="mount-edit") + "\n# 改一行\n")
        loader.scan()
        second = registry.get("mount-edit")
        assert second is not first, "重载必须替换类对象，而不是复用缓存模块"
    finally:
        _cleanup(("mount-edit",))


def test_loader_skips_underscore_files(ai_dir: Path) -> None:
    """`_` 开头与 `__init__.py` 不当作策略文件（留给私有草稿/包声明）。"""
    _write(ai_dir / "_draft.py", _BOT_SOURCE.format(name="mount-private"))
    _write(ai_dir / "__init__.py", "")
    try:
        report = StrategyLoader(ai_dir).scan()
        assert report.files == []
        assert not registered("mount-private")
    finally:
        _cleanup(("mount-private",))


def test_loader_missing_directory_is_harmless(tmp_path: Path) -> None:
    """目录不存在时扫描返回空清单而不抛（部署未带 `ai/` 也不该崩）。"""
    report = StrategyLoader(tmp_path / "not-there").scan()
    assert report.files == []
    assert report.errors == []
    assert {"hold", "greedy", "random"} <= {s.name for s in report.strategies}


def test_repository_ai_dir_loads_cleanly() -> None:
    """仓库自带的 `ai/` 目录必须能无损加载（示例策略即手册里的模板）。"""
    loader = _Loader()
    report = loader.scan()
    assert report.errors == [], f"仓库 ai/ 目录有加载失败的文件：{report.errors}"
    assert "sample-greedy" in all_names()


# ---------------------------------------------------------------------------
# Room：真的驱动座位
# ---------------------------------------------------------------------------


def test_bot_seat_is_actually_driven() -> None:
    """`join(bot=...)` 的座位会被真正驱动：地块数增长，而不是干坐着。"""
    room = make_room("bot-driven")
    seat = room.add(None, "Bot", bot="greedy")
    room.start()
    for _ in range(30):
        room.step_once()
    assert room.state is not None
    assert room.state.land_count(seat.player_id) > 1


def test_autopilot_can_be_set_before_start() -> None:
    """**开局前**指定接管策略：开局第一刻起就由策略操作。"""
    room = make_room("auto-before")
    human = room.add(FakeConn(), "Alice")
    other = room.add(FakeConn(), "Bob")
    room.set_autopilot(human, "greedy")
    assert human.autopilot == "greedy"

    room.start()
    for _ in range(30):
        room.step_once()
        pump(human, other)
    assert room.state is not None
    assert room.state.land_count(human.player_id) > 1
    # 对照：没被托管的 Bob 一步不动（地块数仍是首都 1 格）。
    assert room.state.land_count(other.player_id) == 1


def test_autopilot_broadcasts_receipt_to_everyone() -> None:
    """托管回执广播给全房间（观战者也能看出谁在托管，PROTOCOL §3.11）。"""
    room = make_room("auto-receipt")
    a = room.add(FakeConn(), "Alice")
    b = room.add(FakeConn(), "Bob")
    watcher = room.add(FakeConn(), "Watcher", spectate=True)
    room.start()
    pump(a, b, watcher)

    room.set_autopilot(a, "greedy")
    for seat in (a, b, watcher):
        frames = [f for _s, f in pump(seat) if f["t"] == "autopilot"]
        assert frames and frames[-1] == {
            "t": "autopilot",
            "playerId": a.player_id,
            "strategy": "greedy",
        }


def test_autopilot_can_be_released() -> None:
    """取消托管后操作权立刻回到真人，策略实例被释放。"""
    room = make_room("auto-release")
    a = room.add(FakeConn(), "Alice")
    room.start()
    room.set_autopilot(a, "greedy")
    assert room._runners, "托管后应存在 runner"

    room.set_autopilot(a, None)
    assert a.autopilot is None
    assert room._runners == {}
    room.step_once()
    pump(a)
    assert room.state is not None
    assert room.state.land_count(a.player_id) == 1, "取消托管后不应再自动扩张"


def test_autopilot_rejects_unknown_strategy() -> None:
    """未注册的策略名 → `bad_frame`（不回退到静默忽略）。"""
    room = make_room("auto-unknown")
    a = room.add(FakeConn(), "Alice")
    room.start()
    with pytest.raises(protocol.ProtocolError) as exc:
        room.set_autopilot(a, "no-such-strategy")
    assert exc.value.code == "bad_frame"
    assert a.autopilot is None


def test_autopilot_rejects_spectator() -> None:
    """观战者没有座位，不能被托管。"""
    room = make_room("auto-spec")
    room.add(FakeConn(), "Alice")
    watcher = room.add(FakeConn(), "Watcher", spectate=True)
    room.start()
    with pytest.raises(protocol.ProtocolError) as exc:
        room.set_autopilot(watcher, "greedy")
    assert exc.value.code == "not_your_turn"


def test_autopilot_frame_sets_strategy() -> None:
    """`autopilot` 上行帧走 `Room.handle`（与其它指令同一条路径）。"""
    room = make_room("auto-frame")
    a = room.add(FakeConn(), "Alice")
    room.start()
    room.handle(a, {"t": "autopilot", "strategy": "greedy"})
    assert a.autopilot == "greedy"

    room.handle(a, {"t": "autopilot", "strategy": None})
    assert a.autopilot is None


def test_autopilot_frame_with_unknown_strategy_is_rejected() -> None:
    """帧里写未注册策略 → 抛 `bad_frame`（由 `Session` 转成 `reject`）。"""
    room = make_room("auto-frame-bad")
    a = room.add(FakeConn(), "Alice")
    room.start()
    with pytest.raises(protocol.ProtocolError) as exc:
        room.handle(a, {"t": "autopilot", "strategy": "no-such-strategy"})
    assert exc.value.code == "bad_frame"


def test_validate_autopilot_normalizes() -> None:
    """`validate_autopilot`：空串/缺省 = 取消托管；非字符串 → `bad_frame`。"""
    assert protocol.validate_autopilot({"t": "autopilot"}) == {"strategy": None}
    assert protocol.validate_autopilot({"t": "autopilot", "strategy": "  "}) == {"strategy": None}
    assert protocol.validate_autopilot({"t": "autopilot", "strategy": " greedy "}) == {
        "strategy": "greedy"
    }
    with pytest.raises(protocol.ProtocolError):
        protocol.validate_autopilot({"t": "autopilot", "strategy": 3})


def test_bot_strategy_can_be_swapped() -> None:
    """机器人座位改投别的策略会**替换**策略（不是叠加两个策略抢操作）。"""
    room = make_room("bot-swap")
    bot = room.add(None, "Bot", bot="hold")
    room.start()
    for _ in range(10):
        room.step_once()
    assert room.state is not None
    assert room.state.land_count(bot.player_id) == 1, "hold 不操作"

    room.set_autopilot(bot, "greedy")
    for _ in range(30):
        room.step_once()
    assert room.state.land_count(bot.player_id) > 1
    assert len(room._runners) == 1


def test_failed_strategy_construction_does_not_break_room() -> None:
    """策略实例化失败时该座位退化为不操作，房间照常推进（错误可查）。"""

    @registry.register("broken-ctor")
    class _Boom:
        name = "broken-ctor"

        def __init__(self) -> None:
            raise RuntimeError("ctor boom")

        def reset(self, spec) -> None:  # pragma: no cover - 构造就会失败
            pass

        def act(self, obs):  # pragma: no cover - 构造就会失败
            return None

    try:
        room = make_room("bot-broken")
        bot = room.add(None, "Bot", bot="broken-ctor")
        other = room.add(FakeConn(), "Alice")
        room.start()
        for _ in range(5):
            room.step_once()
            pump(other)
        assert room.state is not None and room.state.tick == 5
        errors = [row for row in room.strategy_state() if row["seat"] == bot.seq]
        assert errors and "boom" in (errors[0]["error"] or "")
    finally:
        registry.unregister("broken-ctor")


def test_step_async_steps_strategy_exactly_once() -> None:
    """`step_async()` 每刻只让策略决策一次（与 `step_once()` 等价）。"""
    calls: list[int] = []

    @registry.register("count-steps")
    class _Counter:
        name = "count-steps"

        def reset(self, spec) -> None:
            calls.clear()

        def act(self, obs):
            calls.append(obs.turn)
            return None

    try:
        room = make_room("async-once")
        room.add(None, "Bot", bot="count-steps")
        room.start()

        async def scenario() -> None:
            for _ in range(4):
                await room.step_async()

        asyncio.run(scenario())
        assert len(calls) == 4, f"每刻只该决策一次，实际 {len(calls)} 次"
        assert room.state is not None and room.state.tick == 4
    finally:
        registry.unregister("count-steps")


def test_strategy_state_lists_seats() -> None:
    """`strategy_state()` 给出各座位的 bot/autopilot/错误（供排错与 `/api/ai/state`）。"""
    room = make_room("state-rows")
    a = room.add(FakeConn(), "Alice")
    bot = room.add(None, "Bot", bot="hold")
    room.start()
    room.set_autopilot(a, "greedy")
    rows = {row["seat"]: row for row in room.strategy_state()}
    assert rows[a.seq]["autopilot"] == "greedy"
    assert rows[bot.seq]["bot"] == "hold"
    assert all(row["error"] is None for row in rows.values())


def test_room_close_releases_strategies() -> None:
    """关闭房间时释放所有策略实例（远程策略的连接池等）。"""
    room = make_room("close-runners")
    room.add(None, "Bot", bot="greedy")
    room.start()
    assert room._runners
    room.close()
    assert room._runners == {}


def test_autopilot_receipt_is_json_serializable() -> None:
    """回执帧必须能直接 `json.dumps`（否则广播会抛 TypeError）。"""
    frame = protocol.encode_autopilot(3, "greedy")
    assert json.loads(json.dumps(frame)) == {
        "t": "autopilot",
        "playerId": 3,
        "strategy": "greedy",
    }
    assert protocol.encode_autopilot(3, None)["strategy"] is None
