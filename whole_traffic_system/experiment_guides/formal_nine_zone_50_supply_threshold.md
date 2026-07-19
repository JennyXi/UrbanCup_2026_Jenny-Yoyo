# 50-Agent 网约车供给阈值实验

本实验是正式政策实验之前的机制筛选，不是上海真实车辆规模校准。它使用九区、50 Agents、walking、bus、metro、ride_hailing，并保持同一 seed 下的 Agent、活动、OD、天气、方式选择扰动和派单优先值不变。

## 设计

- 工作日，W0与W2。
- seeds 47–56，共10个 seeds。
- 日初网约车池：16、12、10、8、6辆。
- 车辆池严格空间嵌套：降低供给时只移除车辆，不在区域之间重新配置车辆。
- 竞争事件：一次 seed-weather 运行中至少出现一次网约车容量失败。
- 系统失效事件：fallback之后仍至少有一个 `transport_unmet`。

## 结果

| 车辆 | W0竞争seed比例 | W2竞争seed比例 | W0/W2系统失效比例 |
|---:|---:|---:|---:|
| 16 | 10% | 30% | 0% / 0% |
| 12 | 10% | 50% | 0% / 0% |
| 10 | 60% | 80% | 0% / 0% |
| 8 | 70% | 90% | 0% / 0% |
| 6 | 80% | 100% | 0% / 0% |

12辆被选为后续50-Agent政策实验的候选基准：W2已有稳定但不普遍的车辆竞争，W0仍相对宽松，且没有fallback后的最终交通未满足。该选择来自预先声明的机制规则，不代表现实最优车辆数。

供给从16辆降到6辆时，W2平均网约车失败由0.4次增至5.3次，平均每请求等待由11.21分钟增至14.74分钟，总通行时间由63.68分钟增至66.15分钟。公交和地铁fallback吸收了全部首次派单失败，因此本实验范围内没有找到系统失效阈值。

## 运行

```cmd
"C:\Users\Jenny Xi\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -B -X utf8 -m scripts.run_formal_nine_zone_50_supply_threshold
```

输出位于 `outputs/formal_nine_zone_50_supply_threshold`。核心文件为 `per_seed_macro.csv`、`aggregate_means.csv`、`metric_distributions.csv`、`supply_classification.csv`、`monotonicity_audit.csv` 和 `candidate_baseline.json`。
