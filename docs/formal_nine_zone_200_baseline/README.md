# 九区正式交通系统：200 Agents P0 冒烟实验

## 目的

本实验只回答一个问题：把已经通过50人机制检查的九区系统同比放大到200人后，P0无政策基线是否仍保持车辆守恒、结果口径一致和可解释的交通竞争。它不是正式政策结论，也不是上海人口规模预测。

## 尺度

- 人口：50增至200。
- 网约车：按已选50人/12辆基准同比增至48辆；工作日初始分布为Z1=8、Z2=8、Z3=4、Z4=4、Z5=4、Z6=4、Z7=8、Z8=4、Z9=4。
- 公交与地铁：保留原班次、速度、线路和换乘规则，不因Agent数增加而自动增班。
- 公交与地铁不设满载拒乘；有限供给竞争只发生在网约车。
- 行为参数、天气偏好、活动规则和fallback规则均不改变。
- 道路流量仍是实验中的代表性压力指标，不解释为真实全市车流。

## 第一阶段运行范围

同一seed内使用同一批Agents、活动和OD，比较W0普通天气、W1极端高温与W2普通强降雨的工作日P0基线。默认先跑3 seeds；通过后再把`--seed-count`改为10。优惠券、数字接入和老年优先派单都不在本步骤启用。

```bat
"C:\Users\Jenny Xi\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -B -X utf8 -m scripts.run_formal_nine_zone_200_baseline --seed-count 3 --output-dir "outputs\formal_nine_zone_200_baseline_smoke"
```

## 输出

- `system_per_seed.csv`：每个seed、天气一行，包含活动完成、方式份额、网约车请求/成功/失败、fallback、等待、总时间、费用、道路压力和暴露；请求、失败等另有每100 Agents指标。
- `system_distributions.csv`：各宏观指标跨seed的均值、标准差、最小值和最大值。
- `mode_choices.csv`：逐leg最终选择与到达结果。
- `ride_hailing_dispatch.csv`：逐请求车辆、等待、成功和失败原因。
- `vehicle_end_states.csv`：每日车辆期末状态与区域。
- `population_by_seed.csv`：人口属性，便于审计年龄与数字接入构成。
- `consistency_checks.csv`：200人、九区覆盖、车辆守恒、同车不重叠、方式计数和有限非负输出检查。

只有全部一致性检查通过，且P0没有出现几乎零竞争或大规模系统崩溃，才进入200人政策实验。后续顺序仍为：优惠券 → 老年数字接入 → 老年优先派单；所有政策与P0共享Agents、活动、天气、车辆池和基础随机数。
