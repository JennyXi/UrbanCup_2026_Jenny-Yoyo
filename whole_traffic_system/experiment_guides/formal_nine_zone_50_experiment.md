# 九区正式50-Agent实验

## 定位

本实验把`experiment`分支中已经验证的活动状态流程迁移到正式九区连续城市，但第一轮只运行P0无政策情景。Agent人口、居住区、活动目的、目的地区域、OD距离和计划时间全部来自main的T1/T6流程，不复制S1/S2距离和目的地规则。

```text
50个main异质Agent
→ main七日活动计划与T6目的地
→ 选取一个工作日和一个休息日
→ 配对W0/W1/W2
→ work remote work或非必要活动天气取消
→ 重新构建保留活动的连续leg链
→ 计算四种方式完整预计门到门时间与迟到成本
→ 按所选方式时间和可靠性缓冲反推固定活动的出发时刻
→ 公交、网约车与道路共享状态反馈，并进行两次有界出发—选择更新
→ 最终选择、守恒车辆派单和一次fallback
→ 活动完成、transport unmet、费用、等待、热暴露和雨中暴露
```

## 活动规则

- work不进入普通天气取消；暴露work按W0=0%、W1=2%、W2=5%在activity层抽样一次remote work。
- medical不进入普通天气取消。
- 其他活动只有出发落入实际天气窗口时才计算天气取消。
- remote work视为work完成，不生成任何通勤leg。
- 取消活动不生成交通leg和交通暴露。
- 必要活动去程最终失败才形成必要活动transport unmet；返程不撤销活动完成。

## 交通与空间

九区是连续城市功能分区。公交和地铁跨越分区边界。地铁M1/M2/M3参与四方式效用选择；公交首末端接驳短于地铁。每条地铁leg分别进行起点和终点的Agent—区域—用途稳定直接步行可达性抽样，该抽样只使用seed、agent_id、zone和purpose，不使用天气或政策标签。任一端不能直接步行到站时，模型生成区内公交接驳—地铁组合方案，而不是直接禁用地铁；接驳公交的完整接驳、候车、车内、换乘和票价进入同一个门到门效用。只有Z1、Z2、Z3、Z7允许满足距离条件的区内地铁，Z4、Z5、Z6、Z8主要通过本区车站进行跨区地铁出行，Z9继续通过B2接驳Z6。

公交与网约车共享道路速度；地铁不进入道路车辆流量且不受天气和道路拥堵影响。网约车使用九区整数车辆池，到达后留在目的地区域。

## 暴露

- walking：整个成功步行尝试。
- bus：起点接驳、候车和终点接驳。
- metro：起点接驳、站台候车和终点接驳。
- ride_hailing：接驾等待。
- 首次网约车失败：保留fallback前已经消耗的等待。

W1使用既有完整日UTCI曲线计算26°C阈值以上的`heat_hazard_dose_c_min`及年龄加权`heat_risk_burden`；W2另外统计户外片段与强降雨活动窗口重叠的分钟数。

## 输出

- `planned_activities_main_od.csv`与`planned_legs_main_od.csv`：main生成的共同计划和OD；
- `activity_states.csv`：暴露、remote work、天气取消与travel required；
- `activity_results.csv`：completed、weather_cancelled、transport_unmet；
- `mode_choices.csv`：四方式、fallback、时间、费用、等待和暴露；
- `ride_hailing_dispatch.csv`与`vehicle_end_states.csv`：派单和车辆守恒；
- `macro_summary.csv`：每个天气×日期类型的统一宏观结果；
- `activity_purpose_summary.csv`：按活动目的的暴露和取消结果。

本实验仍是机制迁移，不是上海交通预测。优惠券、数字接入政策、老年优先和供给政策将在P0口径审计后单独迁移。

`mode_choices.csv`同时输出`metro_origin_accessible`、`metro_destination_accessible`、`origin_feeder_mode`、`destination_feeder_mode`、`bus_metro_transfer_count`、公交接驳时间/等待/费用、`actual_arrival_time`、`arrival_delay_minutes`和`on_time_arrival`。活动开始时间保持固定；可靠性缓冲为walk/bus/metro/ride_hailing = 3/10/5/8分钟，最多提前120分钟出发，5分钟以内记为准时，预计迟到超过30分钟的方式不进入选择。迟到30分钟以内不自动撤销活动完成，交通成功、准时到达和活动完成分别统计。以上均为可配置情景假设。
