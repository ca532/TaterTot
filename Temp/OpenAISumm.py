import openai
import re
from dataclasses import dataclass
from typing import Optional, List
import json

@dataclass
class ArticleSummary:
    title: str
    author: str
    summary: str
    url: str
    publication: str
    topics: List[str] = None

class ArticleSummarizer:
    def __init__(self, api_key: str, model: str = "gpt-4"):
        """
        Initialize the Article Summarizer with OpenAI API
        
        Args:
            api_key: OpenAI API key
            model: GPT model to use (default: gpt-4)
        """
        self.client = openai.OpenAI(api_key=api_key)
        self.model = model
    
    def create_summary_prompt(self, article_content: str, publication: str) -> str:
        """Create a structured prompt for article summarization"""
        return f"""
You are a luxury PR professional creating concise article summaries for media outreach in the jewelry/luxury goods industry.

Article Content: {article_content}
Publication: {publication}

Please analyze this article and provide:
1. A clean, professional title (remove any extra formatting/symbols)
2. The journalist's full name (first and last name only)
3. A single sentence summary that captures the main point and key details
4. 2-3 relevant topics/themes

Requirements:
- Summary should be one clear sentence, professional tone
- Focus on luxury/jewelry/fashion angles when present
- If it's a guide/advice article, mention what advice it provides
- If it's news, mention the key facts (prices, companies, deals, etc.)

Format your response as JSON:
{{
    "title": "Clean article title",
    "author": "First Last",
    "summary": "Single sentence summary with key details",
    "topics": ["topic1", "topic2", "topic3"]
}}
"""

    def extract_journalist_fallback(self, article_content: str) -> Optional[str]:
        """
        Fallback method to extract journalist name using regex patterns
        Common byline patterns in articles
        """
        patterns = [
            r"[Bb]y\s+([A-Z][a-z]+\s+[A-Z][a-z]+)",  # "By John Smith"
            r"[Ww]ritten\s+by\s+([A-Z][a-z]+\s+[A-Z][a-z]+)",  # "Written by John Smith"
            r"[Aa]uthor:\s*([A-Z][a-z]+\s+[A-Z][a-z]+)",  # "Author: John Smith"
            r"^([A-Z][a-z]+\s+[A-Z][a-z]+)\s*$",  # Just the name on a line
        ]
        
        for pattern in patterns:
            match = re.search(pattern, article_content[:500])  # Check first 500 chars
            if match:
                return match.group(1)
        
        return None

    def summarize_article(self, article_content: str, article_url: str, publication: str) -> Optional[ArticleSummary]:
        """
        Main method to summarize an article
        
        Args:
            article_content: Full text content of the article
            article_url: URL of the article
            publication: Name of the publication
            
        Returns:
            ArticleSummary object or None if processing fails
        """
        try:
            # Create and send prompt to OpenAI
            prompt = self.create_summary_prompt(article_content, publication)
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a professional PR assistant specializing in luxury goods and jewelry industry analysis."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,  # Lower temperature for more consistent outputs
                max_tokens=300
            )
            
            # Parse JSON response
            result_text = response.choices[0].message.content
            result = json.loads(result_text)
            
            # Fallback for author extraction if LLM didn't find it
            if not result.get("author") or result.get("author") == "Unknown":
                fallback_author = self.extract_journalist_fallback(article_content)
                if fallback_author:
                    result["author"] = fallback_author
            
            # Create ArticleSummary object
            return ArticleSummary(
                title=result.get("title", ""),
                author=result.get("author", "Unknown"),
                summary=result.get("summary", ""),
                url=article_url,
                publication=publication,
                topics=result.get("topics", [])
            )
            
        except json.JSONDecodeError:
            print(f"Failed to parse JSON response for article: {article_url}")
            return None
        except Exception as e:
            print(f"Error processing article {article_url}: {str(e)}")
            return None

    def format_summary_output(self, summary: ArticleSummary) -> str:
        """
        Format the summary in the preferred output format:
        * Title hyperlinked by Author - Summary
        """
        if not summary:
            return ""
            
        # Create hyperlinked title
        hyperlinked_title = f"[{summary.title}]({summary.url})"
        
        # Format: * Title by Author - Summary
        formatted = f"* {hyperlinked_title} by {summary.author} - {summary.summary}"
        
        return formatted

    def process_multiple_articles(self, articles: List[dict]) -> List[str]:
        """
        Process multiple articles and return formatted summaries
        
        Args:
            articles: List of dicts with keys: 'content', 'url', 'publication'
            
        Returns:
            List of formatted summary strings
        """
        formatted_summaries = []
        
        for article in articles:
            summary = self.summarize_article(
                article['content'], 
                article['url'], 
                article['publication']
            )
            
            if summary:
                formatted_output = self.format_summary_output(summary)
                formatted_summaries.append(formatted_output)
                
        return formatted_summaries

# Example usage and testing
def test_summarizer():
    """Test function to demonstrate usage"""
    
    # You'll need to add your OpenAI API key here
    API_KEY = "Enter your key here"
    
    summarizer = ArticleSummarizer(API_KEY)
    
    # Example article data
    test_articles = [
        {
            'content': """
            Luxury jeweller Faberge sold to tech investor in $50m deal
            By Lauren Almeida
            
            Faberge, the luxury Russian jewelry house famous for its ornate Easter eggs, has been sold by its current owners Gemfields to SMG Capital for $50 million. The price represents almost a third of what Gemfields paid for the brand in 2013, highlighting the challenges facing luxury jewelry brands in the current market.
            
            The sale comes as Gemfields focuses on its core mining operations while SMG Capital, led by tech investor Sarah Morgan, plans to expand Faberge's digital presence and international reach.
            """,
            'url': 'https://example.com/faberge-sale',
            'publication': 'Luxury Daily'
        }
    ]
    
    # Process articles
    formatted_summaries = summarizer.process_multiple_articles(test_articles)
    
    # Print results
    for summary in formatted_summaries:
        print(summary)

if __name__ == "__main__":
    test_summarizer()