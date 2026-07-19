# Agent 决策关联与顺序共享状态系统

## 先回答：原实验中的 Agent 决策是否关联

不是完全独立，但关联是“批量、间接、有限”的。

- `simple_experiment.py` 先统计首轮网约车请求，再统一增加第二轮网约车等待时间。
- `formal_nine_zone_experiment.py` 先汇总首轮成功网约车与计划公交车辆，形成 30 分钟道路流量，再在下一轮用动态拥堵时间重算方式选择。
- 修改前的 `dynamic_road_congestion.json` 把全局状态注册表标记为尚未实现。因此，原模型没有记录“A 的决定写入状态，随后 B 读取状态并改变各方式概率”的逐事件因果链。现在该配置会指向新增的 `SharedTrafficStateRegistry`，同时明确原有正式批量基线并不自动使用顺序注册表。

原批量反馈适合已有基线和政策对照；本目录新增的系统用于显式研究 Agent 间的决策依赖。

## 新系统的因果链

```text
A 读取当前道路状态
  -> 计算 walk / bus / metro / ride_hailing 概率
  -> A 选择 ride_hailing
  -> 立即向共享状态提交一笔网约车 PCU 流量
  -> 同走廊、同方向、同 30 分钟时段内的 B 读取更新后状态
  -> B 的公交与网约车时间改变
  -> B 的四种方式效用及选择概率改变
  -> B 的决定继续影响后续 Agent
```

共享状态键是：

```text
(corridor_id, direction, time_bin)
```

每个 Agent 使用多项 Logit 概率：

```text
P(mode=m) = exp(U_m / temperature) / sum_j exp(U_j / temperature)
```

其中 `U_m` 沿用正式九区模型的时间成本、票价、迟到成本、年龄偏好、天气偏好和户外暴露惩罚。道路方式的时间由现有 T10 BPR 动态拥堵层计算。

## 关键设计

1. 按出发时间顺序决策；同一时刻用固定种子哈希排序，保证可复现。
2. 每次决策后立即发布状态，不等整轮 Agent 全部完成。
3. 乘客选择公交不新增公交车辆；公交计划班次作为外生基础流量。选择网约车才新增道路车辆流。
4. 每次 B 决策同时计算两组概率：
   - 不含先前 Agent 的反事实概率；
   - 包含先前 Agent 交通流的实际概率。
5. 两组概率之差直接写入审计表，并输出 A→B 影响边。

## 文件

- `config/interdependent_agent_decisions.json`：机制、Logit 温度、状态键、Agent 放大权重和审计开关。
- `custom/agents/interdependent_decision_system.py`：共享状态注册表与顺序决策引擎。
- `scripts/run_interdependent_agent_decisions.py`：实验入口。
- `tests/test_interdependent_decision_system.py`：状态传播、概率归一化和真实九区 Agent 影响链测试。

## 运行

在仓库根目录执行：

```powershell
python scripts/run_interdependent_agent_decisions.py
```

也可指定情景：

```powershell
python scripts/run_interdependent_agent_decisions.py `
  --weather-scenario W2 `
  --day-type workday `
  --seed 47 `
  --output-dir outputs/interdependent_w2
```

输出包括：

- `decision_audit.csv`：每个 Agent 的前后流量、反事实/实际概率、概率变化、选择结果和影响源。
- `traffic_state_events.csv`：每次网约车选择如何修改共享状态。
- `traffic_state_final.csv`：每个状态桶的最终内生流量。
- `influence_edges.csv`：先前网约车决策到后续 Agent 决策的有向影响边。
- `summary.json`：受影响决策数、最大概率变化和方式数量。

## 固定种子验证示例

默认配置（W0、workday、seed 47、50 Agents）产生 97 次 leg 决策、8 次网约车交通事件；18 次后续决策的方式概率受到先前 Agent 影响，最大单项概率变化为 0.024700414。

其中，Agent 8 的 `8-2026-07-07-L01` 在 08:00 状态桶选择网约车后，Agent 30 的 `30-2026-07-07-L01` 读取到该新增流量，其概率发生如下变化：

| 方式 | 无先前 Agent | 有先前 Agent | 变化 |
|---|---:|---:|---:|
| bus | 0.677880626 | 0.665685342 | -0.012195284 |
| metro | 0.192902756 | 0.202145169 | +0.009242414 |
| ride_hailing | 0.129216618 | 0.132169488 | +0.002952870 |

这些数值用于验证因果机制与审计链是否工作，不应作为政策结论。

## 参数解释与限制

默认 `represented_trips_per_agent = 120`，表示小样本中的一个 Agent 代表 120 个相似出行者，使机制效应在 50-Agent 实验中可观测。它是敏感性分析假设，不是由上海数据标定的扩样系数，正式报告应至少比较低、中、高三档。

当前版本使用代表性全市走廊，并把一次网约车选择的流量计入其出发时段；尚未做路段级路径分配、跨时段车辆占用传播、Agent 社交模仿或实时网约车价格反馈。这些可在同一状态注册表上继续增加，但不应把当前输出解释为真实城市预测。
