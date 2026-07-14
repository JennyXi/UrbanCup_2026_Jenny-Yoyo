# 简化交通涌现实验

该实验独立于正式天气日历和 T2。它复用现有三方式效用参数，但把 Agent 首轮选择按30分钟、区域和方向汇总为共享系统状态。

```text
年龄分层的工作日/休息日活动
→ W0/W1/W2 首轮方式选择
→ 公交负载、网约车供需、道路速度
→ 一次反馈后的重新选择
→ primary / 一次 fallback
→ 按最终选择重新汇总系统状态
→ 完成、unmet、滞留、费用和暴露
```

活动数量和目的概率读取 main 的年龄分层规则。代表工作日包含 regular 和 part-time worker；休息日不生成 work。W0/W1/W2 共用完全相同的活动日程。

公交容量单位是“代表性 Agent trips/30分钟/方向”，不是一辆真实公交车的物理座位数。全部供给和反馈参数均为机制压力测试假设，不解释为上海实测值。

行为反馈只迭代一次。首轮需求生成 `pre_feedback_system_state`，第二轮选择读取该状态；完成 primary / fallback 后重新汇总报告用的最终 `system_state`。最终公交负载统计成功公交 legs，网约车需求统计所有实际派单尝试（包括 fallback），道路流量统计成功网约车 legs。最终状态不会触发第三轮方式选择，因此仍不是交通均衡或派单仿真。

## 推荐运行

快速检查：

```bat
python -B -X utf8 -m scripts.run_emergence_experiment --seed-count 3 --detail --output outputs\emergence_smoke
```

30-seed基准：

```bat
python -B -X utf8 -m scripts.run_emergence_experiment --seed-count 30 --detail --output outputs\emergence_baseline_30
```

100-seed稳健性：

```bat
python -B -X utf8 -m scripts.run_emergence_experiment --seed-count 100 --output outputs\emergence_baseline_100
```

容量与网约车供给网格：

```bat
python -B -X utf8 -m scripts.run_emergence_sensitivity --seed-count 30 --output outputs\emergence_sensitivity_30
```

不要把单个 seed 或单一供给参数的结果解释为现实预测。优先寻找跨 seed 稳定的方向、容量临界点、级联 fallback 和群体风险差异。
