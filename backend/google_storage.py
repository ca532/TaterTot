"""
Google Sheets Storage Backend
Handles reading from and writing to Google Sheets as a database
"""

import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import json
import os


class GoogleSheetsDB:
    """
    Use Google Sheets as a database for the article pipeline
    """
    
    def __init__(self, credentials_path='credentials.json', sheet_id=None):
        """
        Initialize connection to Google Sheets
        
        Args:
            credentials_path: Path to service account JSON file
            sheet_id: Google Sheet ID (from URL)
        """
        # Define scopes
        scope = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        
        # Load credentials
        if os.path.exists(credentials_path):
            # Local development
            creds = Credentials.from_service_account_file(
                credentials_path,
                scopes=scope
            )
        else:
            # GitHub Actions (credentials from environment)
            creds_json = os.getenv('GOOGLE_CREDENTIALS')
            if creds_json:
                creds_dict = json.loads(creds_json)
                creds = Credentials.from_service_account_info(
                    creds_dict,
                    scopes=scope
                )
            else:
                raise ValueError("No credentials found. Set GOOGLE_CREDENTIALS env variable or provide credentials.json")
        
        # Authorize and get client
        self.client = gspread.authorize(creds)
        
        # Open spreadsheet
        sheet_id = sheet_id or os.getenv('GOOGLE_SHEET_ID')
        if not sheet_id:
            raise ValueError("No Sheet ID provided. Set GOOGLE_SHEET_ID env variable or pass sheet_id parameter")
        
        try:
            self.spreadsheet = self.client.open_by_key(sheet_id)
            print(f"✅ Connected to Google Sheet: {self.spreadsheet.title}")
        except Exception as e:
            raise ValueError(f"Failed to open spreadsheet with ID {sheet_id}: {str(e)}")
        
        # Get worksheets
        try:
            self.articles_sheet = self.spreadsheet.worksheet('Articles')
            self.drafts_sheet = self.spreadsheet.worksheet('Outreach Drafts')
            self.pitching_sheet = self.spreadsheet.worksheet('Pitching Menu')
            print("✅ All worksheets found: Articles, Outreach Drafts, Pitching Menu")
        except Exception as e:
            raise ValueError(f"Failed to find worksheets: {str(e)}")
    
    def save_articles(self, articles):
        """
        Save collected articles to Google Sheets
        
        Args:
            articles: List of article dictionaries
        """
        if not articles:
            print("⚠️  No articles to save")
            return
        
        rows = []
        for article in articles:
            rows.append([
                article.get('id', ''),
                article.get('title', ''),
                article.get('url', ''),
                article.get('publication', ''),
                article.get('journalist', 'Unknown'),
                article.get('summary', ''),
                datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ])
        
        # Append to sheet (keeps history)
        try:
            self.articles_sheet.append_rows(rows, value_input_option='USER_ENTERED')
            print(f"✅ Saved {len(articles)} articles to Google Sheets")
        except Exception as e:
            print(f"❌ Error saving articles: {str(e)}")
            raise
    
    def get_recent_articles(self, limit=100):
        """
        Get recent articles for summarization
        
        Args:
            limit: Maximum number of articles to return
            
        Returns:
            List of article dictionaries
        """
        try:
            # Get all records (skips header row)
            records = self.articles_sheet.get_all_records()
            
            # Return most recent
            recent = records[-limit:] if len(records) > limit else records
            print(f"✅ Retrieved {len(recent)} recent articles")
            return recent
        except Exception as e:
            print(f"❌ Error retrieving articles: {str(e)}")
            return []
    
    def save_drafts(self, drafts):
        """
        Save outreach drafts for review
        
        Args:
            drafts: List of draft email dictionaries
        """
        if not drafts:
            print("⚠️  No drafts to save")
            return
        
        rows = []
        for draft in drafts:
            rows.append([
                draft.get('id', ''),
                draft.get('journalist', ''),
                draft.get('email', ''),
                draft.get('subject', ''),
                draft.get('body', ''),
                draft.get('topic', ''),
                'pending',  # status
                'FALSE',    # approved
                datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ])
        
        try:
            self.drafts_sheet.append_rows(rows, value_input_option='USER_ENTERED')
            print(f"✅ Saved {len(drafts)} drafts to Google Sheets")
        except Exception as e:
            print(f"❌ Error saving drafts: {str(e)}")
            raise
    
    def get_pending_drafts(self):
        """
        Get drafts awaiting approval
        
        Returns:
            List of pending draft dictionaries
        """
        try:
            records = self.drafts_sheet.get_all_records()
            
            # Filter for pending drafts
            pending = [r for r in records if r.get('Status', '').lower() == 'pending']
            
            print(f"✅ Retrieved {len(pending)} pending drafts")
            return pending
        except Exception as e:
            print(f"❌ Error retrieving drafts: {str(e)}")
            return []
    
    def get_pitching_menu(self):
        """
        Get active pitching menu items
        
        Returns:
            List of pitching menu dictionaries
        """
        try:
            records = self.pitching_sheet.get_all_records()
            
            # Filter active items
            active = [r for r in records if str(r.get('Active', '')).upper() == 'TRUE']
            
            print(f"✅ Retrieved {len(active)} active pitching menu items")
            return active
        except Exception as e:
            print(f"❌ Error retrieving pitching menu: {str(e)}")
            return []
    
    def update_draft_status(self, draft_id, approved=True):
        """
        Update draft status (for future use)
        
        Args:
            draft_id: ID of the draft to update
            approved: Whether the draft was approved
        """
        try:
            # Find the row with this draft_id
            cell = self.drafts_sheet.find(str(draft_id))
            if cell:
                row_num = cell.row
                
                # Update status columns
                if approved:
                    self.drafts_sheet.update_cell(row_num, 7, 'approved')  # Status column
                    self.drafts_sheet.update_cell(row_num, 8, 'TRUE')      # Approved column
                
                print(f"✅ Draft {draft_id} marked as {'approved' if approved else 'rejected'}")
            else:
                print(f"⚠️  Draft {draft_id} not found")
        except Exception as e:
            print(f"❌ Error updating draft: {str(e)}")


# Test function
def test_connection():
    """
    Test the Google Sheets connection
    """
    print("\n" + "="*60)
    print("Testing Google Sheets Connection")
    print("="*60 + "\n")
    
    try:
        # Create instance (no import needed - defined in this file)
        db = GoogleSheetsDB()
        
        print("\n📊 Testing save_articles...")
        test_articles = [
            {
                'id': 'test-1',
                'title': 'Test Article 1',
                'url': 'https://example.com/article1',
                'publication': 'Test Publication',
                'journalist': 'Test Author',
                'summary': 'This is a test article summary'
            }
        ]
        db.save_articles(test_articles)
        
        print("\n📊 Testing get_pitching_menu...")
        menu = db.get_pitching_menu()
        print(f"Found {len(menu)} pitching menu items:")
        for item in menu:
            print(f"  - {item.get('Topic')}: {item.get('Keywords')}")
        
        print("\n✅ All tests passed!\n")
        
    except Exception as e:
        print(f"\n❌ Test failed: {str(e)}\n")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    # Run test when executed directly
    test_connection()