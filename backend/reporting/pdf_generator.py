# PDF rendering for the final validation report.

import html
import unicodedata
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Flowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


INK = colors.HexColor("#172033")
MUTED = colors.HexColor("#5D6978")
TEAL = colors.HexColor("#2F6F73")
BLUE = colors.HexColor("#315F8F")
GREEN = colors.HexColor("#1F7A5B")
AMBER = colors.HexColor("#B86B12")
RED = colors.HexColor("#B42318")
LINE = colors.HexColor("#D8E0EA")
SOFT = colors.HexColor("#F3F6F9")
STATUS_COLORS = {"PASS": GREEN, "FLAG": AMBER, "NOT_FOUND": RED}


class DonutChart(Flowable):
    def __init__(self, counts: dict[str, int], width: float = 48 * mm, height: float = 48 * mm):
        super().__init__()
        self.counts = counts
        self.width = width
        self.height = height

    def draw(self) -> None:
        canvas = self.canv
        size = min(self.width, self.height)
        left = (self.width - size) / 2
        bottom = (self.height - size) / 2
        total = max(1, sum(self.counts.values()))
        start = 90.0
        for status in ("PASS", "FLAG", "NOT_FOUND"):
            value = int(self.counts.get(status.lower(), 0) or 0)
            if value <= 0:
                continue
            extent = -360.0 * value / total
            canvas.setFillColor(STATUS_COLORS[status])
            canvas.setStrokeColor(colors.white)
            canvas.wedge(left, bottom, left + size, bottom + size, start, extent, fill=1, stroke=1)
            start += extent
        inset = size * 0.28
        canvas.setFillColor(colors.white)
        canvas.setStrokeColor(colors.white)
        canvas.circle(left + size / 2, bottom + size / 2, inset, fill=1, stroke=0)
        canvas.setFillColor(INK)
        canvas.setFont("Helvetica-Bold", 14)
        canvas.drawCentredString(left + size / 2, bottom + size / 2 + 2, str(sum(self.counts.values())))
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 7)
        canvas.drawCentredString(left + size / 2, bottom + size / 2 - 9, "RULES")


def generate_report_pdf(report: dict[str, Any], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary = report.get("summary", {})
    title = summary.get("report_title", "Validation Report")
    kicker = summary.get("report_kicker", summary.get("standard_name", "Submission Validation"))
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=18 * mm,
        bottomMargin=17 * mm,
        title=title,
        author=f"{summary.get('standard_name', 'Submission')} Validation System",
    )
    styles = _styles()
    story: list[Any] = []
    narrative = report.get("narrative", {})
    key_fields = summary.get("key_field_summary", {})

    story.extend(_cover_block(summary, key_fields, styles, title, kicker))
    story.append(Spacer(1, 7 * mm))
    story.append(Paragraph("Global Findings", styles["section"]))
    story.append(Paragraph(_text(narrative.get("executive_summary")), styles["body"]))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(_text(narrative.get("risk_summary")), styles["body"]))
    story.append(Spacer(1, 5 * mm))
    story.extend(_global_issue_table(report, styles))

    visible_elements = [
        element
        for element in report.get("element_reports", [])
        if element.get("presence_status") != "OPTIONAL_NOT_SUBMITTED"
    ]
    for element in visible_elements:
        story.append(PageBreak())
        story.extend(_element_section(element, styles))

    doc.build(
        story,
        onFirstPage=lambda canvas, document: _page_frame(canvas, document, summary),
        onLaterPages=lambda canvas, document: _page_frame(canvas, document, summary),
    )
    return output_path


def _cover_block(
    summary: dict[str, Any],
    fields: dict[str, str],
    styles: dict[str, ParagraphStyle],
    title_text: str,
    kicker: str,
) -> list[Any]:
    title = Paragraph(_text(title_text).upper(), styles["title"])
    subtitle = Paragraph(f"{_text(kicker)} - Validation and Review Record", styles["subtitle"])
    status = _status_paragraph(summary.get("overall_status", "N/A"), styles)
    header = Table([[title, status], [subtitle, ""]], colWidths=[145 * mm, 28 * mm])
    header.setStyle(
        TableStyle(
            [
                ("SPAN", (0, 1), (1, 1)),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    rows = [
        ("Part Number", fields.get("customer_part_number") or fields.get("part_number") or "Not identified"),
        ("Part Name", fields.get("part_name") or "Not identified"),
        ("Drawing / Revision", " / ".join(filter(None, [fields.get("drawing_number"), fields.get("revision")])) or "Not identified"),
        ("Supplier", fields.get("supplier_name") or "Not identified"),
        ("Customer", fields.get("customer_name") or "Not identified"),
        ("Scope", str(summary.get("submission_level") or "Configured / customer agreed")),
        ("Reason for Submission", fields.get("reason_for_submission") or "Not identified"),
        ("Human Decisions", str(summary.get("human_override_count", 0))),
    ]
    data = []
    for index in range(0, len(rows), 2):
        first = rows[index]
        second = rows[index + 1]
        data.append(
            [
                Paragraph(_text(first[0]), styles["field_label"]),
                Paragraph(_text(first[1]), styles["field_value"]),
                Paragraph(_text(second[0]), styles["field_label"]),
                Paragraph(_text(second[1]), styles["field_value"]),
            ]
        )
    table = Table(data, colWidths=[28 * mm, 58 * mm, 30 * mm, 57 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), SOFT),
                ("BACKGROUND", (2, 0), (2, -1), SOFT),
                ("GRID", (0, 0), (-1, -1), 0.5, LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return [header, Spacer(1, 5 * mm), table]


def _global_issue_table(report: dict[str, Any], styles: dict[str, ParagraphStyle]) -> list[Any]:
    prefix = report.get("summary", {}).get("artifact_prefix", "E")
    issues = report.get("tables", {}).get("critical_issues", [])[:12]
    if not issues:
        return [Paragraph("No unresolved global findings were identified.", styles["success_note"])]
    rows = [["Priority", "Artifact", "Rule", "Finding", "Action"]]
    for issue in issues:
        rows.append(
            [
                _cell(issue.get("priority"), styles),
                _cell(f"{prefix}{int(issue.get('element_number', 0)):02d}", styles),
                _cell(issue.get("rule_id"), styles),
                _cell(issue.get("issue"), styles),
                _cell(issue.get("recommended_action") or "Review and resolve.", styles),
            ]
        )
    table = Table(rows, colWidths=[18 * mm, 17 * mm, 14 * mm, 61 * mm, 63 * mm], repeatRows=1)
    table.setStyle(_table_style())
    return [table]


def _element_section(element: dict[str, Any], styles: dict[str, ParagraphStyle]) -> list[Any]:
    number = int(element["element_number"])
    prefix = element.get("artifact_prefix", "E")
    heading = Table(
        [[Paragraph(f"{prefix}{number:02d}  {_text(element['element_name'])}", styles["element_title"]), _status_paragraph(element.get("element_status"), styles)]],
        colWidths=[145 * mm, 28 * mm],
    )
    heading.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (1, 0), (1, 0), "RIGHT")]))
    issues = element.get("top_issues", [])
    important = issues[0].get("issue") if issues else "No material finding was identified for this artifact."
    finding = Paragraph(f"<b>Important finding:</b> {_text(important)}", styles["finding"])

    counts = element.get("checkpoint_summary", {})
    chart_counts = {
        "pass": int(counts.get("pass", 0)),
        "flag": int(counts.get("flag", 0)),
        "not_found": int(counts.get("not_found", 0)),
    }
    legend = Table(
        [
            [_legend_marker(GREEN), Paragraph(f"Passed: {chart_counts['pass']}", styles["body"])],
            [_legend_marker(AMBER), Paragraph(f"Flagged: {chart_counts['flag']}", styles["body"])],
            [_legend_marker(RED), Paragraph(f"Not found: {chart_counts['not_found']}", styles["body"])],
            ["", Paragraph(_text(element.get("narrative", {}).get("risk_statement")), styles["small"])],
        ],
        colWidths=[8 * mm, 90 * mm],
    )
    legend.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    chart_row = Table([[DonutChart(chart_counts), legend]], colWidths=[58 * mm, 115 * mm])
    chart_row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("BOX", (0, 0), (-1, -1), 0.5, LINE), ("BACKGROUND", (0, 0), (-1, -1), colors.white), ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))

    rows = [["Rule", "Final", "Source", "Reason / Remark", "Evidence Location", "Action"]]
    for checkpoint in element.get("checkpoint_results", []):
        rows.append(
            [
                _cell(checkpoint.get("rule_id"), styles),
                _cell(checkpoint.get("status"), styles),
                _cell(checkpoint.get("final_decision_source", "AI"), styles),
                _cell(checkpoint.get("reason"), styles),
                _cell(checkpoint.get("final_evidence_location") or "No location available", styles),
                _cell(checkpoint.get("recommended_action") or "-", styles),
            ]
        )
    rule_table = Table(
        rows,
        colWidths=[11 * mm, 18 * mm, 16 * mm, 48 * mm, 52 * mm, 28 * mm],
        repeatRows=1,
    )
    rule_table.setStyle(_table_style())
    return [heading, Spacer(1, 3 * mm), finding, Spacer(1, 4 * mm), chart_row, Spacer(1, 5 * mm), rule_table]


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("PPAPTitle", parent=base["Title"], fontName="Helvetica-Bold", fontSize=24, leading=27, textColor=INK, alignment=TA_LEFT, spaceAfter=0),
        "subtitle": ParagraphStyle("PPAPSubtitle", parent=base["BodyText"], fontSize=9, leading=12, textColor=MUTED),
        "section": ParagraphStyle("PPAPSection", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=14, leading=17, textColor=INK, spaceBefore=2, spaceAfter=7),
        "element_title": ParagraphStyle("PPAPElement", parent=base["Heading1"], fontName="Helvetica-Bold", fontSize=16, leading=19, textColor=INK),
        "body": ParagraphStyle("PPAPBody", parent=base["BodyText"], fontSize=9, leading=13, textColor=INK),
        "small": ParagraphStyle("PPAPSmall", parent=base["BodyText"], fontSize=8, leading=11, textColor=MUTED),
        "cell": ParagraphStyle("PPAPCell", parent=base["BodyText"], fontSize=7, leading=9, textColor=INK),
        "field_label": ParagraphStyle("FieldLabel", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=7.5, leading=10, textColor=MUTED),
        "field_value": ParagraphStyle("FieldValue", parent=base["BodyText"], fontSize=8.5, leading=11, textColor=INK),
        "finding": ParagraphStyle("Finding", parent=base["BodyText"], fontSize=9, leading=13, textColor=INK, backColor=colors.HexColor("#FFF4DF"), borderColor=colors.HexColor("#E6C58F"), borderWidth=0.5, borderPadding=7),
        "success_note": ParagraphStyle("Success", parent=base["BodyText"], fontSize=9, leading=13, textColor=GREEN, backColor=colors.HexColor("#E8F6EF"), borderPadding=7),
        "status": ParagraphStyle("Status", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=8, leading=10, alignment=TA_CENTER, textColor=colors.white),
    }


def _status_paragraph(status: Any, styles: dict[str, ParagraphStyle]) -> Table:
    text = str(status or "N/A").upper()
    color = {"PASS": GREEN, "REVIEW": AMBER, "NOT_FOUND": RED, "N/A": MUTED}.get(text, BLUE)
    table = Table([[Paragraph(_text(text.replace("_", " ")), styles["status"])]], colWidths=[27 * mm])
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), color), ("BOX", (0, 0), (-1, -1), 0, color), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    return table


def _legend_marker(color: colors.Color) -> Table:
    marker = Table([[""]], colWidths=[4 * mm], rowHeights=[4 * mm])
    marker.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), color), ("BOX", (0, 0), (-1, -1), 0, color)]))
    return marker


def _table_style() -> TableStyle:
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#315F8F")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 7),
            ("GRID", (0, 0), (-1, -1), 0.35, LINE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SOFT]),
        ]
    )


def _cell(value: Any, styles: dict[str, ParagraphStyle]) -> Paragraph:
    return Paragraph(_text(value), styles["cell"])


def _text(value: Any) -> str:
    raw = "" if value is None else str(value)
    replacements = {"\u2013": "-", "\u2014": "-", "\u2192": "->", "\u21d4": "<->", "\u2265": ">=", "\u2264": "<=", "\u00d7": "x"}
    for source, target in replacements.items():
        raw = raw.replace(source, target)
    normalized = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    return html.escape(" ".join(normalized.split()))


def _page_frame(canvas, document, summary: dict[str, Any]) -> None:
    canvas.saveState()
    width, height = A4
    canvas.setStrokeColor(LINE)
    canvas.line(16 * mm, 12 * mm, width - 16 * mm, 12 * mm)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 7)
    title = _text(summary.get("report_title") or "Validation Report")
    canvas.drawString(16 * mm, 8 * mm, f"{title} | Case {_text(summary.get('case_id'))}")
    canvas.drawRightString(width - 16 * mm, 8 * mm, f"Page {document.page}")
    canvas.restoreState()
