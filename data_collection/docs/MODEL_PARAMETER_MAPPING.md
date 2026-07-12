# 模型参数映射

| 参数 | 情景 | 低/基准/高 | 状态 | 置信度 | 敏感性 | 证据记录数 | 局限 |
|---|---|---|---|---|---|---:|---|
| `extreme_heat_event_window_hours` | W1 | 24 / 72 / 168 hours | assumed | low | True | 0 | 不得解释为上海实测持续时间 |
| `road_capacity_multiplier` | W2 | 0.7 / 0.85 / 1.0 multiplier | assumed | low | True | 23 | 缺少统一道路速度面板 |
| `transit_speed_multiplier` | W2 | 0.6 / 0.8 / 1.0 multiplier | assumed | low | True | 23 | 不能用事件个案估计全市均值 |
| `ride_hailing_vehicle_turnover_multiplier` | W2 | 0.6 / 0.8 / 1.0 multiplier | assumed | low | True | 0 | 核心证据缺口 |
| `coupon_claim_required` | P1 | 0 / 0.5 / 1 probability/share | derived | low | True | 4 | 不能把NULL转为false |
| `coupon_awareness_probability` | P1 | 0.1 / 0.5 / 0.9 probability | assumed | low | True | 0 | 需实验校准 |
| `coupon_claim_probability` | P1 | 0.05 / 0.4 / 0.85 probability | assumed | low | True | 4 | 不得从券存在性推断领取率 |
| `coupon_auto_credit` | P2 | 0 / 0 / 1 binary/range | derived | low | True | 4 | 平台历史页面缺失 |
| `coupon_auto_apply` | P2 | 0 / 0 / 1 binary/range | derived | low | True | 4 | 支付收银台行为可能变化 |
| `phone_hailing_available` | P3 | 1 / 1 / 1 binary | observed | high | False | 3 | 渠道存在不等于必然派车成功 |
| `community_assistance_availability` | elder_agent | 0.2 / 0.5 / 0.8 probability/range | derived | medium | True | 42 | 不得等同家庭全天协助 |
| `digital_support_level` | elder_agent | 0.1 / 0.4 / 0.8 score | derived | low | True | 8 | 培训不等于独立叫车能力 |
| `family_assistance_available` | elder_agent | 0.1 / 0.5 / 0.9 probability | assumed | low | True | 0 | 必须与community_assistance分开 |

完整 supporting_record_ids、supporting_source_ids、推导方法和证据依据见 `config/calibration/shanghai_evidence_based.yaml`。家庭协助与社区正式协助始终作为不同变量。
