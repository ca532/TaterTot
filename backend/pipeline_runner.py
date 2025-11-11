"""
Main Pipeline Runner
Integrates AgentCollector, AgentSumm, and Google Sheets storage
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
except ImportError:
    print("⚠️  Could not import AgentCollector or AgentSumm")
    print("Make sure these files are in the same directory")
    sys.exit(1)


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
        Step 1: Collect articles from publications
        """
        print("\n" + "="*60)
        print("STEP 1: COLLECTING ARTICLES")
        print("="*60 + "\n")
        
        try:
            # Run your collector (collects top 3 from each publication)
            articles = self.collector.collect_top_3_per_publication()
            
            if not articles:
                print("⚠️  No articles collected")
                return []
            
            print(f"\n✅ Collected {len(articles)} total articles")
            
            # Convert to format for Google Sheets
            articles_data = []
            for article in articles:
                articles_data.append({
                    'id': f"article-{datetime.now().strftime('%Y%m%d')}-{article.id if hasattr(article, 'id') else len(articles_data)}",
                    'title': article.title,
                    'url': article.url,
                    'publication': article.publication,
                    'journalist': article.author,
                    'summary': article.summary,
                })
            
            # Save to Google Sheets
            self.db.save_articles(articles_data)
            
            return articles_data
            
        except Exception as e:
            print(f"❌ Error during collection: {str(e)}")
            raise
    
    def run_summarization(self):
        """
        Step 2: Summarize articles (if needed - your collector already does this)
        """
        print("\n" + "="*60)
        print("STEP 2: SUMMARIZATION")
        print("="*60 + "\n")
        
        # Your collector already creates summaries
        # This step is optional - only needed if you want to re-summarize
        print("✅ Summaries already created during collection")
        
        # Get recent articles from Sheets
        recent_articles = self.db.get_recent_articles(limit=10)
        print(f"📊 {len(recent_articles)} articles in database")
        
        return recent_articles
    
    def generate_drafts(self):
        """
        Step 3: Generate outreach drafts (Phase 3 of your plan)
        For now, this is a placeholder
        """
        print("\n" + "="*60)
        print("STEP 3: GENERATING OUTREACH DRAFTS")
        print("="*60 + "\n")
        
        print("⚠️  Draft generation not yet implemented")
        print("This will be added in Phase 3 of your project")
        
        # Get pitching menu
        menu = self.db.get_pitching_menu()
        print(f"📋 {len(menu)} topics in pitching menu")
        
        return []
    
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
            articles = self.run_collection()
            
            # Step 2: Summarization (already done in collection)
            self.run_summarization()
            
            # Step 3: Generate drafts (Phase 3 - future)
            # self.generate_drafts()
            
            # Summary
            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()
            
            print("\n" + "="*60)
            print("✅ PIPELINE COMPLETED SUCCESSFULLY")
            print("="*60)
            print(f"⏱️  Duration: {duration:.1f} seconds")
            print(f"📊 Articles collected: {len(articles)}")
            print(f"⏰ End time: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
            print("="*60 + "\n")
            
            return {
                'success': True,
                'articles_collected': len(articles),
                'duration_seconds': duration
            }
            
        except Exception as e:
            print("\n" + "="*60)
            print("❌ PIPELINE FAILED")
            print("="*60)
            print(f"Error: {str(e)}")
            print("="*60 + "\n")
            raise


def main():
    """
    Main entry point
    """
    try:
        runner = PipelineRunner()
        result = runner.run_full_pipeline()
        
        # Exit with success
        sys.exit(0)
        
    except Exception as e:
        print(f"\n❌ Pipeline failed with error: {str(e)}")
        # Exit with error code
        sys.exit(1)


if __name__ == "__main__":
    main()