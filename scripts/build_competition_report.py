"""Build deterministic competition tables and SVG figures from archived results.

The script deliberately uses only the Python standard library so the report can
be regenerated in CI without installing a plotting stack.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from xml.sax.saxutils import escape


ROOT = Path(__file__).resolve().parents[1]
RESULT_ROOT = (
    ROOT
    / "whole_traffic_system"
    / "results"
    / "formal_nine_zone_200"
    / "formal_nine_zone_200_coupon_10seeds"
)
SYSTEM_PATH = RESULT_ROOT / "system_per_seed.csv"
GROUP_PATH = RESULT_ROOT / "group_per_seed.csv"
CITY_PATH = ROOT / "config" / "shanghai_synthetic_city.json"
NETWORK_PATH = ROOT / "config" / "multimodal_transport_network.json"

POLICY_ORDER = [
    "C0_no_coupon",
    "C1_public_limited",
    "C2_elder_limited",
    "C3_mixed",
]
POLICY_SHORT = {
    "C0_no_coupon": "C0 无券",
    "C1_public_limited": "C1 公共券",
    "C2_elder_limited": "C2 老年定向券",
    "C3_mixed": "C3 混合券",
}
WEATHER_ORDER = ["W0", "W1", "W2"]
WEATHER_SHORT = {"W0": "W0 常态", "W1": "W1 高温", "W2": "W2 强降雨"}
MODE_FIELDS = [
    ("walking_mode_share", "步行", "#7B61FF"),
    ("bus_mode_share", "公交", "#2F80ED"),
    ("metro_mode_share", "地铁", "#00A896"),
    ("ride_hailing_mode_share", "网约车", "#F2994A"),
]
T_CRITICAL_95 = {
    1: 12.706,
    2: 4.303,
    3: 3.182,
    4: 2.776,
    5: 2.571,
    6: 2.447,
    7: 2.365,
    8: 2.306,
    9: 2.262,
    10: 2.228,
    11: 2.201,
    12: 2.179,
    13: 2.160,
    14: 2.145,
    15: 2.131,
    16: 2.120,
    17: 2.110,
    18: 2.101,
    19: 2.093,
    20: 2.086,
    21: 2.080,
    22: 2.074,
    23: 2.069,
    24: 2.064,
    25: 2.060,
    26: 2.056,
    27: 2.052,
    28: 2.048,
    29: 2.045,
    30: 2.042,
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def number(row: Mapping[str, str], key: str) -> float:
    value = row.get(key, "")
    if value in (None, ""):
        return math.nan
    return float(value)


def clean(values: Iterable[float]) -> list[float]:
    return [value for value in values if math.isfinite(value)]


def mean_ci(values: Iterable[float]) -> tuple[float, float, int]:
    data = clean(values)
    if not data:
        return math.nan, math.nan, 0
    mean = statistics.fmean(data)
    if len(data) == 1:
        return mean, 0.0, 1
    critical = T_CRITICAL_95.get(len(data) - 1, 1.96)
    half_width = critical * statistics.stdev(data) / math.sqrt(len(data))
    return mean, half_width, len(data)


def fmt(value: float, digits: int = 2) -> str:
    if not math.isfinite(value):
        return "NA"
    return f"{value:.{digits}f}"


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def aggregate_system(rows: Sequence[Mapping[str, str]]) -> list[dict[str, Any]]:
    metrics = [
        "ride_hailing_requests",
        "successful_ride_hailing_requests",
        "failed_ride_hailing_requests",
        "ride_hailing_mode_share",
        "necessary_activity_completion_rate",
        "transport_unmet",
        "coupon_reached",
        "coupon_participated",
        "coupon_awarded",
        "coupon_redeemed",
        "coupon_induced_requests",
        "coupon_subsidy_yuan",
        "total_heat_risk_burden",
    ]
    grouped: dict[tuple[str, str], list[Mapping[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(row["policy"], row["weather_scenario"])].append(row)
    output: list[dict[str, Any]] = []
    for policy in POLICY_ORDER:
        for weather in WEATHER_ORDER:
            group = grouped[(policy, weather)]
            for metric in metrics:
                mean, ci, n = mean_ci(number(row, metric) for row in group)
                output.append(
                    {
                        "policy": policy,
                        "weather_scenario": weather,
                        "metric": metric,
                        "mean": fmt(mean, 6),
                        "ci95_half_width": fmt(ci, 6),
                        "seed_count": n,
                    }
                )
    return output


def paired_effects(rows: Sequence[Mapping[str, str]]) -> list[dict[str, Any]]:
    metrics = [
        "ride_hailing_requests",
        "failed_ride_hailing_requests",
        "ride_hailing_mode_share",
        "necessary_activity_completion_rate",
        "transport_unmet",
        "coupon_redeemed",
        "coupon_subsidy_yuan",
        "total_heat_risk_burden",
    ]
    by_key = {
        (int(row["seed"]), row["policy"], row["weather_scenario"]): row for row in rows
    }
    seeds = sorted({int(row["seed"]) for row in rows})
    output: list[dict[str, Any]] = []
    for policy in POLICY_ORDER[1:]:
        for weather in WEATHER_ORDER:
            for metric in metrics:
                differences = []
                for seed in seeds:
                    policy_row = by_key[(seed, policy, weather)]
                    baseline_row = by_key[(seed, POLICY_ORDER[0], weather)]
                    differences.append(number(policy_row, metric) - number(baseline_row, metric))
                mean, ci, n = mean_ci(differences)
                output.append(
                    {
                        "policy": policy,
                        "baseline_policy": POLICY_ORDER[0],
                        "weather_scenario": weather,
                        "metric": metric,
                        "paired_mean_change": fmt(mean, 6),
                        "ci95_low": fmt(mean - ci, 6),
                        "ci95_high": fmt(mean + ci, 6),
                        "seed_count": n,
                    }
                )
    return output


def fairness_summary(rows: Sequence[Mapping[str, str]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[Mapping[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(row["policy"], row["weather_scenario"], row["group"])].append(row)
    output: list[dict[str, Any]] = []
    for policy in POLICY_ORDER:
        for weather in WEATHER_ORDER:
            groups = sorted(group for p, w, group in grouped if p == policy and w == weather)
            for group_name in groups:
                group_rows = grouped[(policy, weather, group_name)]
                for metric in (
                    "necessary_activity_completion_rate",
                    "ride_hailing_requests",
                    "failed_ride_hailing_requests",
                    "coupon_redeemed",
                    "total_heat_risk_burden",
                ):
                    mean, ci, n = mean_ci(number(row, metric) for row in group_rows)
                    output.append(
                        {
                            "policy": policy,
                            "weather_scenario": weather,
                            "group": group_name,
                            "metric": metric,
                            "mean": fmt(mean, 6),
                            "ci95_half_width": fmt(ci, 6),
                            "seed_count": n,
                        }
                    )
    return output


def equity_gaps(rows: Sequence[Mapping[str, str]]) -> list[dict[str, Any]]:
    by_key = {
        (int(row["seed"]), row["policy"], row["weather_scenario"], row["group"]): row
        for row in rows
    }
    seeds = sorted({int(row["seed"]) for row in rows})
    output: list[dict[str, Any]] = []
    reference = "18-39"
    vulnerable = "60+_nondigital_unassisted"
    for policy in POLICY_ORDER:
        for weather in WEATHER_ORDER:
            gaps = []
            for seed in seeds:
                vulnerable_row = by_key[(seed, policy, weather, vulnerable)]
                reference_row = by_key[(seed, policy, weather, reference)]
                gaps.append(
                    100.0
                    * (
                        number(vulnerable_row, "necessary_activity_completion_rate")
                        - number(reference_row, "necessary_activity_completion_rate")
                    )
                )
            mean, ci, n = mean_ci(gaps)
            output.append(
                {
                    "policy": policy,
                    "weather_scenario": weather,
                    "vulnerable_group": vulnerable,
                    "reference_group": reference,
                    "metric": "necessary_activity_completion_rate_gap_percentage_points",
                    "paired_mean_gap": fmt(mean, 6),
                    "ci95_low": fmt(mean - ci, 6),
                    "ci95_high": fmt(mean + ci, 6),
                    "seed_count": n,
                }
            )
    return output


def budget_efficiency(rows: Sequence[Mapping[str, str]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for policy in POLICY_ORDER:
        group = [
            row
            for row in rows
            if row["policy"] == policy and row["weather_scenario"] == "W2"
        ]
        means = {
            key: statistics.fmean(number(row, key) for row in group)
            for key in (
                "coupon_subsidy_yuan",
                "coupon_redeemed",
                "coupon_induced_requests",
                "successful_ride_hailing_requests",
            )
        }
        subsidy = means["coupon_subsidy_yuan"]
        redeemed = means["coupon_redeemed"]
        induced = means["coupon_induced_requests"]
        output.append(
            {
                "policy": policy,
                "weather_scenario": "W2",
                **{key: fmt(value, 6) for key, value in means.items()},
                "yuan_per_redeemed_coupon": fmt(subsidy / redeemed if redeemed else math.nan, 6),
                "yuan_per_induced_request": fmt(subsidy / induced if induced else math.nan, 6),
                "note": "descriptive mechanism efficiency; not a welfare or causal cost-effectiveness estimate",
            }
        )
    return output


def svg_document(width: int, height: int, body: str, title: str, description: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">\n'
        f"<title id=\"title\">{escape(title)}</title>\n"
        f"<desc id=\"desc\">{escape(description)}</desc>\n"
        '<rect width="100%" height="100%" fill="#ffffff"/>\n'
        '<style>text{font-family:"Microsoft YaHei","Noto Sans CJK SC",Arial,sans-serif;fill:#263238}'
        '.title{font-size:24px;font-weight:600}.subtitle{font-size:13px;fill:#607d8b}'
        '.axis{stroke:#cfd8dc;stroke-width:1}.tick{font-size:11px;fill:#607d8b}'
        '.label{font-size:12px}.value{font-size:11px;font-weight:600}</style>\n'
        f"{body}\n</svg>\n"
    )


def build_network_svg(city: Mapping[str, Any], network: Mapping[str, Any]) -> str:
    width, height = 980, 620
    zones = city["zones"]
    raw = {}
    for zone in zones:
        angle = math.radians(float(zone["angular_position_degrees"]))
        radius = float(zone["radial_distance_from_center"])
        raw[zone["zone_id"]] = (radius * math.cos(angle), radius * math.sin(angle))
    xs = [point[0] for point in raw.values()]
    ys = [point[1] for point in raw.values()]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    plot_x0, plot_x1, plot_y0, plot_y1 = 70, 700, 90, 550
    scale = min((plot_x1 - plot_x0) / (max_x - min_x), (plot_y1 - plot_y0) / (max_y - min_y))
    points = {
        zone: (
            plot_x0 + (x - min_x) * scale,
            plot_y1 - (y - min_y) * scale,
        )
        for zone, (x, y) in raw.items()
    }
    body = [
        '<text x="48" y="42" class="title">九区合成城市与多方式网络</text>',
        '<text x="48" y="66" class="subtitle">示意布局来自可审计的半径与角度参数，不代表上海行政区边界</text>',
    ]
    seen_edges = set()
    for zone in zones:
        left = zone["zone_id"]
        for right in zone["connected_to"]:
            edge = tuple(sorted((left, right)))
            if edge in seen_edges:
                continue
            seen_edges.add(edge)
            x1, y1 = points[left]
            x2, y2 = points[right]
            body.append(
                f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                'stroke="#b0bec5" stroke-width="3"/>'
            )
    for route in network["graphs"]["bus"]["routes"]:
        coords = " ".join(f"{points[z][0]:.1f},{points[z][1]:.1f}" for z in route["zones"])
        body.append(
            f'<polyline points="{coords}" fill="none" stroke="#2F80ED" stroke-width="3" '
            'stroke-opacity="0.62" stroke-linejoin="round"/>'
        )
    for line in network["graphs"]["metro"]["lines"]:
        coords = " ".join(f"{points[z][0]:.1f},{points[z][1]:.1f}" for z in line["zones"])
        body.append(
            f'<polyline points="{coords}" fill="none" stroke="#9C27B0" stroke-width="5" '
            'stroke-opacity="0.72" stroke-linejoin="round"/>'
        )
    ring_colors = {"core": "#FFE082", "inner": "#A5D6A7", "outer": "#90CAF9", "remote": "#FFAB91"}
    for zone in zones:
        zone_id = zone["zone_id"]
        x, y = points[zone_id]
        radius = 15 + math.sqrt(float(zone["target_area"])) / 5
        color = ring_colors[zone["spatial_ring"]]
        body.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" fill="{color}" '
            'stroke="#455a64" stroke-width="1.5"/>'
        )
        body.append(f'<text x="{x:.1f}" y="{y + 4:.1f}" text-anchor="middle" class="value">{zone_id}</text>')
    legend_x = 755
    body.extend(
        [
            f'<text x="{legend_x}" y="110" class="label" font-weight="600">图例</text>',
            f'<line x1="{legend_x}" y1="142" x2="{legend_x + 46}" y2="142" stroke="#b0bec5" stroke-width="3"/>',
            f'<text x="{legend_x + 58}" y="146" class="label">道路连接</text>',
            f'<line x1="{legend_x}" y1="176" x2="{legend_x + 46}" y2="176" stroke="#2F80ED" stroke-width="3"/>',
            f'<text x="{legend_x + 58}" y="180" class="label">公交线路</text>',
            f'<line x1="{legend_x}" y1="210" x2="{legend_x + 46}" y2="210" stroke="#9C27B0" stroke-width="5"/>',
            f'<text x="{legend_x + 58}" y="214" class="label">地铁线路</text>',
        ]
    )
    y = 265
    for ring, label in (("core", "核心"), ("inner", "内圈"), ("outer", "外围"), ("remote", "远郊")):
        body.append(f'<circle cx="{legend_x + 12}" cy="{y}" r="10" fill="{ring_colors[ring]}" stroke="#455a64"/>')
        body.append(f'<text x="{legend_x + 34}" y="{y + 4}" class="label">{label}</text>')
        y += 30
    body.append(f'<text x="{legend_x}" y="420" class="subtitle">节点面积仅用于视觉区分目标面积</text>')
    body.append(f'<text x="{legend_x}" y="442" class="subtitle">Z9 通过 Z6 接入主体网络</text>')
    return svg_document(width, height, "\n".join(body), "九区合成城市与多方式网络", "道路、公交和地铁在九个合成功能区之间的示意连接。")


def build_mode_svg(rows: Sequence[Mapping[str, str]]) -> str:
    width, height = 980, 540
    baseline = [row for row in rows if row["policy"] == POLICY_ORDER[0]]
    body = [
        '<text x="48" y="42" class="title">天气情景下的交通方式转移</text>',
        '<text x="48" y="66" class="subtitle">200 Agents、C0 无券、10 seeds；误差线为跨 seed 均值的 95% t 区间</text>',
    ]
    left, top, plot_w, plot_h = 85, 100, 825, 345
    for tick in range(0, 71, 10):
        y = top + plot_h - tick / 70 * plot_h
        body.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" class="axis"/>')
        body.append(f'<text x="{left - 12}" y="{y + 4:.1f}" text-anchor="end" class="tick">{tick}%</text>')
    group_w = plot_w / len(WEATHER_ORDER)
    bar_w = 42
    gap = 10
    for wi, weather in enumerate(WEATHER_ORDER):
        weather_rows = [row for row in baseline if row["weather_scenario"] == weather]
        total_w = len(MODE_FIELDS) * bar_w + (len(MODE_FIELDS) - 1) * gap
        start_x = left + wi * group_w + (group_w - total_w) / 2
        for mi, (field, label, color) in enumerate(MODE_FIELDS):
            mean, ci, _ = mean_ci(100 * number(row, field) for row in weather_rows)
            x = start_x + mi * (bar_w + gap)
            y = top + plot_h - mean / 70 * plot_h
            h = mean / 70 * plot_h
            body.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w}" height="{h:.1f}" rx="3" fill="{color}"/>')
            ci_y1 = top + plot_h - min(70, mean + ci) / 70 * plot_h
            ci_y2 = top + plot_h - max(0, mean - ci) / 70 * plot_h
            center = x + bar_w / 2
            body.append(f'<line x1="{center:.1f}" y1="{ci_y1:.1f}" x2="{center:.1f}" y2="{ci_y2:.1f}" stroke="#37474f"/>')
            body.append(f'<line x1="{center - 5:.1f}" y1="{ci_y1:.1f}" x2="{center + 5:.1f}" y2="{ci_y1:.1f}" stroke="#37474f"/>')
            body.append(f'<text x="{center:.1f}" y="{max(92, y - 8):.1f}" text-anchor="middle" class="value">{mean:.1f}</text>')
        body.append(f'<text x="{left + (wi + 0.5) * group_w:.1f}" y="{top + plot_h + 30}" text-anchor="middle" class="label">{WEATHER_SHORT[weather]}</text>')
    legend_y = 500
    legend_x = 195
    for _, label, color in MODE_FIELDS:
        body.append(f'<rect x="{legend_x}" y="{legend_y - 12}" width="16" height="16" rx="2" fill="{color}"/>')
        body.append(f'<text x="{legend_x + 24}" y="{legend_y + 1}" class="label">{label}</text>')
        legend_x += 145
    return svg_document(width, height, "\n".join(body), "天气情景下的交通方式转移", "常态、高温和强降雨条件下步行、公交、地铁和网约车的平均方式份额及置信区间。")


def build_policy_svg(rows: Sequence[Mapping[str, str]]) -> str:
    width, height = 1080, 650
    w2 = [row for row in rows if row["weather_scenario"] == "W2"]
    panels = [
        ("ride_hailing_requests", "网约车请求", 0.0, 75.0, "次"),
        ("failed_ride_hailing_requests", "派单失败", 0.0, 3.0, "次"),
        ("necessary_activity_completion_rate", "必要活动完成率", 92.0, 100.0, "%"),
        ("coupon_subsidy_yuan", "补贴支出", 0.0, 130.0, "元"),
    ]
    colors = ["#90A4AE", "#2F80ED", "#00A896", "#F2994A"]
    body = [
        '<text x="48" y="42" class="title">强降雨下的政策权衡</text>',
        '<text x="48" y="66" class="subtitle">200 Agents、W2、10 seeds；均值与 95% t 区间。不同面板纵轴独立。</text>',
    ]
    panel_w, panel_h, top = 235, 430, 115
    for pi, (field, title, ymin, ymax, unit) in enumerate(panels):
        x0 = 55 + pi * 255
        body.append(f'<text x="{x0 + panel_w / 2:.1f}" y="96" text-anchor="middle" class="label" font-weight="600">{title}</text>')
        body.append(f'<line x1="{x0}" y1="{top + panel_h}" x2="{x0 + panel_w}" y2="{top + panel_h}" class="axis"/>')
        body.append(f'<line x1="{x0}" y1="{top}" x2="{x0}" y2="{top + panel_h}" class="axis"/>')
        for tick_i in range(5):
            value = ymin + (ymax - ymin) * tick_i / 4
            y = top + panel_h - tick_i / 4 * panel_h
            body.append(f'<line x1="{x0}" y1="{y:.1f}" x2="{x0 + panel_w}" y2="{y:.1f}" class="axis"/>')
            body.append(f'<text x="{x0 - 8}" y="{y + 4:.1f}" text-anchor="end" class="tick">{value:.0f}{unit}</text>')
        bar_w = 38
        for index, policy in enumerate(POLICY_ORDER):
            policy_rows = [row for row in w2 if row["policy"] == policy]
            scale = 100.0 if field == "necessary_activity_completion_rate" else 1.0
            mean, ci, _ = mean_ci(scale * number(row, field) for row in policy_rows)
            clipped = min(ymax, max(ymin, mean))
            x = x0 + 16 + index * 54
            y = top + panel_h - (clipped - ymin) / (ymax - ymin) * panel_h
            zero_y = top + panel_h - (0 if ymin == 0 else 0) * panel_h
            h = top + panel_h - y if ymin == 0 else (clipped - ymin) / (ymax - ymin) * panel_h
            body.append(f'<rect x="{x}" y="{y:.1f}" width="{bar_w}" height="{h:.1f}" rx="3" fill="{colors[index]}"/>')
            ci_top = top + panel_h - (min(ymax, mean + ci) - ymin) / (ymax - ymin) * panel_h
            ci_bottom = top + panel_h - (max(ymin, mean - ci) - ymin) / (ymax - ymin) * panel_h
            center = x + bar_w / 2
            body.append(f'<line x1="{center:.1f}" y1="{ci_top:.1f}" x2="{center:.1f}" y2="{ci_bottom:.1f}" stroke="#37474f"/>')
            body.append(f'<line x1="{center - 4:.1f}" y1="{ci_top:.1f}" x2="{center + 4:.1f}" y2="{ci_top:.1f}" stroke="#37474f"/>')
            body.append(f'<text x="{center:.1f}" y="{max(top + 10, y - 7):.1f}" text-anchor="middle" class="value">{mean:.1f}</text>')
            body.append(f'<text x="{center:.1f}" y="{top + panel_h + 24}" text-anchor="middle" class="tick">C{index}</text>')
    legend_x, legend_y = 180, 600
    for index, policy in enumerate(POLICY_ORDER):
        body.append(f'<rect x="{legend_x}" y="{legend_y - 12}" width="16" height="16" rx="2" fill="{colors[index]}"/>')
        body.append(f'<text x="{legend_x + 23}" y="{legend_y + 1}" class="label">{POLICY_SHORT[policy]}</text>')
        legend_x += 225
    return svg_document(width, height, "\n".join(body), "强降雨下的政策权衡", "四种优惠券政策在网约车请求、派单失败、必要活动完成率和补贴支出上的比较。")


def build_funnel_svg(rows: Sequence[Mapping[str, str]]) -> str:
    width, height = 980, 520
    policies = POLICY_ORDER[1:]
    stages = [
        ("coupon_reached", "触达", "#B39DDB"),
        ("coupon_participated", "参与", "#64B5F6"),
        ("coupon_awarded", "获券", "#4DB6AC"),
        ("coupon_redeemed", "核销", "#FFB74D"),
    ]
    w2 = [row for row in rows if row["weather_scenario"] == "W2"]
    body = [
        '<text x="48" y="42" class="title">优惠券从触达到核销的漏斗</text>',
        '<text x="48" y="66" class="subtitle">200 Agents、W2、10 seeds；条长为每个 seed 的平均人数</text>',
    ]
    left, top, plot_w = 190, 115, 700
    max_value = 200.0
    row_h = 115
    for pi, policy in enumerate(policies):
        y0 = top + pi * row_h
        body.append(f'<text x="{left - 18}" y="{y0 + 36}" text-anchor="end" class="label" font-weight="600">{POLICY_SHORT[policy]}</text>')
        policy_rows = [row for row in w2 if row["policy"] == policy]
        for si, (field, label, color) in enumerate(stages):
            mean = statistics.fmean(number(row, field) for row in policy_rows)
            y = y0 + si * 21
            bar_w = mean / max_value * plot_w
            body.append(f'<rect x="{left}" y="{y}" width="{bar_w:.1f}" height="15" rx="3" fill="{color}"/>')
            body.append(f'<text x="{left + bar_w + 7:.1f}" y="{y + 12}" class="value">{mean:.1f}</text>')
    legend_x, legend_y = 255, 485
    for _, label, color in stages:
        body.append(f'<rect x="{legend_x}" y="{legend_y - 12}" width="16" height="16" rx="2" fill="{color}"/>')
        body.append(f'<text x="{legend_x + 24}" y="{legend_y + 1}" class="label">{label}</text>')
        legend_x += 140
    return svg_document(width, height, "\n".join(body), "优惠券从触达到核销的漏斗", "公共券、老年定向券和混合券的触达、参与、获券与核销人数比较。")


def markdown_report(
    system_rows: Sequence[Mapping[str, str]],
    paired_rows: Sequence[Mapping[str, Any]],
    gap_rows: Sequence[Mapping[str, Any]],
    budget_rows: Sequence[Mapping[str, Any]],
) -> str:
    seeds = sorted({int(row["seed"]) for row in system_rows})
    w2 = [row for row in system_rows if row["weather_scenario"] == "W2"]
    by_policy = {policy: [row for row in w2 if row["policy"] == policy] for policy in POLICY_ORDER}
    lines = [
        "# 竞赛结果卡",
        "",
        "> 自动生成文件。运行 `python -B -X utf8 -m scripts.build_competition_report` 可重建本页、CSV 与 SVG。",
        "",
        "## 证据范围",
        "",
        f"- 九区综合系统，200 Agents，工作日，seeds {seeds[0]}–{seeds[-1]}（共 {len(seeds)} 个）。",
        "- 同一 seed 内政策共享 Agents、活动、OD、天气与基础随机数，政策差异使用配对比较。",
        "- 95% 区间使用跨 seed 的 Student t 区间；10 seeds 适合机制证据，不等同于现实政策效应估计。",
        "",
        "## W2 强降雨核心结果",
        "",
        "| 政策 | 请求 | 成功 | 失败 | 券核销 | 补贴（元） | 必要活动完成率 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for policy in POLICY_ORDER:
        rows = by_policy[policy]
        means = {
            key: statistics.fmean(number(row, key) for row in rows)
            for key in (
                "ride_hailing_requests",
                "successful_ride_hailing_requests",
                "failed_ride_hailing_requests",
                "coupon_redeemed",
                "coupon_subsidy_yuan",
                "necessary_activity_completion_rate",
            )
        }
        lines.append(
            f"| {POLICY_SHORT[policy]} | {means['ride_hailing_requests']:.1f} | "
            f"{means['successful_ride_hailing_requests']:.1f} | {means['failed_ride_hailing_requests']:.1f} | "
            f"{means['coupon_redeemed']:.1f} | {means['coupon_subsidy_yuan']:.1f} | "
            f"{100 * means['necessary_activity_completion_rate']:.2f}% |"
        )
    lines.extend(
        [
            "",
            "## 配对不确定性",
            "",
            "以下变化均为相对 C0 的 seed 内配对差异。区间跨越 0 时，不把方向描述为稳定政策效应。",
            "",
            "| 政策 | 情景 | 指标 | 平均变化 | 95% CI |",
            "|---|---|---|---:|---:|",
        ]
    )
    paired_lookup = {
        (row["policy"], row["weather_scenario"], row["metric"]): row for row in paired_rows
    }
    for policy in POLICY_ORDER[1:]:
        for weather, metric, label, scale, suffix in (
            ("W2", "ride_hailing_requests", "网约车请求", 1.0, " 次"),
            ("W2", "failed_ride_hailing_requests", "派单失败", 1.0, " 次"),
            ("W2", "necessary_activity_completion_rate", "必要活动完成率", 100.0, " 个百分点"),
            ("W1", "total_heat_risk_burden", "总热风险负担", 1.0, ""),
        ):
            row = paired_lookup[(policy, weather, metric)]
            mean = float(row["paired_mean_change"]) * scale
            low = float(row["ci95_low"]) * scale
            high = float(row["ci95_high"]) * scale
            lines.append(
                f"| {POLICY_SHORT[policy]} | {WEATHER_SHORT[weather]} | {label} | "
                f"{mean:+.2f}{suffix} | [{low:+.2f}, {high:+.2f}]{suffix} |"
            )
    lines.extend(
        [
            "",
            "## 等支出解释",
            "",
            "当前 C1–C3 都配置为每日 40 张券，但核销量和实际补贴支出不同，因此不是严格等预算实验。下表只报告描述性机制效率。",
            "",
            "| 政策 | 补贴（元） | 核销 | 诱发请求 | 元/核销 | 元/诱发请求 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in budget_rows[1:]:
        lines.append(
            f"| {POLICY_SHORT[row['policy']]} | {float(row['coupon_subsidy_yuan']):.1f} | "
            f"{float(row['coupon_redeemed']):.1f} | {float(row['coupon_induced_requests']):.1f} | "
            f"{float(row['yuan_per_redeemed_coupon']):.2f} | {float(row['yuan_per_induced_request']):.2f} |"
        )
    gap_lookup = {(row["policy"], row["weather_scenario"]): row for row in gap_rows}
    lines.extend(
        [
            "",
            "## 交叉公平性检查",
            "",
            "指标为“60+、非数字且无家庭协助”相对 18–39 岁组的必要活动完成率差距（百分点）。负值表示脆弱组更低。",
            "",
            "| 政策 | W2 平均差距 | 95% CI |",
            "|---|---:|---:|",
        ]
    )
    for policy in POLICY_ORDER:
        row = gap_lookup[(policy, "W2")]
        lines.append(
            f"| {POLICY_SHORT[policy]} | {float(row['paired_mean_gap']):+.2f} | "
            f"[{float(row['ci95_low']):+.2f}, {float(row['ci95_high']):+.2f}] |"
        )
    lines.extend(
        [
            "",
            "## 可支持的结论",
            "",
            "1. 强降雨使道路公交吸引力下降，地铁与网约车承担更多出行。",
            "2. 公共券和混合券稳定增加网约车请求，但有限车辆池会把部分增量转化为派单失败。",
            "3. fallback 吸收了大部分局部失败，因此当前样本中必要活动完成率变化很小。",
            "4. 老年定向券名义覆盖高、核销低，说明触达、实际请求与成功服务是不同环节。",
            "",
            "## 不应支持的结论",
            "",
            "- 不把 200 个合成 Agent 外推为上海人口或交通量。",
            "- 不把 10 seeds 的机制差异表述为现实政策因果效应。",
            "- 不用组成均值 `mean_road_speed_kmh` 单独声称全城拥堵改善。",
            "- 不把发券数量当作等预算；正式比较需按实际支出重新设定政策池。",
            "",
        ]
    )
    return "\n".join(lines)


def build(output_root: Path) -> list[Path]:
    system_rows = read_csv(SYSTEM_PATH)
    group_rows = read_csv(GROUP_PATH)
    expected = len(POLICY_ORDER) * len(WEATHER_ORDER) * len({row["seed"] for row in system_rows})
    if len(system_rows) != expected:
        raise ValueError(f"system_per_seed row count {len(system_rows)} != expected {expected}")
    city = json.loads(CITY_PATH.read_text(encoding="utf-8"))
    network = json.loads(NETWORK_PATH.read_text(encoding="utf-8"))

    aggregate_rows = aggregate_system(system_rows)
    paired_rows = paired_effects(system_rows)
    fairness_rows = fairness_summary(group_rows)
    gap_rows = equity_gaps(group_rows)
    budget_rows = budget_efficiency(system_rows)

    results_dir = output_root / "docs" / "results"
    figures_dir = output_root / "docs" / "figures"
    results_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)
    artifacts = [
        results_dir / "competition_metrics.csv",
        results_dir / "paired_policy_effects.csv",
        results_dir / "fairness_metrics.csv",
        results_dir / "equity_gaps.csv",
        results_dir / "budget_efficiency.csv",
        results_dir / "COMPETITION_RESULTS.md",
        figures_dir / "nine_zone_network.svg",
        figures_dir / "weather_mode_shift.svg",
        figures_dir / "w2_policy_tradeoffs.svg",
        figures_dir / "coupon_funnel.svg",
    ]
    write_csv(
        artifacts[0],
        aggregate_rows,
        ["policy", "weather_scenario", "metric", "mean", "ci95_half_width", "seed_count"],
    )
    write_csv(
        artifacts[1],
        paired_rows,
        [
            "policy",
            "baseline_policy",
            "weather_scenario",
            "metric",
            "paired_mean_change",
            "ci95_low",
            "ci95_high",
            "seed_count",
        ],
    )
    write_csv(
        artifacts[2],
        fairness_rows,
        ["policy", "weather_scenario", "group", "metric", "mean", "ci95_half_width", "seed_count"],
    )
    write_csv(
        artifacts[3],
        gap_rows,
        [
            "policy",
            "weather_scenario",
            "vulnerable_group",
            "reference_group",
            "metric",
            "paired_mean_gap",
            "ci95_low",
            "ci95_high",
            "seed_count",
        ],
    )
    write_csv(
        artifacts[4],
        budget_rows,
        [
            "policy",
            "weather_scenario",
            "coupon_subsidy_yuan",
            "coupon_redeemed",
            "coupon_induced_requests",
            "successful_ride_hailing_requests",
            "yuan_per_redeemed_coupon",
            "yuan_per_induced_request",
            "note",
        ],
    )
    artifacts[5].write_text(
        markdown_report(system_rows, paired_rows, gap_rows, budget_rows), encoding="utf-8"
    )
    artifacts[6].write_text(build_network_svg(city, network), encoding="utf-8")
    artifacts[7].write_text(build_mode_svg(system_rows), encoding="utf-8")
    artifacts[8].write_text(build_policy_svg(system_rows), encoding="utf-8")
    artifacts[9].write_text(build_funnel_svg(system_rows), encoding="utf-8")
    return artifacts


def check_committed_artifacts() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        generated = build(temp_root)
        mismatches = []
        for generated_path in generated:
            relative = generated_path.relative_to(temp_root)
            committed = ROOT / relative
            if not committed.exists() or committed.read_bytes() != generated_path.read_bytes():
                mismatches.append(str(relative))
        if mismatches:
            raise SystemExit(
                "Generated competition artifacts are stale or missing: " + ", ".join(mismatches)
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Regenerate in a temporary directory and verify committed artifacts are current.",
    )
    args = parser.parse_args()
    if args.check:
        check_committed_artifacts()
        print("Competition report artifacts are current.")
        return
    artifacts = build(ROOT)
    print(f"Generated {len(artifacts)} competition report artifacts.")


if __name__ == "__main__":
    main()
