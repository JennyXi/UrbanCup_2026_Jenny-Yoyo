# T6：Baseline activity destination-zone assignment

T6只为已有七日 baseline activities 填充 `destination_zone`。它不生成 origin、正式 leg、OD、distance字段、mode choice、天气响应、优惠执行、订单或派单。

## 接口

```python
assign_destination_zones(
    agents,
    weekly_activities,
    derived_spatial_config,
    destination_config,
    seed,
) -> list[dict]
```

函数返回 activity 的深拷贝，除把 `destination_zone=None` 更新为 `Z1–Z9` 外，不修改 activity ID、时间、purpose、`activity_sequence` 或 `sequence_order`。已有非空 destination 默认报错。

## 目的地选择

```text
score(j) = purpose_attraction(j) * exp(-beta * effective_distance(i,j))
```

超过purpose-specific soft limit后，score额外乘以：

```text
exp[-extra_decay * (distance - soft_limit)]
```

超过extreme hard limit的候选区被排除。如果没有合法候选，fallback严格按：最小effective distance、原始gravity score最高、zone ID最小的顺序选择，并记录审计事件。

同区选择不使用0 km，并读取T4经过`spatial_scale=0.82`缩放后的区内平均距离：

```text
effective_distance(i,i) = mean_intrazonal_distance[i]
```

跨区使用质心欧氏距离。这里的 effective distance 只用于目的地选择和审计，不写入 activity，也不代表最终实际行程距离。

映射关系：

| Purpose | Attraction | Beta |
|---|---|---:|
| work | employment_weight | 0.06 |
| medical | medical_weight | 0.10 |
| visit / out-of-home family | population_weight | 0.06 |
| shopping | service_weight | 0.14 |
| social / leisure | service_weight | 0.10 |

Purpose-specific约束：

| Purpose | Soft limit | Extra decay | Hard limit |
|---|---:|---:|---:|
| work | 15 km | 0.10 | 30 km |
| medical | 10 km | 0.12 | 25 km |
| visit | 15 km | 0.10 | 30 km |
| out_of_home_family_care | 12 km | 0.12 | 25 km |
| out_of_home_family_activity | 12 km | 0.12 | 25 km |
| shopping | 8 km | 0.18 | 20 km |
| social | 10 km | 0.14 | 25 km |
| leisure | 12 km | 0.12 | 25 km |

`medical_weight`是综合医疗服务吸引力，不区分医院等级、医疗类型或真实设施数量。`service_weight`是商业、社交和休闲设施的MVP综合近似。所有参数均为合成机制，不解释为上海实证值。

## 固定目的地

- 每名 regular/part-time worker整周复用一个 `work_zone`；
- 每名有医疗活动的Agent整周复用一个 `medical_zone`；
- `visit`、`out_of_home_family_care`、`out_of_home_family_activity`整周复用同一个 `family_zone`；
- shopping、social、leisure按activity独立分配。

work、medical和family在任何天气或政策场景分支前按Agent各抽样一次，之后所有反事实场景复用。Family使用baseline week中实际出现的family purposes的最严格soft/hard约束，避免逐activity抽样绕过约束。

同区目的地允许存在。Z7是人口较多且具有一定本地就业的新城，`work_zone=Z7`合法。T6不再设置Z7工作候选区硬限制；Z7同区就业与前往Z1–Z3的比例完全由就业吸引力、缩放后距离、beta和稳定抽样共同形成，并只在审计中报告。

晚间activity只获得destination，不写死origin，因为Agent可能从工作地直接前往晚间活动。

## 审计

`build_destination_audit()`内部重算有效选择距离并输出：

- 每个purpose的平均effective distance；
- 同区destination数量和比例；
- 超过20 km及30 km的数量和比例；
- purpose目的地区域分布；
- 完整home-zone到destination-zone计数；
- Z7居民work目的地分布；
- Z8/Z9居民medical目的地分布。

`assign_destination_zones_with_audit()`另外报告candidate exclusion、selection event、fallback数量和比例、Agent-level固定地点分布。`build_destination_audit()`报告activity-level需求流量；两种口径不会混用。

审计不会向activity新增distance字段，也不会自动调参。
