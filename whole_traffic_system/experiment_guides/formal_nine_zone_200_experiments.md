# 九区200-Agent机制与优惠券实验

本组实验将50-Agent九区综合交通系统扩大到200 Agents，用于检查交通方式选择、有限网约车竞争、天气响应和优惠券外部性是否在更高需求下继续成立。它仍是机制实验，不是上海人口抽样预测。

## 1. 固定条件

- 城市空间：Z1–Z9九个相互连通的功能区；
- 交通方式：walking、bus、metro、ride_hailing，以及公交—地铁接驳组合；
- 天气：W0正常、W1极端高温、W2普通强降雨；
- 日期类型：工作日；
- Agent规模：200；
- 方式选择：A1适度年龄加权户外暴露厌恶；
- 公交和地铁保证上车；网约车实行逐车空间—时间守恒；
- 网约车非运力随机失败关闭；
- 首次交通失败后最多fallback一次；
- 配对设计：同一seed中的政策共用Agents、活动、OD、天气、方式扰动和基础派单优先值。

## 2. 实验顺序

### 2.1 P0基线

先运行W0/W1/W2无政策基线，确认200-Agent人口、活动、九区OD、四方式选择和车辆守恒能够运行。

配置：`config/formal_nine_zone_200_baseline.json`

脚本：`scripts/run_formal_nine_zone_200_baseline.py`

归档：`results/formal_nine_zone_200/formal_nine_zone_200_baseline_smoke`

### 2.2 年龄—天气行为敏感性

比较A0、A1、A2：

- A0：不把预计户外暴露加入方式效用；
- A1：适度户外暴露厌恶；
- A2：较强户外暴露厌恶。

A1被保留为后续机制基线。系数是透明情景假设，不是估计得到的健康支付意愿。

归档：`results/formal_nine_zone_200/formal_nine_zone_200_age_weather_sensitivity`

### 2.3 网约车供给阈值与确认

初筛比较48、36、24、18、12辆空间守恒车辆池；随后用10 seeds确认36辆基线，并以24辆作为紧供给敏感性。

36辆的九区日初分布为：

```text
Z1=6, Z2=6, Z3=3, Z4=3, Z5=3,
Z6=3, Z7=6, Z8=3, Z9=3
```

36辆在W2下呈现适度竞争：10 seeds中60%的seed至少出现一次运力失败，平均失败1.2次、fallback成功1.1次、最终transport_unmet 0.1次。它没有大规模崩溃，适合作为优惠券外部性机制基线。

这里的36辆是“有效可调度车辆池”，不是上海现实车辆/居民比例。正式规模必须结合Agent权重、订单率、车辆周转和分区需求重新标定。

归档：

- `results/formal_nine_zone_200/formal_nine_zone_200_supply_threshold_a1`
- `results/formal_nine_zone_200/formal_nine_zone_200_supply_confirmation_a1_10seeds`

### 2.4 优惠券10-seed实验

四种政策每天使用40张有限八折券：

- C0：无券；
- C1：全年龄有限公共抢券；
- C2：老年定向有限发券；
- C3：70%公共券＋30%老年保留券。

每人每天最多获得1张、最多核销1次。券在第一次网约车请求时绑定，成功后核销，失败后当日失效。C3的社区/电话触达覆盖率为40%，仅用于机制测试。

最终政策确认固定使用以下老年行为候选参数：老年网约车方式常数0.3、恶劣天气户外暴露权重1.6、W1/W2必要出行费用敏感度0.9，以及每次公交—地铁换乘3分钟等价时间负担。这些数值来自200-Agent配对敏感性筛选，只是机制情景假设，不是上海实测系数。

运行命令：

```cmd
python -B -X utf8 -m scripts.run_formal_nine_zone_200_coupon_experiment --config config\formal_nine_zone_200_final_coupon_policy_confirmation.json --seed-start 47 --seed-count 10 --workers 3 --output-dir outputs\formal_nine_zone_200_final_coupon_policy_confirmation_10
```

W2十个seed的均值：

| 政策 | 网约车请求 | 成功 | 失败/fallback | 核销 | 券诱发请求 | 道路车辆量 | 必要活动完成率 |
|---|---:|---:|---:|---:|---:|---:|---:|
| C0 | 64.8 | 63.2 | 1.6 | 0.0 | 0.0 | 1082.1 | 96.94% |
| C1 | 74.8 | 72.1 | 2.7 | 18.6 | 10.0 | 1091.0 | 96.94% |
| C2 | 67.8 | 66.0 | 1.8 | 5.0 | 3.0 | 1084.9 | 96.87% |
| C3 | 72.8 | 69.8 | 3.0 | 14.2 | 8.0 | 1088.7 | 96.94% |

C1和C3稳定增加网约车请求、道路车辆量、等待和派单失败，构成“补贴—新增需求—有限车辆竞争”的队列外部性。fallback吸收了大部分新增失败，因此最终transport_unmet和必要活动完成率没有实质变化。

C2每天定向发出40张老年券，W2平均核销5.0张；数字老人请求由1.9增至3.5，非数字但有家庭协助者由0.7增至2.1。定向券的系统挤出小于公共券，但仍存在“名义覆盖高、实际核销有限”的断层，不能外推为现实老人需求。

W1中C1、C2、C3相对C0的总热风险均小幅下降，C3降幅约0.83%。优惠券能通过减少接驳和候车暴露产生小幅热风险改善，但不是强热健康政策。

归档：`results/formal_nine_zone_200/formal_nine_zone_200_final_coupon_policy_confirmation_10`

## 3. 稳定的机制结论

1. 强降雨使道路公交吸引力下降，地铁与网约车承担更多出行。
2. 有限车辆供给使需求增长转化为局部派单失败；fallback阻止局部失败立即变成活动未完成。
3. 公共优惠券的需求诱导最强，且可能对未用券乘客产生队列外部性。
4. 老年定向券的名义覆盖高、实际核销低，价格补贴不能替代数字接入、活动需求和服务可达性。
5. 混合券在公共券利用效率与老年覆盖之间折中，但不能自动保证老年人实际受益。

## 4. 大规模正式实验如何继承

正式实验应继承机制和相对政策，不应机械继承绝对数量：

```text
继承：车辆逐车守恒、天气行为、fallback、优惠券规则、共同随机数和输出指标。

重新标定：
Agent代表居民权重
→ Agent trips换算为PCU
→ 九区网约车有效车辆池
→ 道路容量和背景V/C
→ 活动、OD和出发时间分布。
```

正式规模至少运行P0、C1、C2、C3，并用相同seed配对比较。建议先做单seed性能与守恒检查，再做3-seed全政策冒烟，最后运行不少于10 seeds的正式比较。若正式Agent数量很大，可在seed之间并行，但每个seed内部的车辆事件顺序必须保持串行。

## 5. 输出与限制

- `system_per_seed.csv`：每个seed、政策和天气的宏观结果；
- `system_distributions.csv`：10 seeds均值、标准差、中位数、最小和最大值；
- `group_per_seed.csv`、`group_distributions.csv`：年龄与数字接入群体结果；
- `coupon_allocations.csv`、`coupon_outcomes.csv`：触达、参与、获得和核销；
- `ride_hailing_dispatch.csv`：逐订单车辆、等待、成功与失败；
- `consistency_checks.csv`：车辆守恒和政策配对检查，40/40通过。

`mean_experienced_road_leg_speed_kmh`是成功道路legs的组成均值，可能随OD与方式构成变化，不能单独解释为全城拥堵改善。旧字段`mean_road_speed_kmh`仅作为兼容alias保留。当前政策拥堵方向主要依据`road_vehicle_volume`与V/C；正式模型应比较固定道路、方向和时间段的V/C与速度。
