# UrbanCup 2026：高温天气下的网约车出行公平模拟

本仓库正在构建一个以上海总体人口结构和空间趋势为参考的九区合成城市，用于研究极端夏季天气、数字接入和出行补贴政策对不同年龄人群潜在出行机会的影响。

当前实现已覆盖 baseline population、home-zone 安置、七日基础活动、活动目的地区域、连续活动—leg时间链、九区多方式交通网络、正常天气分时段基础交通供给、天气响应规则和补贴资格规则。尚未实现 Agent 交通方式选择、订单或派单结果。

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

### T4：九区合成功能城市与 home-zone 安置

- 参考上海“西部传统中心—东部综合副中心—外围新城—产业园区—远郊弱势区”的功能结构，不复刻真实行政区；
- 使用显式目标面积、质心方向、交通邻接和道路绕行系数构建连续主体城市及远郊 Z9；
- 跨区道路、公交和地铁均沿各自配置图寻路；公交距离与时间保留实际所选线路的分段累计结果；
- 动态校准区域年龄构成并生成二维 `zone × age_group` 整数配额；
- 按精确配额为每个 Agent 分配唯一 `home_zone`；
- 使用稳定哈希，输入 Agent 或配额字典顺序不影响个人安置结果。

详细说明见 [`docs/T4_spatial/README.md`](docs/T4_spatial/README.md)。

### T6：Baseline activity destination zone

- 使用purpose attraction与距离衰减为已有activity分配`destination_zone`；
- 同区选择使用区内平均距离，不把同区视为0 km；
- 普通活动使用可调同区偏好系数，提高本区购物、医疗、探访、家庭和社交休闲概率；work不加该系数，以保留年轻郊区居民跨区通勤；
- 跨区距离考虑道路网络绕行；Z1、Z7、Z6分别承担主中心、副中心和产业就业节点作用；
- work和medical destination按Agent固定；家庭活动约80%沿用主要亲属地点、约20%稳定抽取其他亲属地点；
- 只更新destination字段，不生成origin、leg、正式OD或distance字段。

详细说明见 [`docs/T6_destination/README.md`](docs/T6_destination/README.md)。

### T7：九区多方式交通网络

- 以Z1–Z9为节点，配置化生成道路、公交和地铁图；
- 为81组OD生成walk、bus、metro、ride_hailing四种基准方案；
- 输出距离、行驶、接驳、等待、换乘、总时间、费用和换乘次数；
- 距离字段区分质心直线距离、合成道路距离、主方式走行距离和接驳距离，不再输出含义模糊的`effective_distance_km`；
- 九区均有区内道路、公交和网约车；区内地铁按分区覆盖率、实际leg距离和稳定端点覆盖判断，不将有地铁解释为全区任意OD可达；
- Z9无本区地铁站，但可经公交接驳Z6进入地铁；其公交等待和接驳负担更高；本阶段不进行Agent方式选择。

详细说明见 [`docs/T7_transport_network/README.md`](docs/T7_transport_network/README.md)。

### T8：分时段基础交通供给

- 配置化定义早晚肩部、核心高峰、回落、日间平峰和跨午夜夜间共八段；
- 为每条具体leg—mode保留静态基础时间，并另算分时等待、速度、换乘、运营状态和调整后总时间；
- T7静态基础值定义为日间平峰；跨时段候车与车内行程均按实际边界和郊区方向偏移边界切段；
- 正常基础速度与拥堵因子分离：公交/网约车按非高峰1.00、普通高峰0.85、最强方向0.75三选一，步行和地铁恒为1.00；
- 早高峰外围→Z1/Z7/Z6、晚高峰反方向的负荷倍率按区域组生效，并对普通外围/Z9分别采用15/30分钟的小幅相位偏移；
- 地铁按OD反推末班车最晚可行出发时间；Z9公交接驳也按实际时段计算；
- 正常日内网约车车队总量保持不变，`baseline_availability`仅为描述字段且不参与计算；实际车辆占用、空间再分布和派单成功率留给后续供需模块；
- 本层不含天气、Agent偏好、内生拥堵、动态加价或派单。

详细说明见 [`docs/T8_time_supply/README.md`](docs/T8_time_supply/README.md)。

## 当前核心流程

```text
total_agents
→ 三年龄层人口
→ zone × age_group 精确配额
→ Agent.home_zone
→ Monday–Sunday baseline activities
→ activity.destination_zone
→ 连续outbound / between-activities / return-home legs
→ 九区多方式OD备选方案
→ 正常天气分时段leg—mode供给
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
python -B -X utf8 -m unittest tests.test_transport_network -v
python -B -X utf8 -m unittest tests.test_time_dependent_transport_supply -v
```

## 尚未实现

- mode choice；
- 网约车订单、价格计算和优惠实际使用；
- 车辆竞争、派单成功、等待时间与拥堵；
- AgentSociety 端到端仿真。

下一阶段需要把每条Agent leg与多方式OD备选方案连接起来并实现mode choice；当前交通网络不读取Agent属性，也不生成订单或派单结果。

## 合成城市声明

九个功能区、面积、质心、人口权重和年龄空间梯度均为可审计的合成参数，不代表上海真实行政区边界、真实行政区面积或实证通勤矩阵。

## 2026-07-13 时间链更新

当前已生成可审计的活动—leg时间链：Agent身份字段逐行继承到activity；工作到岗、下班时间和工作地点按Agent保持周内一致；活动间保留实际旅行时间并从上一地点连续出发；天气暴露按leg的实际出发—到达区间判断。shopping受10:00–22:00商场营业时间限制。

非工作活动按类型使用不同的离散时长分布，最短30分钟，不设置统一的8小时硬上限；较长的聚会休闲、探访和家庭活动可以超过8小时。若剩余时间窗口不足，程序会优先缩短到该活动类型允许的时长，仍不可行时取消非必要活动，绝不会把开始时间静默改为次日00:00。

返家到达上限为：18–39岁24:00、40–59岁22:00、60+ 20:00。无法同时满足活动时长、活动间旅行、营业时间和返家上限的非必要晚间活动不会生成。当前旅行时间是用于机制测试的generalized travel time（18 km/h、5分钟取整、10–90分钟），尚未替代未来的mode choice、拥堵、等车与派单模块。
