# 简化Agent交通实验结果

本目录只保存适合版本管理的精简统计结果。完整的逐Agent、逐活动、逐请求和逐券审计文件体积较大，由实验脚本在本地 `outputs/` 中生成，不上传GitHub。

两种规模必须分开解释：

- [`50_agent`](50_agent/README.md)：机制开发、小规模行为检查和优惠券初筛；
- [`200_agent`](200_agent/README.md)：在固定交通供给下放大需求压力，检查优惠券、车辆竞争、道路反馈和派单优先权。

两者都是S1/S2、步行/公交/网约车的简化机制实验，不代表上海人口规模、交通容量或政策效果预测。

重新生成本目录中的统计表：

```bat
python -B -X utf8 -m scripts.build_simple_200_agent_summary --repository-output docs\simple_experiment_results
```

主分析使用30 seeds；覆盖率和优先派单属于200人的3-seed机制扫描，只用于判断机制是否出现，不能用于估计现实阈值。
