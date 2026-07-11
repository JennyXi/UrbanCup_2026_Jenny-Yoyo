# T1/T5B：七日基础活动计划

本阶段把已完成空间安置的 Agent 与 Monday–Sunday baseline activity 生成器做最小整合。

## 输入

- Agent 的 `agent_id`
- Agent 的 `age_group`
- 已由空间安置模块分配的 `home_zone`
- Monday 00:00 的 `simulation_week_start`
- `random_seed`

活动生成器直接读取 Agent 的真实 `home_zone`。若 `home_zone` 为 `None` 或不属于 `Z1–Z9`，会明确报错；不再使用根据 `agent_id` 推导区域的占位逻辑。

开发阶段首次整合使用显式的 `total_agents=50`。人口、配额及计划入口均不将 1000 作为开发运行默认值。

## 输出

每条 baseline activity 包含：

- `agent_id`, `age_group`, `work_status`, `medical_need_level`
- `day_of_week`, `is_weekend`, `sequence_order`
- `activity_id`, `activity_sequence`, `activity_purpose`
- `home_zone`, `destination_zone`
- `planned_start_datetime`, `planned_end_datetime`
- `is_mandatory`, `baseline_cancel_probability`

本阶段 `destination_zone` 固定为 `None`，且不展开 `trip_id`、`leg_id` 或 outbound/return legs。晚间活动仅按时间接在工作之后，不假定 Agent 已先回家，也不写死 origin。

## 活动范围

- 18–39 与 40–59 先固定为 `regular_worker` 或少量 `flexible_non_worker`。后者不生成固定工作，但可在工作日白天生成研究范围内活动，并保持数字接入和独立叫车能力。
- 18–39 regular worker 的工作日晚间活动触发概率为 0.30；40–59 为 0.20。晚间开始时间在 18:30/19:00 稳定抽样。
- 年轻和中年周末均显式保留少量 `no_in_scope_trip`，不会把这部分概率重新分配给其他活动。
- 60+ 先固定为 `retired` 或 `part_time_worker`；兼职者每周固定安排 1–2 个工作日。医疗次数由稳定的 `medical_need_level` 决定，普通 `standard` 老人最多两次且不连续；`high` 才允许最多三次或连续治疗。
- 家庭类 modeled activity 只使用 `out_of_home_family_care` 和 `out_of_home_family_activity`。居家、家附近或通常不使用网约车的家庭活动归入不输出的 `no_in_scope_trip`。
- `medical` 保持 `is_mandatory=True`、`baseline_cancel_probability=0.01`。
- `daily_errand`、`grocery`、`community`、`park` 和 `no_in_scope_trip` 不输出 activity，其概率不转移给其他活动。

## 一致性与边界

每个 Agent 使用由 `random_seed + agent_id` 经 `hashlib.sha256` 派生的独立稳定随机源，因此固定 seed 可复现，且 Agent 输入顺序变化不影响个人计划。

`sequence_order` 在每个 Agent 的每天从 1 开始连续编号，先按开始时间，再按结束时间与 purpose 稳定排序。它只保存活动顺序，不生成 origin 或正式 leg。

审计接口 `generate_weekly_activity_plan_with_audit()` 和 `generate_seven_day_activity_plans_with_audit()` 分别报告 candidate slot、modeled slot、`no_in_scope_trip` slot、空 Agent-day 与固定工作槽。固定工作不混入 candidate slot，且始终满足 `modeled_activity_slot_count + no_in_scope_slot_count = total_candidate_slots`。

所有开始和结束时间均位于 30 分钟网格。周末开始时间从上午 09:00–11:30 或下午 13:00–16:00 的半小时节点稳定抽样。

本阶段不生成 destination、OD、distance、mode、weather response、coupon response、pricing、dispatch、waiting time 或 congestion。
