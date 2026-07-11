# UrbanCup 2026：高温天气下的网约车出行公平模拟

本仓库正在构建一个以上海总体人口结构和空间趋势为参考的九区合成城市，用于研究极端夏季天气、数字接入和出行补贴政策对不同年龄人群潜在出行机会的影响。

当前实现已覆盖 baseline population、home-zone 安置、七日基础活动、活动目的地区域、天气响应规则和补贴资格规则。尚未生成正式 OD、交通方式选择、订单或派单结果。

## 已完成模块

### T1：Agent population 与七日 baseline activities

- 按 `18–39`、`40–59`、`60+` 三个年龄层动态生成 Agent；
- 工作状态和医疗需求等级按可配置比例稳定分配；
- 读取真实 `home_zone` 生成 Monday–Sunday baseline activities；
- 所有时间位于30分钟网格；
- 每日活动通过 `sequence_order` 保存稳定顺序；
- 区分 modeled candidate slot、`no_in_scope_trip` slot、空 Agent-day 和固定工作槽；
- 固定 seed 可复现，Agent 输入顺序不影响个人计划。

详细说明见 [`docs/T1_trip_planning/README.md`](docs/T1_trip_planning/README.md)。

### T2：极端天气行为规则

- 支持 W0、W1、W2 独立天气情景；
- 根据天气、年龄层、活动目的和取消概率判断 `trip_continues`；
- 不直接决定交通方式、订单或派单。

详细说明见 [`docs/T2_weather/README.md`](docs/T2_weather/README.md)。

### T3：出行补贴政策规则

- 支持 P0–P4 五种政策情景；
- 区分 policy scenario 与 low/high discount level；
- 输出优惠资格、触达/领取状态、可用渠道和老年派单优先资格；
- 不直接生成订单、优惠实际使用或派单成功结果。

详细说明见 [`docs/T3_policy/README.md`](docs/T3_policy/README.md)。

### T4：九区合成城市与 home-zone 安置

- 使用合成圈层、质心坐标、代表性面积和相对居住密度构建九区城市；
- 动态校准区域年龄构成并生成二维 `zone × age_group` 整数配额；
- 按精确配额为每个 Agent 分配唯一 `home_zone`；
- 使用稳定哈希，输入 Agent 或配额字典顺序不影响个人安置结果。

详细说明见 [`docs/T4_spatial/README.md`](docs/T4_spatial/README.md)。

### T6：Baseline activity destination zone

- 使用purpose attraction与距离衰减为已有activity分配`destination_zone`；
- 同区选择使用区内平均距离，不把同区视为0 km；
- work、medical和family destination按Agent固定，其他目的按activity判断；
- 只更新destination字段，不生成origin、leg、正式OD或distance字段。

详细说明见 [`docs/T6_destination/README.md`](docs/T6_destination/README.md)。

## 当前核心流程

```text
total_agents
→ 三年龄层人口
→ zone × age_group 精确配额
→ Agent.home_zone
→ Monday–Sunday baseline activities
→ activity.destination_zone
→ T2天气继续/取消判断
→ T3政策优惠与派单资格
```

开发联调显式使用 `total_agents=50`，机制调试建议逐步扩展至100和200；500或1000仅作为后续正式实验候选规模。代码不把50或1000硬编码为通用运行规模。

## 测试

```powershell
python -B -X utf8 -m unittest tests.test_agent_population_t1 -v
python -B -X utf8 -m unittest tests.test_trip_planning_t1 -v
python -B -X utf8 -m unittest tests.test_home_zone_assignment -v
python -B -X utf8 -m unittest tests.test_zone_configuration -v
python -B -X utf8 tests\test_weather_t2.py
python -B -X utf8 -m unittest tests.test_policy_t3 -v
```

## 尚未实现

- 正式 outbound/return legs 与 OD；
- 区内或区际出行距离；
- mode choice；
- 网约车订单、价格计算和优惠实际使用；
- 车辆竞争、派单成功、等待时间与拥堵；
- AgentSociety 端到端仿真。

下一阶段仍需把activity sequence展开为正式legs与OD；当前destination分配不生成origin、distance或mode。

## 合成城市声明

九个功能区、面积、质心、人口权重和年龄空间梯度均为可审计的合成参数，不代表上海真实行政区边界、真实行政区面积或实证通勤矩阵。
