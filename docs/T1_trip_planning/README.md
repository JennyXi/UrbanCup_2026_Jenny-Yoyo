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

老年数字接入采用2025材料中的分层口径：智能手机拥有率为87.3%，但可独立完成应用注册的比例为48.3%。`smartphone_access`表示设备条件，`digital_access`表示独立注册和进入数字服务的能力；后者为真时前者必须为真。56.65%是“数字接入作为障碍的重要性”指标，不作为人口接入率。

- 18–39 与 40–59 先固定为 `regular_worker` 或少量 `flexible_non_worker`。后者不生成固定工作，但可在工作日白天生成研究范围内活动，并保持数字接入和独立叫车能力。
- 工作日总活动数通常为0–2个、少量3个；周末通常为0–3个、少量4个。工作及已安排的医疗活动计入当日总数。
- 18–39岁整体最活跃，40–59岁居中，60+较少；三组都可出现 shopping、`social_leisure`、visit、出门家庭活动和合理的医疗活动，差异通过概率而不是禁止规则表达。
- 60+ 先固定为 `retired` 或 `part_time_worker`；兼职者每周固定安排 1–2 个工作日。医疗次数由稳定的 `medical_need_level` 决定，普通 `standard` 老人最多两次且不连续；`high` 才允许最多三次或连续治疗。
- 家庭类 modeled activity 只使用 `out_of_home_family_care` 和 `out_of_home_family_activity`。居家、家附近或通常不使用网约车的家庭活动归入不输出的 `no_in_scope_trip`。
- `social_leisure` 统一表示聚会、聚餐、电影、健身、公园和文化娱乐等非必要社交休闲活动；`visit` 保持独立。
- `medical` 保持 `is_mandatory=True`、`baseline_cancel_probability=0.01`。
- `daily_errand`、`grocery`、`community`、`park` 和 `no_in_scope_trip` 不输出 activity，其概率不转移给其他活动。

## 一致性与边界

每个 Agent 使用由 `random_seed + agent_id` 经 `hashlib.sha256` 派生的独立稳定随机源，因此固定 seed 可复现，且 Agent 输入顺序变化不影响个人计划。

`sequence_order` 在每个 Agent 的每天从 1 开始连续编号，先按开始时间，再按结束时间与 purpose 稳定排序。它只保存活动顺序，不生成 origin 或正式 leg。

审计接口 `generate_weekly_activity_plan_with_audit()` 和 `generate_seven_day_activity_plans_with_audit()` 分别报告 candidate slot、modeled slot、`no_in_scope_trip` slot、空 Agent-day 与固定工作槽。固定工作不混入 candidate slot，且始终满足 `modeled_activity_slot_count + no_in_scope_slot_count = total_candidate_slots`。

所有开始和结束时间均位于 30 分钟网格。周末开始时间从上午 09:00–11:30 或下午 13:00–16:00 的半小时节点稳定抽样。

本阶段不生成 destination、OD、distance、mode、weather response、coupon response、pricing、dispatch、waiting time 或 congestion。

## 时间可行性链（2026-07-13更新）

目的地区域确定后，`custom/agents/leg_generation.py` 将活动展开为连续的 outbound、between-activities 和 return-home legs。`activity_start_time` 表示到达目的地并开始活动，`activity_end_time` 表示活动结束且可以离开。每条 leg 都满足 `departure_time + travel_time = arrival_time`；连续活动从上一活动地点出发，不强制中途回家。

工作活动先确定同一 Agent 一周固定的到岗、下班时间和工作地点，再根据通勤时间反推出发时间。非工作活动使用按目的区分的时长分布，最短30分钟，不设置统一的8小时硬上限；聚会休闲、探访和家庭活动可低概率持续更久。shopping 只能在商场营业时间 10:00–22:00 内进行，`out_of_home_family_care` 保持 `is_mandatory=False`。

返家到达时间按年龄设上限：18–39岁最晚在活动日结束后的24:00到家，40–59岁最晚22:00到家，60+最晚20:00到家。程序使用实际 return-home leg 的到达时间检查上限；时间窗口不足时会提前活动、缩短到该目的允许的时长，仍不可行则取消非必要活动，不会把时间绕到次日00:00。固定工作时间不为满足非必要活动而推迟。

当前 generalized travel time 使用 `road_network_distance_km` 按18 km/h换算，向上取整至5分钟，最低10分钟、极端上限90分钟。跨区leg同时保存质心直线距离，并按 `road_network_distance_km = euclidean_distance_km × detour_factor` 构造道路距离；同区leg的直线距离字段为0，道路距离以 `mean_intrazonal_distance` 为尺度，按purpose和稳定seed抽取正值。抽样键绑定无方向的实际地点对：home、固定公司、固定医疗地点、亲属目的区或activity-specific地点，因此同一地点之间往返道路距离一致，不因outbound/return分别重抽。该规则用于时间链可行性检查，尚未按最终交通方式、高峰拥堵或等待时间进行实证校准。
