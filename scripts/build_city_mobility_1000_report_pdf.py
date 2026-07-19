"""Build the two-page Chinese run report for the 1000-Agent API experiment."""

from __future__ import annotations

import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "outputs" / "city_mobility_1000_api_w2_seed47_main_elder_v2"
PREVIOUS_DATA_DIR = ROOT / "outputs" / "city_mobility_1000_api_w2_seed47"
OUTPUT = ROOT / "output" / "pdf" / "city_mobility_1000_main_elder_v2_report_zh.pdf"

NAVY = colors.HexColor("#17365D")
TEAL = colors.HexColor("#0F6B78")
BLUE = colors.HexColor("#2F75B5")
LIGHT_BLUE = colors.HexColor("#D9EAF7")
LIGHT_GREEN = colors.HexColor("#E2F0D9")
LIGHT_ORANGE = colors.HexColor("#FCE4D6")
GRAY = colors.HexColor("#666666")
LIGHT_GRAY = colors.HexColor("#F2F2F2")
WHITE = colors.white


def pct(value: float, digits: int = 1) -> str:
    return f"{value * 100:.{digits}f}%"


def para(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(text, style)


def base_table_style(header: bool = True) -> TableStyle:
    commands = [
        ("FONTNAME", (0, 0), (-1, -1), "MSYH"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.4),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#222222")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, -1), (-1, -1), 0.4, colors.HexColor("#B8C4CE")),
    ]
    if header:
        commands.extend(
            [
                ("BACKGROUND", (0, 0), (-1, 0), TEAL),
                ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
                ("FONTNAME", (0, 0), (-1, 0), "MSYHBD"),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
            ]
        )
    return TableStyle(commands)


def section(title: str, styles: dict[str, ParagraphStyle]) -> list:
    return [Spacer(1, 2.5 * mm), Paragraph(title, styles["section"]), Spacer(1, 1.3 * mm)]


def on_page(canvas, doc) -> None:
    canvas.saveState()
    width, height = A4
    canvas.setFillColor(NAVY)
    canvas.rect(0, height - 8 * mm, width, 8 * mm, stroke=0, fill=1)
    canvas.setFont("MSYH", 7)
    canvas.setFillColor(colors.HexColor("#777777"))
    canvas.drawString(16 * mm, 8 * mm, "Urban Cup 2026 · 1000人城市出行多Agent真实API实验")
    canvas.drawRightString(width - 16 * mm, 8 * mm, f"第 {doc.page} / 2 页")
    canvas.restoreState()


def main() -> None:
    summary = json.loads((DATA_DIR / "summary.json").read_text(encoding="utf-8"))
    validation = json.loads((DATA_DIR / "validation.json").read_text(encoding="utf-8"))
    security = json.loads((DATA_DIR / "security_scan.json").read_text(encoding="utf-8"))
    previous = json.loads(
        (PREVIOUS_DATA_DIR / "summary.json").read_text(encoding="utf-8")
    )

    pdfmetrics.registerFont(TTFont("MSYH", r"C:\Windows\Fonts\msyh.ttc"))
    pdfmetrics.registerFont(TTFont("MSYHBD", r"C:\Windows\Fonts\msyhbd.ttc"))
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="Ctitle",
            parent=styles["Title"],
            fontName="MSYHBD",
            fontSize=17,
            leading=22,
            textColor=NAVY,
            alignment=TA_LEFT,
            spaceAfter=3 * mm,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Csubtitle",
            parent=styles["Normal"],
            fontName="MSYH",
            fontSize=8.5,
            leading=12,
            textColor=GRAY,
            spaceAfter=2 * mm,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Csection",
            parent=styles["Heading2"],
            fontName="MSYHBD",
            fontSize=10.5,
            leading=13,
            textColor=WHITE,
            backColor=BLUE,
            borderPadding=(3, 5, 3, 5),
            spaceBefore=0,
            spaceAfter=0,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Cbody",
            parent=styles["BodyText"],
            fontName="MSYH",
            fontSize=8.1,
            leading=12,
            textColor=colors.HexColor("#222222"),
            alignment=TA_LEFT,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Csmall",
            parent=styles["BodyText"],
            fontName="MSYH",
            fontSize=7.2,
            leading=10,
            textColor=GRAY,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Ckpi",
            parent=styles["Normal"],
            fontName="MSYHBD",
            fontSize=11.5,
            leading=14,
            textColor=NAVY,
            alignment=TA_CENTER,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Ckpilabel",
            parent=styles["Normal"],
            fontName="MSYH",
            fontSize=7,
            leading=9,
            textColor=GRAY,
            alignment=TA_CENTER,
        )
    )
    s = {
        "title": styles["Ctitle"],
        "subtitle": styles["Csubtitle"],
        "section": styles["Csection"],
        "body": styles["Cbody"],
        "small": styles["Csmall"],
        "kpi": styles["Ckpi"],
        "kpilabel": styles["Ckpilabel"],
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title="1000人城市出行多Agent真实API实验主线老年算法更新版运行报告",
        author="Urban Cup 2026",
        subject="Seed 47, W2, workday, real API",
    )
    story = []
    story.append(Paragraph("1000人城市出行多Agent真实API实验 - 老年算法更新版", s["title"]))
    story.append(
        Paragraph(
            f"Seed 47 · W2暴雨工作日 · {summary['model']} · A1主线老年行为",
            s["subtitle"],
        )
    )
    status = Table(
        [[
            para("运行状态", s["kpilabel"]),
            para(summary["status"], s["kpi"]),
            para("一致性检查", s["kpilabel"]),
            para(f"{validation['checks_passed']}/{validation['checks_total']} PASS", s["kpi"]),
            para("密钥落盘扫描", s["kpilabel"]),
            para(f"{security['credential_matches']} 命中", s["kpi"]),
        ]],
        colWidths=[24 * mm, 34 * mm, 28 * mm, 40 * mm, 30 * mm, 24 * mm],
        rowHeights=[18 * mm],
    )
    status.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), LIGHT_GREEN),
                ("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor("#8DB37B")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(status)
    story += section("一、真实API与运行规模", s)
    kpis = [
        (f"{summary['agents']:,}", "Agent总数"),
        (f"{summary['api_successful_travel_decisions']}/{summary['travel_decisions']}", "出行API成功"),
        (f"{summary['coupon_agent']['api_contribution_decisions'] - summary['coupon_agent']['api_decision_failures']}/{summary['coupon_agent']['api_contribution_decisions']}", "公共品贡献API成功"),
        (f"{summary['api_attempt_failures']}", "API尝试失败"),
        (f"{summary['usage']['total_tokens']:,}", "总tokens"),
        (f"{summary['elapsed_seconds']:.0f}秒", "总运行耗时"),
    ]
    kpi_table = Table(
        [[para(v, s["kpi"]) for v, _ in kpis], [para(l, s["kpilabel"]) for _, l in kpis]],
        colWidths=[29.5 * mm] * 6,
        rowHeights=[9 * mm, 6 * mm],
    )
    kpi_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BLUE),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#AABBC8")),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.white),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(kpi_table)
    story.append(Spacer(1, 1.5 * mm))
    story.append(
        Paragraph(
            "每一次出行腿均真实调用API；错误路径不使用本地最高概率补位。成功响应即时写入检查点，"
            f"调用尝试、HTTP状态、token、耗时和响应哈希均逐条审计。本次共{summary['api_attempts_total']}次API尝试，失败{summary['api_attempt_failures']}次。",
            s["body"],
        )
    )

    story += section("二、方式结构与Agent联动", s)
    modes = summary["final_successful_mode_counts"]
    mode_total = sum(modes.values())
    mode_data = [
        ["方式", "次数", "占比", "旧1000 A0占比"],
        ["步行", modes["walk"], pct(modes["walk"] / mode_total), pct(previous["final_successful_mode_counts"]["walk"] / previous["travel_decisions"])],
        ["公交", modes["bus"], pct(modes["bus"] / mode_total), pct(previous["final_successful_mode_counts"]["bus"] / previous["travel_decisions"])],
        ["地铁", modes["metro"], pct(modes["metro"] / mode_total), pct(previous["final_successful_mode_counts"]["metro"] / previous["travel_decisions"])],
        ["网约车", modes["ride_hailing"], pct(modes["ride_hailing"] / mode_total), pct(previous["final_successful_mode_counts"]["ride_hailing"] / previous["travel_decisions"])],
    ]
    link_data = [
        ["联动指标", "A1更新版", "旧1000 A0"],
        ["网约车交通事件", summary["ride_hailing_traffic_events"], previous["ride_hailing_traffic_events"]],
        ["受影响决策", summary["affected_decisions"], previous["affected_decisions"]],
        ["联动覆盖率", pct(summary["linkage_coverage_rate"]), pct(previous["linkage_coverage_rate"])],
        ["条件联动率", pct(summary["conditional_linkage_rate"]), pct(previous["conditional_linkage_rate"])],
        ["影响边", f"{summary['influence_edges']:,}", f"{previous['influence_edges']:,}"],
        ["最大绝对概率变化", f"{summary['maximum_absolute_probability_change']:.4f}", f"{previous['maximum_absolute_probability_change']:.4f}"],
    ]
    mt = Table(mode_data, colWidths=[18 * mm, 18 * mm, 22 * mm, 30 * mm])
    mt.setStyle(base_table_style())
    lt = Table(link_data, colWidths=[32 * mm, 28 * mm, 28 * mm])
    lt.setStyle(base_table_style())
    combined = Table([[mt, lt]], colWidths=[88 * mm, 88 * mm])
    combined.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
    story.append(combined)
    story.append(Spacer(1, 1.5 * mm))
    story.append(
        Paragraph(
            "同一30分钟时间窗内，网约车选择即时增加6 PCU/小时的内生流量；后续Agent重新计算四种方式概率。"
            f"本次{summary['decisions_with_prior_influencers']}个决策存在前序影响源，其中{summary['affected_decisions']}个概率实际改变，"
            f"形成{summary['influence_edges']:,}条严格按时间先后排列的影响边。",
            s["body"],
        )
    )

    story += section("三、A1主线老年算法与可比性", s)
    comp = Table(
        [
            ["维度", "旧1000 A0", "本次A1", "处理"],
            ["60+网约车常数", "-0.5", "0.3", "采用main稳定候选"],
            ["W2年龄暴露权重", "未加载", "60+=1.6", "明确改变口径"],
            ["医疗需要暴露权重", "未加载", "1.0/1.2/1.5", "仅老年选择阶段"],
            ["必要出行费用敏感", "1.0", "0.9", "仅W1/W2 work/medical"],
            ["公交-地铁换乘负担", "0", "3分钟/次", "只改变感知时间"],
        ],
        colWidths=[31 * mm, 31 * mm, 34 * mm, 80 * mm],
    )
    comp.setStyle(base_table_style())
    story.append(comp)
    story.append(PageBreak())

    story.append(Paragraph("全年龄结果、优惠券与活动完成", s["title"]))
    story.append(
        Paragraph(
            "以下均为本次1000人、Seed 47、W2工作日、A1主线老年行为的单次机制实验结果。",
            s["subtitle"],
        )
    )
    story += section("四、三年龄组结果", s)
    age_rows = [["年龄组", "Agent", "出行腿", "步行", "公交", "地铁", "网约车", "网约车占比", "平均时间", "必要活动"]]
    for group in ("18-39", "40-59", "60+"):
        row = summary["age_results"][group]
        mc = row["final_successful_mode_counts"]
        age_rows.append(
            [
                group,
                row["agents"],
                row["travel_decisions"],
                mc["walk"],
                mc["bus"],
                mc["metro"],
                mc["ride_hailing"],
                pct(mc["ride_hailing"] / row["travel_decisions"]),
                f"{row['mean_total_travel_time']:.1f}分",
                pct(row["necessary_activity_completion_rate"]),
            ]
        )
    at = Table(age_rows, colWidths=[15 * mm, 15 * mm, 17 * mm, 12 * mm, 14 * mm, 14 * mm, 16 * mm, 22 * mm, 21 * mm, 22 * mm])
    at.setStyle(base_table_style())
    story.append(at)
    story.append(Spacer(1, 1.4 * mm))
    story.append(
        Paragraph(
            f"60+继续保留步行偏好常数-1.5、较低时间价值和数字接入约束，同时采用网约车常数0.3、W2暴露权重1.6、"
            f"必要出行费用敏感系数0.9和每次换乘3分钟感知负担；本次公交占{pct(summary['age_results']['60+']['final_successful_mode_counts']['bus'] / summary['age_results']['60+']['travel_decisions'])}、"
            f"地铁占{pct(summary['age_results']['60+']['final_successful_mode_counts']['metro'] / summary['age_results']['60+']['travel_decisions'])}、"
            f"网约车占{pct(summary['age_results']['60+']['final_successful_mode_counts']['ride_hailing'] / summary['age_results']['60+']['travel_decisions'])}。",
            s["body"],
        )
    )

    story += section("五、老年数字鸿沟与家庭代叫", s)
    elder = summary["elder_digital_gap_results"]
    elder_data = [
        ["60+分组", "Agent", "出行腿", "网约车腿", "网约车占比", "平均时间", "必要活动完成率"],
        ["数字自助", elder["digital_self"]["agents"], elder["digital_self"]["travel_legs"], elder["digital_self"]["ride_hailing_legs"], pct(elder["digital_self"]["ride_hailing_share"]), f"{elder['digital_self']['mean_total_travel_time']:.1f}分", pct(elder["digital_self"]["necessary_activity_completion_rate"])],
        ["家庭代叫", elder["family_proxy"]["agents"], elder["family_proxy"]["travel_legs"], elder["family_proxy"]["ride_hailing_legs"], pct(elder["family_proxy"]["ride_hailing_share"]), f"{elder['family_proxy']['mean_total_travel_time']:.1f}分", pct(elder["family_proxy"]["necessary_activity_completion_rate"])],
        ["无数字且无家庭协助", elder["nondigital_unassisted"]["agents"], elder["nondigital_unassisted"]["travel_legs"], elder["nondigital_unassisted"]["ride_hailing_legs"], pct(elder["nondigital_unassisted"]["ride_hailing_share"]), f"{elder['nondigital_unassisted']['mean_total_travel_time']:.1f}分", pct(elder["nondigital_unassisted"]["necessary_activity_completion_rate"])],
    ]
    et = Table(elder_data, colWidths=[35 * mm, 17 * mm, 18 * mm, 21 * mm, 23 * mm, 23 * mm, 27 * mm])
    et.setStyle(base_table_style())
    story.append(et)
    story.append(Spacer(1, 1.3 * mm))
    story.append(
        Paragraph(
            f"数字鸿沟主要体现在网约车访问：{elder['nondigital_unassisted']['agents']}名无数字且无家庭协助老人共{elder['nondigital_unassisted']['travel_legs']}条出行腿，"
            f"网约车为{elder['nondigital_unassisted']['ride_hailing_legs']}；家庭代叫组为{elder['family_proxy']['ride_hailing_legs']}/{elder['family_proxy']['travel_legs']}，"
            f"数字自助组为{elder['digital_self']['ride_hailing_legs']}/{elder['digital_self']['travel_legs']}。由于模型设定公交/地铁不受容量限制，三组必要活动完成率仍较高，"
            "不能把网约车可达性差异直接解释为活动损失。",
            s["body"],
        )
    )

    story += section("六、公共品优惠券漏斗", s)
    funnel = summary["coupon_funnel"]
    funnel_data = [
        ["阶段", "人数", "相对上一步", "规则"],
        ["触达", funnel["reached"], pct(funnel["reached"] / 1000), "数字自助、家庭或社区代叫"],
        ["参与", funnel["participated"], pct(funnel["participated"] / funnel["reached"]), "622次真实API第一轮贡献"],
        ["获券", funnel["awarded"], pct(funnel["awarded"] / funnel["participated"]), "需求65% + 合作35%排序；实体池200"],
        ["首决策可用", funnel["available_at_first_travel_choice"], pct(funnel["available_at_first_travel_choice"] / funnel["awarded"]), "仅首个出行决策"],
        ["绑定并核销", funnel["redeemed"], pct(funnel["redeemed"] / funnel["available_at_first_travel_choice"]), "仅网约车且派单成功"],
    ]
    ft = Table(funnel_data, colWidths=[31 * mm, 24 * mm, 30 * mm, 91 * mm])
    ft.setStyle(base_table_style())
    story.append(ft)
    story.append(Spacer(1, 1.3 * mm))
    story.append(
        Paragraph(
            "公共品倍率1.6只改变虚拟收益，新增实体券为0。按优先级结果，18-39获券0、40-59获券105、60+获券95；"
            "该分布来自当前需求权重与合作权重，不是政策建议。",
            s["body"],
        )
    )

    story += section("七、活动结果与限制", s)
    activity_table = Table(
        [
            ["指标", "总体", "18-39", "40-59", "60+"],
            ["活动完成率", pct(summary["activity_completion_rate"]), pct(summary["age_results"]["18-39"]["activity_completion_rate"]), pct(summary["age_results"]["40-59"]["activity_completion_rate"]), pct(summary["age_results"]["60+"]["activity_completion_rate"])],
            ["必要活动完成率", pct(summary["necessary_activity_completion_rate"]), pct(summary["age_results"]["18-39"]["necessary_activity_completion_rate"]), pct(summary["age_results"]["40-59"]["necessary_activity_completion_rate"]), pct(summary["age_results"]["60+"]["necessary_activity_completion_rate"])],
            ["交通成功率", pct(summary["transport_success_rate"]), "100.0%", "100.0%", "100.0%"],
        ],
        colWidths=[48 * mm, 30 * mm, 30 * mm, 30 * mm, 30 * mm],
    )
    activity_table.setStyle(base_table_style())
    activity_table.setStyle(TableStyle([("ALIGN", (1, 1), (-1, -1), "CENTER")]))
    story.append(activity_table)
    story.append(Spacer(1, 1.4 * mm))
    limitations = [
        "这是九区机制实验，不是经观测数据标定的上海交通预测；单一Seed不能提供统计置信区间。",
        "公交和地铁容量不受限，网约车车队按200人基线同比例扩为240辆，因此本次派单失败为0。",
        "本次明确采用main提交7d21a4f的A1老年行为，因此不能与200人API或旧1000人A0结果视为严格同口径重复；报告单列A0对照。",
        "API响应具有模型随机性；检查点可避免中断后重复消耗，但幂等去重仍取决于服务商支持。",
    ]
    story.append(
        KeepTogether(
            [
                Paragraph(
                    "<br/>".join(f"• {item}" for item in limitations),
                    s["small"],
                )
            ]
        )
    )

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    print(json.dumps({"output": str(OUTPUT), "pages": 2}, ensure_ascii=True))


if __name__ == "__main__":
    main()
