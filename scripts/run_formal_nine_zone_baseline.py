"""Run the formal nine-zone, four-mode, no-policy Agent baseline."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from custom.agents.formal_nine_zone_experiment import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    run_formal_nine_zone_baseline,
)


DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "formal_nine_zone_baseline"


def _serialise(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (list, dict, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _write_csv(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    rows = [{key: _serialise(value) for key, value in row.items()} for row in rows]
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _readme(result: Mapping[str, Any]) -> str:
    config = result["config"]
    lines = [
        "# 九区正式 Agent 无政策基线", "",
        "本目录是从两区简化机制实验转向正式九区连续城市的第一个四方式交通基线。", "",
        "## 范围", "",
        f"- Agent：{config['total_agents']}",
        "- 空间：Z1–Z9是连续城市中的功能分区和OD统计单元，不是九个独立城镇。",
        "- 日期：一个工作日与一个休息日。",
        "- 天气：W0正常、W1极端高温窗口、W2普通强降雨窗口。",
        "- 政策：P0_no_policy，不发券、不优先派单、不改变公交班次或网约车供给。",
        "- 可选方式：walking、bus、metro、ride_hailing。", "",
        "## 地铁口径", "",
        "- M1：Z8—Z2—Z1—Z7—Z4。",
        "- M2：Z6—Z3—Z1。",
        "- M3：Z3—Z5—Z7。",
        "- Z1、Z2、Z3、Z7站点较密，可产生满足距离和覆盖条件的区内地铁leg。",
        "- Z4、Z5、Z6、Z8可从本区车站进入跨区地铁，但普通区内OD不直接提供地铁。",
        "- Z9无地铁，通过公交接驳Z6。", "",
        "## 机制", "",
        "1. 同一批Agent和活动计划用于W0/W1/W2。",
        "2. 方式效用考虑总时间、费用、年龄偏好、天气偏好和固定随机扰动。",
        "3. 非数字接入且无family_assistance者不能独立选择网约车。",
        "4. 公交与网约车共享道路反馈；地铁不增加道路车辆流量。",
        "5. 地铁班次随早晚高峰变化，速度和供给不受天气及道路拥堵影响。",
        "6. 网约车使用九区空间—时间守恒车辆池。",
        "7. 首次交通失败后最多fallback一次，并使用顺延后的时间。", "",
        "## 本轮不做", "",
        "- 不启用天气取消、remote work或跨日学习。",
        "- 不做路段级交通分配；道路反馈仍是全市代表性共享走廊。",
        "- 不将50 Agent结果解释为上海现实预测。", "",
        "## 汇总", "",
        "|天气|日期类型|legs|步行|公交|地铁|网约车|RH请求/成功|fallback成功|平均总时间(min)|平均费用(元)|平均道路速度(km/h)|",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in result["summary_rows"]:
        lines.append(
            f"|{row['weather_scenario']}|{row['day_type']}|{row['planned_legs']}|"
            f"{row['walking_legs']}|{row['bus_legs']}|{row['metro_legs']}|"
            f"{row['ride_hailing_legs']}|"
            f"{row['ride_hailing_requests']}/{row['successful_ride_hailing_requests']}|"
            f"{row['fallback_successes']}|{row['mean_total_travel_time']}|"
            f"{row['mean_fare_yuan']}|{row['mean_road_speed_kmh']}|"
        )
    lines.extend(["", "详细字段见`baseline_summary.csv`、`mode_choices.csv`和`ride_hailing_dispatch.csv`。"])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()
    with args.config.open(encoding="utf-8-sig") as stream:
        config = json.load(stream)
    result = run_formal_nine_zone_baseline(config=config, seed=args.seed)
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(output / "agents.csv", result["inputs"]["agents"])
    _write_csv(output / "activities.csv", result["inputs"]["activities"])
    _write_csv(output / "planned_legs.csv", result["inputs"]["legs"])
    _write_csv(output / "activity_results.csv", result["activity_results"])
    _write_csv(output / "mode_choices.csv", result["mode_choices"])
    _write_csv(output / "ride_hailing_dispatch.csv", result["ride_hailing_dispatch"])
    _write_csv(output / "vehicle_end_states.csv", result["vehicle_end_states"])
    _write_csv(output / "baseline_summary.csv", result["summary_rows"])
    summary = {
        "experiment_id": config["experiment_id"], "policy": config["policy"],
        "enabled_modes": config["enabled_modes"], "seed": result["inputs"]["seed"],
        "summary_rows": result["summary_rows"],
    }
    (output / "baseline_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (output / "README.md").write_text(_readme(result), encoding="utf-8")
    print("Formal nine-zone four-mode P0 baseline complete")
    for row in result["summary_rows"]:
        print(
            f"  {row['weather_scenario']} {row['day_type']}: "
            f"walk={row['walking_legs']}, bus={row['bus_legs']}, "
            f"metro={row['metro_legs']}, ride_hailing={row['ride_hailing_legs']}, "
            f"RH requests/success={row['ride_hailing_requests']}/"
            f"{row['successful_ride_hailing_requests']}, "
            f"avg_time={row['mean_total_travel_time']} min, "
            f"avg_fare={row['mean_fare_yuan']} yuan"
        )
    print(f"Files: {output.resolve()}")


if __name__ == "__main__":
    main()
