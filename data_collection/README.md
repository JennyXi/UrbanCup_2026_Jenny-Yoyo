# Shanghai Mobility Evidence Data Collection

本目录包含上海极端天气—交通扰动、老年多渠道叫车、网约车优惠规则和街镇老年支持能力四个证据型数据库。

## 重建

```bash
python scripts/build_all_databases.py
python -m pytest -q
```

核心数据库位于 `data/processed/shanghai_mobility_evidence.sqlite`，CSV/Parquet 位于 `data/exports/`，最终验收结果位于 `outputs/reports/requirements_audit.md`。

SQLite、完整检索日志和大型候选JSON使用 Git LFS 保存；克隆后执行 `git lfs pull` 获取完整文件。
