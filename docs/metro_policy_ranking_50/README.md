# 50 Agent 地铁—政策排序实验

## 研究问题

本实验不是预测上海真实客流，而是在同一组 50 Agents、活动、天气、车辆池和随机优先值下，比较加入一条现实可达性的 S1–S2 地铁后，三类政策的相对表现是否改变：

- C0–C3：无券、公共有限券、老年定向券、混合券；
- D0–D3：基线、定向数字培训、家庭协助、老年普遍数字接入；
- R0–R2：先到先得、老年医疗优先、所有老年请求优先。

政策只在各自家族内排序，不把优惠券、数字接入和派单优先解释为同一种政策工具。

## 成对交通情景

- `M0_no_metro`：步行、公交、网约车；
- `M2_realistic_access`：增加一条 S1–S2 地铁，但不假定所有人都能方便到站。

M2 的 S1/S2 稳定 Agent 层覆盖率分别为 0.55/0.40，接驳步行分别为 8/12 分钟。覆盖抽样对同一 Agent 跨天气和政策保持不变。地铁平峰每 30 分钟 2.5 班、高峰 5 班，平均等待分别为 6/3 分钟；高峰窗口为 07:00–10:00、17:00–20:00。地铁速度、班次和成功率不受 W0/W1/W2 天气变化，也不进入道路拥堵。

这些数值是简化机制假设，不是上海观测参数。

## 共同随机条件

每个 seed 的 M0/M2 和各政策保持相同的：

- 人口属性、年龄、就业和老年数字接入基线；
- 工作日/休息日活动及出发时间；
- W0/W1/W2；
- S1/S2 日初网约车车辆池与车辆守恒；
- 网约车基础派单随机优先值；
- 方式选择随机扰动。

因此，M0/M2 的差异来自地铁是否进入可行方式集及其对后续需求、fallback、车辆竞争和道路压力的影响。

## 输出

为避免保存重复的大型逐 Agent 文件，只输出四张紧凑表：

- `policy_macro_per_seed.csv`：每 seed、地铁情景、政策、天气和日期类型的宏观指标；
- `policy_metric_summary.csv`：30 seeds 的均值、标准差、中位数、最小值和最大值；
- `target_group_summary.csv`：年龄、老年数字接入/协助群体的结果；
- `policy_rank_comparison.csv`：M0 与 M2 的政策顺序及是否变化。

排序变化必须结合效应量与 seed 波动解释。均值极其接近、原来并列或只交换末位时，不应称为实质性政策逆转。

## CMD 运行

```bat
cd /d "C:\Users\Jenny Xi\Documents\Urban Cup 2026"
"C:\Users\Jenny Xi\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -B -X utf8 -m scripts.run_metro_policy_ranking_experiment --seed-count 30 --output "E:\Urban Cup-3\outputs\metro_policy_ranking_50"
```

核心代码只保留在当前工程，E 盘仅保存本次紧凑输出，避免维护第二份模型内核。
