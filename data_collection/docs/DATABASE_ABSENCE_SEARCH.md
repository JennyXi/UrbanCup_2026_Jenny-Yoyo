# 现成数据库不存在性检索

检索日期：2026-07-11；平台：网页搜索、上海市政府站内检索、官方机构站点。

查询模板覆盖：`主题 + 数据库/数据集/CSV/API/下载/开放数据`，`site:data.sh.gov.cn`、GitHub、Zenodo、Figshare、Kaggle、data.world，以及英文的 Shanghai elderly ride hailing dataset、Shanghai extreme weather transport disruption dataset、Shanghai ride hailing promotion dataset、Shanghai subdistrict elderly support dataset。另按16区、四库、三轮模板写入 `data_sources/search_log.csv`。

未发现字段等价、覆盖上海、可直接下载并可直接用于本实验的统一公开结构化数据库。近似公开材料多为政策文本、单次公告、基础行政目录或统计年鉴，缺少逐事实证据摘录、规则链字段、服务渠道三态字段、事件恢复字段及统一来源关联，因此没有直接复制。行政区名称仅作为 `auxiliary_existing_dataset` 使用。

检索不足：开放平台没有发现可直接等价的数据集；促销规则历史尤其稀缺，官方页面经常为动态/失效页面。缺口及影响详见 `DATA_QUALITY_REPORT.md`。
