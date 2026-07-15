# 两区三方式简化交通涌现实验

## 1. 实验目标

本实验是独立于正式九区模型、T2天气日历和后续政策模块的机制测试。它不试图预测上海真实交通量，而是用一个透明、可复现的最小模型回答：

> 在活动需求、天气和必要出行给定的情况下，年龄、数字接入、家庭协助与共享交通供给如何共同决定 Agent 是否出行、选择何种方式、是否需要 fallback，以及谁最终无法完成必要活动？

实验保留人口与活动异质性，但将城市压缩为两个区域、三种方式和两个代表日，以便识别个体选择如何汇总成公交拥挤、网约车等待、道路拥堵和必要活动未完成等宏观结果。

所有供给、容量、成功率和反馈强度都是机制压力测试假设，不是上海实测参数。结果应解释为模型内部的方向性和临界现象，而不是现实预测。

## 2. 实验结构

```text
50个异质Agent
→ 工作日与休息日活动计划
→ 配对的W0/W1/W2天气情景
→ remote work或非必要活动天气取消
→ 第一轮walking/bus/ride_hailing选择
→ 按30分钟汇总公交、网约车和道路状态
→ Agent读取共享状态后重新选择一次
→ primary失败后最多一次fallback
→ 按最终结果重新汇总交通系统
→ 活动完成、unmet、滞留、费用和天气暴露
```

W0、W1、W2使用同一组 Agent 和完全相同的基础活动日程。同一个 activity 的 `agent_id`、目的、出发时间、起终点和距离均保持不变，只改变天气及其对应参数，从而减少天气效应与活动目的、时间窗口混淆。

## 3. 空间与交通方式

### 3.1 两区空间

| 区域 | 功能 | 区内距离 | 公交覆盖 | 公交接驳 | 基础候车 |
|---|---|---:|---:|---:|---:|
| S1 | 中心就业核心 | 1.8 km | 0.90 | 4 min | 6 min |
| S2 | 外围居住混合区 | 2.5 km | 0.55 | 9 min | 12 min |

S1与S2中心相距4.5 km，由一条公交线路B1连接。60%的 Agent 居住在S2、40%居住在S1。S2公交覆盖较弱，用于保留 main 模型中“中心服务较好、外围服务较弱”的核心空间差异。

### 3.2 三种方式

- `walk`：4.8 km/h，最长可接受距离6 km，费用为0；
- `bus`：18 km/h，基础票价2元，S1与S2的接驳和候车不同；
- `ride_hailing`：32 km/h，包含接驾等待、行驶时间、里程费和时长费。

当前网约车计价是简化模拟基准：最低消费14元、2.3元/km、0.5元/min，超过20 km的部分增加0.8元/km。该参数不是2026年滴滴官方统一价格。

暂未加入 metro，也没有扩展九区空间。

## 4. Agent属性

### 4.1 年龄与人口结构

50个 Agent 按 main 的成年人比例生成：

| 年龄组 | 比例 | 50人代表数量 |
|---|---:|---:|
| 18–39 | 40% | 20 |
| 40–59 | 33% | 17 |
| 60+ | 27% | 13 |

每个 Agent 具有稳定的 `agent_id`、`age_group`、`age_range`、`is_elder` 和基于年龄的方式偏好与时间价值。固定 seed 下人口和个人属性可复现。

### 4.2 工作身份

Agent可属于：

- `regular_worker`；
- `part_time_worker`；
- `flexible_non_worker`；
- `retired`。

常规就业者的到岗时间分布在08:00–10:30，兼职就业者通常在10:00或10:30开始，且工作时长较短。工作日生成 work，休息日不生成 work。工作安排对同一 Agent 保持稳定。

### 4.3 老年人的数字介入

实验明确考虑了老年人的数字接入差异，而且该差异实际进入交通方式可用性，不只是输出标签。

60+ Agent分别具有：

- `smartphone_access`：是否拥有智能手机；
- `digital_access`：是否能独立完成应用注册、叫车等数字操作；
- `family_assistance`：是否有家庭成员协助使用数字服务；
- `medical_need_level`：`low`、`standard`或`high`。

当前人口生成采用：老年智能手机拥有率87.3%、独立数字接入率48.3%、家庭协助率68%。前两项沿用当前人口模块口径，家庭协助率属于实验假设。代表性50人中通常有13名老年人，其中约11人拥有智能手机、6人可以独立使用数字服务；家庭协助人数随 seed 稳定抽样。

网约车可用性规则为：

```text
ride_hailing_access = digital_access OR family_assistance
```

因此：

- 数字接入者可以独立选择网约车，但不会被强制打车；
- 非数字老年人有家庭协助时仍可使用网约车；
- 非数字接入且无家庭协助者不能选择网约车，也不能在 fallback 中选择网约车；
- `digital_access`不改变天气取消概率，只影响交通方式可用性及交通失败风险。

目前建模的是“数字接入异质性”，还不是手机培训、线下叫车或代叫平台等政策干预。后续可将这些政策表现为提高独立接入或家庭/人工协助可用性。

## 5. 活动设计

实验包含一个代表工作日和一个代表休息日。活动目的包括：

- `work`；
- `medical`；
- `shopping`；
- `social_leisure`；
- `visit`；
- `out_of_home_family_activity`；
- `out_of_home_family_care`。

活动数量与目的概率使用 main 的年龄分层规则。medical数量低于普通活动；年轻 Agent 平均外出活动较多，老年 Agent 的医疗概率受 `medical_need_level`影响。

每条活动具有目的、日期类型、出发和返回时间、起终点、距离、必要性、最大可接受单程时间和预算。work和medical被视为必要活动；shopping、social/leisure等为可取消的非必要活动。

## 6. 天气、remote work与活动取消

天气情景为：

- W0：正常天气；
- W1：短时极端高温；
- W2：普通强降雨，不代表台风、自然灾害或停工情景。

### 6.1 Work

work不进入普通 `weather_cancellation`。每个 work activity 在活动层只抽样一次 remote work，去程和返程不会分别抽样：

| 天气 | 暴露work转为remote work的总概率 |
|---|---:|
| normal | 0% |
| extreme heat | 2% |
| heavy rain | 5% |

高温和降雨概率只作用于出发时间落在对应天气窗口内的 work；未暴露 work 使用 normal 的0%。这些比例是情景假设，不是上海实测居家办公率。

抽中 remote work 后，work视为完成，不生成去程或返程，不产生交通费用和天气暴露。未抽中则继续要求通勤。

### 6.2 Medical与非必要活动

- medical不进入普通天气取消，本轮继续要求出行；
- shopping、social/leisure等保留天气取消；
- 被取消活动不生成 leg，也不产生交通暴露；
- 暂未加入 schedule shift。

## 7. 交通方式选择

只有 `travel_required=true` 的活动进入交通方式选择。Agent并非按天气被直接指定方式，而是在可用方式中比较效用：

```text
utility
= 年龄方式常数
+ 天气方式preference
- generalized_cost_weight × (时间价值折算时间 + 费用)
+ 固定seed的个体随机项
```

方式效用同时读取：

- 步行、公交和网约车时间；
- 公交接驳与候车；
- 网约车等待和费用；
- 年龄方式偏好；
- W1/W2对三种方式的 preference；
- S1/S2公交覆盖；
- 数字接入与家庭协助；
- 第一轮形成的公交拥挤、网约车等待和道路速度。

天气只能改变效用、速度、等待和价格，不能直接命令 Agent 选择某种方式。W1/W2下步行仍然可用，因此短距离出行保留少量步行可能。

## 8. 必要活动状态机与fallback

交通阶段采用：

```text
primary mode
→ 成功：记录最终方式
→ 失败：移除首次失败方式
→ 使用剩余时间和预算选择一次fallback
→ fallback仍失败：transport_failure
```

最多只允许一次 fallback。首次失败产生的等待、费用和户外暴露不会被删除。

- work或medical去程最终失败：`transport_related_unmet=true`；
- shopping等非必要活动交通失败：不计入 mandatory unmet；
- 必要活动去程成功后即视为完成；
- 返程单独模拟；
- 返程仍参与交通系统负荷模拟，但不再作为活动评价指标或活动最终状态；活动完成只由去程成功并完成活动决定。

## 9. 户外天气暴露与热风险负担

每条实际发生或尝试的 leg 首先计算户外时间：

- walking：整个出行时间计入户外暴露；
- bus：步行接驳时间加候车时间；
- ride_hailing：接驾等待时间；
- 失败尝试：保留已经发生的等待和暴露；
- 取消活动与remote work：暴露为0。

W1保留 `heat_exposure_index` 作为户外暴露分钟数，并新增分时间片的环境热剂量：

```text
heat_hazard_dose_c_min
= 户外分钟
 × outdoor_segment_factor
 × max(该30分钟UTCI - 26°C, 0)
```

户外片段跨越30分钟边界时按实际分钟拆分。当前W1使用配置文件中的合成UTCI情景曲线：06:00为27°C、08:00为30°C、12:00为38°C、14:00–14:30达到42°C，之后逐步下降。该曲线只用于体现早晨、午间和午后的热压力差异，不是上海观测气象数据。

例如，同样在户外10分钟：

```text
08:00：10 × (30 - 26) = 40 UTCI °C·min
14:00：10 × (42 - 26) = 160 UTCI °C·min
```

环境热剂量不随年龄或数字接入变化。个体风险负担在剂量计算后乘以年龄情景权重：

```text
heat_risk_burden
= heat_hazard_dose_c_min × age_vulnerability_weight
```

| 年龄组 | 基准权重 | 敏感性范围 |
|---|---:|---:|
| 18–39 | 1.00 | 1.00–1.00 |
| 40–59 | 1.10 | 1.00–1.20 |
| 60+ | 1.30 | 1.15–1.50 |

权重是机制敏感性假设，不是临床风险比。`digital_access`和`family_assistance`不会改变生理权重，只能通过改变可用方式、步行和等待间接改变暴露。当前各方式的 `outdoor_segment_factor` 均为1，避免在没有遮阴或站棚数据时人为偏袒某种方式；未来公交站棚政策可以单独改变该因子。

失败尝试已经发生的热剂量保存在 `failed_attempt_heat_hazard_dose_c_min`，不会因fallback成功而被删除。活动层汇总去程和返程；宏观层同时输出总热剂量、年龄加权总风险、必要活动风险和每个完成必要活动的平均风险。

W0和W2的热剂量为0；W2继续单独输出 `rain_exposure_index`。降雨暴露不会进入热风险负担。新增热指标只作为结果，不进入方式效用，避免与现有天气preference重复计数。

## 10. 共享交通系统反馈

第一轮选择按30分钟汇总：

- 公交：`day_type × time_bin × direction`；
- 网约车：`day_type × time_bin × origin_zone`；
- 道路：`day_type × time_bin`。

需求增加后会形成：

- 公交负载、额外候车和拥挤效用惩罚；
- 网约车需求/供给比、额外等待和派单成功率；
- 网约车道路流量与道路速度下降。

行为反馈只迭代一次。第一轮状态保存为 `pre_feedback_system_state`，Agent读取它进行一次重新选择。primary/fallback完成后，模型重新汇总最终 `system_state`：

- 最终公交负载统计成功公交 legs；
- 最终网约车需求统计所有实际派单尝试，包括 fallback；
- 最终道路流量统计成功网约车 legs。

最终状态不会触发第三轮方式选择，因此仍不是交通均衡或完整派单仿真。

## 11. 修复后的30-seed基准结果

以下为30个 seed 的均值。方式份额以成功发生的 legs 为分母。

| 情景 | walking | bus | ride_hailing | fallback均值 | transport unmet均值 |
|---|---:|---:|---:|---:|---:|
| W0工作日 | 24.4% | 60.4% | 15.2% | 0.17 | 0.00 |
| W1工作日 | 15.1% | 59.9% | 25.0% | 4.00 | 0.07 |
| W2工作日 | 8.4% | 56.2% | 35.4% | 11.20 | 0.63 |
| W0休息日 | 29.0% | 55.0% | 16.0% | 0.57 | 0.00 |
| W1休息日 | 18.7% | 53.9% | 27.4% | 4.67 | 0.03 |
| W2休息日 | 11.6% | 51.8% | 36.6% | 12.70 | 0.17 |

活动层结果：

| 情景 | 计划活动 | travel required | 天气取消 | remote work |
|---|---:|---:|---:|---:|
| W0工作日 | 53.03 | 53.03 | 0.00 | 0.00 |
| W1工作日 | 53.03 | 50.67 | 1.73 | 0.63 |
| W2工作日 | 53.03 | 47.60 | 3.73 | 1.70 |
| W0休息日 | 61.70 | 61.70 | 0.00 | 0.00 |
| W1休息日 | 61.70 | 56.17 | 5.53 | 0.00 |
| W2休息日 | 61.70 | 48.23 | 13.47 | 0.00 |

跨 seed 的稳定方向包括：

- walking满足W0 > W1 > W2：工作日30/30、休息日30/30；
- ride_hailing满足W0 < W1 < W2：工作日30/30、休息日29/30；
- W2 fallback高于W0：工作日30/30、休息日30/30；
- 公交在W0/W1/W2均保持最大方式份额；
- W2最终道路速度低于W0：工作日28/30、休息日23/30。

这些是当前参数与结构下的候选涌现规律，不是现实预测。

## 12. 供给敏感性实验

敏感性网格包含：

- 公交班次倍数：0.6、0.8、1.0、1.2、1.5；单车容量固定；
- 网约车供给倍数：0.6、1.0、1.4；
- W0/W1/W2；
- 工作日与休息日；
- 每个组合30个 seed。

在网约车供给倍数1.0时，出现至少一个公交超载时间片的 seed 比例为：

| 公交班次倍数 | W0工作日 | W1工作日 | W2工作日 | W0休息日 | W1休息日 | W2休息日 |
|---:|---:|---:|---:|---:|---:|---:|
| 0.6 | 33.3% | 46.7% | 40.0% | 93.3% | 83.3% | 90.0% |
| 0.8 | 30.0% | 23.3% | 26.7% | 80.0% | 63.3% | 53.3% |
| 1.0 | 0.0% | 3.3% | 0.0% | 43.3% | 43.3% | 23.3% |
| 1.2 | 0.0% | 0.0% | 0.0% | 16.7% | 20.0% | 6.7% |
| 1.5 | 0.0% | 0.0% | 0.0% | 3.3% | 3.3% | 0.0% |

休息日虽然没有work，但购物、社交和探访活动可能集中，同时休息日公交供给较低，因此仍可出现比工作日更高的公交压力。

在公交班次倍数1.0、W2下，提高网约车供给的结果为：

| 供给倍数 | 日类型 | 网约车份额 | 平均系统额外等待 | 最低道路速度乘数 | fallback |
|---:|---|---:|---:|---:|---:|
| 0.6 | 工作日 | 32.6% | 1.80 min | 0.866 | 12.37 |
| 1.0 | 工作日 | 35.4% | 1.11 min | 0.847 | 11.20 |
| 1.4 | 工作日 | 35.8% | 0.81 min | 0.846 | 11.17 |
| 0.6 | 休息日 | 31.7% | 2.13 min | 0.857 | 14.60 |
| 1.0 | 休息日 | 36.6% | 1.34 min | 0.818 | 12.70 |
| 1.4 | 休息日 | 38.5% | 0.98 min | 0.799 | 11.83 |

这说明网约车供给增加会降低等待、提高使用份额并通常减少 fallback，但更多成功网约车出行也可能降低道路速度。必要活动未完成尚未呈现严格单调改善，因此不能宣称“增加网约车供给一定提高必要活动完成率”。

修复后的全部30组“公交班次 × 天气 × 日类型”组合，其最终道路速度都会随网约车供给变化；旧版本中第二轮选择未回写道路状态的问题已修复。

## 13. 输出文件

基准实验输出：

- `per_seed_macro.csv`；
- `per_seed_age_access_group.csv`；
- `distribution_summary.csv`；
- `emergence_direction_checks.csv`；
- `paired_schedule_identity_audit.csv`；
- `experiment_metadata.json`；
- 可选 activity、leg和time-bin明细。

敏感性实验输出：

- `sensitivity_per_seed.csv`；
- `sensitivity_aggregate.csv`。

生成结果默认位于本地 `outputs/`，该目录不提交Git，避免把大量可再生文件写入仓库。

## 14. 运行方法

快速检查：

```bat
python -B -X utf8 -m scripts.run_emergence_experiment --seed-count 3 --detail --output outputs\emergence_smoke
```

30-seed基准：

```bat
python -B -X utf8 -m scripts.run_emergence_experiment --seed-count 30 --output outputs\emergence_baseline_30_final_feedback
```

完整供给敏感性网格：

```bat
python -B -X utf8 -m scripts.run_emergence_sensitivity --seed-count 30 --output outputs\emergence_sensitivity_30_final_feedback
```

100-seed稳健性：

```bat
python -B -X utf8 -m scripts.run_emergence_experiment --seed-count 100 --output outputs\emergence_baseline_100
```

测试：

```bat
python -B -X utf8 -m unittest tests.test_emergence_experiment -v
python -B -X utf8 -m unittest discover -s tests -v
```

## 15. 热暴露定义与阈值敏感性（当前版本）

主实验使用 `heat_stress_threshold_c = 26`，并用 32°C 做独立敏感性比较。阈值只进入出行后的热剂量核算，不进入方式效用、等待、派单成功率或活动状态，因此改变 26/32°C 阈值不得改变 walking、bus、ride_hailing 的选择结果。

W1 的含义分成两层：

- W1 整天按完整的 48 个半小时 UTCI 时间片计算环境热剂量；跨午夜行程按 24 小时周期映射到次日时间片。
- 11:00–18:00 只是极端高温行为窗口，用于触发天气取消、remote work 和 W1 交通偏好变化。窗口外仍计算热剂量，但不触发这些行为变化。

环境热剂量为：

```text
heat_hazard_dose_c_min
= outdoor_segment_minutes × max(UTCI_at_segment - threshold, 0)
```

年龄加权热风险为环境热剂量乘以年龄情景权重。它是模型内比较指标，不是临床风险概率。

公交一次成功尝试拆成 `起点步行 → 候车 → 车内 → 终点步行`；只有起点步行、候车和终点步行计入户外暴露。公交失败发生在完成起点步行和候车后、上车前，因此失败尝试不计车内时间和终点步行。fallback 从 `原始出发时间 + 首次实际消耗时间` 开始，已发生暴露只保留一次。

`outdoor_exposure_minutes` 是正式字段。为了兼容旧 CSV，暂时保留 `heat_exposure_index`，并用 `heat_exposure_index_is_outdoor_minutes_alias = true` 明确标记；旧字段只是 W1 户外暴露分钟数，不是热风险指数，不能替代 `heat_hazard_dose_c_min` 或 `heat_risk_burden`。

必要活动同时报告：

- `necessary_activity_completion_rate`；
- `transport_related_unmet`；
- `heat_risk_per_completed_travel_required_necessary_activity`；
- `planned_travel_required_necessary_activities`；
- `heat_risk_per_planned_travel_required_necessary_activity = necessary_heat_risk_burden / planned_travel_required_necessary_activities`。

运行 26/32°C 小型敏感性实验：

```bat
python -B -X utf8 -m scripts.run_heat_threshold_sensitivity --seed-count 3 --output outputs\heat_threshold_sensitivity_3
```

该脚本比较基准、公交班次增加和网约车供给增加三个情景，输出逐 seed、均值和政策排序文件。所有 UTCI 曲线、阈值与年龄权重都是透明的机制实验假设，不是上海实测气象或医学参数。

## 16. 统一活动—交通—热风险结果表

`run_heat_threshold_sensitivity` 现在生成 `unified_per_seed_macro.csv`。每一行由以下五个维度唯一确定：

```text
seed + weather_scenario + day_type + policy + heat_threshold_c
```

当前阈值实验的 `weather_scenario` 为 W1；同一行同时包含活动完成结果、交通系统结果和热暴露结果。活动最终状态使用互斥分类：

- `completed`：去程成功并完成活动，或work通过remote work完成；返程结果不改变该状态；
- `weather_cancelled`：出发前主动取消；
- `transport_unmet`：需要出行，但去程最终失败；

一致性审计使用 `completed + weather_cancelled + transport_unmet = planned_activities`。返程结果不进入这一活动最终状态分类。

交通需求口径如下：

- `bus_demand`：公交首次尝试和公交 fallback 尝试总数；
- `ride_hailing_requests`：全部实际网约车派单尝试；
- `successful_ride_hailing_requests`：最终成功的网约车 legs；
- `failed_ride_hailing_requests`：派单请求减去成功请求；
- `road_vehicle_volume`：计划公交车辆班次与成功网约车车辆之和；
- 公交和网约车平均等待时间均以相应方式的实际尝试为分母；
- `mean_road_speed_kmh` 以公交车辆和成功网约车的共享道路车辆流量加权。

`policy_changes_vs_p0.csv` 逐 seed 输出 P1/P2 相对 P0 的绝对变化和百分比变化。P0 分母为0时，`percent_change` 留空、`percent_change_defined=false`、`undefined_reason=baseline_zero`，不生成无穷值。

`consistency_checks.csv` 检查方式 legs 守恒、失败与 fallback 守恒、活动最终状态互斥、方式份额、政策不改变计划活动，以及26/32°C阈值只改变热剂量而不改变活动、方式、等待、费用或拥堵。

## 17. 公交班次政策与共享道路口径

P1正式定义为公交班次增加50%，不是单车容量增加：

```text
单车容量 = 固定的6个代表性乘客
平峰班次 = 2车次/30分钟（双向合计）
早晚高峰班次 = 4车次/30分钟（双向合计）
P1班次 = 基准班次 × 1.5
时段方向总载客能力 = 单车容量 × 该方向班次数
公交负载率 = 选择公交的乘客legs ÷ 时段方向总载客能力
```

因此，Agent选择决定公交乘客需求和负载；P1改变班次，从而同时改变候车间隔、时段总载客能力和道路公交车辆数。单车容量不随P0/P1/P2变化。

公交与网约车共享道路：

```text
road_vehicle_volume
= scheduled_bus_vehicle_trips
+ successful_ride_hailing_vehicle_trips
```

同一个 `dynamic_congestion_multiplier` 同时乘到公交和网约车车内速度。公交候车不受道路速度直接影响，只由班次频率、天气等待倍数和既有拥挤等待规则决定。

等待负担正式输出总公交等待、总网约车等待、两者之和，以及按公交尝试和网约车请求计算的平均等待。fallback只在宏观表中保留 `fallback_attempts` 和 `fallback_successes`。

分析时，活动、方式、等待、失败与拥堵等非热指标只读取26°C行；32°C行只用于检查热剂量和年龄加权热风险对阈值的敏感性。

## 15. 当前限制与下一步

当前实验仍有以下边界：

- 只有S1、S2和一条公交线路；
- 没有metro；
- 只有一次行为反馈，不求解交通均衡；
- 没有车辆级派单、空驶、拼车和司机行为；
- 没有跨日学习、长期适应或居住/就业迁移；
- 数字介入尚未建模为可改变的政策；
- 供给与行为参数未使用上海观测数据校准；
- 50个代表性 Agent 用于机制测试，不代表城市人口规模。

下一步应先用100 seeds复核候选规律和年龄—数字接入群体差异，再考虑加入数字协助政策、背景道路流量或更复杂空间。不要根据单个 seed 调参，也不要把结果解释为现实预测。

## 16. 关键文件

- 配置：`config/emergence_experiment.json`；
- 天气与状态机配置：`config/symmetric_weather_experiment.json`；
- 模型：`custom/agents/emergence_experiment.py`；
- 基准脚本：`scripts/run_emergence_experiment.py`；
- 敏感性脚本：`scripts/run_emergence_sensitivity.py`；
- 测试：`tests/test_emergence_experiment.py`。
