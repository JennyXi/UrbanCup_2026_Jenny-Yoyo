# T10：动态道路拥堵层

## 层级与边界

T10 接在 T7 基础网络、T8 时段方向供给和 T9 天气供给之后，只把一个外生或聚合的道路交通量状态转换为动态拥堵速度折减。T7、T8、T9 的原始字段全部保留，T10 每次从原始 leg 重新运行上游计算，不读取先前的 T10 输出，因此重复运行不会累计折减。

本层只作用于 `bus` 和 `ride_hailing`。二者在相同 `road_state_id`、容量配置和交通量输入下共享完全相同的道路容量、v/c 和动态拥堵乘数，但保留各自的 T7 基础速度及 T9 天气后自由流速度。`walk`（文档语义中的 walking）和 `metro` 不参与机动车道路容量计算，其容量、交通量和 v/c 输出为空，动态拥堵乘数固定为 1.00。

本层不实现 Agent 方式选择、网约车车辆周转、车辆占用、空间再分布、派单、动态等待、动态加价或派单失败。

## 计算顺序

```text
weather_free_flow_speed
= base_speed
× period_direction_multiplier
× weather_speed_multiplier

weather_capacity
= normal_road_capacity
× road_capacity_multiplier

volume_capacity_ratio
= current_road_volume / weather_capacity

dynamic_congestion_multiplier
= 1 / (1 + alpha × volume_capacity_ratio ^ beta)

final_speed
= weather_free_flow_speed
× dynamic_congestion_multiplier

final_in_vehicle_time
= T9 weather_adjusted_vehicle_time
/ dynamic_congestion_multiplier
```

这里的 `road_capacity_multiplier` 只进入 `weather_capacity`，不会直接进入速度公式。暴雨天气速度倍率描述低流量下仍存在的雨天减速；容量倍率描述同一交通量在暴雨下产生更高 v/c、因而更容易拥堵。两条机制分别计算，不重复惩罚。

## 拥堵函数

配置文件为 `config/dynamic_road_congestion.json`。默认采用可解释的 BPR 类速度乘数：

```text
dynamic_congestion_multiplier = 1 / (1 + α × (v/c)^β)
```

默认 `α=0.15`、`β=4.0`。流量为零时乘数严格为 1.00；v/c 上升时乘数单调下降。α、β 及其敏感性范围均为模型假设，不是 `yoyo_database` 的直接估计。

## 正常道路容量

当前仅提供 `aggregate_network` 容量配置：

- `normal_road_capacity = 1800 PCU/hour/direction`；
- 敏感性范围为 1200 / 1800 / 2400；
- 单位是每方向每小时乘用车当量；
- 这是聚合道路状态的初始模型假设，不代表上海任一真实道路、车道或路段的实测容量。

未来若引入路段级网络，应以不同 `capacity_profile_id` 配置各路段或道路等级容量，而不是修改 T7 基础速度。

## 交通量输入

`current_road_volume` 由调用方显式传入，单位与容量相同。其预期来源是模型运行器预先汇总的外生道路交通量、情景交通量表或未来方式选择模块的聚合结果。本层：

- 不从当前候选 leg 推断交通量；
- 不把本次被评估行程自动加入流量；
- 不根据公交或网约车各自候选数量生成流量；
- 不要求或执行 Agent 方式选择。

当公交和网约车共享道路状态时，调用方必须传入相同的 `road_state_id`、`capacity_profile_id` 和 `current_road_volume`。

## 输出

在完整保留 T7–T9 输出的基础上新增：

```text
road_state_id
capacity_profile_id
normal_road_capacity
weather_capacity
current_road_volume
volume_capacity_ratio
dynamic_congestion_multiplier
weather_free_flow_speed
final_speed
final_in_vehicle_time
```

`road_capacity_multiplier` 直接沿用并保留 T9 原字段，不被 T10 覆盖。

## 测试

```powershell
python -B -X utf8 -m unittest tests.test_dynamic_road_congestion -v
```

专项测试覆盖容量不直接乘速度、零流量、暴雨容量下降、v/c 单调性、公交与网约车共享状态、步行与地铁排除、三个速度层各叠加一次、重复运行幂等，以及 T7–T9 字段不被覆盖。
