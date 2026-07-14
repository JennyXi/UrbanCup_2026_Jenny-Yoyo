# T10：相对背景负载的边际动态拥堵层

## 方案 A 的职责

T8 固定表示正常城市早晚高峰背景，T10 只计算情景新增道路流量相对于该背景负载造成的额外速度下降。天气或政策是上游原因，T10 是道路系统的间接响应。

```text
T7 基础网络与速度
→ T8 正常背景高峰
→ T9 天气直接降速与道路容量
→ T10 相对背景负载的边际拥堵
```

T10 只作用于 `bus` 和 `ride_hailing`。`walk` 和 `metro` 不参与机动车道路容量计算，`extra_multiplier` 固定为 1.00。本层不实现方式选择、车辆周转、派单、动态等待或动态加价。

## 输入语义

道路流量输入只能是：

```text
excess_road_flow_pcu_per_hour
```

单位必须是 `PCU/hour/direction`，不能直接传车辆总数。其定义为：

```text
当前情景的走廊分时道路流量
- T8基准情景的同走廊、同方向、同时段道路流量
```

该情景差值应由 Agent 方式选择结果统一汇总，因此已经包含天气导致的方式转移，不得再手工叠加一份“天气新增网约车需求”。允许来源是：

- `agent_mode_choice_scenario_delta`；
- `policy_scenario_delta`；
- `combined_scenario_delta`。

T10 不生成流量，也不会把正在评估的候选 leg 自动加入流量。

当前接口只接受非负新增量。上游应计算：

```text
excess_flow = max(0, scenario_flow - baseline_flow)
```

为防止语义混淆，当前 T10 对负输入直接报错；本版暂不模拟流量减少带来的道路改善。

## 边际拥堵公式

定义 BPR 速度函数：

```text
BPR_speed(vc)
= 1 / (1 + alpha × vc ^ beta)
```

每个分段计算：

```text
weather_capacity
= corridor_capacity
× weather_capacity_multiplier

baseline_vc_weather
= baseline_vc
/ weather_capacity_multiplier

scenario_vc
= baseline_vc_weather
+ excess_road_flow_pcu_per_hour / weather_capacity

extra_multiplier
= min(
    1.00,
    BPR_speed(scenario_vc)
    / BPR_speed(baseline_vc_weather)
  )

final_speed_kmh
= T9_speed_kmh
× extra_multiplier
```

因此：

```text
excess_road_flow_pcu_per_hour = 0
→ scenario_vc = baseline_vc_weather
→ extra_multiplier = 1.00
```

T8 仍然负责背景高峰；T10 只保留新增流量造成的边际下降。暴雨容量下降会同时提高背景有效 v/c 和新增流量对应的 v/c 增量，所以相同新增流量在暴雨下影响更大。

## baseline_vc

`baseline_vc` 是与 T8 状态一致的背景负载模型假设，不是 `yoyo_database` 估计值。

| T8 时段 | 反向/普通方向 | 主通勤方向附加值 |
|---|---:|---:|
| morning_shoulder | 0.68 | +0.10 |
| morning_core_peak | 0.88 | +0.17 |
| morning_recovery | 0.64 | +0.08 |
| day_off_peak | 0.45 | +0.00 |
| evening_shoulder | 0.70 | +0.10 |
| evening_core_peak | 0.90 | +0.17 |
| evening_recovery | 0.65 | +0.08 |
| night | 0.30 | +0.00 |

核心高峰高于平峰；T8 识别出的主通勤方向高于反方向。方向相位偏移继续复用 T8 逻辑，不在 T10 重复定义。

## 跨边界分段

程序逐段消耗不可变的 T7 基础车内分钟。若 T10 延误使行程跨越以下边界，会在新分段重新计算 `baseline_vc`、天气容量、`scenario_vc` 和边际倍率：

- T8 时段边界；
- T8 主通勤方向相位边界；
- 天气开始或结束；
- 暴雨恢复结束；
- 最大分段时间边界。

最终时间闭合为：

```text
final_total_time_min
= T9 weather_adjusted_total_time_min
- T9 weather_adjusted_vehicle_time_min
+ T10 final_in_vehicle_time_min
```

## 共享道路状态

公交和网约车共享道路时，必须读取完全相同的状态键：

```text
corridor_id + direction + time_bin
```

两种方式可以保留不同基础速度，但同一状态键下必须共享走廊容量、天气容量、背景 v/c、情景新增流量和 `scenario_vc`。调用 T10 前，模型运行器必须先把该状态键下的网约车、公交及其他新增 PCU 汇总为一个 `excess_road_flow_pcu_per_hour`，只计算一次拥堵倍率，再由公交和网约车共同读取；不得按方式分别传入不同流量。

当前 T10 尚未实现跨调用的全局状态注册器，该注册器是后续模型运行器的责任。现有接口要求调用方显式确认输入已经完成全机动车方式预汇总，否则拒绝计算。

## 参数与安全边界

默认配置位于 `config/dynamic_road_congestion.json`：

- 代表性单向走廊容量：1800 PCU/hour/direction；
- 容量敏感性范围：1200 / 1800 / 2400；
- `alpha = 0.15`，`beta = 4.0`；
- 最大 v/c：3.0；
- 严重超饱和机动车速度下限：10.0 km/h，仅作用于 `bus` 和 `ride_hailing`；
- 最大单分段时间：120 分钟。

所有值均标记为比赛 MVP 模型假设。10 km/h 是严重超饱和情况下的数值速度下限，不代表正常运行速度，也不影响步行或地铁车内速度。原始未截断速度保留在 `unclipped_final_speed_kmh`；仅当其低于 10 km/h 时，`motor_vehicle_speed_floor_applied` 和 `motor_vehicle_oversaturated` 同时为 `true`，否则均为 `false`。

代码对 BPR 幂运算使用对数形式保护，`scenario_vc` 超过上限时截断。`maximum_segment_time_min = 120` 只用于单个动态分段或迭代保护：达到边界后继续计算下一分段，绝不截断合法的整趟长距离公交行程。

## 输出

主要输出包括：

```text
baseline_vc
baseline_vc_weather
scenario_vc
extra_multiplier
unclipped_final_speed_kmh
motor_vehicle_speed_floor_applied
motor_vehicle_oversaturated
final_speed_kmh
final_in_vehicle_time_min
final_total_time_min
dynamic_congestion_segments
```

同时输出 `corridor_id`、`direction`、`time_bin_at_vehicle_start`、共享状态键、容量配置、天气容量、PCU 新增流量及其来源。T7–T9 原字段全部保留且不覆盖。

## 测试

```powershell
python -B -X utf8 -m unittest tests.test_dynamic_road_congestion -v
```

测试覆盖零新增量严格为 1、八时段显式配置、核心高峰与平峰比较、暴雨容量、主通勤与反方向比较、速度下限及状态标记、长距离行程分段但不截断、负输入拒绝、共享状态语义、最终总时间闭合、极端输入保护、跨边界重算、非道路方式排除、幂等性及上游字段不覆盖。
