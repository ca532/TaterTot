"""
Main Pipeline Runner
Integrates AgentCollector, AgentSumm, Google Sheets storage, and PDF generation
"""

import os
import sys
from datetime import datetime
from google_storage import GoogleSheetsDB

# Import your existing agents
try:
    from AgentCollector import CustomArticleCollector
    from AgentSumm import ArticleSummarizer
    print("✅ Collector and Summarizer loaded")
except ImportError as e:
    print(f"❌ Could not import agents: {e}")
    sys.exit(1)

# Import PDF generator
PDF_AVAILABLE = False
try:
    from PDFGenerator import weeklyRoundupPDF
    PDF_AVAILABLE = True
    print("✅ PDF Generator loaded successfully")
except ImportError as e:
    print(f"⚠️  PDF Generator not available: {e}")
    print("Install reportlab: pip install reportlab")
    sys.exit(1)


def _parse_keywords_override() -> list[str]:
    raw = os.getenv("KEYWORDS_OVERRIDE", "").strip()
    if not raw:
        return []
    return [k.strip().lower() for k in raw.split(",") if k.strip()]


class PipelineRunner:
    """
    Main pipeline orchestrator
    """
    
    def __init__(self):
        print("\n" + "="*60)
        print("Article Pipeline - Initializing")
        print("="*60 + "\n")
        
        # Initialize Google Sheets storage
        self.db = GoogleSheetsDB()
        
        # Initialize your agents
        print("🤖 Initializing Article Collector...")
        self.collector = CustomArticleCollector()

        
        override_keywords = _parse_keywords_override()
        if override_keywords:
            self.collector.set_keywords_override(override_keywords)
            print(f"Using keyword override ({len(override_keywords)} keywords)")
        
        print("🤖 Initializing Article Summarizer...")
        self.summarizer = ArticleSummarizer()
        
        print("\n✅ Pipeline initialized successfully\n")
    
    def run_collection(self):
        """
        Collect top 3 articles from each publication
        """
        print("\n" + "="*60)
        print("STEP 1: COLLECTING ARTICLES")
        print("="*60 + "\n")
        
        try:
            articles = self.collector.collect_top_3_per_publication()
            
            if not articles:
                print("⚠️  No articles collected")
                return []
            
            print(f"\n✅ Collected {len(articles)} total articles")
            
            # Convert to dictionaries - Author already handled in Collector
            articles_data = []
            for idx, article in enumerate(articles):
                author = article.author if hasattr(article, 'author') and article.author else 'Unknown'

                article_dict = {
                    'id': f"article-{datetime.now().strftime('%Y%m%d')}-{idx+1}",
                    'title': article.title,
                    'url': article.url,
                    'publication': article.publication,
                    'full_content': article.full_content,  # Keep for summarization
                    'journalist': 'Unknown',  # Placeholder
                    'author': author,      # Placeholder
                    'summary': '',            # Will be filled by summarizer
                }
                articles_data.append(article_dict)
            
            return articles_data
            
        except Exception as e:
            print(f"❌ Error during collection: {str(e)}")
            import traceback
            traceback.print_exc()
            raise

    def run_summarization(self, articles_data):
        """
        Summarize articles and extract authors using AgentSumm
        """
        print("\n" + "="*60)
        print("STEP 2: SUMMARIZATION & AUTHOR EXTRACTION")
        print("="*60 + "\n")
        
        if not articles_data:
            print("⚠️  No articles to summarize")
            return []
        
        summarized_articles = []
        
        for idx, article in enumerate(articles_data):
            print(f"[{idx+1}/{len(articles_data)}] Processing: {article['title'][:60]}...")
            
            try:
                # Use AgentSumm to summarize AND extract author
                summary_obj = self.summarizer.summarize_article(
                    article['full_content'],
                    article['url'],
                    article['publication'],
                    article['title'],
                    article['author']  # Pass placeholder
                )
                
                if summary_obj:
                    # Update article with summary and author from AgentSumm
                    article['summary'] = summary_obj.summary
                    article['author'] = summary_obj.author
                    article['journalist'] = summary_obj.author
                    
                    # Remove full_content before saving to Sheets
                    article.pop('full_content', None)
                    
                    summarized_articles.append(article)
                    print(f"    ✅ Author: {summary_obj.author}")
                else:
                    print(f"    ❌ Failed to summarize")
                    
            except Exception as e:
                print(f"    ❌ Error: {str(e)[:60]}")
        
        print(f"\n✅ Summarized {len(summarized_articles)} articles")
        return summarized_articles
        
    def generate_pdf(self, articles_data):
        """
        Generate PDF output only
        """
        print("\n" + "="*60)
        print("STEP 2: GENERATING PDF")
        print("="*60 + "\n")
        
        if not articles_data:
            print("⚠️  No data to generate PDF")
            return None
        
        # Create output directory
        os.makedirs('output', exist_ok=True)
        
        # Generate filenames
        date_str = datetime.now().strftime('%Y%m%d')
        
        # Temporary JSON (needed by PDFGenerator)
        json_file = f"output/temp_{date_str}.json"
        pdf_file = f"output/weekly_roundup_{date_str}.pdf"
        
        # Save temporary JSON for PDF generator
        import json
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(articles_data, f, indent=2, ensure_ascii=False)
        
        # Generate PDF
        try:
            print("📝 Generating PDF...")
            pdf_gen = weeklyRoundupPDF()
            pdf_path = pdf_gen.generate_pdf(json_file, pdf_file)
            
            # Clean up temporary JSON
            if os.path.exists(json_file):
                os.remove(json_file)
                print("🧹 Cleaned up temporary files")
            
            if pdf_path and os.path.exists(pdf_path):
                file_size = os.path.getsize(pdf_path)
                print(f"✅ PDF generated successfully!")
                print(f"   File: {pdf_file}")
                print(f"   Size: {file_size:,} bytes ({file_size/1024:.1f} KB)")
                return pdf_path
            else:
                print(f"❌ PDF file not created")
                return None
                
        except Exception as e:
            print(f"❌ PDF generation failed: {e}")
            import traceback
            traceback.print_exc()
            
            # Clean up temp file even on error
            if os.path.exists(json_file):
                os.remove(json_file)
            
            return None
    
    def save_run_metadata(self):
        """
        Save GitHub Actions run info to Metadata sheet
        """
        github_run_number = os.getenv('GITHUB_RUN_NUMBER')
        
        if not github_run_number:
            print("⚠️  Not running on GitHub Actions, skipping metadata save")
            return
        
        github_server = os.getenv('GITHUB_SERVER_URL', 'https://github.com')
        github_repo = os.getenv('GITHUB_REPOSITORY', '')
        github_run_id = os.getenv('GITHUB_RUN_ID', '')
        github_run_url = f"{github_server}/{github_repo}/actions/runs/{github_run_id}"
        
        print(f"\n💾 Saving run metadata to Google Sheets...")
        self.db.save_artifact_info(github_run_number, github_run_url)
        print(f"✅ Run #{github_run_number} metadata saved")
    
    def run_full_pipeline(self):
        """
        Run the complete pipeline
        """
        print("\n" + "="*60)
        print("🚀 STARTING FULL PIPELINE")
        print("="*60)
        print(f"⏰ Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        
        start_time = datetime.now()
        
        try:
            # Step 1: Collect articles (NO author extraction)
            articles_data = self.run_collection()
            
            # Step 2: Summarize and extract authors (AUTHOR EXTRACTION HERE)
            summarized_articles = self.run_summarization(articles_data)
            
            # Step 3: Save to Google Sheets
            print("\n💾 Saving to Google Sheets...")
            self.db.save_articles(summarized_articles)
            
            # Step 4: Generate PDF (renumber this)
            pdf_file = self.generate_pdf(summarized_articles)

            # Step 4.5: Upload PDF to Drive and save links to Metadata
            if pdf_file and os.path.exists(pdf_file):
                print("\n📤 Uploading PDF to Google Drive...")
                download_link, view_link = self.db.upload_pdf_to_drive(pdf_file)
                if download_link or view_link:
                    self.db.save_pdf_links(download_link, view_link)
                else:
                    print("⚠️  PDF upload returned no links; skipping Metadata PDF keys")
            else:
                print("⚠️  No PDF file available for Drive upload")
            
            # Step 5: Save metadata
            self.save_run_metadata()
            
            # Summary
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            
            print("\n" + "="*60)
            print("✅ PIPELINE COMPLETED SUCCESSFULLY")
            print("="*60)
            print(f"⏱️  Duration: {duration:.1f} seconds ({duration/60:.1f} minutes)")
            print(f"📊 Articles collected: {len(summarized_articles)}")
            if pdf_file:
                print(f"📄 PDF file: {pdf_file}")
            print(f"⏰ End time: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
            print("="*60 + "\n")
            
            return {
                'success': True,
                'articles_collected': len(summarized_articles),
                'duration_seconds': duration,
                'pdf_file': pdf_file
            }
            
        except Exception as e:
            print("\n" + "="*60)
            print("❌ PIPELINE FAILED")
            print("="*60)
            print(f"Error: {str(e)}")
            import traceback
            traceback.print_exc()
            print("="*60 + "\n")
            raise


def main():
    """
    Main entry point
    """
    try:
        runner = PipelineRunner()
        result = runner.run_full_pipeline()
        
        print("\n🎉 Pipeline completed successfully!")
        print(f"Check Google Sheets for articles and GitHub artifacts for PDF")
        
        sys.exit(0)
        
    except Exception as e:
        print(f"\n❌ Pipeline failed: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
