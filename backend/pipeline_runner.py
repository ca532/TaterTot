"""
Main Pipeline Runner
Integrates AgentCollector, AgentSumm, Google Sheets storage, and PDF generation
"""

import os
import sys
import json
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
    from PDFGenerator import BiweeklyRoundupPDF
    PDF_AVAILABLE = True
    print("✅ PDF Generator loaded successfully")
except ImportError as e:
    print(f"⚠️  PDF Generator not available: {e}")
    print("Will save JSON and TXT only")
    PDF_AVAILABLE = False


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
        
        print("🤖 Initializing Article Summarizer...")
        self.summarizer = ArticleSummarizer()
        
        print("\n✅ Pipeline initialized successfully\n")
    
    def run_collection(self):
        """
        Step 1: Collect top 3 articles from each publication
        """
        print("\n" + "="*60)
        print("STEP 1: COLLECTING ARTICLES (Top 3 per Publication)")
        print("="*60 + "\n")
        
        try:
            # Run your collector
            articles = self.collector.collect_top_3_per_publication()
            
            if not articles:
                print("⚠️  No articles collected")
                return [], []
            
            print(f"\n✅ Collected {len(articles)} total articles")
            
            # Convert ArticleCandidate objects to dictionaries
            articles_data = []
            for idx, article in enumerate(articles):
                # Extract author properly
                author = article.author if hasattr(article, 'author') else 'Unknown'
                summary_text = article.summary if hasattr(article, 'summary') else ''
                
                article_dict = {
                    'id': f"article-{datetime.now().strftime('%Y%m%d')}-{idx+1}",
                    'title': article.title,
                    'url': article.url,
                    'publication': article.publication,
                    'journalist': author,
                    'author': author,  # For PDF generation
                    'summary': summary_text,
                }
                articles_data.append(article_dict)
            
            # Save to Google Sheets
            print("\n💾 Saving to Google Sheets...")
            self.db.save_articles(articles_data)
            
            return articles, articles_data
            
        except Exception as e:
            print(f"❌ Error during collection: {str(e)}")
            import traceback
            traceback.print_exc()
            raise
    
    def generate_outputs(self, articles_data):
        """
        Generate TXT, JSON, and PDF outputs, then upload PDF to Google Drive
        """
        print("\n" + "="*60)
        print("STEP 2: GENERATING OUTPUT FILES")
        print("="*60)
        print(f"PDF_AVAILABLE: {PDF_AVAILABLE}")
        print(f"Number of articles: {len(articles_data)}")
        print(f"Current directory: {os.getcwd()}\n")
        
        if not articles_data:
            print("⚠️  No data to generate outputs")
            return None, None, None
        
        # Create output directory
        os.makedirs('output', exist_ok=True)
        print(f"✅ Output directory ready: {os.path.abspath('output')}\n")
        
        # Generate filenames
        date_str = datetime.now().strftime('%Y%m%d')
        txt_file = f"output/biweekly_roundup_{date_str}.txt"
        json_file = f"output/biweekly_roundup_{date_str}.json"
        pdf_file = f"output/biweekly_roundup_{date_str}.pdf"
        
        # Generate formatted text output
        print("📝 Generating TXT file...")
        formatted_text = self._generate_formatted_text(articles_data)
        with open(txt_file, 'w', encoding='utf-8') as f:
            f.write(formatted_text)
        print(f"✅ Saved: {txt_file}")
        
        # Save JSON
        print("📝 Generating JSON file...")
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(articles_data, f, indent=2, ensure_ascii=False)
        print(f"✅ Saved: {json_file}")
        
        # Generate PDF
        pdf_path = None
        if PDF_AVAILABLE:
            try:
                print("📝 Generating PDF file...")
                pdf_gen = BiweeklyRoundupPDF()
                pdf_path = pdf_gen.generate_pdf(json_file, pdf_file)
                
                if pdf_path and os.path.exists(pdf_path):
                    print(f"✅ PDF generated: {pdf_file}")
                    print(f"   File size: {os.path.getsize(pdf_path)} bytes")
                    
                    # NOW UPLOAD TO GOOGLE DRIVE
                    print("\n" + "="*60)
                    print("STEP 3: UPLOADING PDF TO GOOGLE DRIVE")
                    print("="*60 + "\n")
                    
                    try:
                        download_link, view_link = self.db.upload_pdf_to_drive(pdf_path)
                        
                        if download_link and view_link:
                            print(f"✅ PDF uploaded to Google Drive successfully!")
                            print(f"   View link: {view_link}")
                            print(f"   Download link: {download_link}")
                            
                            # Save links to Metadata sheet
                            print("\n💾 Saving PDF links to Metadata sheet...")
                            self.db.save_pdf_link(download_link, view_link)
                            print("✅ PDF links saved to Google Sheets")
                        else:
                            print("⚠️  Google Drive upload returned no links")
                            
                    except Exception as upload_error:
                        print(f"❌ Error uploading to Google Drive: {str(upload_error)}")
                        import traceback
                        traceback.print_exc()
                        print("\n⚠️  Continuing without Drive upload - PDF still saved locally")
                else:
                    print(f"⚠️  PDF generation did not create file at: {pdf_file}")
                    
            except Exception as e:
                print(f"⚠️  PDF generation failed: {e}")
                import traceback
                traceback.print_exc()
        else:
            print("⚠️  PDF generation skipped (PDFGenerator not available)")
        
        return txt_file, json_file, pdf_path
    
    def _generate_formatted_text(self, articles_data):
        """Generate formatted text output"""
        output = []
        output.append("\nBI-WEEKLY READING ROUNDUP")
        output.append("=" * 60)
        output.append(f"Date: {datetime.now().strftime('%Y-%m-%d')}")
        output.append(f"Coverage Period: Last 14 days")
        output.append(f"Total Articles: {len(articles_data)}\n")
        
        # Group by publication
        by_publication = {}
        for article in articles_data:
            pub = article.get('publication', 'Unknown')
            if pub not in by_publication:
                by_publication[pub] = []
            by_publication[pub].append(article)
        
        output.append("By Publication:")
        for pub, items in sorted(by_publication.items(), key=lambda x: len(x[1]), reverse=True):
            output.append(f"  {pub}: {len(items)}")
        
        output.append("\n\nARTICLE SUMMARIES:")
        output.append("-" * 60)
        
        for article in articles_data:
            formatted = f"\n* [{article['title']}]({article['url']}) by {article['journalist']} - {article['summary']}"
            output.append(formatted)
        
        return "\n".join(output)
    
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
            # Step 1: Collect articles
            articles, articles_data = self.run_collection()
            
            # Step 2: Generate outputs
            txt_file, json_file, pdf_file = self.generate_outputs(articles_data)
            
            # Step 3: Save GitHub Actions info (if running on GitHub)
            github_run_number = os.getenv('GITHUB_RUN_NUMBER')
            github_run_url = os.getenv('GITHUB_SERVER_URL') + '/' + os.getenv('GITHUB_REPOSITORY', '') + '/actions/runs/' + os.getenv('GITHUB_RUN_ID', '')
            
            if github_run_number:
                print(f"\n💾 Saving GitHub Actions artifact info...")
                self.db.save_artifact_info(github_run_number, github_run_url)
            
            # Summary
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            
            print("\n" + "="*60)
            print("✅ PIPELINE COMPLETED SUCCESSFULLY")
            print("="*60)
            print(f"⏱️  Duration: {duration:.1f} seconds ({duration/60:.1f} minutes)")
            print(f"📊 Articles collected: {len(articles)}")
            print(f"📄 TXT file: {txt_file}")
            print(f"📄 JSON file: {json_file}")
            if pdf_file:
                print(f"📄 PDF file: {pdf_file}")
            if github_run_number:
                print(f"🔗 Artifact run: #{github_run_number}")
            print(f"⏰ End time: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
            print("="*60 + "\n")
            
            return {
                'success': True,
                'articles_collected': len(articles),
                'duration_seconds': duration,
                'files': {
                    'txt': txt_file,
                    'json': json_file,
                    'pdf': pdf_file
                }
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
        print(f"Check Google Sheets and Google Drive for results")
        
        # Exit with success
        sys.exit(0)
        
    except Exception as e:
        print(f"\n❌ Pipeline failed with error: {str(e)}")
        # Exit with error code
        sys.exit(1)


if __name__ == "__main__":
    main()