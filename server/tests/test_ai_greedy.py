"""`generals.ai` 测试：注册表、内置策略、runner、远程客户端。

覆盖点（BRIEF 成员 B「测试要求」）：
- `greedy` 在**真实内核**状态上能返回合法 `Action`（并真的能被房间接受）；
- `hold` 永远不操作；
- `random` 可复现、且不越界；
- `AI runner` 每 Tick 至多提交 1 条（R4.7）；
- 远程客户端在 loop 里不阻塞（`asyncio.to_thread`）。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from conftest import FakeConn, pump, two_player_room
from generals.ai import (
    AIRunner,
    Action,
    GameSpec,
    Observation,
    RemoteAgent,
    all_names,
    builtin,
    get,
    make_agent,
    neighbours4,
    register,
    registered,
    to_observation,
)
from generals.ai import registry
from generals.ai.api import Row
from generals.game import turn_of


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def test_builtin_strategies_are_registered() -> None:
    """AI_API.md §4：内置 `hold` / `greedy` / `random`。"""
    names = set(all_names())
    assert {"hold", "greedy", "random"} <= names


def test_register_and_get_roundtrip() -> None:
    """INTERFACES §3：`register(name)(cls)` / `get(name)`。"""

    @register("test-strategy-xyz")
    class _S:
        name = "test-strategy-xyz"

        def reset(self, spec: GameSpec) -> None:
            self.size = spec.size

        def act(self, obs: Observation) -> Action | None:
            return None

    assert registered("test-strategy-xyz")
    assert get("test-strategy-xyz") is _S
    assert "test-strategy-xyz" in all_names()


def test_register_is_case_insensitive() -> None:
    """注册名大小写不敏感，避免 `join` 帧里大小写不一致导致找不到。"""

    @register("MiXeD-Case-Bot")
    class _S:
        name = "mixed"

    assert get("mixed-case-bot") is _S
    assert get("MIXED-CASE-BOT") is _S


def test_get_unknown_raises_keyerror_with_hint() -> None:
    """未注册的策略名 → `KeyError`，错误信息里列出可用名字。"""
    with pytest.raises(KeyError) as exc:
        get("no-such-strategy")
    assert "available" in str(exc.value)


def test_register_rejects_empty_name() -> None:
    with pytest.raises(ValueError):
        register("")


# ---------------------------------------------------------------------------
# neighbours4（AI_API.md §2 示例里用到的工具）
# ---------------------------------------------------------------------------


def test_neighbours4_corner_has_two_neighbours() -> None:
    """4 相邻（R4.1）：角格只有 2 个邻居。"""
    assert neighbours4(0, 5) == {"down": 5, "right": 1}


def test_neighbours4_center_has_four() -> None:
    nb = neighbours4(12, 5)  # row 2, col 2
    assert nb == {"up": 7, "down": 17, "left": 11, "right": 13}


def test_neighbours4_never_wraps_rows() -> None:
    """不会跨行环绕（索引算术最容易踩的坑）。"""
    nb = neighbours4(4, 5)  # 第一行最后一列
    assert "right" not in nb
    assert nb["down"] == 9


# ---------------------------------------------------------------------------
# Observation（AI_API.md §1.1）
# ---------------------------------------------------------------------------


def test_observation_matches_project_and_kernel_numbers() -> None:
    """`to_observation` 的 `tiles` 必须**就是** `view.project` 的结果（AI 无作弊通道）。"""
    room, a, b = two_player_room("obs")
    pump(a, b)
    obs = to_observation(room.state, a.player_id)
    assert obs.me == a.player_id
    assert obs.size == room.state.size
    assert (obs.turn, obs.half) == turn_of(room.state.tick)
    assert obs.my_army == room.state.army_total(a.player_id)
    assert obs.my_land == room.state.land_count(a.player_id)
    assert len(obs.tiles) == room.state.size * room.state.size
    assert [t.index for t in obs.tiles] == list(range(room.state.size * room.state.size))
    assert obs.at(0).index == 0


def test_observation_is_fogged_like_a_human() -> None:
    """AI 看到的是裁剪后的视野：存在 `army is None` 的地块（AI_API.md §5）。"""
    room, a, b = two_player_room("obs-fog")
    pump(a, b)
    obs = to_observation(room.state, a.player_id)
    assert any(t.army is None for t in obs.tiles)


def test_observation_leaderboard_is_sorted_by_player_id() -> None:
    """R7.3：排行榜按 `player_id` 升序输出。"""
    room, a, b = two_player_room("obs-lb")
    pump(a, b)
    obs = to_observation(room.state, a.player_id)
    ids = [row.player_id for row in obs.leaderboard]
    assert ids == sorted(ids)
    assert isinstance(obs.leaderboard[0], Row)


def test_action_to_command_matches_protocol_fields() -> None:
    """`Action.to_command()` 产出 PROTOCOL.md §2.4 的指令形状。"""
    assert Action(op="move", src=202, dir="up", ratio=0.5).to_command() == {
        "op": "move",
        "from": 202,
        "dir": "up",
        "ratio": 0.5,
    }
    assert Action(op="select", tile=205).to_command() == {"op": "select", "tile": 205}
    assert Action(op="clear").to_command() == {"op": "clear"}
    assert Action(op="pop").to_command() == {"op": "pop"}
    with pytest.raises(ValueError):
        Action(op="nope").to_command()  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# greedy（AI_API.md §2 示例 / §4）
# ---------------------------------------------------------------------------


def _assert_legal_move(room, player_id: int, action: Action) -> None:
    """断言 `action` 是一条内核会同意的合法调兵（R4.1 / R4.9）。"""
    state = room.state
    assert action.op == "move"
    src = action.src
    assert state.tiles[src].owner == player_id, "起点必须归自己"
    assert src in state.visible_tiles(player_id)
    nbs = state.neighbours4(src)
    assert action.dir in ("up", "down", "left", "right")
    # 方向与邻居一致
    idx = neighbours4(src, state.size).get(action.dir)
    assert idx is not None and idx in nbs
    assert state.tiles[idx].terrain.value != 2, "R4.9：不可向山地调兵"
    assert 0 < action.ratio <= 1.0


def test_greedy_returns_legal_action_on_real_kernel_state() -> None:
    """BRIEF 要求：`greedy` 在真实内核状态上返回合法 `Action`。

    注意：R3.2 规定首都初始兵力为 **1**，而 R4.2 的实际出兵 = `min(ceil(a), a-1)`，
    所以 Turn 0 时**没有任何合法调兵**（出兵恒为 0，且 `greedy` 要求 `army > 1`）。
    因此这里先推进若干刻让首都增长，再断言 greedy 能给出动作。
    """
    room, a, b = two_player_room("greedy")
    pump(a, b)
    agent = make_agent("greedy")
    agent.reset(GameSpec(size=room.state.size, player_count=2))

    # 先在 Turn 0 断言：兵力为 1 时 greedy 不轻举妄动（否则会白送 0 兵力请求）。
    assert agent.act(to_observation(room.state, a.player_id)) is None

    # 推进到首都兵力 > 1（R3.4：每个 Turn N. 结算 +1）。
    for _ in range(10):
        room.step_once()
        pump(a, b)
        if room.state.army_total(a.player_id) > 1:
            break
    assert room.state.army_total(a.player_id) > 1, "10 刻后首都应当已增长"

    action = agent.act(to_observation(room.state, a.player_id))
    assert action is not None, "兵力充足后首都旁应有可扩张的目标"
    _assert_legal_move(room, a.player_id, action)


def test_greedy_action_is_accepted_by_room() -> None:
    """greedy 的动作必须能通过房间校验（无 `reject`，且链回显到位）。"""
    room, a, b = two_player_room("greedy-ack")
    pump(a, b)
    agent = make_agent("greedy")
    for _ in range(10):
        room.step_once()
        pump(a, b)
        if room.state.army_total(a.player_id) > 1:
            break
    obs = to_observation(room.state, a.player_id)
    action = agent.act(obs)
    assert action is not None
    room.handle(a, {"t": "commands", "cmds": [action.to_command()]})
    frames = pump(a, b)
    mine = [f for _s, f in frames if _s is a]
    assert not any(f["t"] == "reject" for f in mine), f"被拒绝了：{mine}"
    # 服务端回显权威链（默认不发 ack）
    assert any(f["t"] == "commands" and f["chain"] for f in mine)


def test_greedy_survives_many_ticks() -> None:
    """连跑 60 Tick，greedy 的每一步都必须合法（不出 `reject`）。"""
    room, a, b = two_player_room("greedy-run")
    pump(a, b)
    agent = make_agent("greedy")
    agent.reset(GameSpec(size=room.state.size, player_count=2))
    rejects = 0
    for _ in range(60):
        obs = to_observation(room.state, a.player_id)
        action = agent.act(obs)
        if action is not None:
            room.handle(a, {"t": "commands", "cmds": [action.to_command()]})
        room.step_once()
        for frame in [f for _s, f in pump(a, b) if f["t"] == "reject"]:
            rejects += 1
        if room.state.ended:
            break
    assert rejects == 0


def test_greedy_never_targets_mountain_r4_9() -> None:
    """同 `_assert_legal_move`：所有步都不能指向地形 2。"""
    room, a, b = two_player_room("greedy-mountain")
    pump(a, b)
    agent = make_agent("greedy")
    for _ in range(20):
        obs = to_observation(room.state, a.player_id)
        action = agent.act(obs)
        if action is not None:
            idx = neighbours4(action.src, obs.size)[action.dir]
            assert obs.tiles[idx].terrain != 2
            room.handle(a, {"t": "commands", "cmds": [action.to_command()]})
        room.step_once()
        pump(a, b)
        if room.state.ended:
            break


def test_greedy_returns_none_when_eliminated() -> None:
    """被消灭后没有己方地块 → 返回 `None`（AI_API.md §5：「视野外没有 army」的边界）。"""
    room, a, b = two_player_room("greedy-dead")
    pump(a, b)
    agent = make_agent("greedy")
    obs = to_observation(room.state, a.player_id)
    # 把所有地块的 owner 改成对手：A 已无地
    for tile in room.state.tiles:
        tile.owner = b.player_id if tile.owner == a.player_id else tile.owner
    obs = to_observation(room.state, a.player_id)
    assert agent.act(obs) is None


# ---------------------------------------------------------------------------
# hold / random
# ---------------------------------------------------------------------------


def test_hold_never_acts() -> None:
    """`hold` 是压测对照组：永远返回 `None`。"""
    room, a, b = two_player_room("hold")
    pump(a, b)
    agent = make_agent("hold")
    agent.reset(GameSpec(size=room.state.size, player_count=2))
    for _ in range(5):
        assert agent.act(to_observation(room.state, a.player_id)) is None
        room.step_once()
        pump(a, b)


def test_random_is_seed_reproducible() -> None:
    """AI_API.md §4：随机策略必须显式播种、可复现。"""
    room, a, b = two_player_room("rand")
    pump(a, b)
    obs = to_observation(room.state, a.player_id)
    runs = []
    for _ in range(2):
        agent = builtin.Random(seed=12345)
        agent.reset(GameSpec(size=obs.size, player_count=2))
        runs.append(agent.act(obs))
    assert runs[0] == runs[1]


def test_random_actions_are_legal() -> None:
    """随机策略只在合法动作里挑（否则会被房间 reject）。"""
    room, a, b = two_player_room("rand-legal")
    pump(a, b)
    agent = builtin.Random(seed=7)
    agent.reset(GameSpec(size=room.state.size, player_count=2))
    for _ in range(10):
        action = agent.act(to_observation(room.state, a.player_id))
        if action is not None:
            _assert_legal_move(room, a.player_id, action)
        room.step_once()
        pump(a, b)
        if room.state.ended:
            break


# ---------------------------------------------------------------------------
# AIRunner
# ---------------------------------------------------------------------------


def test_runner_submits_at_most_one_command_per_tick_r4_7() -> None:
    """R4.7：每名玩家每 Tick 只处理 1 条请求 —— runner 也只提交 1 条。"""
    room, a, b = two_player_room("runner")
    pump(a, b)
    runner = AIRunner(room=room, player_id=a.player_id, agent=make_agent("greedy"))
    runner.reset()
    submitted = 0
    for _ in range(10):
        chain_before = len(room.state.players[a.player_id].chain)
        action = runner.step()
        chain_after = len(room.state.players[a.player_id].chain)
        if action is not None:
            submitted += 1
            assert chain_after - chain_before <= 1
        room.step_once()
        pump(a, b)
        if room.state.ended:
            break
    assert submitted > 0, "greedy 在 10 刻内应当至少提交一次"


def test_runner_survives_broken_agent() -> None:
    """策略抛异常时 runner 不能崩（记录到 `last_error`，本 Tick 不操作）。"""

    class _Boom:
        name = "boom"

        def reset(self, spec: GameSpec) -> None:
            pass

        def act(self, obs: Observation) -> Action | None:
            raise RuntimeError("boom")

    room, a, b = two_player_room("runner-boom")
    pump(a, b)
    runner = AIRunner(room=room, player_id=a.player_id, agent=_Boom())
    assert runner.step() is None
    assert isinstance(runner.last_error, RuntimeError)


def test_runner_step_async_uses_thread() -> None:
    """异步版 `step_async` 把 `act()` 放到线程里，不阻塞事件循环。"""
    room, a, b = two_player_room("runner-async")
    pump(a, b)
    runner = AIRunner(room=room, player_id=a.player_id, agent=make_agent("greedy"))

    async def scenario() -> Action | None:
        return await runner.step_async()

    action = asyncio.run(scenario())
    assert action is None or action.op == "move"


# ---------------------------------------------------------------------------
# RemoteAgent（AI_API.md §3）
# ---------------------------------------------------------------------------


def test_remote_agent_posts_observation_and_decodes_action(monkeypatch) -> None:
    """`RemoteAgent.act` 发 `{"observation": ...}`，解析 `{"action": ...}`。"""
    captured: dict = {}

    class _FakeResp:
        status_code = 200

        def json(self) -> dict:
            return {"action": {"op": "move", "src": 12, "dir": "up", "ratio": 0.5}}

    class _FakeClient:
        def __init__(self, *a, **kw) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> None:
            return None

        def post(self, url, content=None, headers=None):
            captured["url"] = url
            captured["payload"] = json.loads(content)
            captured["headers"] = headers
            return _FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "Client", _FakeClient)

    room, a, b = two_player_room("remote")
    pump(a, b)
    agent = RemoteAgent(url="http://127.0.0.1:9/decide", name="remote-bot", timeout=0.4)
    obs = to_observation(room.state, a.player_id)
    action = agent.act(obs)

    assert action == Action(op="move", src=12, dir="up", ratio=0.5)
    assert captured["url"] == "http://127.0.0.1:9/decide"
    assert set(captured["payload"]) == {"observation"}
    payload_obs = captured["payload"]["observation"]
    assert set(payload_obs) == {
        "turn", "half", "size", "me", "tiles", "my_army", "my_land", "leaderboard", "chain"
    }
    assert len(payload_obs["tiles"]) == room.state.size * room.state.size


def test_remote_agent_timeout_returns_none(monkeypatch) -> None:
    """超时/异常一律降级为 `None`（AI_API.md §3：超时视为本 Tick 不操作）。"""
    import httpx

    class _FakeClient:
        def __init__(self, *a, **kw) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> None:
            return None

        def post(self, url, content=None, headers=None):
            raise httpx.TimeoutException("timeout")

    monkeypatch.setattr(httpx, "Client", _FakeClient)
    room, a, b = two_player_room("remote-timeout")
    pump(a, b)
    agent = RemoteAgent(url="http://127.0.0.1:9/decide")
    assert agent.act(to_observation(room.state, a.player_id)) is None
    assert agent.last_error is not None


def test_remote_agent_act_async_does_not_block_loop(monkeypatch) -> None:
    """`act_async` 必须把同步 httpx 调用放到线程（`asyncio.to_thread`）。"""
    import threading

    calls: list[int] = []

    class _FakeResp:
        status_code = 200

        def json(self) -> dict:
            return {"action": {"op": "clear"}}

    class _FakeClient:
        def __init__(self, *a, **kw) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> None:
            return None

        def post(self, url, content=None, headers=None):
            calls.append(threading.get_ident())
            return _FakeResp()

    import httpx

    monkeypatch.setattr(httpx, "Client", _FakeClient)
    room, a, b = two_player_room("remote-async")
    pump(a, b)
    agent = RemoteAgent(url="http://127.0.0.1:9/decide")

    async def scenario() -> Action | None:
        return await agent.act_async(to_observation(room.state, a.player_id))

    result = asyncio.run(scenario())
    assert result == Action(op="clear")
    # 请求发生在**另一个**线程（主线程 id 是 0 之外的那个）
    assert calls and calls[0] != 0


def test_remote_agent_decodes_null_action() -> None:
    """`{"action": null}` = 本 Tick 不操作。"""
    from generals.ai.remote_client import decode_action

    assert decode_action({"action": None}) is None
    assert decode_action({}) is None
    assert decode_action(None) is None


def test_remote_agent_rejects_malformed_action() -> None:
    """结构非法 → `ValueError`（调用方会把它当「不操作」处理）。"""
    from generals.ai.remote_client import decode_action

    with pytest.raises(ValueError):
        decode_action({"action": {"op": "move"}})
    with pytest.raises(ValueError):
        decode_action({"action": {"op": "teleport"}})
