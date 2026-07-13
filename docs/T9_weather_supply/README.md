# T9：天气对外生交通供给的影响层

## 职责与边界

本层位于 T7 静态交通网络和 T8 正常天气分时供给之后，并复用 T2 的天气类型与事件窗口。它只计算天气造成的外生速度变化和独立道路容量状态，不修改原始 OD、距离、T7 基础速度、基础行程时间或 T8 等待/换乘参数。

本层不实现网约车车辆周转、车辆占用或空间再分布、派单成功率、动态等待、动态拥堵、Agent 活动取消、方式偏好和方式选择。T2 继续独立负责活动取消和网约车偏好标签，不再持有公交或网约车供给速度参数。

## 计算顺序

每个实际行驶分段使用：

```text
final_speed
= T7 base_speed
× T8 period_direction_multiplier
× T9 weather_speed_multiplier
```

T8 对道路方式只会从 `1.00 / 0.85 / 0.75` 中选一个最终时段方向乘数；`0.85` 和 `0.75` 永不叠乘。T9 随后再额外乘一次天气速度乘数。去程和返程按各自 `departure_time` 独立重算；行程遇到时段边界、方向相位边界、天气开始、天气结束或恢复结束时切段，并逐段消耗 T7 基础车内分钟。

`road_capacity_multiplier` 是同段输出的独立供给状态，不进入速度公式，也不会在当前静态层反推拥堵、等待或派单结果。

## 默认参数

配置文件为 `config/weather_transport_supply.json`：

| 天气阶段 | walk | bus | metro | ride_hailing | 道路容量 |
|---|---:|---:|---:|---:|---:|
| 正常 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| 高温事件 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| 暴雨事件 | 0.80 | 0.80 | 1.00 | 0.80 | 0.85 |
| 暴雨恢复 | 0.90 | 0.90 | 1.00 | 0.90 | 0.925 |

暴雨恢复阶段基准持续 120 分钟。恢复速度和容量采用“事件乘数与 1.00 的中点”规则，恢复结束后才回到正常。地铁在本模型中始终按正常运行速度计算，不建模停运、局部不可用或天气降速。

## 参数来源与证据限制

- `yoyo_database/weather_transport_disruptions` 中的道路积水和容量受损记录支持“暴雨会扰动道路供给”这一方向性判断，但不能估计全市统一速度乘数。
- `weather_transport_calibration` 的 `cal_94ad4396e0d3ff` 给出 30/120/360 分钟恢复期敏感性范围；该记录自身标记为假设情景边界，并明确不存在统一公开恢复时长。
- 暴雨方式速度 0.60/0.80/1.00、道路容量 0.70/0.85/1.00 均是模型敏感性范围，不是数据库估计值。
- 高温默认速度乘数为 1.00。未来若测试高温降速，只能标为模型假设或敏感性分析，不能声称由数据库估计。
- `ride_hailing_vehicle_turnover_multiplier` 属于后续动态供需模块，不得与本层的道路行驶速度混用。

## 接口与幂等性

`calculate_weather_adjusted_leg_mode_option(...)` 每次都从原始 leg、T7 网络和 T8 配置重新计算，不读取上一次天气调整后的时间，因此重复运行不会重复叠加。`weather_events_from_t2_config(...)` 可把 T2 当前周的星期/时钟窗口转换成带日期的供给事件；也可直接传入明确的 `start`、`end` 和 `weather_type` 事件列表。

主要新增输出包括 `weather_type`、`weather_phase`、`weather_speed_multiplier`、`final_speed_multiplier`、`road_capacity_multiplier`、天气调整后车内/总时间，以及逐段可审计的 `weather_supply_segments`。

## 测试

```powershell
python -B -X utf8 -m unittest tests.test_weather_transport_supply -v
```
