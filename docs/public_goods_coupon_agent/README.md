# PublicGoodsAgent 公共品博弈分券机制

## 实现结论

优惠券实验保留 C0–C3，并新增 `C4_public_goods` 配对条件。AgentSociety 侧的
`CouponPublicGoodsAgent` **直接继承官网类**
`agentsociety2.contrib.agent.PublicGoodsAgent`；主仓库使用相同的确定性分配核心，
使交通实验不依赖付费 LLM 输出也能固定 seed 复现。

这不是把优惠券数量乘以公共品乘数。三轮博弈使用虚拟贡献 token：

1. 每个可触达且参与的居民第一轮从 20 个 token 中决定贡献量；
2. 公共池总贡献乘以 1.6，形成虚拟公共回报；
3. 第二、三轮每个 Agent 的贡献同时响应上一轮人均贡献和公共回报，因此 A 的贡献会改变 B 下一轮的决定；
4. 最后以 65% 可达性需求分数与 35% 合作分数组合排序，稳定破同分；
5. 实体优惠券仍严格受每日 10 张（50-Agent）或 40 张（200-Agent）约束，每人每天最多 1 张。

需求分数取年龄、医疗需要、非数字且无家庭协助三项中的最大值，避免对高度相关的
脆弱属性重复计权。非数字且无家庭协助的老人只有在既有 40% 社区/电话覆盖抽样命中时
才可参与，获券后仅获得一次代叫车通道，不会被永久改写为 `digital_access=true`。

## 代码位置

- `custom/agents/public_goods_coupon.py`：贡献回合、同伴反馈、需求—合作排序与资源守恒；
- `custom/agents/coupon_experiment.py`：把 C4 接入原 C0–C3 分券接口；
- `scripts/run_formal_nine_zone_50_coupon_experiment.py`：C4 交通、派单、核销和一致性审计；
- `integrations/agentsociety/coupon_public_goods_agent.py`：纳入主仓库版本控制的官网 `PublicGoodsAgent` 子类适配器；
- `../AgentSociety-local/experiments/coupon_public_goods_agent.py`：本地 AgentSociety 运行时转接；
- `../AgentSociety-local/experiments/smoke_coupon_public_goods_agent.py`：不调用 LLM 的官网继承关系与分券冒烟测试。

## 审计字段

`coupon_allocations.csv` 新增：

- `pg_round_contributions`：例如 `13|14|15`；
- `pg_peer_signal_round_2/3`：前一轮公共池给下一轮的共同信号；
- `pg_need_score`、`pg_cooperation_score`、`pg_priority_score`；
- `pg_peer_feedback_source_count`、`pg_linked_decision`；
- `pg_physical_coupon_pool`、`pg_coupons_created_by_multiplier`；
- 官网父类与适配器的完整类路径。

`consistency_checks.csv` 另外检查实体券守恒、跨 Agent 决策关联、官网父类声明和连续分配名次。

## 运行

完整 50-Agent 配对实验：

```bash
python -B -X utf8 -m scripts.run_formal_nine_zone_50_coupon_experiment
```

仅验证 AgentSociety 官网子类适配器（在 `AgentSociety-local` 环境中）：

```bash
python experiments/smoke_coupon_public_goods_agent.py --agents 50 --seed 47
```

seed 47 的一次集成冒烟结果为：32 人参与，10 人获券，32 人具有同伴反馈关联，
公共品乘数新增实体券 0 张；W2 中核销 2 张。它只是软件验收结果，不替代多 seed
正式估计，也不应解释为上海真实领券率或政策福利。
