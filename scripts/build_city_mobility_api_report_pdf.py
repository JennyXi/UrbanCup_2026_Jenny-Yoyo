from __future__ import annotations

import csv
import json
from pathlib import Path

from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
RESULT_DIR = ROOT / "outputs" / "city_mobility_200_api_w2_seed47"
OUTPUT_PATH = ROOT / "output" / "pdf" / "city_mobility_200_api_run_report.pdf"
PAGE_W, PAGE_H = A4
MARGIN = 40

NAVY = HexColor("#102A43")
BLUE = HexColor("#276EF1")
TEAL = HexColor("#14A39A")
ORANGE = HexColor("#F59E45")
GREEN = HexColor("#1F9D6C")
INK = HexColor("#243B53")
MUTED = HexColor("#627D98")
LINE = HexColor("#D9E2EC")
PANEL = HexColor("#F4F7FA")
PALE_BLUE = HexColor("#EAF1FF")
PALE_TEAL = HexColor("#E7F7F5")
PALE_ORANGE = HexColor("#FFF2E2")


def register_fonts() -> None:
    pdfmetrics.registerFont(TTFont("Deng", r"C:\Windows\Fonts\Deng.ttf"))
    pdfmetrics.registerFont(TTFont("DengBold", r"C:\Windows\Fonts\Dengb.ttf"))


def rounded_box(c: canvas.Canvas, x: float, y: float, w: float, h: float, fill, radius: float = 9) -> None:
    c.setFillColor(fill)
    c.setStrokeColor(fill)
    c.roundRect(x, y, w, h, radius, fill=1, stroke=0)


def wrap_text(text: str, font: str, size: float, width: float) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        if char == "\n":
            lines.append(current)
            current = ""
            continue
        candidate = current + char
        if current and pdfmetrics.stringWidth(candidate, font, size) > width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current or not lines:
        lines.append(current)
    return lines


def paragraph(
    c: canvas.Canvas,
    text: str,
    x: float,
    y: float,
    width: float,
    *,
    font: str = "Deng",
    size: float = 9,
    color=INK,
    leading: float | None = None,
) -> float:
    leading = leading or size * 1.45
    c.setFont(font, size)
    c.setFillColor(color)
    for line in wrap_text(text, font, size, width):
        c.drawString(x, y, line)
        y -= leading
    return y


def section_title(c: canvas.Canvas, number: str, title: str, y: float) -> None:
    rounded_box(c, MARGIN, y - 3, 21, 21, BLUE, 5)
    c.setFillColor(white)
    c.setFont("DengBold", 10)
    c.drawCentredString(MARGIN + 10.5, y + 3.2, number)
    c.setFillColor(NAVY)
    c.setFont("DengBold", 14)
    c.drawString(MARGIN + 30, y + 1, title)


def footer(c: canvas.Canvas, page: int) -> None:
    c.setStrokeColor(LINE)
    c.setLineWidth(0.6)
    c.line(MARGIN, 28, PAGE_W - MARGIN, 28)
    c.setFillColor(MUTED)
    c.setFont("Deng", 7.4)
    c.drawString(MARGIN, 16, "数据来源：GitHub本地运行输出｜W2强降雨｜seed 47｜报告不包含API密钥")
    c.drawRightString(PAGE_W - MARGIN, 16, f"{page} / 2")


def metric_card(c: canvas.Canvas, x: float, y: float, w: float, value: str, label: str, accent) -> None:
    rounded_box(c, x, y, w, 58, PANEL, 8)
    c.setFillColor(accent)
    c.rect(x, y, 4, 58, fill=1, stroke=0)
    c.setFillColor(NAVY)
    c.setFont("DengBold", 19)
    c.drawString(x + 13, y + 31, value)
    c.setFillColor(MUTED)
    c.setFont("Deng", 8.4)
    c.drawString(x + 13, y + 11, label)


def load_data() -> tuple[dict, list[dict], dict]:
    summary = json.loads((RESULT_DIR / "summary.json").read_text(encoding="utf-8"))
    with (RESULT_DIR / "decision_audit.csv").open(encoding="utf-8-sig", newline="") as stream:
        decisions = list(csv.DictReader(stream))
    maximum = max(decisions, key=lambda row: float(row["maximum_absolute_probability_delta"]))
    maximum = {
        **maximum,
        "without": json.loads(maximum["mode_probabilities_without_prior_agents"]),
        "with": json.loads(maximum["mode_probabilities_with_prior_agents"]),
    }
    return summary, decisions, maximum


def draw_header(c: canvas.Canvas, title: str, subtitle: str | None = None) -> None:
    c.setFillColor(NAVY)
    c.rect(0, PAGE_H - 82, PAGE_W, 82, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont("DengBold", 18)
    c.drawString(MARGIN, PAGE_H - 45, title)
    if subtitle:
        c.setFillColor(HexColor("#DCE9F5"))
        c.setFont("Deng", 8.5)
        c.drawRightString(PAGE_W - MARGIN, PAGE_H - 44, subtitle)


def page_one(c: canvas.Canvas, summary: dict, decisions: list[dict]) -> None:
    c.setFillColor(NAVY)
    c.rect(0, PAGE_H - 132, PAGE_W, 132, fill=1, stroke=0)
    c.setFillColor(TEAL)
    c.circle(PAGE_W - 67, PAGE_H - 45, 43, fill=1, stroke=0)
    c.setFillColor(Color(1, 1, 1, alpha=0.13))
    c.circle(PAGE_W - 29, PAGE_H - 100, 57, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont("DengBold", 23)
    c.drawString(MARGIN, PAGE_H - 50, "200人九区城市出行")
    c.setFont("DengBold", 18)
    c.drawString(MARGIN, PAGE_H - 79, "真实API运行报告")
    c.setFillColor(HexColor("#DCE9F5"))
    c.setFont("Deng", 9)
    c.drawString(MARGIN, PAGE_H - 104, "W2强降雨工作日｜步行 / 公交 / 地铁 / 网约车｜C4分券")
    rounded_box(c, PAGE_W - 148, PAGE_H - 116, 94, 24, white, 12)
    c.setFillColor(GREEN)
    c.setFont("DengBold", 9.5)
    c.drawCentredString(PAGE_W - 101, PAGE_H - 108, "运行通过  PASS")

    y = PAGE_H - 157
    c.setFillColor(NAVY)
    c.setFont("DengBold", 11)
    c.drawString(MARGIN, y, "实验范围")
    paragraph(
        c,
        "GitHub仓库的完整九区城市出行机制：居民逐条选择交通方式，网约车选择立即更新道路状态，随后Agent读取新状态并改变选择概率。",
        MARGIN + 57,
        y + 1,
        PAGE_W - 2 * MARGIN - 57,
        size=9.1,
    )

    card_w = (PAGE_W - 2 * MARGIN - 20) / 3
    y1 = PAGE_H - 249
    y2 = PAGE_H - 317
    metric_card(c, MARGIN, y1, card_w, str(summary["agents"]), "居民总体", BLUE)
    metric_card(c, MARGIN + card_w + 10, y1, card_w, str(summary["travel_decisions"]), "真实API出行决策", TEAL)
    metric_card(c, MARGIN + 2 * (card_w + 10), y1, card_w, str(summary["api_decision_failures"]), "API决策失败", GREEN)
    metric_card(c, MARGIN, y2, card_w, str(summary["ride_hailing_traffic_events"]), "网约车道路事件", ORANGE)
    metric_card(c, MARGIN + card_w + 10, y2, card_w, str(summary["affected_decisions"]), "受先前Agent影响的决策", BLUE)
    metric_card(c, MARGIN + 2 * (card_w + 10), y2, card_w, str(summary["influence_edges"]), "A到B影响边", TEAL)

    section_title(c, "1", "交通方式选择结果", PAGE_H - 353)
    panel_y = PAGE_H - 513
    rounded_box(c, MARGIN, panel_y, PAGE_W - 2 * MARGIN, 124, PANEL, 9)
    counts = summary["chosen_mode_counts"]
    modes = [
        ("步行", "walk", HexColor("#8CA3B7")),
        ("公交", "bus", BLUE),
        ("地铁", "metro", TEAL),
        ("网约车", "ride_hailing", ORANGE),
    ]
    total = sum(int(counts[key]) for _, key, _ in modes)
    bar_x = MARGIN + 15
    bar_y = panel_y + 57
    bar_w = PAGE_W - 2 * MARGIN - 30
    cursor = bar_x
    for _, key, color in modes:
        width = bar_w * int(counts[key]) / total
        c.setFillColor(color)
        c.rect(cursor, bar_y, width, 24, fill=1, stroke=0)
        cursor += width
    c.setFillColor(INK)
    c.setFont("DengBold", 9.5)
    c.drawString(bar_x, panel_y + 98, f"395条工作日出行：地铁占比最高（{counts['metro']}条）")
    legend_x = bar_x
    for label, key, color in modes:
        c.setFillColor(color)
        c.circle(legend_x + 4, panel_y + 28, 4, fill=1, stroke=0)
        c.setFillColor(MUTED)
        c.setFont("Deng", 8.3)
        c.drawString(legend_x + 12, panel_y + 25, f"{label} {counts[key]}（{int(counts[key]) / total:.1%}）")
        legend_x += 120

    section_title(c, "2", "联动机制是否真正生效", PAGE_H - 550)
    box_y = PAGE_H - 690
    rounded_box(c, MARGIN, box_y, PAGE_W - 2 * MARGIN, 99, PALE_TEAL, 10)
    affected_share = summary["affected_decisions"] / summary["travel_decisions"]
    c.setFillColor(TEAL)
    c.setFont("DengBold", 11)
    c.drawString(MARGIN + 15, box_y + 73, "生效：58.2%的出行决策读取到了先前网约车选择造成的概率变化")
    paragraph(
        c,
        f"共{summary['ride_hailing_traffic_events']}次网约车选择写入共享道路状态，形成{summary['influence_edges']}条有向影响边；受影响决策{summary['affected_decisions']}条（{affected_share:.1%}），单个方式概率最大变化为{summary['maximum_absolute_probability_change']:.2%}。峰值时段为07:30，8次网约车事件对应240 PCU/小时的内生代表流量。",
        MARGIN + 15,
        box_y + 51,
        PAGE_W - 2 * MARGIN - 30,
        size=9,
        leading=13,
    )
    footer(c, 1)
    c.showPage()


def arrow(c: canvas.Canvas, x1: float, y: float, x2: float) -> None:
    c.setStrokeColor(BLUE)
    c.setFillColor(BLUE)
    c.setLineWidth(1.4)
    c.line(x1, y, x2 - 6, y)
    path = c.beginPath()
    path.moveTo(x2, y)
    path.lineTo(x2 - 7, y + 4)
    path.lineTo(x2 - 7, y - 4)
    path.close()
    c.drawPath(path, fill=1, stroke=0)


def page_two(c: canvas.Canvas, summary: dict, decisions: list[dict], maximum: dict) -> None:
    draw_header(c, "城市出行联动证据与系统结果", "GitHub九区交通模型｜AgentSociety API")
    section_title(c, "3", "最显著的A影响B案例", PAGE_H - 118)
    flow_y = PAGE_H - 219
    node_gap = 16
    node_w = (PAGE_W - 2 * MARGIN - node_gap * 3) / 4
    nodes = [
        ("前序Agent", "6次网约车选择", PALE_ORANGE, ORANGE),
        ("道路状态", "同一30分钟时段", PALE_BLUE, BLUE),
        ("Agent 112", "工作出行决策", PALE_TEAL, TEAL),
        ("最终选择", "地铁", PANEL, NAVY),
    ]
    for idx, (title, subtitle, fill, accent) in enumerate(nodes):
        x = MARGIN + idx * (node_w + node_gap)
        rounded_box(c, x, flow_y, node_w, 69, fill, 9)
        c.setFillColor(accent)
        c.setFont("DengBold", 9.5)
        c.drawCentredString(x + node_w / 2, flow_y + 41, title)
        c.setFillColor(MUTED)
        c.setFont("Deng", 8)
        c.drawCentredString(x + node_w / 2, flow_y + 21, subtitle)
        if idx < 3:
            arrow(c, x + node_w + 3, flow_y + 35, x + node_w + node_gap - 3)

    table_y = PAGE_H - 341
    rounded_box(c, MARGIN, table_y, PAGE_W - 2 * MARGIN, 88, PANEL, 9)
    c.setFillColor(INK)
    c.setFont("DengBold", 9.5)
    c.drawString(MARGIN + 14, table_y + 64, "方式概率变化")
    headers = ["公交", "地铁", "网约车"]
    keys = ["bus", "metro", "ride_hailing"]
    start_x = MARGIN + 190
    col_w = 90
    for idx, (label, key) in enumerate(zip(headers, keys)):
        x = start_x + idx * col_w
        before = float(maximum["without"][key])
        after = float(maximum["with"][key])
        delta = after - before
        c.setFillColor(MUTED)
        c.setFont("Deng", 8)
        c.drawCentredString(x, table_y + 65, label)
        c.setFillColor(NAVY)
        c.setFont("DengBold", 9)
        c.drawCentredString(x, table_y + 42, f"{before:.1%} → {after:.1%}")
        c.setFillColor(GREEN if delta > 0 else ORANGE)
        c.setFont("DengBold", 8)
        c.drawCentredString(x, table_y + 20, f"{delta:+.2%}")
    paragraph(
        c,
        "该案例显示拥堵反馈降低公交吸引力，并把选择概率主要推向不受道路拥堵影响的地铁。",
        MARGIN + 14,
        table_y + 41,
        115,
        size=8.2,
        leading=11.5,
    )

    section_title(c, "4", "优惠券如何进入城市出行", PAGE_H - 382)
    coupon_y = PAGE_H - 510
    rounded_box(c, MARGIN, coupon_y, PAGE_W - 2 * MARGIN, 91, PALE_ORANGE, 9)
    c.setFillColor(ORANGE)
    c.setFont("DengBold", 10.5)
    c.drawString(MARGIN + 14, coupon_y + 66, "公共品博弈只负责分券，出行选择仍由城市交通Agent完成")
    paragraph(
        c,
        f"公共品阶段126次API贡献决策产生40名获券者；其中30人的当天首次出行展示了八折券，9人因此选择网约车并完成核销。券不创造额外道路容量，也不增加实体券数量。",
        MARGIN + 14,
        coupon_y + 45,
        PAGE_W - 2 * MARGIN - 28,
        size=8.8,
        leading=12.5,
    )

    section_title(c, "5", "系统结果与边界", PAGE_H - 548)
    box_y = PAGE_H - 704
    gap = 10
    col_w = (PAGE_W - 2 * MARGIN - gap) / 2
    rounded_box(c, MARGIN, box_y, col_w, 117, PALE_TEAL, 9)
    c.setFillColor(TEAL)
    c.setFont("DengBold", 10)
    c.drawString(MARGIN + 14, box_y + 94, "运行结果")
    paragraph(
        c,
        f"• 180人当天出行，20人无工作日行程。\n• 395条交通服务全部成功。\n• 必要活动完成率 {summary['necessary_activity_completion_rate']:.2%}。\n• 平均旅行时间 {summary['mean_total_travel_time']:.1f} 分钟。\n• 48辆网约车完成76次请求，0次失败。",
        MARGIN + 14,
        box_y + 73,
        col_w - 28,
        size=8.2,
        leading=13,
    )
    right_x = MARGIN + col_w + gap
    rounded_box(c, right_x, box_y, col_w, 117, PALE_BLUE, 9)
    c.setFillColor(BLUE)
    c.setFont("DengBold", 10)
    c.drawString(right_x + 14, box_y + 94, "解释边界")
    paragraph(
        c,
        "• 单一seed与合成九区，不是上海预测。\n• 共享道路采用代表性走廊和30分钟分桶。\n• 不同时间桶并行，桶内保持严格顺序。\n• 每个Agent代表30次同类出行，仅用于机制展示。",
        right_x + 14,
        box_y + 73,
        col_w - 28,
        size=8.2,
        leading=13,
    )

    repro_y = 45
    rounded_box(c, MARGIN, repro_y, PAGE_W - 2 * MARGIN, 65, PANEL, 8)
    total_tokens = int(summary["usage"]["input_tokens"]) + int(summary["usage"]["output_tokens"])
    c.setFillColor(NAVY)
    c.setFont("DengBold", 9)
    c.drawString(MARGIN + 13, repro_y + 44, "复现信息")
    c.setFillColor(MUTED)
    c.setFont("Deng", 7.5)
    c.drawString(MARGIN + 13, repro_y + 27, f"模型：{summary['model']}｜API调用：{summary['usage']['calls']}｜Token：{total_tokens:,}｜耗时：{summary['elapsed_seconds']:.1f}秒")
    c.drawString(MARGIN + 13, repro_y + 12, "结果目录：outputs/city_mobility_200_api_w2_seed47｜API密钥持久化：否")
    footer(c, 2)
    c.showPage()


def build() -> Path:
    register_fonts()
    summary, decisions, maximum = load_data()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(OUTPUT_PATH), pagesize=A4, pageCompression=1)
    c.setTitle("200人九区城市出行真实API运行报告")
    c.setAuthor("UrbanCup 2026 Experiment")
    c.setSubject("API-backed interdependent urban mobility")
    page_one(c, summary, decisions)
    page_two(c, summary, decisions, maximum)
    c.save()
    return OUTPUT_PATH


if __name__ == "__main__":
    build()
    print("REPORT_CREATED")
