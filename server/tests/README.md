# 内核测试说明（kernel 组成员维护）

本目录下的 `test_kernel_*.py` 与 `helpers_kernel.py` 由「游戏内核」成员负责，
覆盖 `server/generals/game/**`（纯内核）与 `server/generals/config.py`。
其它成员的测试（`test_net_*` / `test_ai_*`）不在本文件范围内。

## 一、怎么跑

从仓库根目录：

```
.\.venv\Scripts\python.exe -m pytest server/tests -q -p no:cacheprovider 2>&1 | Out-String
```

本轮实测全量 kernel 文件耗时 **约 0.6 s**（105+ 用例），远低于 30 s 上限；
单文件均 < 0.2 s。所有用例都是**有界**的固定次数循环（最多推进 49 个 tick），
不存在 `while True` / 无界搜索。

### 只跑内核用例（跳过 `net`/`ai`）

若 `server/tests/conftest.py` 因 `generals.net` 暂时无法导入而导致整个
`server/tests` 收集失败（协作期间 `net` 正在改 `protocol.py` 时会发生），
可用 `--noconftest` 隔离运行内核用例（内核用例不依赖 conftest 的 fixture）：

```
$env:PYTHONPATH="D:\COSMOS\generals\server"
.\.venv\Scripts\python.exe -m pytest server/tests/test_kernel_view.py `
  server/tests/test_kernel_mapgen.py server/tests/test_kernel_rules.py `
  server/tests/test_kernel_combat.py server/tests/test_kernel_commands.py `
  server/tests/test_kernel_turn.py server/tests/test_kernel_determinism.py `
  -q -p no:cacheprovider --no-header --noconftest
```

### 环境怪现象（记录以免误判）

本机 pwsh 下 `python -m pytest` 偶发**卡在 import/收集阶段**（不是用例挂住）。
处理方法：换 `-p no:randomly` 再跑一次，或直接用 `--noconftest` 分组跑。
两次里只要有一次正常返回并全绿即视为通过。已确认**不是**内核代码死循环：
`faulthandler.dump_traceback_later` 与逐函数直接调用均能在 0.6 s 内跑完。

## 二、规则 → 测试覆盖表

### 规则条目（RULES.md 正文）

| 规则 | 含义（简） | 覆盖用例 |
| --- | --- | --- |
| R1.1 | 1 tick = 500 ms，服务器权威推进 | `test_kernel_turn.py::test_step_increments_tick_and_reports_half_r1_3` |
| R1.2 | `turn = tick // 2`，`half = tick % 2` | `test_kernel_turn.py::test_turn_of_maps_ticks_r1_2`、`test_turn_of_rejects_negative_tick_r1_2`、`test_kernel_determinism.py::test_turn_of_is_pure_function_r1_2` |
| R1.3 | 每回合两个半刻，报告带 `turn`/`half` | `test_kernel_turn.py::test_step_increments_tick_and_reports_half_r1_3` |
| R2.1 | 地图 `size × size`，地块总数恒定 | `test_kernel_determinism.py::test_tile_count_conserved_r2_1`、`test_kernel_mapgen.py::test_generate_works_across_sizes` |
| R3.2 | 每名玩家一座王城，初始兵力 1 | `test_kernel_mapgen.py::test_generate_supports_any_player_count_r3_2`、`test_capitals_have_no_mountain_within_radius_r3_2` |
| R3.3 | 中立城市初始兵力 40..50 | `test_kernel_mapgen.py::test_neutral_cities_have_expected_army_range_r3_3` |
| R3.4 | 平原/王城/城市按周期增长 | `test_kernel_turn.py::test_plain_growth_first_refresh_at_turn_25_dot_r3_4_c01`、`test_plain_second_refresh_at_turn_50_dot_r3_4`、`test_plain_growth_not_on_other_halves_r3_7` |
| R3.5 | 山地不可通行/不可占领 | `test_kernel_mapgen.py::test_mountains_are_impassable_terrain_r3_5`、`test_kernel_determinism.py::test_mountain_blocks_expansion_invariant_r3_5`、`test_kernel_combat.py::test_mountain_target_never_receives_flight_r4_9` |
| R3.6 | 视野外地形折叠 | `test_kernel_view.py::test_fog_folds_terrain_r3_6_c17` |
| R3.7 | 非增长刻不涨兵 | `test_kernel_turn.py::test_plain_growth_not_on_other_halves_r3_7` |
| R3.8 | `army_total` = 该玩家地块兵力之和 | `test_kernel_rules.py::test_army_total_sums_owned_tiles_r3_8` |
| R4.1 | 调兵必须相邻、己方地块出发 | `test_kernel_commands.py::test_enqueue_rejects_not_owned_r4_1`、`test_enqueue_rejects_out_of_board_r4_1` |
| R4.2 | 实际兵力 `min(ceil(army*ratio), army-1)`，可 0 | `test_kernel_commands.py::test_launched_amount_ceil_and_cap_r4_2_c07`、`test_launched_amount_never_exceeds_army_minus_one_r4_2`、`test_zero_army_request_is_legal_and_consumed_r4_2_c02` |
| R4.3 | 阵地战：多方互减；并列最大不占领 | `test_kernel_combat.py::test_battle_multi_party_subtraction_r4_3`、`test_battle_ties_all_zero_and_no_capture_r4_3`、`test_battle_defender_alone_against_attacker_r4_3`、`test_battle_is_pure_and_sorted_r4_3_r7_3`、`test_battle_tie_keeps_owner_and_zeroes_r4_3`、`test_battle_result_has_no_tile_mutation_contract_r4_3` |
| R4.4 | 独大即占领，占领后剩余兵力归胜方 | `test_kernel_combat.py::test_encounter_then_battle_in_one_tick_r7_1_c10`、`test_capture_zeroes_defender_visible_army_c13` |
| R4.5 | 对穿遭遇战：抵消、胜方剩余继续飞 | `test_kernel_combat.py::test_encounter_mutual_cross_r4_5`、`test_encounter_equal_amounts_both_vanish_r4_5`、`test_encounter_does_not_touch_unpaired_flights_r4_5` |
| R4.6 | 单方到达 = 累加（无战斗） | `test_kernel_combat.py::test_battle_friendly_arrival_accumulates_r4_6_c14`、`test_battle_neutral_single_arrival_no_fight_r4_6` |
| R4.7 | 每玩家每刻只消费 1 条请求，FIFO | `test_kernel_commands.py::test_fifo_pops_one_request_per_tick_r4_7`、`test_chain_limit_rejects_21st_request_r4_7` |
| R4.9 | 山地为目标时请求被拒 | `test_kernel_commands.py::test_enqueue_rejects_mountain_target_r4_9`、`test_kernel_combat.py::test_mountain_target_never_receives_flight_r4_9` |
| R5.1 | 王城唯一；地块计数含王城/城市 | `test_kernel_rules.py::test_land_count_includes_capital_and_cities_r5_1_c16` |
| R5.2 | 攻下王城 = 全境继承（兵力减半） | `test_kernel_rules.py::test_conquer_capital_inherits_all_tiles_r5_2_c11`、`test_own_capital_capture_by_third_party_only_inherits_victim_r5_2` |
| R5.3 | 投降过渡期 40 tick 倒计时 | `test_kernel_rules.py::test_surrender_counts_down_40_ticks_r5_3_c08` |
| R5.4 | 转化型城市此后按普通城市处理 | `test_kernel_rules.py::test_captured_capital_becomes_ordinary_city_r5_4_c09`、`test_kernel_view.py::test_city_from_capital_visible_code_r5_4` |
| R5.5 | 投降即清链、不能操作；期满地块转中立 | `test_kernel_rules.py::test_surrender_clears_chain_and_blocks_operations_r5_5_c08`、`test_tiles_go_neutral_on_surrender_expiry_keeping_army_c08`、`test_surrendering_player_is_eliminated_at_expiry_r5_5_c08` |
| R6.4 | `clear_chain` / `pop_chain` 语义 | `test_kernel_commands.py::test_clear_chain_removes_all_r6_4`、`test_pop_chain_removes_latest_r6_4` |
| R6.9 | 视野变化增量下发 | `test_kernel_view.py::test_visible_diff_reports_newly_visible_tiles_r6_9` |
| R7.1 | 六阶段固定顺序 | `test_kernel_combat.py::test_encounter_then_battle_in_one_tick_r7_1_c10`、`test_kernel_mapgen.py::test_generated_map_can_be_stepped_r7_1` |
| R7.2 | 发起调兵是原子阶段（不占领） | `test_kernel_combat.py::test_encounter_mutual_cross_r4_5`（`src` 保持原值）、`test_kernel_commands.py::test_fifo_pops_one_request_per_tick_r4_7` |
| R7.3 | 确定性：无随机/时间、遍历有序、地图对称 | `test_kernel_determinism.py` 全部 12 项、`test_kernel_mapgen.py::test_same_seed_same_map_r7_3`、`test_different_seeds_differ_r7_3`、`test_even_maps_are_fully_symmetric_r7_3`、`test_odd_maps_symmetric_outside_center_3x3_r7_3`、`test_kernel_view.py::test_diff_views_sorted_by_index_r7_3` |
| R7.4 | 只剩一名有地玩家即结束 | `test_kernel_rules.py::test_last_standing_player_wins_r7_4_c12`、`test_step_returns_empty_report_once_ended_r7_4`、`test_kernel_turn.py::test_step_on_ended_state_is_noop_r7_4`、`test_kernel_mapgen.py::test_fresh_map_is_not_ended_r7_4` |

### 附录 A 补充裁决（C-01..C-18）

| 条目 | 含义（简） | 覆盖用例 |
| --- | --- | --- |
| C-01 | 增长只发生在 `half == 1`（`Turn N.`） | `test_kernel_turn.py::test_city_growth_only_on_half_one_c01`、`test_plain_growth_first_refresh_at_turn_25_dot_r3_4_c01` |
| C-02 | 0 兵力请求合法 | `test_kernel_commands.py::test_zero_army_request_is_legal_and_consumed_r4_2_c02`、`test_ratio_is_clamped_into_open_unit_interval_c02` |
| C-03 | 4/8 相邻与视野邻接语义 | `test_kernel_view.py::test_neighbours4_orders_and_drops_out_of_board`、`test_step_index_wraps_correctly`、`test_visible_tiles_include_capital_and_neighbours` |
| C-04 | 遭遇战成对匹配（同对地块对穿） | `test_kernel_combat.py::test_encounter_mutual_cross_r4_5`、`test_encounter_equal_amounts_both_vanish_r4_5` |
| C-05 | 调兵链 FIFO + 上限 20 | `test_kernel_commands.py::test_fifo_pops_one_request_per_tick_r4_7`、`test_chain_limit_rejects_21st_request_r4_7` |
| C-06 | 每条请求各自记录比例 | `test_kernel_commands.py::test_each_request_keeps_its_own_ratio_c06` |
| C-07 | `ceil` 取整 + 至少留 1 兵 | `test_kernel_commands.py::test_launched_amount_ceil_and_cap_r4_2_c07`、`test_launched_amount_never_exceeds_army_minus_one_r4_2` |
| C-08 | 投降 40 tick、清链、地块转中立保留兵力 | `test_kernel_rules.py::test_surrender_clears_chain_and_blocks_operations_r5_5_c08`、`test_surrender_counts_down_40_ticks_r5_3_c08`、`test_tiles_go_neutral_on_surrender_expiry_keeping_army_c08`、`test_surrendering_player_is_eliminated_at_expiry_r5_5_c08`、`test_kernel_commands.py::test_enqueue_rejects_dead_or_surrendering_player_c08` |
| C-09 | 被攻陷王城 → 转化型城市（地形码 4） | `test_kernel_rules.py::test_conquer_capital_inherits_all_tiles_r5_2_c11`、`test_captured_capital_becomes_ordinary_city_r5_4_c09`、`test_kernel_view.py::test_city_from_capital_visible_code_r5_4` |
| C-10 | 六阶段内"遭遇战先于阵地战" | `test_kernel_combat.py::test_encounter_then_battle_in_one_tick_r7_1_c10` |
| C-11 | 继承时非王城地块兵力 `max(army//2, 1)` | `test_kernel_rules.py::test_conquer_capital_inherits_all_tiles_r5_2_c11` |
| C-12 | 结束判定与胜利者 | `test_kernel_rules.py::test_last_standing_player_wins_r7_4_c12`、`test_step_returns_empty_report_once_ended_r7_4` |
| C-13 | 战败方立刻看到该地块兵力 0 | `test_kernel_combat.py::test_capture_zeroes_defender_visible_army_c13` |
| C-14 | 同阵营到达累加不设上限 | `test_kernel_combat.py::test_battle_friendly_arrival_accumulates_r4_6_c14` |
| C-16 | `land_count` 含王城与城市 | `test_kernel_rules.py::test_land_count_includes_capital_and_cities_r5_1_c16` |
| C-17 | 迷雾：视野外 `army` 为 `None`、地形折叠 | `test_kernel_view.py::test_out_of_vision_army_is_hidden_c17`、`test_in_vision_army_is_real_c17`、`test_spectator_sees_full_truth_c17`、`test_fog_folds_terrain_r3_6_c17`、`test_city_from_capital_fogged_folds_to_plain_c17` |
| C-18 | 4 相邻下不存在三方遭遇战 | `test_kernel_combat.py::test_encounter_three_way_not_possible_c18` |

> C-15 在 RULES.md 附录 A 中为「预留/无实现要求」条目，内核无对应逻辑，故无用例。

## 三、实现假设（与 BRIEF / 裁决的对应）

1. **`tick.step()` 先自增 tick，再按新 `(turn, half)` 结算**（lead 已批准）。
   因此开局 `tick=0` 的第一次 `step()` 落在 `tick=1 = Turn 0.`，**首刻即增长**（C-01）。
2. **单人局（`len(players) < 2`）不判结束**，便于离线驱动与单测（AI_API §2.1）；
   多人局在六阶段全部走完后判定 R7.4。
3. **`mapgen.generate` 支持 1..8 人（含奇数）**（lead 裁决）：偶数为 180° 成对放置；
   奇数多出的一座落在中心 3×3，该局部在对称自检中豁免，其余地块严格对称。
4. **被攻下的王城**变为 `Terrain.CITY` + `city_from_capital=True`，胜方不因此获得王城资格。
5. **并列最大**时地块归属不变、兵力归 0。

## 四、文件清单

| 文件 | 覆盖内容 |
| --- | --- |
| `test_kernel_turn.py` | 回合/刻映射、增长时机、结束态幂等 |
| `test_kernel_commands.py` | 移动兵力公式、调兵链、校验错误码 |
| `test_kernel_combat.py` | 遭遇战、阵地战、占领、山地拦截 |
| `test_kernel_rules.py` | 王城继承、投降、出局、胜负、地块计数 |
| `test_kernel_mapgen.py` | 确定性、对称性、1..8 人、地形比例、参数校验 |
| `test_kernel_view.py` | 迷雾、地形折叠、投影结构、增量 diff、相邻语义 |
| `test_kernel_determinism.py` | 禁用依赖、脚本复现、不变量守恒 |
| `helpers_kernel.py` | 公共夹具（`make_state` / `make_tile`），**非** `test_` 前缀以免被收集 |
