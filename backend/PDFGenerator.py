from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from datetime import datetime
import json
from typing import List, Dict

class weeklyRoundupPDF:
    def __init__(self):
        """Initialize PDF generator with styling"""
        self.styles = getSampleStyleSheet()
        
        # Custom styles for the roundup
        self.title_style = ParagraphStyle(
            'CustomTitle',
            parent=self.styles['Heading1'],
            fontSize=24,
            textColor='#2C3E50',
            spaceAfter=30,
            alignment=TA_CENTER
        )
        
        self.header_style = ParagraphStyle(
            'CustomHeader',
            parent=self.styles['Heading2'],
            fontSize=14,
            textColor='#34495E',
            spaceAfter=12,
            spaceBefore=20
        )
        
        self.publication_style = ParagraphStyle(
            'PublicationHeader',
            parent=self.styles['Heading3'],
            fontSize=12,
            textColor='#2C3E50',
            spaceAfter=8,
            spaceBefore=16,
            fontName='Helvetica-Bold'
        )
        
        self.article_style = ParagraphStyle(
            'ArticleText',
            parent=self.styles['BodyText'],
            fontSize=10,
            textColor='#2C3E50',
            spaceAfter=6,
            leading=14,
            leftIndent=10
        )
        
        self.meta_style = ParagraphStyle(
            'MetaText',
            parent=self.styles['BodyText'],
            fontSize=9,
            textColor='#7F8C8D',
            spaceAfter=12,
            leftIndent=10,
            fontName='Helvetica-Oblique'
        )
    
    def generate_pdf(self, json_file: str, output_file: str = None):
        """Generate PDF from JSON summary data"""
        
        # Load JSON data
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                summaries = json.load(f)
        except Exception as e:
            print(f"Error loading JSON file: {e}")
            return None
        
        if not summaries:
            print("No summaries found in JSON file")
            return None
        
        # Generate output filename if not provided
        if not output_file:
            date_str = datetime.now().strftime('%Y%m%d')
            output_file = f"weekly_roundup_{date_str}.pdf"
        
        # Create PDF
        doc = SimpleDocTemplate(
            output_file,
            pagesize=letter,
            rightMargin=72,
            leftMargin=72,
            topMargin=72,
            bottomMargin=18
        )
        
        # Container for PDF elements
        story = []
        
        # Title
        title = Paragraph("Weekly Luxury Jewellery Reading Roundup", self.title_style)
        story.append(title)
        story.append(Spacer(1, 0.2 * inch))
        
        # Metadata
        date_text = f"<b>Date:</b> {datetime.now().strftime('%d %B %Y')}"
        coverage_text = f"<b>Coverage Period:</b> Last 14 days"
        total_text = f"<b>Total Articles:</b> {len(summaries)}"
        
        story.append(Paragraph(date_text, self.article_style))
        story.append(Paragraph(coverage_text, self.article_style))
        story.append(Paragraph(total_text, self.article_style))
        story.append(Spacer(1, 0.3 * inch))
        
        # Group by publication
        by_publication = {}
        for summary in summaries:
            pub = summary.get('publication', 'Unknown')
            if pub not in by_publication:
                by_publication[pub] = []
            by_publication[pub].append(summary)
        
        # Publication summary
        story.append(Paragraph("Publications Covered", self.header_style))
        for pub, items in sorted(by_publication.items(), key=lambda x: len(x[1]), reverse=True):
            pub_line = f"• <b>{pub}</b>: {len(items)} article(s)"
            story.append(Paragraph(pub_line, self.article_style))
        
        story.append(Spacer(1, 0.4 * inch))
        
        # Article summaries section
        story.append(Paragraph("Article Summaries", self.header_style))
        story.append(Spacer(1, 0.2 * inch))
        
        # Add each publication's articles
        for pub, articles in sorted(by_publication.items()):
            # Publication header
            pub_header = f"{pub}"
            story.append(Paragraph(pub_header, self.publication_style))
            
            # Articles for this publication
            for i, article in enumerate(articles, 1):
                title = article.get('title', 'Untitled')
                author = article.get('author', 'Unknown')
                summary = article.get('summary', 'No summary available')
                url = article.get('url', '')
                
                # Format: Title by Author
                article_header = f"<b>{title}</b> by {author}"
                story.append(Paragraph(article_header, self.article_style))
                
                # Summary text
                summary_text = f"{summary}"
                story.append(Paragraph(summary_text, self.article_style))
                
                # URL as metadata
                if url:
                    url_text = f"<link href='{url}'>{url}</link>"
                    story.append(Paragraph(url_text, self.meta_style))
                
                story.append(Spacer(1, 0.15 * inch))
            
            story.append(Spacer(1, 0.1 * inch))
        
        # Build PDF
        try:
            doc.build(story)
            print(f"\nPDF generated successfully: {output_file}")
            return output_file
        except Exception as e:
            print(f"Error generating PDF: {e}")
            return None


def main():
    """Standalone PDF generation from JSON file"""
    import sys
    
    if len(sys.argv) > 1:
        json_file = sys.argv[1]
    else:
        json_file = input("Enter JSON file path: ").strip()
    
    if not json_file:
        print("JSON file required!")
        return
    
    generator = weeklyRoundupPDF()
    pdf_file = generator.generate_pdf(json_file)
    
    if pdf_file:
        print(f"Success! PDF ready for client review: {pdf_file}")
    else:
        print("Failed to generate PDF")


if __name__ == "__main__":
    main()