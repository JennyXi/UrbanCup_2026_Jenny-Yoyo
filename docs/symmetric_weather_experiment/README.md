# 对称天气暴露实验：必要活动状态机

本实验独立于正式 W0/W1/W2 日历，不修改 T2、城市空间结构或现有方式选择参数。W0、W1 与 W2 对同一组 activity 使用相同的 Agent、日型、目的、时刻、OD 和距离，仅改变实验天气参数。

实验包含一个工作日和一个休息日。两类日子都为每个 Agent 构造平衡的 medical/shopping 测试活动；就业者仅在工作日增加 work。该结构用于机制比较，不表示每位居民每天都真实发生 medical/shopping。

工作时间继承 main：regular worker 在 08:00–10:30 之间按30分钟时点抽样，工作8–10小时；part-time worker 在10:00或10:30开始，工作6.5–7.5小时。休息日不生成 work。计划出发时间使用 W0 公交参考通勤时间从到岗时间反推，避免天气方式选择反过来改变是否暴露。

## 活动状态

- `work`：只为 `regular_worker` 和 `part_time_worker` 生成。每个 activity 只抽样一次 remote work；抽中后工作完成且不生成去程或返程。未抽中时必须出行，不进入普通天气取消。
- `medical`：不进入普通天气取消，本轮必须出行。
- `shopping`：保留现有 T2 天气取消逻辑。
- 暂不模拟 schedule shift。
- W0：remote work、天气取消和天气风险暴露均为0，作为正常运行基准。

Remote-work 概率是情景假设，不是上海实测比例。工作出发落入独立实验天气窗口时，W1/W2 分别使用 2%/5% 的总概率；未暴露工作使用 normal 0%。这里的 W2 表示普通强降雨，不是台风、自然灾害或停工情景。

## 交通状态机

```text
travel_required
→ primary mode
→ 成功，或失败后移除 primary
→ 使用剩余时间和预算选择一次 fallback
→ fallback 仍失败才形成最终 transport failure
```

失败尝试产生的等待、费用和户外暴露会保留。work/medical 去程最终失败记为 `transport_related_unmet`；shopping 不记为 unmet。去程成功即表示必要活动完成。返程单独模拟，失败记为 `stranded_after_activity`，不会撤销活动完成，也不会增加 unmet mandatory。

失败费用采用独立实验的透明假设：公交失败视为已支付车票（100% 票价），步行与未成功派单的网约车不收费；这些比例均在实验配置中，可做敏感性分析。

## 暴露指标

- walking：完整出行时间计为户外暴露；
- bus：步行接驳与候车时间计为户外暴露；
- ride_hailing：等待时间计为户外暴露；
- W1：`heat_exposure_index = outdoor_exposure_minutes × W1_heat weight`；
- W2：`rain_exposure_index = outdoor_exposure_minutes × W2_rain weight`。

两个风险指标分开统计，不把暴雨暴露写入 heat exposure。

## 运行

```powershell
python -B -X utf8 -m scripts.run_symmetric_weather_experiment
python -B -X utf8 -m unittest tests.test_symmetric_weather_experiment -v
```
