# 九区50-Agent老年优先派单实验

本实验是九区50-Agent机制验证的最后一个政策实验。它在选定的12辆网约车基线下，使用相同Agents、活动、天气、车辆池、方式选择扰动和基础派单优先值，比较P0与P4。实验使用W0、W2工作日及seeds 47–56，不是上海平台派单规则或政策效果预测。

## 政策与排序

- `P0_first_come`：车辆释放时，从仍在等待的请求中按`实际请求时间 → 共同基础dispatch priority → leg_id`分配。
- `P4_elder_priority`：车辆释放时，从仍在等待的请求中按`60+组别优先 → 实际请求时间 → 共同基础dispatch priority → leg_id`分配。

已经完成的派单不会被撤销；P4只重排尚未获得车辆且未超过等待上限的pending队列。政策不增加车辆，也不改变活动、数字接入、价格、天气、方式效用、fallback或基础随机值。

## 10-seed结果

P0和P4在W0、W2下的结果完全相同：

| 天气 | 政策 | 请求 | 成功 | 失败/fallback | 平均等待（分钟/请求） | 网约车份额 | 必要活动完成率 |
|---|---|---:|---:|---:|---:|---:|---:|
| W0 | P0/P4 | 5.9 | 5.7 | 0.2 | 10.31 | 5.78% | 96.00% |
| W2 | P0/P4 | 13.5 | 12.2 | 1.3 | 11.73 | 13.02% | 96.78% |

没有出现老人由失败转成功、非老人由成功转失败或等待时间转移。189条P0请求在P4中都有配对请求，基础dispatch priority全部一致。

## 为什么没有资源转移

10个seeds合计，60+老人仍只有2条请求：W0一条、W2一条。新队列机制下已有订单在等待后获得车辆，但两条老人请求均未与非老人形成同区域pending竞争。因此，P4仍没有实际可以重排的跨年龄竞争队列。

这是有效的零结果：优先规则已经运行，但当前50-Agent正式需求中没有形成“老人和非老人同时竞争同一有限车辆”的触发条件。不能将其解释为现实中老年优先无效。

## 机制结论

本实验区分了三个政策环节：

1. 数字接入决定老人能否独立或代理进入网约车市场；
2. 方式选择决定老人是否实际发出请求；
3. 优先派单只在老人请求与其他请求发生同时竞争时重新分配车辆。

前两轮结果表明，当前老人请求极少；本轮进一步确认，没有请求竞争时，单独改变派单顺序不会产生宏观政策效果。

## 输出

- `system_per_seed.csv`、`system_distributions.csv`
- `system_policy_changes_vs_p0.csv`
- `group_per_seed.csv`、`group_distributions.csv`
- `group_policy_changes_vs_p0.csv`
- `request_transfer_audit.csv`
- `mode_choices.csv`、`ride_hailing_dispatch.csv`
- `consistency_checks.csv`、`experiment_metadata.json`

## 运行

```cmd
"C:\Users\Jenny Xi\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -B -X utf8 -m scripts.run_formal_nine_zone_50_elder_dispatch_priority
```
