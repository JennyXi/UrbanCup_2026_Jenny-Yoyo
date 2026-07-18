# 复现说明

本文档给出核心模型、归档实验和竞赛结果卡的最小复现路径。复现时必须同时记录分支、提交 SHA、Python 版本、配置文件、seed 范围和输出目录。

## 1. 环境层级

### 核心交通模型与结果卡

- Python 3.11–3.13；
- 只使用 Python 标准库；
- 不需要数据库、GPU、LLM 或网络；
- Windows、Linux 和 GitHub Actions 使用相同入口。

### 数据证据层

数据重建另需：

```bash
python -m pip install -r requirements-data.txt
git lfs pull
```

其中网页重新采集需要网络；已归档的 CSV、Parquet、SQLite、来源登记和质量报告可以离线检查。

### AgentSociety 扩展

AgentSociety/HPC 属于独立扩展环境，不是重建本仓库核心机制结果的必要条件。未通过完整端到端验收的运行不得写入竞赛主结果。

## 2. 冻结代码与输入

```bash
git branch --show-current
git rev-parse HEAD
python --version
```

正式复现至少保存：

- `config/*.json` 的完整副本或 SHA256；
- seed 起点与数量；
- Agent 数量、天气、日期类型和政策集合；
- 输出脚本、命令行和提交 SHA；
- `experiment_metadata.json`、一致性检查和逐 seed 系统表。

不要在同一输出目录覆盖第二次运行。建议输出目录包含实验名、seed 范围和日期。

## 3. 快速验证

```bash
python -B -X utf8 -m unittest discover -s tests
python -B -X utf8 -m scripts.build_competition_report --check
```

第一条验证模型机制、车辆守恒、政策配对和边界条件；第二条在临时目录确定性重建竞赛 CSV/Markdown/SVG，并逐字节比较提交版本。

## 4. 九区 50-Agent 冒烟实验

```bash
python -B -X utf8 -m scripts.run_formal_nine_zone_50_experiment
```

验收重点：

- 九区均有 Agent 与合法 OD；
- 公交—地铁接驳时间不重复计算；
- `transfers` 等于线路换乘与方式换乘之和；
- 网约车同一车辆的忙碌区间不重叠；
- 日初车辆数等于日末 idle + busy 数；
- 方式计数、活动完成和交通未满足口径闭合。

## 5. 200-Agent 配对政策实验

```bash
python -B -X utf8 -m scripts.run_formal_nine_zone_200_coupon_experiment \
  --seed-start 47 \
  --seed-count 10 \
  --workers 4 \
  --output-dir outputs/formal_nine_zone_200_coupon_10seeds
```

每个 seed 内 C0–C3 必须共享：

- 人口与身份属性；
- baseline activities 与 OD；
- 天气与方式选择基础随机量；
- 初始车辆池与基础派单优先值。

并行只允许发生在 seed 之间。单个 seed 内的车辆事件顺序必须串行，避免改变派单竞争结果。

## 6. 重建竞赛统计与图表

```bash
python -B -X utf8 -m scripts.build_competition_report
```

脚本读取归档的 `system_per_seed.csv` 与 `group_per_seed.csv`，生成：

- 政策 × 天气均值及 95% Student t 区间；
- 相对 C0 的 seed 内配对变化；
- 年龄—数字接入群体结果；
- 脆弱组相对 18–39 岁组的必要活动完成率差距；
- 按实际核销和诱发请求计算的描述性支出指标；
- 九区网络、天气方式转移、政策权衡和优惠券漏斗 SVG。

输出不包含当前时间、机器路径或随机数，因此相同输入应生成完全相同的字节。

## 7. 数据证据层重建

```bash
cd data_collection
python scripts/build_all_databases.py
python -m pytest -q
```

验收入口：

- `outputs/reports/acceptance_audit.md`；
- `outputs/reports/requirements_audit.md`；
- `outputs/reports/data_build_summary.md`；
- `docs/DATA_DICTIONARY.md`；
- `docs/DATA_QUALITY_REPORT.md`；
- `docs/SOURCE_LIMITATIONS.md`。

无法统一校准的参数必须继续标记为模型假设，不得仅因来源数量增加而改写为观测事实。

## 8. 复现失败处理

- 测试失败：保留失败 seed、配置和最小复现命令；
- 结果卡过期：运行生成脚本并检查输入归档是否被修改；
- 并行与串行结果不一致：停止使用并行结果，检查事件顺序；
- 数据来源不可访问：保留原始归档、来源登记和 blocked source 记录；
- 任何输出口径变化：提高 schema/version，并在文档中显式迁移，不静默覆盖旧结果。
