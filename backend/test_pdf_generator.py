import json
import tempfile
import unittest
from pathlib import Path

from PDFGenerator import (
    ROUNDUP_FONT,
    ROUNDUP_FONT_BOLD,
    ROUNDUP_FONT_LIGHT,
    weeklyRoundupPDF,
)


class WeeklyRoundupPDFTests(unittest.TestCase):
    def test_uses_unicode_fonts_and_generates_unicode_content(self):
        generator = weeklyRoundupPDF(topic_name="Luxury", lookback_days=14)
        self.assertEqual(ROUNDUP_FONT_BOLD, generator.title_style.fontName)
        self.assertEqual(ROUNDUP_FONT_BOLD, generator.publication_style.fontName)
        self.assertEqual(ROUNDUP_FONT, generator.article_style.fontName)
        self.assertEqual(ROUNDUP_FONT_LIGHT, generator.meta_style.fontName)

        rows = [{
            "publication": "The Jewels Club",
            "title": "Zoë's £2,000 Émeraude necklace",
            "author": "Anaïs Müller",
            "summary": (
                "The jeweller presented a diamond necklace with an emerald "
                "pendant, preserving the collection's original craftsmanship."
            ),
            "published_date": "2026-08-30T00:00:00",
            "url": "https://example.com/unicode-jewelry",
        }]

        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = Path(temp_dir) / "roundup.json"
            pdf_path = Path(temp_dir) / "roundup.pdf"
            json_path.write_text(
                json.dumps(rows, ensure_ascii=False),
                encoding="utf-8",
            )
            result = generator.generate_pdf(str(json_path), str(pdf_path))
            self.assertEqual(str(pdf_path), result)
            self.assertGreater(pdf_path.stat().st_size, 1_000)


if __name__ == "__main__":
    unittest.main()
