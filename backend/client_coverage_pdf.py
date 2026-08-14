from __future__ import annotations

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


def build_coverage_pdf(output_path: str, title: str, highlights: dict, rows: list[dict]) -> str:
    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        rightMargin=0.85 * inch,
        leftMargin=0.85 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )

    styles = getSampleStyleSheet()
    brand = ParagraphStyle(
        "Brand",
        parent=styles["Title"],
        fontSize=22,
        leading=26,
        alignment=1,
        textColor=colors.HexColor("#b8860b"),
        spaceAfter=2,
    )
    small_brand = ParagraphStyle(
        "SmallBrand",
        parent=styles["Normal"],
        fontSize=10,
        leading=12,
        alignment=1,
        spaceAfter=18,
    )
    subtitle = ParagraphStyle(
        "Subtitle",
        parent=styles["Normal"],
        fontSize=16,
        leading=20,
        alignment=1,
        spaceAfter=18,
    )
    body = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontSize=10.5,
        leading=15,
        spaceAfter=8,
    )

    story = [
        Paragraph("CLAIRE ADLER", brand),
        Paragraph("L U X U R Y&nbsp;&nbsp;P R", small_brand),
        Paragraph(title, subtitle),
        Paragraph("Coverage Highlights:", body),
        Spacer(1, 0.1 * inch),
    ]

    bullets = [
        f"{highlights.get('total_coverage', 0)} pieces of coverage",
        f"{highlights.get('country_count', 0)} countries: {highlights.get('countries', '') or 'N/A'}",
        f"Highlights include {highlights.get('highlight_publications', '') or 'N/A'}",
    ]
    for bullet in bullets:
        story.append(Paragraph(f"&bull;&nbsp;&nbsp;{bullet}", body))

    story.append(Spacer(1, 0.15 * inch))

    for idx, row in enumerate(rows, start=1):
        publication = row.get("publication", "Publication")
        article_url = row.get("article_url", "")
        country = row.get("country", "")
        visits = row.get("monthly_visits_display", "N/A")
        link_note = row.get("link_note", "")

        label = publication
        if article_url:
            label = f'<a href="{article_url}" color="blue"><u>{publication}</u></a>'

        country_text = f" ({country})" if country else ""
        note_text = f" ({link_note})" if link_note else ""
        story.append(Paragraph(
            f"{idx}.&nbsp;&nbsp;{label}{country_text} - {visits} Monthly Visits{note_text}",
            body,
        ))

    doc.build(story)
    return output_path
