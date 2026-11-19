# 内核测试说明（kernel 组成员维护）

本目录下的 `test_kernel_*.py` 与 `helpers_kernel.py` 由「游戏内核」成员负责，
覆盖 `server/generals/game/**`（纯内核）与 `server/generals/config.py`。
其它成员的测试（`test_net_*` / `test_ai_*`）不在本文件范围内。

> 规则编号**一律**引用 `docs/RULES.md` 的 `R<n>.<m>`；旧的「附录 A `C-xx`」编号已废止。

## 一、怎么跑

lead 指定的验收命令（从仓库根目录）：

```
.\.venv\Scripts\python.exe -m pytest server/tests -q -p no:cacheprovider --noconftest -k kernel
```

实测 **155 passed, 0.8 s**（远低于 30 s 上限；单文件均 < 0.3 s）。
所有用例都是**有界**的固定次数循环（最多推进 49 个 tick、最多 64 次生成重试），
不存在 `while True` / 无界搜索。

### 环境怪现象（记录以免误判）

本机 pwsh 下 `python -m pytest` 偶发**卡在 import/收集阶段**（不是用例挂住）。
处理方法：换 `-p no:randomly` 再跑一次，或直接分组跑。两次里只要有一次正常返回并全绿即视为通过。
已确认**不是**内核代码死循环：`faulthandler.dump_traceback_later` 与逐函数直接调用均能在 1 s 内跑完。

## 二、规则 → 测试覆盖表

### §1 回合 / Tick

| 规则 | 含义（简） | 覆盖用例 |
| --- | --- | --- |
| R1.1 | 1 回合 = 1 秒，分上下两个半回合（Tick） | `test_kernel_turn.py::test_step_increments_tick_and_reports_half_r1_3` |
| R1.2 | 开局 `Turn 0`；刻序 `Turn 0`, `Turn 0.`, `Turn 1`… | `test_turn_of_maps_ticks_r1_2`、`test_turn_of_rejects_negative_tick_r1_2`、`test_kernel_determinism.py::test_turn_of_is_pure_function_r1_2` |
| R1.3 | 每刻结束触发一次结算，每刻 0.5 s | `test_step_increments_tick_and_reports_half_r1_3` |
| R1.4 | 房主手动开局，不设自动倒计时 | 内核无逻辑（由 `net` 负责，见 `test_net_*`） |

### §2 地图

| 规则 | 含义（简） | 覆盖用例 |
| --- | --- | --- |
| R2.1 | `size × size`，`index = row * size + col` | `test_kernel_determinism.py::test_tile_count_conserved_r2_1`、`test_kernel_view.py::test_step_index_wraps_correctly` |
| R2.2 | 地形四类：首都 / 平原 / 山地 / 城市 | `test_kernel_mapgen.py::test_city_like_helpers_agree_with_terrain_values` |
| R2.3 | `size = clamp(6 + 3N + rand(0..6), 10, 40)`；8 人 ⇒ 30–36 | `test_size_follows_player_count_formula_r2_3`、`test_eight_players_size_in_30_to_36_r2_3`、`test_size_formula_constants_match_r2_3`、`test_explicit_size_is_respected_for_replay` |
| R2.4 | **无任何对称结构**，地形与首都位置独立随机 | `test_map_has_no_mirror_symmetry_r2_4`、`test_capital_positions_are_not_mirrored_r2_4`、`test_mapgen_module_has_no_symmetry_helpers` |
| R2.5 | 首都**曼哈顿**距离 `>= 13`，可位于边缘/角落 | `test_capital_manhattan_distance_at_least_13_r2_5`、`test_capital_min_manhattan_constant_r2_5`、`test_manhattan_is_l1_not_chebyshev_r2_5`、`test_capitals_may_sit_on_edges_r2_5` |
| R2.6 | 放不下换种子重试，边长不变；用尽抛错 | `test_retry_constants_r2_6`、`test_impossible_capital_layout_raises_r2_6`、`test_retry_keeps_size_unchanged_r2_6` |
| R2.7 | 生成参数集中在 `config.MAPGEN_*`；山地 20% / 城市 4% / 首都周围禁山 | `test_terrain_ratios_roughly_respected_r2_7`、`test_capitals_have_no_mountain_within_radius_r2_7` |

### §3 兵力与控制

| 规则 | 含义（简） | 覆盖用例 |
| --- | --- | --- |
| R3.1 | 兵力属于该地块的控制者 | `test_kernel_view.py::test_project_owner_is_none_for_neutral` |
| R3.2 | 每名玩家初始控制自己首都，首都兵力 1；其它地块中立 | `test_kernel_mapgen.py::test_generate_supports_any_player_count_r3_2` |
| R3.3 | 城市初始中立兵力 40–50 | `test_neutral_cities_have_expected_army_range_r3_3` |
| R3.4 | `Turn N.` 增长：首都/城市 +1；平原每 25 回合 +1（首次 `Turn 25.`） | `test_kernel_turn.py::test_city_growth_only_on_half_one_r3_7`、`test_plain_growth_first_refresh_at_turn_25_dot_r3_4`、`test_plain_second_refresh_at_turn_50_dot_r3_4`、`test_mountain_never_grows_r3_4`、`test_city_from_capital_grows_like_city_r5_4` |
| R3.5 | 视野 = 己方地块 ⊕ 8 相邻；视野内给完整信息 | `test_kernel_view.py::test_in_vision_army_is_real_r3_5`、`test_visible_tiles_include_capital_and_neighbours`、`test_kernel_determinism.py::test_mountain_blocks_expansion_invariant_r3_5` |
| R3.6 | 视野外不给兵量，但可识别「首都转化型城市」 | `test_kernel_view.py::test_out_of_vision_army_is_hidden_r3_6`、`test_city_from_capital_fogged_folds_to_plain_r5_4` |
| R3.7 | 只在 `Turn N.` 结算增长，上半回合不增长 | `test_city_growth_only_on_half_one_r3_7`、`test_plain_growth_not_on_other_halves_r3_7` |
| R3.8 | 中立：保有兵力、对所有人敌对、**驻军照常参战（打不下还是中立）**、不调兵、不增长 | `test_neutral_tiles_do_not_grow_r3_8`、`test_neutral_garrison_holds_when_attack_is_weaker_r3_8_r4_3`、`test_tick_weak_attack_leaves_neutral_city_r3_8` |
| R3.9 | 兵力无上限 | `test_kernel_combat.py::test_battle_friendly_arrival_accumulates_r4_6` |
| R3.10 | 观战者视野为全图 | `test_spectator_sees_full_truth_r3_10` |

### §4 移动与战斗

| 规则 | 含义（简） | 覆盖用例 |
| --- | --- | --- |
| R4.1 | 请求 = (起点, 方向, 比例)，方向 4 相邻；不可向山地调兵 | `test_kernel_commands.py::test_enqueue_rejects_out_of_board_r4_1`、`test_enqueue_rejects_out_of_board_src_bad_frame_r4_1`、`test_kernel_view.py::test_neighbours4_orders_and_drops_out_of_board` |
| R4.2 | 移动量 `min(ceil(army*ratio), army-1)`；`<= 0` 不处理；1 兵调不动 | `test_launched_amount_ceil_and_cap_r4_2`、`test_launched_amount_never_exceeds_army_minus_one_r4_2`、`test_zero_army_request_is_dropped_r4_2_r4_12` |
| R4.3 | 阵地战：各方求和互减；最大者胜；并列最大全归零且不占领；**中立驻军也是独立一方** | `test_kernel_combat.py` 全部 `*_r4_3` 用例（含 `test_neutral_garrison_fights_and_is_subtracted_r3_8_r4_3`、`test_two_players_versus_neutral_garrison_r4_3` 等 9 项） |
| R4.4 | 战斗后到达兵力 `> 0` 即被到达方占领，兵力 = 剩余兵力（不与驻军累加） | `test_encounter_then_battle_in_one_tick_r7_1`、`test_capture_zeroes_defender_visible_army_r4_10`、`test_tick_capture_neutral_city_subtracts_garrison_r3_8_r4_4` |
| R4.5 | 遭遇战：A→B 与 B→A 抵消，胜方剩余继续飞 | `test_encounter_mutual_cross_r4_5`、`test_encounter_equal_amounts_both_vanish_r4_5`、`test_encounter_does_not_touch_unpaired_flights_r4_5`、`test_encounter_three_way_not_possible_r4_5` |
| R4.6 | **己方地块**：`目标兵力 = 原兵力 + 到达兵力`（不设上限）；中立不累加 | `test_battle_friendly_arrival_accumulates_r4_6`、`test_battle_neutral_single_arrival_no_fight_r4_6` |
| R4.7 | 每玩家每 Tick 只处理 1 条（FIFO） | `test_fifo_pops_one_request_per_tick_r4_7`、`test_chain_limit_rejects_21st_request_r4_7` |
| R4.8 | 同一地块单 Tick 最多发 1 次调兵 | `test_fifo_pops_one_request_per_tick_r4_7`（R4.7 的直接结果） |
| R4.9 | 山地 = 不可通行；服务端按真实地形判定 | `test_enqueue_rejects_mountain_target_r4_9`、`test_mountain_target_never_receives_flight_r4_9` |
| R4.10 | 攻占后防守方立即看到该地块兵力 0 | `test_capture_zeroes_defender_visible_army_r4_10` |
| R4.11 | **预调兵**：起点可为链中任意目标；入队不校验归属；链可为任意有向图（含环） | `test_enqueue_accepts_any_src_ownership_r4_11`、`test_enqueue_accepts_chain_target_as_src_r4_11`、`test_premove_chain_captures_then_continues_r4_11`、`test_cascade_survives_ring_chain_r4_12` |
| R4.12 | **作废**：起点失守 / `<=0` ⇒ 不移动、本 Tick 不再弹、级联作废下游 | `test_premove_src_lost_is_dropped_r4_12`、`test_drop_stops_popping_more_requests_this_tick_r4_12`、`test_cascade_drops_downstream_requests_r4_12`、`test_cascade_survives_ring_chain_r4_12`、`test_no_cascade_when_nothing_downstream_r4_12`、`test_zero_army_request_is_dropped_r4_2_r4_12` |

### §5 首都与停止抵抗

| 规则 | 含义（简） | 覆盖用例 |
| --- | --- | --- |
| R5.1 | 每名玩家有且仅有一座王城 | `test_kernel_mapgen.py::test_generate_supports_any_player_count_r3_2`、`test_kernel_rules.py::test_conquer_capital_inherits_all_tiles_r5_2` |
| R5.2 | 攻下王城 ⇒ 占领其全部领土，每块 `max(army // 2, 1)` | `test_conquer_capital_inherits_all_tiles_r5_2`、`test_own_capital_capture_by_third_party_only_inherits_victim_r5_2` |
| R5.3 | 无地块玩家立刻出局，其首都转为普通城市 | `test_surrendering_player_is_eliminated_at_expiry_r5_3` |
| R5.4 | 首都转化型城市：等同普通城市，视野外渲染为平原 | `test_captured_capital_becomes_ordinary_city_r5_4`、`test_kernel_view.py::test_city_from_capital_visible_code_r5_4`、`test_city_from_capital_fogged_folds_to_plain_r5_4`、`test_kernel_turn.py::test_city_from_capital_grows_like_city_r5_4` |
| R5.5 | 投降 40 Tick；期间不能操作、链清空；期满地块转中立、兵力保留 | `test_surrender_clears_chain_and_blocks_operations_r5_5`、`test_surrender_counts_down_40_ticks_r5_5`、`test_tiles_go_neutral_on_surrender_expiry_keeping_army_r5_5`、`test_kernel_commands.py::test_enqueue_rejects_dead_or_surrendering_player_r5_5` |

### §6 操控与 UI（内核侧可测部分）

| 规则 | 含义（简） | 覆盖用例 |
| --- | --- | --- |
| R6.3 | 调兵链 FIFO，每 Tick 弹 1 条 | `test_fifo_pops_one_request_per_tick_r4_7` |
| R6.4 | `Q` 清整链；`E` 取消最新一条 | `test_clear_chain_removes_all_r6_4`、`test_pop_chain_removes_latest_r6_4` |
| R6.6 | 比例 10%–100%（内核 clamp 到 `(0, 1]`） | `test_ratio_is_clamped_into_open_unit_interval_r6_6` |
| R6.7 | 每个请求各自记录入队时比例 | `test_each_request_keeps_its_own_ratio_r6_7` |
| R6.8 | 面板展示兵力总和 / 控制区大小 | `test_kernel_rules.py::test_army_total_sums_owned_tiles_r6_8`、`test_land_count_includes_capital_and_cities_r6_8` |
| R6.9 | 视野外渲染折叠；视野内真实地形 | `test_kernel_view.py::test_fog_folds_terrain_r6_9`、`test_visible_diff_reports_newly_visible_tiles_r6_9`、`test_diff_views_*`（3 项） |
| R6.11 | 调兵链上限 20，超出拒绝 | `test_chain_limit_rejects_21st_request_r4_7` |
| R6.12 | 排行榜星数恒为 0 | `test_kernel_determinism.py::test_leaderboard_deterministic_order_r7_3` |

### §7 回合结算

| 规则 | 含义（简） | 覆盖用例 |
| --- | --- | --- |
| R7.1 | 六阶段顺序：增长 → 发起调兵 → 遭遇战 → 阵地战 → 更新占领 → 停止抵抗 | `test_kernel_combat.py::test_encounter_then_battle_in_one_tick_r7_1`、`test_kernel_mapgen.py::test_generated_map_can_be_stepped_r7_1` |
| R7.2 | 发起调兵是原子阶段：按 R4.11 判定 + 按 R4.2 扣兵生成飞行兵力；此刻不更新占领 | `test_premove_chain_captures_then_continues_r4_11`、`test_encounter_mutual_cross_r4_5`（`src` 保持原值） |
| R7.3 | 一致原则：无随机/时间；遍历按 `(玩家 id, 地块 index)` 排序 | `test_kernel_determinism.py` 全部 12 项、`test_kernel_mapgen.py::test_same_seed_same_map_r7_3` / `test_different_seeds_differ_r7_3`、`test_dropped_sorted_by_player_and_src_r7_3` |
| R7.4 | 只剩一名有地玩家 ⇒ 结束（第 6 步之后判定） | `test_kernel_rules.py::test_last_standing_player_wins_r7_4`、`test_step_returns_empty_report_once_ended_r7_4`、`test_kernel_turn.py::test_step_on_ended_state_is_noop_r7_4`、`test_enqueue_rejects_after_game_end_r7_4`、`test_kernel_mapgen.py::test_fresh_map_is_not_ended_r7_4` |

> 说明：R6.1 / R6.2 / R6.5 / R6.10 / R6.13 属 UI / 协议层（`web` / `net`），内核无对应逻辑。

## 三、实现假设（与 RULES.md 的对应）

1. **`tick.step()` 先自增 tick，再按新 `(turn, half)` 结算**（lead 已批准）。
   因此开局 `tick=0` 的第一次 `step()` 落在 `tick=1 = Turn 0.`，**首刻即增长**（R3.4 / R3.7）。
2. **单人局（`len(players) < 2`）不判结束**，便于离线驱动与单测（AI API §2.1）；
   多人局在六阶段全部走完后判定 R7.4。
3. **`mapgen.generate(player_count, rng, *, size=None)`**（lead 裁决的新签名）：
   `size=None` 时按 R2.3 公式算（jitter 取自同一个 `rng`）；显式 `size` 仅供测试/回放。
   **无任何对称逻辑**（R2.4），首都用**曼哈顿拒绝采样**（R2.5），放不下换序列重试、
   边长不变（R2.6），`MAX_ATTEMPTS=64` 用尽抛 `MapGenError`。
4. **预调兵**（R4.11）：`enqueue_move` 只校验帧/方向/山地/链满，**不校验起点归属**；
   `not_owned` 错误码保留在表里但入队阶段不再产生。
5. **作废与级联**（R4.12）：`TickReport.dropped: list[DroppedRequest]`，
   `reason ∈ {"src_lost", "zero_army"}`，`cascade` 记级联条数，按 `(player_id, src)` 排序。
6. **被攻下的王城**变为 `Terrain.CITY` + `city_from_capital=True`（R5.4），胜方不因此获得王城。
7. **并列最大**时地块归属不变、兵力归 0（R4.3）。

## 四、文件清单

| 文件 | 覆盖内容 |
| --- | --- |
| `test_kernel_turn.py` | 回合/刻映射、增长时机、结束态幂等 |
| `test_kernel_commands.py` | 移动兵力公式、调兵链 FIFO、错误码、预调兵与作废/级联 |
| `test_kernel_combat.py` | 遭遇战、阵地战、占领、山地拦截 |
| `test_kernel_rules.py` | 王城继承、投降、出局、胜负、地块计数 |
| `test_kernel_mapgen.py` | 尺寸公式、无对称、曼哈顿间距、重试、1..8 人、地形比例 |
| `test_kernel_view.py` | 迷雾、地形折叠、投影结构、增量 diff、相邻语义 |
| `test_kernel_determinism.py` | 禁用依赖、脚本复现、不变量守恒、排序稳定性 |
| `helpers_kernel.py` | 公共夹具（`make_state` / `make_tile`），**非** `test_` 前缀以免被收集 |