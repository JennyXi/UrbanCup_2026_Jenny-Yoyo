# T2：天气引起的额外活动扰动层

T2 在 Agent 交通方式选择之前执行，固定流程为：

```text
计划活动生成
→ 补充计划去程起点与活动到达时间
→ T2 天气取消判断
→ 只为保留活动生成/保留 leg
→ Agent 交通方式选择
```

T2 只负责天气引起的额外活动取消和网约车偏好信号，不选择实际交通方式，不生成网约车订单或需求，不修改交通供给，也不向 T10 手工添加天气需求。已取消活动不会进入方式选择、网约车需求或 T10 道路流量。T9 负责天气对速度、等待、容量和客观可用性的影响；T10 只读取方式选择后相对基准产生的道路新增流量。

## 参数与来源定位

所有参数集中在 `config/weather_activity_disruption.json`，并统一标记为：

```yaml
source_type: model_assumption
calibration_status: sensitivity_analysis
not_database_estimate: true
```

这些数值是比赛 MVP 模型假设，不是 `yoyo_database` 的估计结果。

| 参数 | 低 | 基准 | 高 |
|---|---:|---:|---:|
| 高温基础取消率 | 0.08 | 0.12 | 0.18 |
| 暴雨基础取消率 | 0.18 | 0.28 | 0.40 |
| 60+ 年龄乘数 | 1.10 | 1.25 | 1.40 |
| medical | 0.25 | 0.30 | 0.40 |
| work | 0.50 | 0.60 | 0.75 |
| 高温网约车赔率乘数 | 1.00 | 1.10 | 1.25 |
| 暴雨网约车赔率乘数 | 1.10 | 1.30 | 1.60 |

固定年龄乘数为 `18–39 = 0.80`、`40–59 = 1.00`。固定活动目的乘数为：

| 活动目的 | 乘数 |
|---|---:|
| family_care | 0.65 |
| family_activity | 0.75 |
| visit | 0.85 |
| shopping | 1.00 |
| social_leisure | 1.10 |

T1 当前实际名称 `out_of_home_family_care` 和 `out_of_home_family_activity` 分别显式映射到上述两个 family 类别。其余未知活动类型直接报错，不存在 `daily` 默认兜底。

行动能力约束乘数为 `none = 1.00`、`mild = 1.10`、`high = 1.25`；日程灵活性乘数为 `low = 0.85`、`medium = 1.00`、`high = 1.15`。这里 high schedule_flexibility 表示更容易取消或推迟本次原计划，low 表示工作、医疗等时间约束更强。`age_group`、`mobility_constraint`、`schedule_flexibility` 都是必填字段；缺失、空值或非法枚举会明确报错，不会静默采用 1.00。

## 取消计算与暴露边界

```text
p_weather_cancel
= weather_cancel_rate_base
× purpose_multiplier
× age_multiplier
× mobility_constraint_multiplier
× schedule_flexibility_multiplier
```

结果截断到 `[0, 1]`。取消暴露只检查：

```text
planned_outbound_departure → planned_activity_arrival
```

是否与天气窗口重叠。仅返程与天气重叠不能反向取消已经完成的活动，返程天气只交给 T9 调整供给。取消活动的 `outbound_leg_executes` 和 `return_leg_executes` 均为 false；主入口 `apply_weather_disruption_before_mode_choice` 不把该活动放入 `retained_activities`，因此下游不会生成孤立 leg。

取消的 work 或 medical 输出 `weather_cancelled = true` 和 `unmet_mandatory_trip = true`。两个标记都只表示本次原计划未完成，不代表需求永久消失。

若 T1 的正常取消率将来参与抽样，总取消概率必须按下式组合，不得直接相加：

```text
p_total_cancel = 1 - (1 - p_baseline_cancel) × (1 - p_weather_cancel)
```

## 稳定随机数

取消抽样由 SHA-256 固定键生成：

```text
agent_id + activity_id + weather_scenario(W0/W1/W2) + seed
```

情景档位不进入随机键，所以同一活动在低、基准、高档复用同一个均匀随机数。由于各参数随扰动档位单调不减，因此取消集合满足 `高 ⊇ 基准 ⊇ 低`，且结果不受调用顺序、分进程或重跑影响。

## 网约车偏好输出

仅对暴露且未取消的活动输出：

```text
ride_hailing_odds_multiplier
ride_hailing_utility_shift = ln(ride_hailing_odds_multiplier)
mode_choice_applied = false
```

后续方式选择模块必须先判断网约车客观可用性、Agent 独立叫车能力或家庭协助资格，再应用效用增量。T2 不直接改变方式选择结果。

网约车偏好按每条实际执行的 leg 独立判断。去程和返程分别使用各自的实际出行时间与天气窗口计算赔率乘数和效用增量。因此，仅返程遇到天气不会取消已经完成的活动，但返程仍会获得对应天气偏好信号。

W0 是严格中性状态：`p_weather_cancel = 0`、`ride_hailing_odds_multiplier = 1`、`ride_hailing_utility_shift = 0`。T2 不覆盖输入活动、leg、基础取消概率或必要出行标记。

T2 只模拟天气对计划出行的额外扰动。取消暴露仅由计划去程区间与天气窗口是否重叠决定；不模拟活动场所关闭，也不因活动进行期间遭遇天气而反向取消活动。
