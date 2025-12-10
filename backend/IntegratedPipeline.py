import json
from datetime import datetime
import sys
import os

# Import your collector and summarizer
try:
    from AgentCollector import CustomArticleCollector
    from AgentSumm import ArticleSummarizer, extract_author
    print("Collector and Summarizer loaded successfully")
except ImportError as e:
    print(f"Error: Make sure AgentCollector.py and AgentSumm.py are in the same directory")
    print(f"Import error: {e}")
    sys.exit(1)

# Import PDF generator
PDF_AVAILABLE = False
try:
    from PDFGenerator import weeklyRoundupPDF
    PDF_AVAILABLE = True
    print("PDF Generator loaded successfully")
except ImportError as e:
    print(f"Warning: PDFGenerator.py not found or reportlab not installed")
    print(f"Import error: {e}")
    print("PDFs will not be generated. Install reportlab: pip install reportlab")


class IntegratedPipeline:
    def __init__(self):
        """Initialize both collector and summarizer"""
        print("Initializing Weekly Luxury Article Processing Pipeline")
        print("=" * 60)
        
        # Initialize collector
        print("\nSetting up Article Collector...")
        self.collector = CustomArticleCollector()
        
        # Initialize summarizer
        print("\nSetting up Article Summarizer...")
        self.summarizer = ArticleSummarizer("facebook/bart-large-cnn")
        
        print("\nPipeline ready!\n")
    
    def collect_top_articles_per_source(self, articles_per_source: int = 3, sources_subset: list = None):
        """Collect top 2-3 articles from EACH publication for weekly roundup"""
        
        print("PHASE 1: WEEKLY ARTICLE COLLECTION")
        print("=" * 60)
        print(f"Collecting top {articles_per_source} articles from each publication\n")
        
        sources_to_use = sources_subset if sources_subset else list(self.collector.target_sources.keys())
        
        all_articles = []
        
        # Collect from each source individually
        for publication in sources_to_use:
            if publication in self.collector.target_sources:
                print(f"{publication}:")
                
                # Collect from this single source
                source_candidates = self.collector.collect_from_source(
                    publication,
                    self.collector.target_sources[publication]
                )
                
                if not source_candidates:
                    print(f"  No articles found")
                    continue
                
                # Process candidates for this source
                source_candidates.sort(key=lambda x: x.relevance_score, reverse=True)
                top_candidates = source_candidates[:articles_per_source * 3]  # Get extras
                
                source_articles = []
                for candidate in top_candidates:
                    enhanced = self.collector.extract_full_content(candidate)
                    if enhanced:
                        source_articles.append(enhanced)
                    
                    if len(source_articles) >= articles_per_source:
                        break
                
                # Sort and take top N for this source
                source_articles.sort(key=lambda x: x.relevance_score, reverse=True)
                source_articles = source_articles[:articles_per_source]
                
                all_articles.extend(source_articles)
                print(f"  Collected: {len(source_articles)} article(s)\n")
                
                # Delay between sources
                import time, random
                time.sleep(random.uniform(3, 6))
        
        print(f"\nCollection Summary:")
        print(f"Total articles collected: {len(all_articles)}")
        
        return all_articles
    def collect_and_summarize(self, articles_per_source: int = 3, sources_subset: list = None):
        """Main pipeline: Collect top articles per source, then summarize them"""
        
        # Collect top articles from each publication
        articles = self.collect_top_articles_per_source(
            articles_per_source=articles_per_source,
            sources_subset=sources_subset
        )
        
        if not articles:
            print("\nNo articles collected. Pipeline stopped.")
            return []
        print("=" * 60)
        print(f"Summarizing {len(articles)} collected articles...\n")
        
        # Summarize each collected article
        summarized_articles = []
        
        for i, article in enumerate(articles, 1):
            print(f"[{i}/{len(articles)}] Summarizing: {article.title[:60]}...")
            
            try:
                summary = self.summarizer.summarize_article(
                    article.full_content,
                    article.url,
                    article.publication,
                    article.title,
                    article.author
                )
                
                if summary:
                    summarized_articles.append(summary)
                    print(f"    Success")
                else:
                    print(f"    Failed to generate summary")
                    
            except Exception as e:
                print(f"    Error: {str(e)[:60]}")
        
        print(f"\n\nPIPELINE COMPLETE")
        print("=" * 60)
        print(f"Collected: {len(articles)} articles")
        print(f"Summarized: {len(summarized_articles)} articles")
        
        return summarized_articles
    
    def generate_formatted_output(self, summaries: list) -> str:
        """Generate the final formatted output for client weekly review"""
        if not summaries:
            return "No summaries generated"
        
        output = []
        output.append("\nWEEKLY READING ROUNDUP")
        output.append("=" * 60)
        output.append(f"Date: {datetime.now().strftime('%Y-%m-%d')}")
        output.append(f"Coverage Period: Last 7 days")
        output.append(f"Total Articles: {len(summaries)}\n")
        
        # Group by publication
        by_publication = {}
        for summary in summaries:
            if summary.publication not in by_publication:
                by_publication[summary.publication] = []
            by_publication[summary.publication].append(summary)
        
        output.append("By Publication:")
        for pub, items in sorted(by_publication.items(), key=lambda x: len(x[1]), reverse=True):
            output.append(f"  {pub}: {len(items)}")
        
        output.append("\n\nARTICLE SUMMARIES:")
        output.append("-" * 60)
        
        for i, summary in enumerate(summaries, 1):
            # Format: * [Title](URL) by Author - Summary
            formatted = f"* [{summary.title}]({summary.url}) by {summary.author} - {summary.summary}"
            output.append(f"\n{formatted}")
        
        return "\n".join(output)
    
    def save_summaries(self, summaries: list, filename: str = "weekly_roundup.txt"):
        """Save formatted summaries to TXT, JSON, and PDF"""
        formatted_output = self.generate_formatted_output(summaries)
        
        # Save text file
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(formatted_output)
        
        print(f"\nSaved text roundup to: {filename}")
        
        # Save JSON for backup and PDF generation
        json_filename = filename.replace('.txt', '.json')
        json_data = []
        for summary in summaries:
            json_data.append({
                'title': summary.title,
                'author': summary.author,
                'summary': summary.summary,
                'url': summary.url,
                'publication': summary.publication
            })
        
        with open(json_filename, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)
        
        print(f"Saved JSON backup to: {json_filename}")
        
        # Generate PDF automatically if PDFGenerator is available
        pdf_filename = None
        if PDF_AVAILABLE:
            try:
                pdf_gen = weeklyRoundupPDF()
                pdf_filename = pdf_gen.generate_pdf(json_filename, filename.replace('.txt', '.pdf'))
            except Exception as e:
                print(f"PDF generation failed: {e}")
                print("Text and JSON files are still available")
        else:
            print("\nNote: Install reportlab to auto-generate PDF: pip install reportlab")
        
        return filename, json_filename, pdf_filename


def main():
    """Run the integrated Weekly pipeline"""
    print("Weekly Luxury Article Roundup Generator")
    print("=" * 60)
    
    # Initialize pipeline
    pipeline = IntegratedPipeline()
    
    # Show available sources
    print(f"\nAvailable Sources ({len(pipeline.collector.target_sources)}):")
    for i, pub in enumerate(pipeline.collector.target_sources.keys(), 1):
        print(f"{i:2d}. {pub}")
    
    # Get user input
    try:
        articles_per_source = int(input(f"\nArticles per publication? (default: 3): ") or "3")
        
        if articles_per_source < 2:
            articles_per_source = 2
        elif articles_per_source > 5:
            articles_per_source = 5
            
        use_subset = input("Use specific sources only? (y/N): ").lower().startswith('y')
        sources_subset = None
        
        if use_subset:
            source_names = input("Enter source numbers (comma-separated): ")
            if source_names.strip():
                indices = [int(x.strip()) - 1 for x in source_names.split(',')]
                sources_list = list(pipeline.collector.target_sources.keys())
                sources_subset = [sources_list[i] for i in indices if 0 <= i < len(sources_list)]
                print(f"Using: {', '.join(sources_subset)}\n")
        
    except (ValueError, KeyboardInterrupt):
        articles_per_source = 3
        sources_subset = None
    
    # Run pipeline
    summaries = pipeline.collect_and_summarize(
        articles_per_source=articles_per_source,
        sources_subset=sources_subset
    )
    
    if summaries:
        # Display results
        formatted_output = pipeline.generate_formatted_output(summaries)
        print(f"\n{formatted_output}")
        
        # Save to files (automatically generates PDF too)
        txt_file, json_file, pdf_file = pipeline.save_summaries(
            summaries, 
            f"weekly_roundup_{datetime.now().strftime('%Y%m%d')}.txt"
        )
        
        print(f"\n\nREADY FOR CLIENT REVIEW")
        print("=" * 60)
        if pdf_file:
            print(f"Primary File: {pdf_file}")
            print(f"Backup Files: {txt_file}, {json_file}")
        else:
            print(f"Primary File: {txt_file}")
            print(f"Backup File: {json_file}")
        print(f"Total Articles: {len(summaries)}")
        print(f"Publications Covered: {len(set(s.publication for s in summaries))}")
        print("\nThe weekly reading roundup is ready for your client!")
        
    else:
        print("\nNo summaries generated.")


if __name__ == "__main__":
    main()