# T7：九区级简化多方式交通网络

本模块建立 Z1–Z9 的基准交通供给与完整 OD 备选方案，不进行 Agent 方式选择。每个 OD 固定输出 `walk`、`bus`、`metro`、`ride_hailing` 四行，供后续行为模型读取。

T7本身是正常天气、无内生拥堵的静态基准网络。其上已新增独立的T8外生分时供给层，用于班次、早晚高峰方向倍率、地铁运营时间与末班车约束；静态OD结果仍不被覆盖，从而避免与后续选择产生的内生拥堵重复计算。详见`docs/T8_time_supply/README.md`。

## 数据库审计与参数来源

首先检查了 `data_collection`（即队友的 yoyo_database）中的参数映射、来源限制、天气交通校准表和原始事件库。可复用内容是：暴雨会降低道路容量和公共交通速度，可能降低网约车车辆周转；现有校准文件提供 `road_capacity_multiplier`、`transit_speed_multiplier`、`ride_hailing_vehicle_turnover_multiplier` 和恢复时间的敏感性范围。

这些材料同时明确说明：缺少统一的上海全市基准公交速度、车辆周转、票价—时间组合、候车、接驳和换乘观测，新闻事件不能外推为全市均值。因此本轮只在配置的 `evidence_references` 中引用天气参数，`applied_in_this_baseline_network=false`；不重复天气配置，也不把它应用到正常天气OD表。

基准速度、票价、等待、接驳、换乘和中心—郊区差异均集中在 `config/multimodal_transport_network.json`，逐项标记为 `model_assumption`，后续可以直接替换或做敏感性分析。

## 网络图

道路边从现有 `config/shanghai_synthetic_city.json` 的双向 `connected_to` 读取，不重复维护：

- Z1–Z2、Z1–Z3、Z1–Z6、Z1–Z7
- Z2–Z4、Z2–Z8
- Z3–Z5、Z3–Z6
- Z4–Z7、Z5–Z7
- Z6–Z9

跨区 `euclidean_distance_km` 是起终区质心直线距离。每条直接道路边的长度等于该边两端质心直线距离乘两端较大的 `network_distance_multiplier`；一个OD的 `road_network_distance_km` 则是沿 `connected_to` 道路图的最短路径边长之和。Z9的1.35只作用于其唯一的Z9—Z6道路边，因此Z9跨区道路行程必须先经过Z6，不能跨越未配置的直达道路。区内没有有意义的质心间直线距离，因此 `euclidean_distance_km=0`，道路距离由区域面积基准与活动地点抽样提供。

公交线路覆盖九区：

- B1：Z8–Z2–Z1–Z7–Z4
- B2：Z9–Z6–Z3–Z1
- B3：Z6–Z1–Z7–Z5
- B4：Z4–Z2–Z1–Z3–Z5

地铁仅覆盖主要走廊：

- M1：Z8–Z2–Z1–Z7–Z4
- M2：Z6–Z3–Z1
- M3：Z3–Z5–Z7

每个分区内部都显式保留道路、公交和网约车服务，因此同区活动也能生成公交或网约车方案。区内地铁不再用一个布尔值解释为全区任意OD可达，而是使用可配置覆盖率：Z1 0.75，Z2/Z3 0.60，Z7 0.50，Z4/Z6 0.35，Z5 0.30，Z8 0.25，Z9 0。具体leg必须距离达到 `max(3 km, 0.75 × 本区mean_intrazonal_distance)`，并且基于稳定seed抽到起点和终点都在覆盖范围内，才提供区内地铁方案。覆盖率是参考上海轨道交通规划量级设置的简化模型假设，不是实测覆盖率。这里的Z1–Z9是分区面，质心只用于估算跨区边长，并不代表整个区域只有一个站点。

Z9没有本区地铁站。Z9的地铁方案先用B2公交接驳到Z6，再进入地铁网络；Z9→Z6公交段已经完整包含步行接驳、公交等待和公交车内时间，整体只计入一次，不再额外叠加固定14分钟。2元公交费计入 `access_fare`，公交—地铁只额外增加6分钟方式转换时间并计入一次 `mode_transfer_count`。Z9→Z9以及Z9→Z6这类没有实际地铁主行程的组合仍标记为不可用。

## 第一版方式参数（均为可修改模型假设）

| 方式 | 速度 | 等待/接驳 | 票价 |
|---|---:|---|---|
| walk | 4.8 km/h | 无等待；最长6 km | 0元 |
| bus | 18 km/h | 按区域；每次换乘8分钟 | 2元平票 |
| metro | 35 km/h | 按区域；每次换乘7分钟 | 3元含6 km，之后每10 km加1元 |
| ride_hailing | 33 km/h | 2分钟接驳；按区域等待 | 14元含3 km，之后2.7元/km |

这里的速度字段统一为`base_speed_kmh`，表示正常、非高峰且不含天气影响的平均运行速度：步行4.8、公交18、地铁35、网约车33 km/h。T7本身不预先写入拥堵；T8再对公交和网约车选择一个最终时段速度乘数。

区级OD表继续读取空间模块由区域面积推导的 `mean_intrazonal_distance`，作为区域总体基准。具体leg则按活动类型使用稳定seed的三角分布抽样，绝对范围0.5–20 km：shopping乘数0.15/0.45/0.90，social_leisure为0.30/0.80/1.40，medical为0.45/1.05/1.65，visit和family类为0.20/0.90/1.80，work为0.50/1.10/1.70（依次为low/mode/high）。所有区使用同一套尺度乘数，因此大样本下大区平均区内距离仍高于小区。步行的 `in_vehicle_time_min` 表示主移动时间，以维持统一输出结构，并不表示乘车。

公交和地铁使用带线路状态的最短总时间寻路。寻路成本至少包括车内时间、起终点接驳时间和线路换乘时间；因此较短但需要换线的路径不一定胜过距离稍长的直达路径。公交和地铁的主方式距离、车内时间及线路换乘次数均保留所选线路路径的累计结果，不再用OD直达距离覆盖。公交的每条线路边以对应道路边乘1.10形成，因此公交绕行发生在线路路径的每一段。

## 输出语义

距离字段统一定义如下：

| 字段 | 定义 |
|---|---|
| `euclidean_distance_km` | 分区质心之间的直线距离，仅作为跨区道路距离的构造基础；同区为0 |
| `road_network_distance_km` | 跨区为沿配置道路图的最短路径累计距离，其中每条道路边应用绕行系数；同区为按地点对抽样的合成道路距离 |
| `main_network_distance_km` | 当前主交通方式使用的近似走行距离 |
| `access_distance_km` | 进站、出站或公交接驳地铁等接驳段距离 |
| `network_distance_km` | `main_network_distance_km + access_distance_km` |

各方式公式：

- walk：`main_network_distance_km = road_network_distance_km`；
- ride_hailing：`main_network_distance_km = road_network_distance_km`，车内时间和里程费都由该道路距离计算；
- bus：跨区为所选公交线路各段道路距离乘1.10后的累计值；区内为抽样道路距离乘1.10；
- metro：`main_network_distance_km`为地铁线路路径距离，`access_distance_km`另计步行进出站或Z9公交接驳；
- 各方式距离不要求相同，但所有可用方案均满足 `network_distance_km = main_network_distance_km + access_distance_km`。

`total_time_min` 始终由以下四项相加：

```text
in_vehicle_time_min
+ access_time_min
+ wait_time_min
+ transfer_time_min
```

`line_transfer_count`只统计公交内部或地铁内部的线路换乘；`mode_transfer_count`统计公交接驳地铁等方式转换；兼容字段 `transfers = line_transfer_count + mode_transfer_count` 可直接提供给 Agent。`access_mode`说明接驳方式。`main_fare`是主方式票价，`access_fare`是接驳费用，`fare = main_fare + access_fare`。

不可用方式保留该OD—方式行，`available=false`；OD本身的 `euclidean_distance_km` 与 `road_network_distance_km`仍保留，方式专属距离、时间、费用和换乘字段为空。旧的 `effective_distance_km` 不再写入leg或OD CSV；T6内部的 `effective_choice_distance()`只用于目的地概率评分，不属于正式交通距离字段。区级 `od_mode_options.csv` 表示该分区是否存在这种供给；具体 `leg_mode_options.csv` 才使用实际抽样道路距离和端点覆盖结果判断某条同区leg能否坐地铁。它为每条leg列出四种备选方案，但不替Agent选择方式。

运行：

```powershell
python scripts/generate_transport_od_table.py
python -m unittest tests.test_transport_network -v
```

生成 `outputs/transport_network/od_mode_options.csv`，共 `9 × 9 × 4 = 324` 行。
