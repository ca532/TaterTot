from __future__ import annotations

from html import escape
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
FONT_NAMES = {
    "light": "CoverageMontserratLight",
    "regular": "CoverageMontserratRegular",
    "medium": "CoverageMontserratMedium",
}


def _register_fonts() -> None:
    for weight, filename in (
        ("light", "Montserrat-Light.ttf"),
        ("regular", "Montserrat-Regular.ttf"),
        ("medium", "Montserrat-Medium.ttf"),
    ):
        font_name = FONT_NAMES[weight]
        if font_name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(font_name, FONT_DIR / filename))


def build_coverage_pdf(output_path: str, title: str, highlights: dict, rows: list[dict]) -> str:
    _register_fonts()
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
        fontName=FONT_NAMES["medium"],
        fontSize=25,
        leading=29,
        alignment=1,
        textColor=colors.HexColor("#b8860b"),
        spaceAfter=2,
    )
    small_brand = ParagraphStyle(
        "SmallBrand",
        parent=styles["Normal"],
        fontName=FONT_NAMES["medium"],
        fontSize=12,
        leading=14,
        alignment=1,
        spaceAfter=24,
    )
    subtitle = ParagraphStyle(
        "Subtitle",
        parent=styles["Normal"],
        fontName=FONT_NAMES["regular"],
        fontSize=20,
        leading=26,
        alignment=1,
        spaceAfter=24,
    )
    body = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontName=FONT_NAMES["light"],
        fontSize=11.5,
        leading=17,
        spaceAfter=10,
    )
    section_heading = ParagraphStyle(
        "SectionHeading",
        parent=body,
        fontName=FONT_NAMES["regular"],
        spaceAfter=10,
    )

    story = [
        Paragraph("CLAIRE ADLER", brand),
        Paragraph("L U X U R Y&nbsp;&nbsp;P R", small_brand),
        Paragraph(escape(str(title or "")), subtitle),
        Paragraph("Coverage Highlights:", section_heading),
        Spacer(1, 0.1 * inch),
    ]

    bullets = [
        f"{highlights.get('total_coverage', 0)} pieces of coverage",
        f"{highlights.get('publication_count', len({row.get('publication') for row in rows if row.get('publication')}))} unique publications",
        f"{highlights.get('country_count', 0)} countries: "
        f"{escape(str(highlights.get('countries', '') or 'N/A'))}",
        "Highlights include "
        f"{escape(str(highlights.get('highlight_publications', '') or 'N/A'))}",
    ]
    for bullet in bullets:
        story.append(Paragraph(f"&bull;&nbsp;&nbsp;{bullet}", body))

    story.append(Spacer(1, 0.15 * inch))

    for idx, row in enumerate(rows, start=1):
        publication = escape(str(row.get("publication") or "Publication"))
        article_url = escape(str(row.get("article_url") or ""), quote=True)
        country = escape(str(row.get("country") or ""))
        link_note = escape(str(row.get("link_note") or ""))

        label = publication
        if article_url:
            label = f'<a href="{article_url}" color="blue"><u>{publication}</u></a>'

        country_text = f" ({country})" if country else ""
        note_text = f" ({link_note})" if link_note else ""
        visits = str(row.get("monthly_visits_display") or "N/A")
        visits_text = (
            f" - {escape(visits)} Monthly Visits"
            if visits != "N/A"
            else ""
        )
        story.append(Paragraph(
            f"{idx}.&nbsp;&nbsp;{label}{country_text}{visits_text}{note_text}",
            body,
        ))

    doc.build(story)
    return output_path
